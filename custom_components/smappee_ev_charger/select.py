"""Set up and manage Smappee Charger select entities."""

import asyncio
from contextlib import suppress
import json
import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .sensor import SmappeeBaseEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Smappee select entities dynamically based on discovered devices."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    client = entry_data["client"]
    coordinator = entry_data["coordinator"]

    entities = []

    if coordinator.data and "smart_devices" in coordinator.data:
        smart_devices = coordinator.data["smart_devices"]

        for device in smart_devices:
            category = device.get("type", {}).get("category")
            device_id = device.get("id")

            if category == "CARCHARGER" and device_id:
                _LOGGER.debug(
                    "Dynamically creating select dropdown entities for Smappee charger: %s",
                    device_id,
                )
                entities.extend(
                    [
                        SmappeeChargingModeSelect(
                            coordinator, client, entry.title, device_id
                        ),
                        SmappeePhaseRotationSelect(
                            coordinator, client, entry.title, device_id
                        ),
                    ]
                )

    if entities:
        async_add_entities(entities)


class SmappeeChargingModeSelect(SmappeeBaseEntity, SelectEntity):
    """Manage dropdown control panels handling smart load balancing behaviors (standard, smart, solar)."""

    _attr_translation_key = "charging_mode_select"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, client, entry_title, device_id: str) -> None:
        """Initialize the Smappee charging mode select entity."""
        super().__init__(
            coordinator,
            client,
            entry_title,
            device_id=device_id,
            device_type="charger",
            platform_domain="select",
        )
        self._attr_options = ["standard", "smart", "solar"]
        self.mapped_location_id = str(coordinator.config_entry.data.get("station_id"))

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this select entity."""
        return f"{self.device_id}_charging_mode_select"

    @property
    def current_option(self) -> str | None:
        """Return the active matching profile translation parsed from MQTT or rich REST structures."""
        # 1. Primary: Evaluate multi-location partitioned state updates coming over real-time streams
        if self.coordinator.data and "mqtt_locations" in self.coordinator.data:
            mqtt_locations = self.coordinator.data["mqtt_locations"]
            location_data = mqtt_locations.get(self.mapped_location_id, {})
            mqtt_state = location_data.get("state")

            if mqtt_state:
                with suppress(Exception):
                    mqtt_json = (
                        mqtt_state
                        if isinstance(mqtt_state, dict)
                        else json.loads(mqtt_state)
                    )
                    if isinstance(mqtt_json, dict):
                        charging_mode = str(mqtt_json.get("chargingMode", "")).upper()
                        optimization_strategy = str(
                            mqtt_json.get("optimizationStrategy", "")
                        ).upper()

                        if charging_mode in ("NORMAL", "STANDARD"):
                            return "standard"
                        if (
                            charging_mode == "SMART"
                            and optimization_strategy == "EXCESS_ONLY"
                        ):
                            return "solar"
                        if charging_mode == "SMART":
                            return "smart"

        # 2. Secondary Fallback: Extract attributes from cached v11 station detail maps
        if (
            self.coordinator.data
            and "charging_station_details" in self.coordinator.data
        ):
            serial = getattr(self.client, "charging_station_serial", None)
            station_data = (
                self.coordinator.data["charging_station_details"].get(str(serial))
                if serial
                else None
            )

            if station_data:
                for module in station_data.get("modules", []):
                    if "carCharger" in module and module["carCharger"]:
                        cc_data = module["carCharger"]
                        charging_mode = str(cc_data.get("chargingMode", "")).upper()
                        optimization_strategy = str(
                            cc_data.get("optimizationStrategy", "")
                        ).upper()

                        if charging_mode in ("STANDARD", "NORMAL"):
                            return "standard"
                        if (
                            charging_mode == "SMART"
                            and optimization_strategy == "EXCESS_ONLY"
                        ):
                            return "solar"
                        if charging_mode == "SMART":
                            return "smart"

        # 3. Tertiary Fallback: Deep safe inspection of nested v10 smart device records
        data = self.smart_device_data
        if data:
            car_charger = data.get("carCharger")
            if isinstance(car_charger, dict):
                charging_mode = str(car_charger.get("chargingMode", "")).upper()
                optimization_strategy = str(
                    car_charger.get("optimizationStrategy", "")
                ).upper()

                if charging_mode in ("NORMAL", "STANDARD"):
                    return "standard"
                if charging_mode == "SMART" and optimization_strategy == "EXCESS_ONLY":
                    return "solar"
                if charging_mode == "SMART":
                    return "smart"

            # Legacy backup if parameters are found at top level in alternative firmware scopes
            charging_mode = str(data.get("chargingMode", "")).upper()
            load_management = data.get("loadManagement", {})
            optimization_strategy = str(
                load_management.get("optimizationStrategy", "")
            ).upper()

            if charging_mode in ("NORMAL", "STANDARD"):
                return "standard"
            if charging_mode == "SMART" and optimization_strategy == "EXCESS_ONLY":
                return "solar"
            if charging_mode == "SMART":
                return "smart"

        return None

    async def async_select_option(self, option: str) -> None:
        """Transmit the requested charging profile to Smappee API and step local caches."""
        _LOGGER.debug(
            "Changing charging mode selection via UI dropdown for %s to: %s",
            self.device_id,
            option,
        )

        service_location_id = None
        data = self.smart_device_data
        if data:
            service_location_id = data.get("serviceLocation")

        if not service_location_id:
            _LOGGER.error(
                "Aborted selection rule change for %s: missing serviceLocation metadata inside cache.",
                self.device_id,
            )
            return

        success = await self.client.set_charging_mode(
            service_location_id, self.device_id, option
        )

        if success:
            if self.coordinator.data:
                serial = getattr(self.client, "charging_station_serial", None)
                api_mode = "SMART" if option in ("smart", "solar") else "STANDARD"
                api_strategy = "EXCESS_ONLY" if option == "solar" else "BALANCED"

                # Optimistic UI caching updates: v11 station structures
                if serial and "charging_station_details" in self.coordinator.data:
                    station_data = self.coordinator.data[
                        "charging_station_details"
                    ].get(str(serial))
                    if station_data:
                        for module in station_data.get("modules", []):
                            if "carCharger" in module and module["carCharger"]:
                                module["carCharger"]["chargingMode"] = api_mode
                                module["carCharger"][
                                    "optimizationStrategy"
                                ] = api_strategy

                # Optimistic UI caching updates: v10 smart device records
                if "smart_devices" in self.coordinator.data:
                    for device in self.coordinator.data["smart_devices"]:
                        if device.get("id") == self.device_id:
                            if "carCharger" in device and isinstance(
                                device["carCharger"], dict
                            ):
                                device["carCharger"]["chargingMode"] = api_mode
                                device["carCharger"][
                                    "optimizationStrategy"
                                ] = api_strategy

                            device["chargingMode"] = api_mode
                            if "loadManagement" not in device:
                                device["loadManagement"] = {}
                            device["loadManagement"][
                                "optimizationStrategy"
                            ] = api_strategy
                            break

                # Optimistic UI caching updates: Real-time MQTT stream states
                if "mqtt_locations" in self.coordinator.data:
                    loc_data = self.coordinator.data["mqtt_locations"].setdefault(
                        self.mapped_location_id, {}
                    )
                    state_data = loc_data.setdefault("state", {})
                    if isinstance(state_data, dict):
                        state_data["chargingMode"] = api_mode
                        state_data["optimizationStrategy"] = api_strategy

                self.coordinator.async_set_updated_data(self.coordinator.data)

            await asyncio.sleep(1.5)
            await self.coordinator.async_request_refresh()

    @property
    def icon(self) -> str:
        """Return a custom icon matching the active operational strategy context."""
        mode = self.current_option
        if mode == "solar":
            return "mdi:solar-power"
        if mode == "smart":
            return "mdi:brain"
        if mode == "standard":
            return "mdi:lightning-bolt"
        return "mdi:ev-station"


class SmappeePhaseRotationSelect(SmappeeBaseEntity, SelectEntity):
    """Adjust the specific electrical installation phase sequencing mapping configuration."""

    _attr_translation_key = "phase_config_select"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, client, entry_title, device_id: str) -> None:
        """Initialize the Smappee phase rotation select entity."""
        super().__init__(
            coordinator,
            client,
            entry_title,
            device_id=device_id,
            device_type="charger",
            platform_domain="select",
        )
        self._attr_options = [
            "l1-l2-l3",
            "l1-l3-l2",
            "l2-l1-l3",
            "l2-l3-l1",
            "l3-l1-l2",
            "l3-l2-l1",
        ]
        self._mapping = {
            "l1-l2-l3": ["PHASEA", "PHASEB", "PHASEC"],
            "l1-l3-l2": ["PHASEA", "PHASEC", "PHASEB"],
            "l2-l1-l3": ["PHASEB", "PHASEA", "PHASEC"],
            "l2-l3-l1": ["PHASEB", "PHASEC", "PHASEA"],
            "l3-l1-l2": ["PHASEC", "PHASEA", "PHASEB"],
            "l3-l2-l1": ["PHASEC", "PHASEB", "PHASEA"],
        }

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this select entity."""
        return f"{self.device_id}_phase_rotation_select"

    @property
    def current_option(self) -> str | None:
        """Return the matching physical selection calculated out of v11 metadata profiles."""
        if (
            not self.coordinator.data
            or "charging_station_details" not in self.coordinator.data
        ):
            return None

        details_dict = self.coordinator.data["charging_station_details"]
        serial = getattr(self.client, "charging_station_serial", None)
        station_data = None

        if serial:
            station_data = details_dict.get(str(serial)) or details_dict.get(
                int(serial)
            )

        if station_data is None and len(details_dict) > 0:
            station_data = list(details_dict.values())[0]

        if not station_data:
            return None

        if "installationConfiguration" in station_data:
            config = station_data["installationConfiguration"].get(
                "currentlyConfigured", {}
            )
            phases_list = config.get("phases", [])

            if phases_list and isinstance(phases_list, list) and len(phases_list) > 0:
                actual_phases = [str(p).strip().upper() for p in phases_list[0]]

                for ha_option, api_array in self._mapping.items():
                    if actual_phases == api_array:
                        return ha_option

        return None

    async def async_select_option(self, option: str) -> None:
        """Construct the structural installation mapping updates and transmit payload shifts."""
        _LOGGER.debug(
            "Modifying phase sequence line assignment mapping for charger %s to: %s",
            self.device_id,
            option,
        )

        target_phases = self._mapping.get(option)
        if not target_phases:
            _LOGGER.error("Invalid phase option selected: %s", option)
            return

        serial = getattr(self.client, "charging_station_serial", None)
        station_data = None
        if (
            self.coordinator.data
            and "charging_station_details" in self.coordinator.data
            and serial
        ):
            station_data = self.coordinator.data["charging_station_details"].get(
                str(serial)
            )

        if not station_data or "installationConfiguration" not in station_data:
            _LOGGER.error(
                "Aborted phase change adjustment for %s: core installation configurations missing from registry metrics.",
                serial,
            )
            return

        currently_configured = station_data["installationConfiguration"].get(
            "currentlyConfigured", {}
        )
        amount_cables = currently_configured.get("amountPowerSupplyCables")
        maximum_current = currently_configured.get("maximumCurrent")

        if not amount_cables or not maximum_current:
            _LOGGER.error(
                "Aborted configuration packaging. Essential parameters (cables: %s, maxCurrent: %s) are empty inside response metrics.",
                amount_cables,
                maximum_current,
            )
            return

        payload = {
            "amountPowerSupplyCables": amount_cables,
            "maximumCurrent": maximum_current,
            "phases": [target_phases],
        }

        _LOGGER.debug(
            "Dispatching dynamic installationConfiguration PUT payload configuration: %s",
            payload,
        )

        success = await self.client.set_installation_configuration(payload)

        if success:
            currently_configured["phases"] = [target_phases]
            self.coordinator.async_set_updated_data(self.coordinator.data)

            await asyncio.sleep(1.5)
            await self.coordinator.async_request_refresh()

    @property
    def icon(self) -> str:
        """Return the phase rotation alignment sync graphics symbol."""
        return "mdi:sync"
