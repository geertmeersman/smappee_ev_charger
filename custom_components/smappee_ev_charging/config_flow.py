import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import (
    SmappeeClient,  # Zorg dat je file client.py heet, anders aanpassen naar .smappee_client
)
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("username"): str,
        vol.Required("password"): str,
    }
)

class SmappeeChargerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Regelt de configuratie-flow voor de Smappee Charger integratie."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialiseer de config flow."""
        self.username: str | None = None
        self.password: str | None = None
        self.stations: list[dict[str, Any]] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Eerste stap: Vraag de gebruiker om zijn Smappee Cloud inloggegevens."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self.username = user_input["username"]
            self.password = user_input["password"]

            try:
                session = async_get_clientsession(self.hass)
                client = SmappeeClient(self.username, self.password, session)

                # REPARATIE 1: Aangepast naar jouw exacte functienaam 'authenticate'
                authenticated = await client.authenticate()

                if not authenticated:
                    raise CannotConnect

                # REPARATIE 2: Aangepast naar jouw exacte v11 servicelocations functienaam
                raw_locations = await client.get_service_locations_full_details()

                # Filter hier direct de locaties die een CHARGINGSTATION zijn (zoals in je client-code)
                self.stations = [
                    loc for loc in raw_locations
                    if loc.get("functionType") == "CHARGINGSTATION"
                ]

                if not self.stations:
                    raise NoChargingStationsFound

                # Als er exact één laadpaal is, direct toevoegen
                if len(self.stations) == 1:
                    station = self.stations[0]
                    # Haal het serienummer veilig op uit de v11 nested dict structuur
                    station_info = station.get("chargingStation", {})
                    serial = station_info.get("serialNumber")

                    return self.async_create_entry(
                        title=station.get("name", f"Smappee Charger {station['id']}"),
                        data={
                            "username": self.username,
                            "password": self.password,
                            "station_id": station["id"],
                            "serial": serial
                        },
                    )

                return await self.async_step_select_station()

            except CannotConnect:
                errors["base"] = "cannot_connect"
            except NoChargingStationsFound:
                errors["base"] = "no_charging_stations_found"
            except Exception as err:
                _LOGGER.exception("Onverwachte fout tijdens authenticatie: %s", err)
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_select_station(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Tweede stap (optioneel): Laat de gebruiker kiezen als er meerdere laders zijn."""
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
                    title=selected_station.get("name", f"Smappee Charger {selected_station['id']}"),
                    data={
                        "username": self.username,
                        "password": self.password,
                        "station_id": selected_station["id"],
                        "serial": serial
                    },
                )

        # Bouw de keuzelijst op voor de dropdown in de UI
        station_options = {}
        for station in self.stations:
            station_info = station.get("chargingStation", {})
            serial = station_info.get("serialNumber", "Onbekend")
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
    """Foutmelding voor mislukte verbinding."""

class NoChargingStationsFound(HomeAssistantError):
    """Foutmelding voor wanneer er geen laadpalen zijn gevonden."""
