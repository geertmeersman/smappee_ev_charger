"""Set up and manage the Smappee Charger configuration flow."""

import logging
from typing import Any

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from .client import SmappeeClient
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("username"): str,
        vol.Required("password"): str,
    }
)


class SmappeeChargerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a configuration flow for the Smappee Charger integration."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the configuration flow entry parameters."""
        self.username: str | None = None
        self.password: str | None = None
        self.stations: list[dict[str, Any]] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Prompt the user for their official Smappee Cloud authentication credentials."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self.username = user_input["username"]
            self.password = user_input["password"]

            try:
                session = async_get_clientsession(self.hass)
                client = SmappeeClient(self.username, self.password, session)

                # Execute official credential challenge check
                authenticated = await client.authenticate()

                if not authenticated:
                    raise CannotConnect

                # Extract explicit service location profiles from backend arrays
                raw_locations = await client.get_service_locations_full_details()

                # Isolate service location records running as functional charging stations
                self.stations = [
                    loc
                    for loc in raw_locations
                    if loc.get("functionType") == "CHARGINGSTATION"
                ]

                if not self.stations:
                    raise NoChargingStationsFound

                # Automatically create the configuration entry if exactly one hardware instance is found
                if len(self.stations) == 1:
                    station = self.stations[0]
                    station_info = station.get("chargingStation", {})
                    serial = station_info.get("serialNumber")

                    return self.async_create_entry(
                        title=station.get("name", f"Smappee Charger {station['id']}"),
                        data={
                            "username": self.username,
                            "password": self.password,
                            "station_id": station["id"],
                            "serial": serial,
                        },
                    )

                return await self.async_step_select_station()

            except CannotConnect:
                errors["base"] = "cannot_connect"
            except NoChargingStationsFound:
                errors["base"] = "no_charging_stations_found"
            except Exception as err:
                _LOGGER.exception(
                    "Unexpected exception triggered during authentication validation: %s",
                    err,
                )
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_select_station(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Prompt the user to isolate their targeted hardware module if multiple units exist."""
        errors: dict[str, str] = {}

        if user_input is not None:
            selected_station_id = user_input["station_id"]
            selected_station = next(
                (s for s in self.stations if s["id"] == selected_station_id), None
            )

            if selected_station:
                station_info = selected_station.get("chargingStation", {})
                serial = station_info.get("serialNumber")

                return self.async_create_entry(
                    title=selected_station.get(
                        "name", f"Smappee Charger {selected_station['id']}"
                    ),
                    data={
                        "username": self.username,
                        "password": self.password,
                        "station_id": selected_station["id"],
                        "serial": serial,
                    },
                )

        # Build structural selection dictionaries for the user drop-down panel interface
        station_options = {}
        for station in self.stations:
            station_info = station.get("chargingStation", {})
            serial = station_info.get("serialNumber", "Unknown")
            name = station.get("name", f"Station {station['id']}")
            station_options[station["id"]] = f"{name} (S/N: {serial})"

        return self.async_show_form(
            step_id="select_station",
            data_schema=vol.Schema(
                {
                    vol.Required("station_id"): vol.In(station_options),
                }
            ),
            errors=errors,
        )


class CannotConnect(HomeAssistantError):
    """Raise exception if the connection authentication sequence fails."""


class NoChargingStationsFound(HomeAssistantError):
    """Raise exception if zero applicable hardware endpoints are returned inside location arrays."""
