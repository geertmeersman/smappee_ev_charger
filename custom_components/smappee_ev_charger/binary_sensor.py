"""Set up and manage Smappee Charger binary sensor entities."""

from contextlib import suppress
import json
import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
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
    """Set up Smappee binary sensor entities dynamically based on discovered devices."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    client = entry_data["client"]
    coordinator = entry_data["coordinator"]

    entities = []

    if coordinator.data and "smart_devices" in coordinator.data:
        smart_devices = coordinator.data["smart_devices"]

        for device in smart_devices:
            category = device.get("type", {}).get("category")
            device_id = device.get("id")

            # Create binary sensors exclusively for CARCHARGER devices
            if category == "CARCHARGER" and device_id:
                _LOGGER.debug(
                    "Dynamically creating binary sensor entities for Smappee charger: %s",
                    device_id,
                )

                entities.extend(
                    [
                        SmappeeNetworkStatusBinarySensor(
                            coordinator, client, entry.title, device_id
                        ),
                        SmappeeCarConnectedBinarySensor(
                            coordinator, client, entry.title, device_id
                        ),
                    ]
                )

    if entities:
        async_add_entities(entities)


class SmappeeNetworkStatusBinarySensor(SmappeeBaseEntity, BinarySensorEntity):
    """Monitor if the charging station is currently online and responding to the cloud platform."""

    _attr_translation_key = "network_status"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, client, entry_title, device_id: str) -> None:
        """Initialize the Smappee network status binary sensor."""
        super().__init__(
            coordinator,
            client,
            entry_title,
            device_id=device_id,
            device_type="charger",
            platform_domain="binary_sensor",
        )

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this binary sensor entity."""
        return f"{self.device_id}_network_status"

    @property
    def is_on(self) -> bool:
        """Return True if the charger is online and communicating properly with the API."""
        # 1. Primary: Evaluate status records within rich charging station detail caches
        if (
            self.coordinator.data
            and "charging_station_details" in self.coordinator.data
        ):
            serial = getattr(self.client, "charging_station_serial", None)
            station_data = self.coordinator.data["charging_station_details"].get(
                str(serial)
            )
            if station_data:
                return bool(
                    station_data.get("active", True)
                    and station_data.get("available", True)
                )

        # 2. Fallback: Parse parameter records stored across generic flat v10 dictionary registries
        data = self.smart_device_data
        if not data:
            return False

        return bool(data.get("available", False))


class SmappeeCarConnectedBinarySensor(SmappeeBaseEntity, BinarySensorEntity):
    """Monitor if a vehicle is physically plugged into the station connector interface."""

    _attr_translation_key = "car_connected"
    _attr_device_class = BinarySensorDeviceClass.PLUG

    def __init__(self, coordinator, client, entry_title, device_id: str) -> None:
        """Initialize the Smappee car connected binary sensor."""
        super().__init__(
            coordinator,
            client,
            entry_title,
            device_id=device_id,
            device_type="charger",
            platform_domain="binary_sensor",
        )
        self.mapped_location_id = str(coordinator.config_entry.data.get("station_id"))

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this binary sensor entity."""
        return f"{self.device_id}_car_connected"

    @property
    def is_on(self) -> bool:
        """Return True if a vehicle connection state is discovered via MQTT stream inputs or REST arrays."""
        location_id = str(
            self.smart_device_data.get("serviceLocation", self.mapped_location_id)
        )

        # 1. Primary: Evaluate real-time push events from the new multi-location MQTT cache
        if self.coordinator.data and "mqtt_locations" in self.coordinator.data:
            location_data = self.coordinator.data["mqtt_locations"].get(location_id, {})
            mqtt_payload = location_data.get("state")  # Dit is nu het juiste pad!

            if mqtt_payload:
                with suppress(Exception):
                    mqtt_json = (
                        mqtt_payload
                        if isinstance(mqtt_payload, dict)
                        else json.loads(mqtt_payload)
                    )

                    if isinstance(mqtt_json, dict):
                        # IEC Status check
                        iec_status = str(
                            mqtt_json.get("iecStatus", {}).get("current")
                            if isinstance(mqtt_json.get("iecStatus"), dict)
                            else mqtt_json.get("iecStatus", "")
                        ).upper()

                        if iec_status.startswith("A"):
                            return False

                        connection_status = str(
                            mqtt_json.get("connectionStatus", "")
                        ).upper()
                        if connection_status == "DISCONNECTED":
                            return False
                        if connection_status == "CONNECTED":
                            return True

                        # State object check
                        status_obj = mqtt_json.get("status", {})
                        state = str(
                            status_obj.get(
                                "current", mqtt_json.get("chargingState", "")
                            )
                        ).upper()

                        if state in ["AVAILABLE", "DISCONNECTED"]:
                            return False
                        if state in [
                            "CABLE_CONNECTED",
                            "CHARGING",
                            "SUSPENDED",
                            "SUSPENDED_EV",
                            "SUSPENDED_EVSE",
                        ]:
                            return True

                        if iec_status in ["B1", "B2", "C1", "C2", "D1", "D2"]:
                            return True

        # 2. Secondary: Evaluate configuration elements derived from rich v11 cached response blocks
        if (
            self.coordinator.data
            and "charging_station_details" in self.coordinator.data
        ):
            serial = getattr(self.client, "charging_station_serial", None)
            station_data = self.coordinator.data["charging_station_details"].get(
                str(serial)
            )

            if station_data:
                for module in station_data.get("modules", []):
                    if "carCharger" in module and module["carCharger"]:
                        cc_data = module["carCharger"]
                        rest_iec = str(cc_data.get("iecStatus", "")).upper()
                        if rest_iec.startswith("A"):
                            return False
                        if cc_data.get("connectionStatus") == "DISCONNECTED":
                            return False
                        if cc_data.get("connectionStatus") == "CONNECTED":
                            return True

                        rest_state = str(
                            cc_data.get("status", {}).get("current", "")
                        ).upper()
                        if rest_state in ["AVAILABLE", "DISCONNECTED"]:
                            return False
                        if rest_state in [
                            "CABLE_CONNECTED",
                            "CHARGING",
                            "SUSPENDED",
                            "SUSPENDED_EV",
                            "SUSPENDED_EVSE",
                        ]:
                            return True
                        if rest_iec in ["B1", "B2", "C1", "C2", "D1", "D2"]:
                            return True

        # 3. Fallback: Parse parameter records stored in smart_device_data
        data = self.smart_device_data
        if data and "carCharger" in data:
            cc_data = data["carCharger"]
            state = str(cc_data.get("status", {}).get("current", "")).upper()
            return state not in ["AVAILABLE", "DISCONNECTED"]

        return False
