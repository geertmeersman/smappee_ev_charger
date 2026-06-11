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
from .coordinator import SmappeeDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Smappee sensor entities dynamically based on discovered devices and maps."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    client = entry_data["client"]
    coordinator: SmappeeDataUpdateCoordinator = entry_data["coordinator"]

    entities: list[SensorEntity] = []

    # 1. Core Discovery Logic: Dynamic smart device discovery loop
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

    # 2. Dynamic multi-location context routing setup
    master_station_id = str(
        entry.data.get("station_id")
    )  # The charger hub (e.g., 317443)
    parent_grid_id = (
        str(coordinator.parent_location_id) if coordinator.parent_location_id else None
    )

    # Safe layout registry mapping matching verified cached data frames
    if hasattr(coordinator, "high_level_configs") and coordinator.high_level_configs:
        for loc_id in coordinator.high_level_configs:
            loc_id_str = str(loc_id)

            if loc_id_str == parent_grid_id:
                _LOGGER.info(
                    "Mounting P1 GRID matrix sensors (Energy & Power) for location %s",
                    loc_id_str,
                )
                entities.append(
                    SmappeeMatrixSensor(coordinator, entry, "grid", loc_id_str)
                )
                entities.append(
                    SmappeeMatrixSensor(coordinator, entry, "grid_power", loc_id_str)
                )
                entities.append(
                    SmappeeMatrixSensor(coordinator, entry, "pv", loc_id_str)
                )
                entities.append(
                    SmappeeMatrixSensor(coordinator, entry, "pv_power", loc_id_str)
                )

            elif loc_id_str == master_station_id:
                _LOGGER.info(
                    "Mounting MID CAR_CHARGER matrix sensors (Energy & Power) for location %s",
                    loc_id_str,
                )
                entities.append(
                    SmappeeMatrixSensor(coordinator, entry, "car", loc_id_str)
                )
                entities.append(
                    SmappeeMatrixSensor(coordinator, entry, "car_power", loc_id_str)
                )

    if entities:
        async_add_entities(entities)


class SmappeeBaseEntity(CoordinatorEntity[SmappeeDataUpdateCoordinator]):
    """Provide a common base entity class for all Smappee device-linked tracking entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SmappeeDataUpdateCoordinator,
        client: Any,
        entry_title: str,
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

        device_name = "Smappee Charging Station - EV Wall"
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
                    "displayName", "Smappee Charging Station"
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
        super().__init__(coordinator, client, entry_title, device_id=device_id)
        # Fallback tracking resolution context to extract location mapping keys smoothly
        self.mapped_location_id = str(coordinator.config_entry.data.get("station_id"))

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this entity."""
        return f"{self.device_id}_charger_status"

    @property
    def icon(self) -> str:
        """Return a dynamic icon based on the operational charger status."""
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

    @property
    def native_value(self) -> str:
        """Determine the current active station operational status string."""
        # Isolate targeting tracking properties derived directly from the dynamic master list definition maps
        data = self.smart_device_data
        location_id = str(data.get("serviceLocation", self.mapped_location_id))

        # 1. Primary Route: Fetch from the cleanly isolated multi-location MQTT cache map
        if self.coordinator.data and "mqtt_locations" in self.coordinator.data:
            location_data = self.coordinator.data["mqtt_locations"].get(location_id, {})
            mqtt_payload = location_data.get("state")

            if mqtt_payload:
                with suppress(Exception):
                    mqtt_json = (
                        mqtt_payload
                        if isinstance(mqtt_payload, dict)
                        else json.loads(mqtt_payload)
                    )
                    if isinstance(mqtt_json, dict):
                        detailed_status = mqtt_json.get("status", {}).get("current")
                        if detailed_status:
                            return str(detailed_status).lower()
                        charging_state = mqtt_json.get("chargingState")
                        if charging_state:
                            return str(charging_state).lower()

        # 2. FALLBACK: Safe deep inspection of the nested REST cloud metadata layers
        if data:
            car_charger = data.get("carCharger")
            if isinstance(car_charger, dict):
                # Primary rest fallback: Parse explicit internal carCharger running parameters
                status_block = car_charger.get("status", {})
                current_status = status_block.get("current")
                if current_status:
                    return str(current_status).lower()

                # Secondary rest fallback: Extract interface connectivity profiles
                conn_status = car_charger.get("connectionStatus")
                if conn_status:
                    return str(conn_status).lower()

            # Legacy fallback loops if fields are flattened on sparse responses
            if "chargingState" in data and data.get("chargingState") is not None:
                return str(data.get("chargingState")).lower()
            if "connectionStatus" in data:
                return str(data.get("connectionStatus")).lower()

        return "available"


class SmappeeLivePowerSensor(SmappeeBaseEntity, SensorEntity):
    """Monitor the real-time active power delivery tracking in kilowatts."""

    _attr_translation_key = "live_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:flash"

    def __init__(self, coordinator, client, entry_title, device_id: str) -> None:
        """Initialize the Smappee live power sensor."""
        super().__init__(coordinator, client, entry_title, device_id=device_id)

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this entity."""
        return f"{self.device_id}_live_power"

    @property
    def native_value(self) -> float | None:
        """Calculate active phase telemetry power values for the charger dynamically."""
        raw_watts = None

        if self.coordinator.data and "mqtt_locations" in self.coordinator.data:
            entry_loc_id = str(self.entry_title)
            if "smart_devices" in self.coordinator.data:
                for dev in self.coordinator.data["smart_devices"]:
                    if dev.get("id") == self.device_id and dev.get("serviceLocation"):
                        entry_loc_id = str(dev.get("serviceLocation"))
                        break

            location_data = self.coordinator.data["mqtt_locations"].get(
                entry_loc_id, {}
            )
            mqtt_data = location_data.get("power")

            if isinstance(mqtt_data, dict) and "activePowerData" in mqtt_data:
                power_array = mqtt_data.get("activePowerData")
                if isinstance(power_array, list) and len(power_array) >= 3:
                    raw_watts = float(sum(power_array[:3]))

        if raw_watts is None:
            data = self.smart_device_data
            if data:
                raw_watts = float(data.get("livePower", 0.0))

        return round(raw_watts / 1000.0, 2) if raw_watts is not None else 0.00


class SmappeeMaxCurrentLimitSensor(SmappeeBaseEntity, SensorEntity):
    """Read the upper safe hardware phase current boundaries configured on the station."""

    _attr_translation_key = "max_current_limit"
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:current-ac"

    def __init__(self, coordinator, client, entry_title, device_id: str) -> None:
        """Initialize the Smappee max current limit sensor."""
        super().__init__(coordinator, client, entry_title, device_id=device_id)

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this entity."""
        return f"{self.device_id}_max_current_limit"

    @property
    def native_value(self) -> float | None:
        """Extract maximum configuration thresholds safely from structured registry lists."""
        data = self.smart_device_data
        if not data:
            return None
        for prop in data.get("configurationProperties", []):
            if (
                prop.get("spec", {}).get("name")
                == "etc.smart.device.type.car.charger.config.max.current"
            ):
                with suppress(IndexError, KeyError, TypeError):
                    return float(
                        prop.get("values", [{}])[0].get("Quantity", {}).get("value")
                    )
        return None


class SmappeeSessionDurationSensor(SmappeeBaseSessionSensor):
    """Calculate the operational running time tracking for ongoing charging sessions."""

    _attr_translation_key = "session_duration"
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:clock-outline"

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this entity."""
        return f"{self.device_id}_session_duration"

    @property
    def native_value(self) -> float:
        """Compute running running session timestamps out to elapsed delta minutes."""
        session = self.active_session_data
        if not session or not session.get("from"):
            return 0.0
        try:
            start_time = datetime.fromtimestamp(
                session["from"] / 1000.0, tz=timezone.utc
            )
            end_ts = session.get("to")
            end_time = (
                datetime.fromtimestamp(end_ts / 1000.0, tz=timezone.utc)
                if end_ts
                else datetime.now(timezone.utc)
            )
            return round((end_time - start_time).total_seconds() / 60.0, 1)
        except Exception:
            return 0.0


class SmappeeSessionEnergySensor(SmappeeBaseSessionSensor):
    """Track overall continuous power accumulation consumed during charging loops."""

    _attr_translation_key = "session_energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:lightning-bolt"

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this entity."""
        return f"{self.device_id}_session_energy"

    @property
    def native_value(self) -> float:
        """Return the current energy consumption for the active session."""
        return round(float(self.active_session_data.get("energy", 0.0)), 2)


class SmappeeSessionRfidSensor(SmappeeBaseSessionSensor):
    """Track the identifier token credentials matching authenticated authorizations."""

    _attr_translation_key = "session_rfid"
    _attr_icon = "mdi:card-account-details"

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this entity."""
        return f"{self.device_id}_session_rfid"

    @property
    def native_value(self) -> Any:
        """Return the authenticated RFID tag value for the active session."""
        return self.active_session_data.get("rfid")


class SmappeeMatrixSensor(
    CoordinatorEntity[SmappeeDataUpdateCoordinator], SensorEntity
):
    """Sensor that extracts variables (Energy/Power) dynamically from sequential MQTT array streams."""

    _attr_has_entity_name = True

    TYPE_METADATA: dict[str, dict[str, Any]] = {
        "grid": {
            "key": "grid_import_energy",
            "icon": "mdi:transmission-tower",
            "device_class": SensorDeviceClass.ENERGY,
            "state_class": SensorStateClass.TOTAL_INCREASING,
            "unit": UnitOfEnergy.KILO_WATT_HOUR,
            "scale_factor": 1000.0,
            "precision": 3,
            "map_key": "grid",
            "fallback_array_key": "importActiveEnergyData",
        },
        "pv": {
            "key": "solar_production_energy",
            "icon": "mdi:solar-power",
            "device_class": SensorDeviceClass.ENERGY,
            "state_class": SensorStateClass.TOTAL_INCREASING,
            "unit": UnitOfEnergy.KILO_WATT_HOUR,
            "scale_factor": 1000.0,
            "precision": 3,
            "map_key": "pv",
            "fallback_array_key": "importActiveEnergyData",
        },
        "car": {
            "key": "charger_matrix_energy",
            "icon": "mdi:ev-station",
            "device_class": SensorDeviceClass.ENERGY,
            "state_class": SensorStateClass.TOTAL_INCREASING,
            "unit": UnitOfEnergy.KILO_WATT_HOUR,
            "scale_factor": 1000.0,
            "precision": 3,
            "map_key": "cars",
            "fallback_array_key": "importActiveEnergyData",
        },
        "grid_power": {
            "key": "grid_active_power",
            "icon": "mdi:transmission-tower-export",
            "device_class": SensorDeviceClass.POWER,
            "state_class": SensorStateClass.MEASUREMENT,
            "unit": UnitOfPower.KILO_WATT,
            "scale_factor": 1000.0,
            "precision": 2,
            "map_key": "grid",
            "fallback_array_key": "activePowerData",
        },
        "pv_power": {
            "key": "solar_active_power",
            "icon": "mdi:solar-power-variant",
            "device_class": SensorDeviceClass.POWER,
            "state_class": SensorStateClass.MEASUREMENT,
            "unit": UnitOfPower.KILO_WATT,
            "scale_factor": 1000.0,
            "precision": 2,
            "map_key": "pv",
            "fallback_array_key": "activePowerData",
        },
        "car_power": {
            "key": "charger_matrix_power",
            "icon": "mdi:ev-station",
            "device_class": SensorDeviceClass.POWER,
            "state_class": SensorStateClass.MEASUREMENT,
            "unit": UnitOfPower.KILO_WATT,
            "scale_factor": 1000.0,
            "precision": 2,
            "map_key": "cars",
            "fallback_array_key": "activePowerData",
        },
    }

    def __init__(
        self,
        coordinator: SmappeeDataUpdateCoordinator,
        entry: ConfigEntry,
        sensor_type: str,
        mapped_location_id: str,
        car_uuid: str | None = None,
    ) -> None:
        """Initialize the flexible matrix multi-sensor platform layout."""
        super().__init__(coordinator)
        self.sensor_type = sensor_type
        self.car_uuid = car_uuid
        self.mapped_location_id = str(mapped_location_id)
        self._entry_id = entry.entry_id

        self.metadata = self.TYPE_METADATA.get(
            sensor_type,
            {
                "key": "matrix_sensor",
                "icon": "mdi:flash",
                "device_class": None,
                "state_class": SensorStateClass.MEASUREMENT,
                "unit": None,
                "scale_factor": 1.0,
                "precision": 2,
                "map_key": "grid",
                "fallback_array_key": "activePowerData",
            },
        )

        self._attr_translation_key = self.metadata["key"]
        self._attr_icon = self.metadata["icon"]
        self._attr_device_class = self.metadata["device_class"]
        self._attr_state_class = self.metadata["state_class"]
        self._attr_native_unit_of_measurement = self.metadata["unit"]
        self._attr_suggested_display_precision = self.metadata["precision"]

        entry_loc_id = str(entry.data.get("station_id"))

        # Clean validation context mapping layout using custom location tokens
        if self.mapped_location_id != entry_loc_id:
            # P1 Smart Meter Hub Context -> Map to main service location hub registry device
            self.device_id = f"location_{self.mapped_location_id}"
            self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, self.device_id)})
        else:
            # EV Wall Charger Context -> Map directly to physical charger device serial
            station_serial = entry.data.get("serial") or getattr(
                coordinator.client, "charging_station_serial", "unknown_charger"
            )
            self.device_id = (
                car_uuid if car_uuid else f"location_{self.mapped_location_id}"
            )
            self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, station_serial)})

        self._attr_unique_id = f"{self.device_id}_{self.metadata['key']}"
        device_key = self.device_id.lower().replace("-", "_")
        self.entity_id = f"sensor.{DOMAIN}_{device_key}_{self.metadata['key']}"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the state attributes containing per-phase breakdowns."""
        if not self.coordinator.data or not self.coordinator.power_mapping:
            return None

        mqtt_locations = self.coordinator.data.get("mqtt_locations", {})
        location_data = mqtt_locations.get(self.mapped_location_id, {})
        mqtt_data = location_data.get("power")

        if not isinstance(mqtt_data, dict):
            return None

        map_key = self.metadata["map_key"]
        if map_key in ("grid", "pv"):
            loc_map = self.coordinator.power_mapping.get(map_key, {})
            energy_indices = loc_map.get("energy", [])
            target_array_key = loc_map.get(
                "array_key", self.metadata["fallback_array_key"]
            )
        elif map_key == "cars":
            car_map = (
                self.coordinator.power_mapping["cars"].get("charger", {})
                if not self.car_uuid
                else self.coordinator.power_mapping["cars"].get(self.car_uuid, {})
            )
            energy_indices = car_map.get("energy", [])
            target_array_key = car_map.get("array_key", "activePowerData")
        else:
            return None

        if "power" in self.sensor_type:
            target_array_key = "activePowerData"
            test_array = mqtt_data.get(target_array_key, [])
            if isinstance(test_array, list) and len(test_array) <= 6:
                energy_indices = list(range(len(test_array)))

        target_array = mqtt_data.get(target_array_key)
        if not isinstance(target_array, list) or not energy_indices:
            return None

        attributes = {}
        phase_labels = ["phase_a", "phase_b", "phase_c"]
        scale = self.metadata["scale_factor"]
        precision = self.metadata["precision"]

        try:
            for i, index in enumerate(energy_indices):
                if i < len(phase_labels) and 0 <= index < len(target_array):
                    raw_val = float(target_array[index])
                    attributes[phase_labels[i]] = round(raw_val / scale, precision)

            attributes["service_location_id"] = self.mapped_location_id
            attributes["mqtt_array_key"] = target_array_key

            return attributes
        except (ValueError, TypeError, IndexError):
            return None

    @property
    def native_value(self) -> float | None:
        """Extract and aggregate index positions dynamically by parsing the live JSON structure."""
        if not self.coordinator.data:
            return None

        mqtt_locations = self.coordinator.data.get("mqtt_locations", {})
        location_data = mqtt_locations.get(self.mapped_location_id, {})
        mqtt_data = location_data.get("power")

        if not isinstance(mqtt_data, dict):
            return None

        config_payload = self.coordinator.high_level_configs.get(
            self.mapped_location_id
        )
        if not config_payload:
            return None

        measurements = (
            config_payload
            if isinstance(config_payload, list)
            else config_payload.get("measurements", [])
        )
        target_channel_block = None

        for meas in measurements:
            mtype = str(meas.get("type", "")).upper()

            if self.sensor_type in ("grid", "grid_power") and mtype == "GRID":
                target_channel_block = meas.get("updateChannels", {})
                break
            elif self.sensor_type in ("pv", "pv_power") and mtype == "PRODUCTION":
                target_channel_block = meas.get("updateChannels", {})
                break
            elif self.sensor_type in ("car", "car_power") and mtype == "APPLIANCE":
                # JSON Target Match: Validate if the nested configuration module matches a CAR_CHARGER profile
                if meas.get("appliance", {}).get("type") == "CAR_CHARGER":
                    target_channel_block = meas.get("updateChannels", {})
                    break

        if not target_channel_block:
            return None

        is_power_sensor = "power" in self.sensor_type
        channel_type = "activePower" if is_power_sensor else "meterReadings"

        if channel_type not in target_channel_block and not is_power_sensor:
            channel_type = "activePower"

        channel_cfg = target_channel_block.get(channel_type, {})
        aspect_paths = channel_cfg.get("aspectPaths") or []

        dynamic_indices = []
        dynamic_array_key = (
            "activePowerData" if is_power_sensor else "importActiveEnergyData"
        )

        for path_obj in aspect_paths:
            path_str = path_obj.get("path", "")

            if "[" in path_str and "]" in path_str:
                try:
                    extracted_key = path_str.split("$")[-1].split(".")[1].split("[")[0]
                    dynamic_array_key = extracted_key
                    idx_str = path_str.split("[")[-1].split("]")[0]
                    dynamic_indices.append(int(idx_str))
                except (ValueError, IndexError, AttributeError):
                    pass

        dynamic_indices = list(set(dynamic_indices))

        if is_power_sensor:
            dynamic_array_key = "activePowerData"
            test_array = mqtt_data.get(dynamic_array_key, [])
            if isinstance(test_array, list) and len(test_array) <= 6:
                dynamic_indices = list(range(len(test_array)))

        target_array = mqtt_data.get(dynamic_array_key)
        if not isinstance(target_array, list) or not dynamic_indices:
            return None

        try:
            total_value = 0.0
            for index in dynamic_indices:
                if 0 <= index < len(target_array):
                    total_value += float(target_array[index])

            scale = self.metadata["scale_factor"]
            return round(total_value / scale, self.metadata["precision"])

        except (ValueError, TypeError, IndexError):
            return None
