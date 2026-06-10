"""Set up and manage Smappee Charger sensor entities."""

from contextlib import suppress
from datetime import datetime, timezone
import json
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Smappee sensor entities dynamically based on discovered devices."""
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
                    "Dynamically creating sensor entities for Smappee charger: %s",
                    device_id,
                )

                entities.extend(
                    [
                        SmappeeStatusSensor(
                            coordinator, client, entry.title, device_id
                        ),
                        SmappeeLivePowerSensor(
                            coordinator, client, entry.title, device_id
                        ),
                        SmappeeMaxCurrentLimitSensor(
                            coordinator, client, entry.title, device_id
                        ),
                        SmappeeSessionDurationSensor(
                            coordinator, client, entry.title, device_id
                        ),
                        SmappeeSessionEnergySensor(
                            coordinator, client, entry.title, device_id
                        ),
                        SmappeeSessionRfidSensor(
                            coordinator, client, entry.title, device_id
                        ),
                    ]
                )

    if entities:
        async_add_entities(entities)


class SmappeeBaseEntity(CoordinatorEntity):
    """Provide a common base entity class for all Smappee device-linked tracking entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator,
        client,
        entry_title,
        device_id: str,
        device_type: str = "charger",
        platform_domain: str = "sensor",
    ) -> None:
        """Initialize the Smappee base entity."""
        super().__init__(coordinator)
        self.client = client
        self.entry_title = entry_title
        self._device_type = device_type
        self.device_id = device_id

        key = getattr(self, "_attr_translation_key", None)
        device_key = device_id.lower().replace("-", "_")

        if key:
            self.entity_id = f"{platform_domain}.{DOMAIN}_{device_key}_{key}"
        else:
            fallback_key = (
                self.__class__.__name__.replace(MANUFACTURER, "")
                .replace("Sensor", "")
                .lower()
            )
            self.entity_id = f"{platform_domain}.{DOMAIN}_{device_key}_{fallback_key}"

    @property
    def smart_device_data(self) -> dict[str, Any]:
        """Fetch the specific device segment directly out of the flat master list."""
        if not self.coordinator.data or "smart_devices" not in self.coordinator.data:
            return {}

        smart_devices = self.coordinator.data["smart_devices"]
        for device in smart_devices:
            if device.get("id") == self.device_id:
                return device
        return {}

    @property
    def device_info(self) -> DeviceInfo:
        """Link the entity tracking instance back to its corresponding physical device."""
        data = self.smart_device_data
        category = data.get("type", {}).get("category", "UNKNOWN")
        child_location_id = data.get("serviceLocation")

        parent_location_id = None
        if self.coordinator.data and "servicelocations" in self.coordinator.data:
            locs = self.coordinator.data["servicelocations"]
            current_loc = next(
                (loc for loc in locs if loc.get("id") == child_location_id), None
            )
            if current_loc:
                parent_location_id = current_loc.get("parentId")

        parent_identifier = (
            (DOMAIN, f"location_{parent_location_id}") if parent_location_id else None
        )

        if category == "LED":
            display_name = data.get("type", {}).get("displayName", "EV Base LED")
            custom_name = data.get("name", "EV Wall - LED")

            return DeviceInfo(
                identifiers={(DOMAIN, self.device_id)},
                name=f"{display_name} - {custom_name}",
                manufacturer="Smappee",
                model=data.get("type", {}).get("name", "acledcontroller"),
                sw_version=self.device_id,
                via_device=parent_identifier if parent_identifier else None,
            )

        station_serial = (
            data.get("stationSerialNumber")
            or data.get("serialNumber")
            or getattr(self.client, "charging_station_serial", None)
            or "unknown_charger"
        )
        model_name = data.get("model", "WALL_QUANTUM_CABLE")

        device_name = "Smappee laadstation - EV Wall"
        if self.coordinator.data and "smart_devices" in self.coordinator.data:
            smart_devices = self.coordinator.data["smart_devices"]
            charging_station_data = next(
                (
                    d
                    for d in smart_devices
                    if d.get("type", {}).get("category") == "CHARGINGSTATION"
                ),
                None,
            )
            if charging_station_data:
                display_name = charging_station_data.get("type", {}).get(
                    "displayName", "Smappee laadstation"
                )
                custom_name = charging_station_data.get("name", "EV Wall")
                device_name = f"{display_name} - {custom_name}"

        return DeviceInfo(
            identifiers={(DOMAIN, station_serial)},
            name=device_name,
            manufacturer="Smappee",
            model=model_name.replace("_", " ").title(),
            sw_version=self.device_id,
            via_device=parent_identifier if parent_identifier else None,
        )


class SmappeeBaseSessionSensor(SmappeeBaseEntity, SensorEntity):
    """Provide a common blueprint for entities reading active session arrays."""

    @property
    def active_session_data(self) -> dict[str, Any]:
        """Extract the most recent chronological session map from the data coordinator."""
        if not self.coordinator.data or "recent_sessions" not in self.coordinator.data:
            return {}

        sessions = self.coordinator.data["recent_sessions"]
        if sessions and isinstance(sessions, list):
            return sessions[0]
        return {}


class SmappeeStatusSensor(SmappeeBaseEntity, SensorEntity):
    """Track the functional hardware state of the charging node."""

    _attr_translation_key = "charger_status"

    def __init__(self, coordinator, client, entry_title, device_id: str) -> None:
        """Initialize the Smappee status sensor."""
        super().__init__(
            coordinator,
            client,
            entry_title,
            device_id=device_id,
            device_type="charger",
            platform_domain="sensor",
        )

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this entity."""
        return f"{self.device_id}_charger_status"

    @property
    def native_value(self) -> str:
        """Determine the current active station operational status string."""
        # 1. Primary: Evaluate real-time push data from Smappee Cloud MQTT WebSocket streams
        if self.coordinator.data and "mqtt_charging_state" in self.coordinator.data:
            mqtt_payload = self.coordinator.data["mqtt_charging_state"]
            try:
                mqtt_json = json.loads(mqtt_payload)
                if isinstance(mqtt_json, dict):
                    detailed_status = mqtt_json.get("status", {}).get("current")
                    if detailed_status:
                        return str(detailed_status).upper()

                    charging_state = mqtt_json.get("chargingState")
                    if charging_state:
                        return str(charging_state).upper()
            except Exception:
                if len(str(mqtt_payload)) <= 255:
                    return str(mqtt_payload).upper()

        # 2. Secondary: Extract status values from rich v11 cached response blocks
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
                        rest_detailed = (
                            module["carCharger"].get("status", {}).get("current")
                        )
                        if rest_detailed:
                            return str(rest_detailed).upper()

                        rest_conn = module["carCharger"].get("connectionStatus")
                        if rest_conn:
                            return str(rest_conn).upper()

        # 3. Fallback: Parse the generic flat v10 smart devices configuration registry
        data = self.smart_device_data
        if data:
            if "chargingState" in data and data.get("chargingState") is not None:
                return str(data.get("chargingState")).upper()
            if "connectionStatus" in data:
                return str(data.get("connectionStatus")).upper()

        return "AVAILABLE"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Store additional contextual live parameters inside state metadata arrays."""
        if self.coordinator.data and "mqtt_charging_state" in self.coordinator.data:
            mqtt_payload = self.coordinator.data["mqtt_charging_state"]
            try:
                mqtt_json = json.loads(mqtt_payload)
                if isinstance(mqtt_json, dict):
                    return {
                        "available": mqtt_json.get("available"),
                        "percentage_limit": mqtt_json.get("percentageLimit"),
                        "charging_mode": mqtt_json.get("chargingMode"),
                        "optimization_strategy": mqtt_json.get("optimizationStrategy"),
                        "iec_status": mqtt_json.get("iecStatus", {}).get("current"),
                        "detailed_status": mqtt_json.get("status", {}).get("current"),
                    }
            except Exception:
                pass
        return None

    @property
    def icon(self) -> str:
        """Return a dynamic icon mapping matching the current parsed charger status."""
        status = self.native_value
        if not status:
            return "mdi:ev-station"

        status_upper = str(status).upper()

        if "AVAILABLE" in status_upper and "NOT" not in status_upper:
            return "mdi:ev-station"
        if "CABLE_CONNECTED" in status_upper:
            return "mdi:car-electric"
        if "CHARGING" in status_upper and "FINISHED" not in status_upper:
            return "mdi:battery-charging-100"
        if "SUSPENDED" in status_upper or "PAUSED" in status_upper:
            return "mdi:pause-circle-outline"
        if "FINISHED" in status_upper or "COMPLETED" in status_upper:
            return "mdi:battery-check"
        if (
            "ERROR" in status_upper
            or "FAULT" in status_upper
            or "NOT_AVAILABLE" in status_upper
        ):
            return "mdi:ev-station-disabled"

        return "mdi:ev-station"


class SmappeeLivePowerSensor(SmappeeBaseEntity, SensorEntity):
    """Monitor the real-time active power delivery tracking in kilowatts."""

    _attr_translation_key = "live_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator, client, entry_title, device_id: str) -> None:
        """Initialize the Smappee live power sensor."""
        super().__init__(
            coordinator,
            client,
            entry_title,
            device_id=device_id,
            device_type="charger",
            platform_domain="sensor",
        )

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this entity."""
        return f"{self.device_id}_live_power"

    @property
    def native_value(self) -> float | None:
        """Calculate active phase telemetry power values and convert them to kilowatts."""
        raw_watts = None

        # 1. Primary: Evaluate cumulative active power values across streaming phase structures
        if self.coordinator.data and "mqtt_power_data" in self.coordinator.data:
            mqtt_data = self.coordinator.data["mqtt_power_data"]
            if isinstance(mqtt_data, dict) and "activePowerData" in mqtt_data:
                with suppress(TypeError, ValueError):
                    raw_watts = float(sum(mqtt_data["activePowerData"]))

        # 2. Secondary: Extract the baseline values from cached module responses if streaming is offline
        if (
            raw_watts is None
            and self.coordinator.data
            and "charging_station_details" in self.coordinator.data
        ):
            serial = getattr(self.client, "charging_station_serial", None)
            station_data = self.coordinator.data["charging_station_details"].get(
                str(serial)
            )
            if station_data:
                for module in station_data.get("modules", []):
                    if "carCharger" in module and module["carCharger"]:
                        live_p = module["carCharger"].get("livePower")
                        if live_p is not None:
                            raw_watts = float(live_p)
                            break

        # 3. Fallback: Query older flat static entity attribute fields
        if raw_watts is None:
            data = self.smart_device_data
            if data:
                raw_watts = float(data.get("livePower", 0.0))

        if raw_watts is not None:
            return round(raw_watts / 1000.0, 2)

        return 0.00


class SmappeeMaxCurrentLimitSensor(SmappeeBaseEntity, SensorEntity):
    """Read the upper safe hardware phase current boundaries configured on the station."""

    _attr_translation_key = "max_current_limit"
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator, client, entry_title, device_id: str) -> None:
        """Initialize the Smappee max current limit sensor."""
        super().__init__(
            coordinator,
            client,
            entry_title,
            device_id=device_id,
            device_type="charger",
            platform_domain="sensor",
        )

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this entity."""
        return f"{self.device_id}_max_current_limit"

    @property
    def native_value(self) -> float | None:
        """Extract the static max target capacity limits from configurations."""
        data = self.smart_device_data
        if not data:
            return None

        config_props = data.get("configurationProperties", [])
        for prop in config_props:
            spec = prop.get("spec", {})
            if (
                spec.get("name")
                == "etc.smart.device.type.car.charger.config.max.current"
            ):
                values = prop.get("values", [{}])
                if values:
                    return values[0].get("Quantity", {}).get("value")
        return None


class SmappeeSessionDurationSensor(SmappeeBaseSessionSensor):
    """Calculate the operational running time tracking for ongoing charging sessions."""

    _attr_translation_key = "session_duration"
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, client, entry_title, device_id: str) -> None:
        """Initialize the Smappee session duration sensor."""
        super().__init__(
            coordinator,
            client,
            entry_title,
            device_id=device_id,
            device_type="charger",
            platform_domain="sensor",
        )

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this entity."""
        return f"{self.device_id}_session_duration"

    @property
    def native_value(self) -> float:
        """Compute active session timestamps into net minutes elapsed."""
        session = self.active_session_data
        if not session:
            return 0.0

        start_ts = session.get("from")
        if not start_ts:
            return 0.0

        try:
            start_time = datetime.fromtimestamp(start_ts / 1000.0, tz=timezone.utc)
            end_ts = session.get("to")

            if end_ts:
                end_time = datetime.fromtimestamp(end_ts / 1000.0, tz=timezone.utc)
                duration = end_time - start_time
            else:
                now = datetime.now(timezone.utc)
                duration = now - start_time

            return round(duration.total_seconds() / 60.0, 1)
        except Exception as err:
            _LOGGER.error(
                "Failed calculating session tracking boundaries for %s: %s",
                self.device_id,
                err,
            )
            return 0.0

    @property
    def icon(self) -> str:
        """Return the clock icon placeholder for timeline elements."""
        return "mdi:clock-outline"


class SmappeeSessionEnergySensor(SmappeeBaseSessionSensor):
    """Track overall continuous power accumulation consumed during charging loops."""

    _attr_translation_key = "session_energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator, client, entry_title, device_id: str) -> None:
        """Initialize the Smappee session energy sensor."""
        super().__init__(
            coordinator,
            client,
            entry_title,
            device_id=device_id,
            device_type="charger",
            platform_domain="sensor",
        )

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this entity."""
        return f"{self.device_id}_session_energy"

    @property
    def native_value(self) -> float:
        """Retrieve the aggregate electrical charge consumption total."""
        energy = self.active_session_data.get("energy", 0.0)
        return round(float(energy), 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Extract historical context values dynamically out of active session footprints."""
        if not self.active_session_data:
            return None

        attributes = dict(self.active_session_data)

        # Remove primary sensor statistics and bulky infrastructure nesting parameters
        attributes.pop("energy", None)
        attributes.pop("controller", None)
        attributes.pop("station", None)
        attributes.pop("address", None)
        attributes.pop("updateChannels", None)

        return attributes


class SmappeeSessionRfidSensor(SmappeeBaseSessionSensor):
    """Track the identifier token credentials matching authenticated authorizations."""

    _attr_translation_key = "session_rfid"

    def __init__(self, coordinator, client, entry_title, device_id: str) -> None:
        """Initialize the Smappee session RFID sensor."""
        super().__init__(
            coordinator,
            client,
            entry_title,
            device_id=device_id,
            device_type="charger",
            platform_domain="sensor",
        )

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this entity."""
        return f"{self.device_id}_session_rfid"

    @property
    def native_value(self) -> Any:
        """Return the signature key identification assigned to transactions."""
        return self.active_session_data.get("rfid")

    @property
    def icon(self) -> str:
        """Return an active smart badge card identification graphic icon."""
        return "mdi:card-account-details"
