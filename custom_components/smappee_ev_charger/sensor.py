import json
import logging
from datetime import datetime, timezone

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
    """Stel de Smappee sensor entiteiten dynamisch in op basis van ontdekte apparaten."""
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
                _LOGGER.debug("Dynamisch sensoren aanmaken voor Smappee lader: %s", device_id)

                entities.extend([
                    SmappeeStatusSensor(coordinator, client, entry.title, device_id),
                    SmappeeLivePowerSensor(coordinator, client, entry.title, device_id),
                    SmappeeMaxCurrentLimitSensor(coordinator, client, entry.title, device_id),
                    SmappeeSessionDurationSensor(coordinator, client, entry.title, device_id),
                    SmappeeSessionEnergySensor(coordinator, client, entry.title, device_id),
                    SmappeeSessionRfidSensor(coordinator, client, entry.title, device_id),
                ])

    if entities:
        async_add_entities(entities)


class SmappeeBaseEntity(CoordinatorEntity):
    """Algemene basisklasse voor alle Smappee entiteiten met Device-koppeling."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, client, entry_title, device_id, device_type="charger", platform_domain="sensor"):
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
            fallback_key = self.__class__.__name__.replace(MANUFACTURER, "").replace("Sensor", "").lower()
            self.entity_id = f"{platform_domain}.{DOMAIN}_{device_key}_{fallback_key}"

    @property
    def smart_device_data(self):
        """Helper om exact de juiste module uit de platte coordinator masterlijst te vissen."""
        if not self.coordinator.data or "smart_devices" not in self.coordinator.data:
            return {}

        smart_devices = self.coordinator.data["smart_devices"]
        for device in smart_devices:
            if device.get("id") == self.device_id:
                return device
        return {}

    @property
    def device_info(self) -> DeviceInfo:
        """Koppel de entiteit aan het juiste apparaat (Lader of LED) met correcte naamgeving."""
        data = self.smart_device_data
        category = data.get("type", {}).get("category", "UNKNOWN")

        child_location_id = data.get("serviceLocation")

        parent_location_id = None
        if self.coordinator.data and "servicelocations" in self.coordinator.data:
            locs = self.coordinator.data["servicelocations"]
            current_loc = next((loc for loc in locs if loc.get("id") == child_location_id), None)
            if current_loc:
                parent_location_id = current_loc.get("parentId")

        parent_identifier = (DOMAIN, f"location_{parent_location_id}") if parent_location_id else None

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

        else:
            station_serial = data.get("stationSerialNumber") or data.get("serialNumber") or self.client.charging_station_serial or "unknown_charger"
            model_name = data.get("model", "WALL_QUANTUM_CABLE")

            device_name = "Smappee laadstation - EV Wall"
            if self.coordinator.data and "smart_devices" in self.coordinator.data:
                smart_devices = self.coordinator.data["smart_devices"]
                charging_station_data = next(
                    (d for d in smart_devices if d.get("type", {}).get("category") == "CHARGINGSTATION"),
                    None
                )
                if charging_station_data:
                    display_name = charging_station_data.get("type", {}).get("displayName", "Smappee laadstation")
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
    """Basisklasse voor sensoren die data uit de laadsessies (v10) halen."""

    @property
    def active_session_data(self):
        """Haal de meest recente laadsessie op uit het v10 endpoint."""
        if not self.coordinator.data or "recent_sessions" not in self.coordinator.data:
            return {}

        sessions = self.coordinator.data["recent_sessions"]
        if sessions and isinstance(sessions, list):
            return sessions[0]
        return {}


class SmappeeStatusSensor(SmappeeBaseEntity, SensorEntity):
    """Sensor die de actuele status van het laadstation weergeeft via MQTT of Rijke REST."""

    _attr_translation_key = "charger_status"

    def __init__(self, coordinator, client, entry_title, device_id):
        super().__init__(coordinator, client, entry_title, device_id=device_id, device_type="charger", platform_domain="sensor")

    @property
    def unique_id(self):
        return f"{self.device_id}_charger_status"

    @property
    def native_value(self):
        # 1. Prioriteit: Live status vanuit Smappee Cloud MQTT (WebSocket - Real-time)
        if self.coordinator.data and "mqtt_charging_state" in self.coordinator.data:
            mqtt_payload = self.coordinator.data["mqtt_charging_state"]
            try:
                mqtt_json = json.loads(mqtt_payload)
                if isinstance(mqtt_json, dict):
                    # Check A: Pak de meest gedetailleerde live status (bvb "CHARGING_FINISHED")
                    detailed_status = mqtt_json.get("status", {}).get("current")
                    if detailed_status:
                        return str(detailed_status).upper()

                    # Check B: Fallback binnen MQTT naar de algemene chargingState
                    charging_state = mqtt_json.get("chargingState")
                    if charging_state:
                        return str(charging_state).upper()
            except Exception:
                if len(str(mqtt_payload)) <= 255:
                    return str(mqtt_payload).upper()

        # 2. Secundair: Haal de status uit de nieuwe rijke v11 station details (Perfect bij HA Herstart)
        if self.coordinator.data and "charging_station_details" in self.coordinator.data:
            serial = getattr(self.client, "charging_station_serial", None)
            station_data = self.coordinator.data["charging_station_details"].get(serial) if serial else None

            if station_data:
                for module in station_data.get("modules", []):
                    if "carCharger" in module and module["carCharger"]:
                        # Pak de diepe status (bvb 'SUSPENDED_EVSE')
                        rest_detailed = module["carCharger"].get("status", {}).get("current")
                        if rest_detailed:
                            return str(rest_detailed).upper()

                        # Fallback binnen de module naar connectionStatus ('CONNECTED')
                        rest_conn = module["carCharger"].get("connectionStatus")
                        if rest_conn:
                            return str(rest_conn).upper()

        # 3. Ultieme Fallback: Oude vlakke smart_devices structuur
        data = self.smart_device_data
        if data:
            if "chargingState" in data and data.get("chargingState") is not None:
                return str(data.get("chargingState")).upper()
            if "connectionStatus" in data:
                return str(data.get("connectionStatus")).upper()

        return "AVAILABLE"

    @property
    def extra_state_attributes(self):
        """Sla de rest van de rijke MQTT JSON data op als attributen."""
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
        if "ERROR" in status_upper or "FAULT" in status_upper or "NOT_AVAILABLE" in status_upper:
            return "mdi:ev-station-disabled"

        return "mdi:ev-station"


class SmappeeLivePowerSensor(SmappeeBaseEntity, SensorEntity):
    """Sensor die het actuele live laadvermogen in Watts weergeeft (MQTT prioritized)."""

    _attr_translation_key = "live_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator, client, entry_title, device_id):
        super().__init__(coordinator, client, entry_title, device_id=device_id, device_type="charger", platform_domain="sensor")

    @property
    def unique_id(self):
        return f"{self.device_id}_live_power"

    @property
    def native_value(self) -> float | None:
        """Bereken het actuele vermogen en converteer naar kW."""
        raw_watts = None

        # 1. Prioriteit: Live power op basis van de 3 actieve MQTT fases
        if self.coordinator.data and "mqtt_power_data" in self.coordinator.data:
            mqtt_data = self.coordinator.data["mqtt_power_data"]
            if isinstance(mqtt_data, dict) and "activePowerData" in mqtt_data:
                try:
                    raw_watts = float(sum(mqtt_data["activePowerData"]))
                except (TypeError, ValueError):
                    pass

        # 2. Secundair: Haal het uit de nieuwe rijke v11 station details mocht MQTT er niet zijn
        if raw_watts is None and self.coordinator.data and "charging_station_details" in self.coordinator.data:
            serial = getattr(self.client, "charging_station_serial", None)
            station_data = self.coordinator.data["charging_station_details"].get(str(serial))
            if station_data:
                for module in station_data.get("modules", []):
                    if "carCharger" in module and module["carCharger"]:
                        live_p = module["carCharger"].get("livePower")
                        if live_p is not None:
                            raw_watts = float(live_p)
                            break

        # 3. Ultieme Fallback: Oude vlakke smart_devices structuur
        if raw_watts is None:
            data = self.smart_device_data
            if data:
                raw_watts = float(data.get("livePower", 0.0))

        # Als er een waarde is gevonden, reken hem om naar kW en rond af op 2 decimalen
        if raw_watts is not None:
            return round(raw_watts / 1000.0, 2)

        return 0.00


class SmappeeMaxCurrentLimitSensor(SmappeeBaseEntity, SensorEntity):
    """Sensor die de maximale stroomlimiet in Ampère toont uit de configuratie."""

    _attr_translation_key = "max_current_limit"
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator, client, entry_title, device_id):
        super().__init__(coordinator, client, entry_title, device_id=device_id, device_type="charger", platform_domain="sensor")

    @property
    def unique_id(self):
        return f"{self.device_id}_max_current_limit"

    @property
    def native_value(self):
        data = self.smart_device_data
        if not data:
            return None

        config_props = data.get("configurationProperties", [])
        for prop in config_props:
            spec = prop.get("spec", {})
            if spec.get("name") == "etc.smart.device.type.car.charger.config.max.current":
                values = prop.get("values", [{}])
                if values:
                    return values[0].get("Quantity", {}).get("value")
        return None


class SmappeeSessionDurationSensor(SmappeeBaseSessionSensor):
    """Sensor die de actuele duur van de actieve laadsessie berekent in minuten."""

    _attr_translation_key = "session_duration"
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, client, entry_title, device_id):
        super().__init__(coordinator, client, entry_title, device_id=device_id, device_type="charger", platform_domain="sensor")

    @property
    def unique_id(self):
        return f"{self.device_id}_session_duration"

    @property
    def native_value(self):
        session = self.active_session_data
        if not session:
            return 0

        start_ts = session.get("from")
        if not start_ts:
            return 0

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
            _LOGGER.error("Fout bij berekenen sessieduur voor %s: %s", self.device_id, err)
            return 0

    @property
    def icon(self):
        return "mdi:clock-outline"


class SmappeeSessionEnergySensor(SmappeeBaseSessionSensor):
    """Sensor die de geladen energie toont, met alle overige API-sessiedata dynamisch als attributen."""

    _attr_translation_key = "session_energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator, client, entry_title, device_id):
        super().__init__(coordinator, client, entry_title, device_id=device_id, device_type="charger", platform_domain="sensor")

    @property
    def unique_id(self):
        return f"{self.device_id}_session_energy"

    @property
    def native_value(self) -> float:
        """Haal de geladen energie op."""
        energy = self.active_session_data.get("energy", 0.0)
        return round(float(energy), 2)

    @property
    def extra_state_attributes(self) -> dict[str, any] | None:
        """Neem een kopie van de volledige sessie JSON en filter de ballast eruit."""
        if not self.active_session_data:
            return None

        # Maak een veilige kopie van de dictionary om de coordinator-data intact te laten
        attributes = dict(self.active_session_data)

        # Snijd de hoofdwaarde en de statische/zware infrastructuur-objecten eruit
        attributes.pop("energy", None)
        attributes.pop("controller", None)
        attributes.pop("station", None)
        attributes.pop("address", None)
        attributes.pop("updateChannels", None)

        return attributes

class SmappeeSessionRfidSensor(SmappeeBaseSessionSensor):
    """Sensor die het RFID-kaartnummer (Token) toont waarmee de sessie is gestart."""

    _attr_translation_key = "session_rfid"

    def __init__(self, coordinator, client, entry_title, device_id):
        super().__init__(coordinator, client, entry_title, device_id=device_id, device_type="charger", platform_domain="sensor")

    @property
    def unique_id(self):
        return f"{self.device_id}_session_rfid"

    @property
    def native_value(self):
        return self.active_session_data.get("rfid")

    @property
    def icon(self):
        return "mdi:card-account-details"
