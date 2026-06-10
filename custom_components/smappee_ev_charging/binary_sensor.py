import logging
import json
from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import EntityCategory

from .sensor import SmappeeBaseEntity
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Stel de Smappee binary sensor entiteiten dynamisch in op basis van ontdekte apparaten."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    client = entry_data["client"]
    coordinator = entry_data["coordinator"]

    entities = []

    if coordinator.data and "smart_devices" in coordinator.data:
        smart_devices = coordinator.data["smart_devices"]

        for device in smart_devices:
            category = device.get("type", {}).get("category")
            device_id = device.get("id")

            # Maak binary sensoren aan per ontdekte CARCHARGER lader
            if category == "CARCHARGER" and device_id:
                _LOGGER.debug("Dynamisch binaire sensoren aanmaken voor Smappee lader: %s", device_id)
                
                entities.extend([
                    SmappeeNetworkStatusBinarySensor(coordinator, client, entry.title, device_id),
                    SmappeeCarConnectedBinarySensor(coordinator, client, entry.title, device_id),
                ])

    if entities:
        async_add_entities(entities)


class SmappeeNetworkStatusBinarySensor(SmappeeBaseEntity, BinarySensorEntity):
    """Geeft aan of de laadpaal momenteel online en beschikbaar is volgens de Smappee Cloud."""

    _attr_translation_key = "network_status"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, client, entry_title, device_id):
        super().__init__(coordinator, client, entry_title, device_id=device_id, device_type="charger", platform_domain="binary_sensor")

    @property
    def unique_id(self):
        return f"{self.device_id}_network_status"

    @property
    def is_on(self) -> bool:
        """Geef True terug als de lader volgens de API online/active is."""
        
        # 1. Check eerst de rijke details van de laadpaal (hier zit de harde waarheid)
        if self.coordinator.data and "charging_station_details" in self.coordinator.data:
            serial = getattr(self.client, "charging_station_serial", None)
            station_data = self.coordinator.data["charging_station_details"].get(str(serial))
            if station_data:
                # Smappee gebruikt 'active' en 'available' op het hoogste niveau van het laadstation
                return station_data.get("active", True) and station_data.get("available", True)

        # 2. Fallback naar de vlakke apparaat data
        data = self.smart_device_data
        if not data:
            return False
        
        return data.get("available", False)


class SmappeeCarConnectedBinarySensor(SmappeeBaseEntity, BinarySensorEntity):
    """Binaire sensor die 'True' wordt zodra er fysiek een voertuig is gekoppeld via de laadkabel."""

    _attr_translation_key = "car_connected"
    _attr_device_class = BinarySensorDeviceClass.PLUG

    def __init__(self, coordinator, client, entry_title, device_id):
        super().__init__(coordinator, client, entry_title, device_id=device_id, device_type="charger", platform_domain="binary_sensor")

    @property
    def unique_id(self):
        return f"{self.device_id}_car_connected"

    @property
    def is_on(self) -> bool:
        """Controleer real-time via MQTT of via de cloud fallback of de auto ingeplugd is."""
        
        # 1. PRIORITEIT: Live MQTT JSON status uit de WebSocket (Real-time)
        if self.coordinator.data and "mqtt_charging_state" in self.coordinator.data:
            mqtt_payload = self.coordinator.data["mqtt_charging_state"]
            try:
                mqtt_json = json.loads(mqtt_payload)
                if isinstance(mqtt_json, dict):
                    # Directe uitsluiting op basis van de harde IEC-norm (A = Vrijblijvend/Los)
                    iec_status = str(mqtt_json.get("iecStatus", "")).upper()
                    if iec_status.startswith("A"):
                        return False

                    connection_status = str(mqtt_json.get("connectionStatus", "")).upper()
                    if connection_status == "DISCONNECTED":
                        return False
                    if connection_status == "CONNECTED":
                        return True

                    status_obj = mqtt_json.get("status", {})
                    state = str(status_obj.get("current", mqtt_json.get("chargingState", ""))).upper()
                    if state in ["AVAILABLE", "DISCONNECTED"]:
                        return False
                    if state in ["CABLE_CONNECTED", "CHARGING", "SUSPENDED", "SUSPENDED_EV", "SUSPENDED_EVSE"]:
                        return True
                    
                    if iec_status in ["B1", "B2", "C1", "C2", "D1", "D2"]:
                        return True
            except Exception:
                pass

        # 2. SECUNDAIR: Haal het uit de rijke charging_station_details cache
        if self.coordinator.data and "charging_station_details" in self.coordinator.data:
            serial = getattr(self.client, "charging_station_serial", None)
            station_data = self.coordinator.data["charging_station_details"].get(str(serial))
            
            if station_data:
                for module in station_data.get("modules", []):
                    if "carCharger" in module and module["carCharger"]:
                        cc_data = module["carCharger"]
                        
                        # Check ook hier direct de live IEC-status van de lader
                        rest_iec = str(cc_data.get("iecStatus", "")).upper()
                        if rest_iec.startswith("A"):
                            return False
                            
                        if cc_data.get("connectionStatus") == "DISCONNECTED":
                            return False
                        if cc_data.get("connectionStatus") == "CONNECTED":
                            return True
                            
                        status_dict = cc_data.get("status", {})
                        rest_state = str(status_dict.get("current", "")).upper()
                        if rest_state in ["AVAILABLE", "DISCONNECTED"]:
                            return False
                        if rest_state in ["CABLE_CONNECTED", "CHARGING", "SUSPENDED", "SUSPENDED_EV", "SUSPENDED_EVSE"]:
                            return True
                            
                        if rest_iec in ["B1", "B2", "C1", "C2", "D1", "D2"]:
                            return True

        # 3. FALLBACK: Oude vlakke smart_devices structuur
        data = self.smart_device_data
        if data:
            # Check IEC status op root of genest
            rest_iec = str(data.get("iecStatus", "")).upper()
            if rest_iec.startswith("A"):
                return False

            if data.get("connectionStatus") == "DISCONNECTED":
                return False
            if data.get("connectionStatus") == "CONNECTED":
                return True

            if "carCharger" in data:
                cc_data = data["carCharger"]
                if str(cc_data.get("iecStatus", "")).upper().startswith("A"):
                    return False
                if cc_data.get("connectionStatus") == "DISCONNECTED":
                    return False
                
                status_dict = cc_data.get("status", {})
                state = str(status_dict.get("current", "")).upper()
                if state in ["AVAILABLE", "DISCONNECTED"]:
                    return False
                if state in ["CABLE_CONNECTED", "CHARGING", "SUSPENDED", "SUSPENDED_EV", "SUSPENDED_EVSE"]:
                    return True

        return False