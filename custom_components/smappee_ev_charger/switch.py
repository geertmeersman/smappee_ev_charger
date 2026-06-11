"""Set up and manage Smappee Charger switch entities."""

import asyncio
from contextlib import suppress
import json
import logging
from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
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
    """Set up Smappee switch entities dynamically based on discovered devices."""
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
                    "Dynamically creating switch entities for Smappee charger: %s",
                    device_id,
                )

                entities.extend(
                    [
                        SmappeeAvailabilitySwitch(
                            coordinator, client, entry.title, device_id
                        ),
                        SmappeeOfflineChargingSwitch(
                            coordinator, client, entry.title, device_id
                        ),
                    ]
                )

    if entities:
        async_add_entities(entities)


class SmappeeAvailabilitySwitch(SmappeeBaseEntity, SwitchEntity):
    """Control the general availability state of the charging station."""

    _attr_translation_key = "charger_availability"
    _attr_device_class = SwitchDeviceClass.OUTLET
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, client, entry_title, device_id: str) -> None:
        """Initialize the Smappee availability switch."""
        super().__init__(
            coordinator,
            client,
            entry_title,
            device_id=device_id,
            device_type="charger",
            platform_domain="switch",
        )
        # Determine the unique location ID of the charger (e.g., 317443)
        self.mapped_location_id = str(coordinator.config_entry.data.get("station_id"))

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this switch entity."""
        return f"{self.device_id}_charger_availability_switch"

    @property
    def is_on(self) -> bool:
        """Return True if the charger is marked available via the official v10 charging station details."""
        # 1. MQTT real-time fallback partition
        if self.coordinator.data and "mqtt_locations" in self.coordinator.data:
            mqtt_state = (
                self.coordinator.data["mqtt_locations"]
                .get(self.mapped_location_id, {})
                .get("state")
            )
            if mqtt_state:
                with suppress(Exception):
                    mqtt_json = (
                        mqtt_state
                        if isinstance(mqtt_state, dict)
                        else json.loads(mqtt_state)
                    )
                    if isinstance(mqtt_json, dict) and "available" in mqtt_json:
                        return bool(mqtt_json["available"])

        # 2. AUTHORITATIVE FALLBACK: Verify tracking records inside charging_station_details (v10/v11 API)
        if (
            self.coordinator.data
            and "charging_station_details" in self.coordinator.data
        ):
            serial = getattr(self.client, "charging_station_serial", None)
            station_data = self.coordinator.data["charging_station_details"].get(
                str(serial)
            )

            if station_data and "available" in station_data:
                return bool(station_data["available"])

        # 3. Last resort fallback payload check: Inspect general smart_devices state mapping table
        data = self.smart_device_data
        if data and "available" in data:
            return bool(data.get("available"))

        return False

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Set the charger status to available."""
        _LOGGER.debug(
            "Manually enabling charging station %s (marking available)", self.device_id
        )

        success = await self.client.set_charger_availability(self.device_id, True)
        if success:
            if self.coordinator.data and "smart_devices" in self.coordinator.data:
                for device in self.coordinator.data["smart_devices"]:
                    if device.get("id") == self.device_id:
                        device["available"] = True
                        break

                # Synchronize the localized MQTT cache dictionary directly for smooth frontend UI transitions
                if "mqtt_locations" in self.coordinator.data:
                    loc_data = self.coordinator.data["mqtt_locations"].setdefault(
                        self.mapped_location_id, {}
                    )
                    state_data = loc_data.setdefault("state", {})
                    if isinstance(state_data, dict):
                        state_data["available"] = True

                self.coordinator.async_set_updated_data(self.coordinator.data)

            await asyncio.sleep(1.5)
            await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Set the charger status to unavailable."""
        _LOGGER.debug(
            "Manually disabling charging station %s (marking unavailable)",
            self.device_id,
        )

        success = await self.client.set_charger_availability(self.device_id, False)
        if success:
            if self.coordinator.data and "smart_devices" in self.coordinator.data:
                for device in self.coordinator.data["smart_devices"]:
                    if device.get("id") == self.device_id:
                        device["available"] = False
                        break

                # Synchronize the localized MQTT cache dictionary directly for smooth frontend UI transitions
                if "mqtt_locations" in self.coordinator.data:
                    loc_data = self.coordinator.data["mqtt_locations"].setdefault(
                        self.mapped_location_id, {}
                    )
                    state_data = loc_data.setdefault("state", {})
                    if isinstance(state_data, dict):
                        state_data["available"] = False

                self.coordinator.async_set_updated_data(self.coordinator.data)

            await asyncio.sleep(1.5)
            await self.coordinator.async_request_refresh()

    @property
    def icon(self) -> str:
        """Return the icon based on the active availability state."""
        return "mdi:ev-station" if self.is_on else "mdi:ev-station-disabled"


class SmappeeOfflineChargingSwitch(SmappeeBaseEntity, SwitchEntity):
    """Manage offline charging / failsafe modes via v11 database schemas."""

    _attr_translation_key = "offline_charging"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, client, entry_title, device_id: str) -> None:
        """Initialize the Smappee offline charging switch."""
        super().__init__(
            coordinator,
            client,
            entry_title,
            device_id=device_id,
            device_type="charger",
            platform_domain="switch",
        )

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this switch entity."""
        return f"{self.device_id}_offline_charging_switch"

    @property
    def is_on(self) -> bool:
        """Return the current offline charging status parsed from v11 details or v10 fallbacks."""
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

            if station_data and "offlineCharging" in station_data:
                return bool(station_data["offlineCharging"].get("enabled", False))

        data = self.smart_device_data
        if data:
            return bool(data.get("loadManagement", {}).get("active", False))

        return False

    async def _send_payload(self, enabled: bool) -> None:
        """Transmit the updated load management state alongside the active failsafe limits."""
        serial = getattr(self.client, "charging_station_serial", None)
        current_failsafe = 3  # Set safe baseline constraint fallback

        if (
            self.coordinator.data
            and "charging_station_details" in self.coordinator.data
            and serial
        ):
            station_data = self.coordinator.data["charging_station_details"].get(
                str(serial)
            )
            if station_data and "offlineCharging" in station_data:
                current_failsafe = int(
                    station_data["offlineCharging"].get("failSafe", 3)
                )
        else:
            data = self.smart_device_data
            if data:
                config_props = data.get("configurationProperties", [])
                for prop in config_props:
                    spec = prop.get("spec", {}) if "spec" in prop else prop
                    if (
                        spec.get("name")
                        == "etc.smart.device.type.car.charger.config.max.gridassistanceamps"
                    ):
                        values = prop.get("values", [{}])
                        if values:
                            current_failsafe = int(values[0].get("Integer", 3))

        _LOGGER.debug(
            "Changing load management state to %s. Dispatching to v11 API with failsafe limit %s A",
            enabled,
            current_failsafe,
        )

        success = await self.client.set_offline_charging_config(
            enabled, current_failsafe
        )

        if success and self.coordinator.data:
            if serial and "charging_station_details" in self.coordinator.data:
                station_data = self.coordinator.data["charging_station_details"].get(
                    str(serial)
                )
                if station_data:
                    if "offlineCharging" not in station_data:
                        station_data["offlineCharging"] = {}
                    station_data["offlineCharging"]["enabled"] = enabled

            if "smart_devices" in self.coordinator.data:
                for device in self.coordinator.data["smart_devices"]:
                    if device.get("id") == self.device_id:
                        if "loadManagement" not in device:
                            device["loadManagement"] = {}
                        device["loadManagement"]["active"] = enabled
                        break

            self.coordinator.async_set_updated_data(self.coordinator.data)

            await asyncio.sleep(1.5)
            await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable offline charging load management."""
        await self._send_payload(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable offline charging load management."""
        await self._send_payload(False)

    @property
    def icon(self) -> str:
        """Return the connectivity icon matching the online/offline switch state."""
        return "mdi:cloud-outline" if self.is_on else "mdi:cloud-off-outline"
