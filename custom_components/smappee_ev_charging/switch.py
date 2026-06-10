import logging
import asyncio
import json
from homeassistant.components.switch import SwitchEntity, SwitchDeviceClass
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
    """Stel de Smappee switch entiteiten dynamisch in op basis van ontdekte apparaten."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    client = entry_data["client"]
    coordinator = entry_data["coordinator"]

    entities = []

    if coordinator.data and "smart_devices" in coordinator.data:
        smart_devices = coordinator.data["smart_devices"]

        for device in smart_devices:
            category = device.get("type", {}).get("category")
            device_id = device.get("id")

            # Maak de schakelaars alleen aan voor apparaten van het type CARCHARGER
            if category == "CARCHARGER" and device_id:
                _LOGGER.debug("Dynamisch schakelaars aanmaken voor Smappee lader: %s", device_id)
                
                entities.extend([
                    SmappeeAvailabilitySwitch(coordinator, client, entry.title, device_id),
                    SmappeeOfflineChargingSwitch(coordinator, client, entry.title, device_id)
                ])

    if entities:
        async_add_entities(entities)


class SmappeeAvailabilitySwitch(SmappeeBaseEntity, SwitchEntity):
    """Schakelaar om de laadpaal algemeen beschikbaar of onbeschikbaar te maken."""

    _attr_translation_key = "charger_availability"
    _attr_device_class = SwitchDeviceClass.OUTLET
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, client, entry_title, device_id):
        super().__init__(coordinator, client, entry_title, device_id=device_id, device_type="charger", platform_domain="switch")

    @property
    def unique_id(self):
        return f"{self.device_id}_charger_availability_switch"

    @property
    def is_on(self) -> bool:
        """Geef True terug als de lader beschikbaar is via MQTT of REST fallback."""
        if self.coordinator.data and "mqtt_charging_state" in self.coordinator.data:
            mqtt_payload = self.coordinator.data["mqtt_charging_state"]
            try:
                mqtt_json = json.loads(mqtt_payload)
                if isinstance(mqtt_json, dict) and "available" in mqtt_json:
                    return bool(mqtt_json["available"])
            except Exception:
                pass

        data = self.smart_device_data
        if not data:
            return False
        return data.get("available", False)

    async def async_turn_on(self, **kwargs) -> None:
        """Stel de lader in als beschikbaar (setAvailable)."""
        _LOGGER.debug("Laadpaal %s handmatig ingeschakeld (beschikbaar gemaakt)", self.device_id)
        
        success = await self.client.set_charger_availability(self.device_id, True)
        if success:
            if self.coordinator.data and "smart_devices" in self.coordinator.data:
                for device in self.coordinator.data["smart_devices"]:
                    if device.get("id") == self.device_id:
                        device["available"] = True
                        break
                self.coordinator.async_set_updated_data(self.coordinator.data)

            await asyncio.sleep(1.5)
            await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        """Stel de lader in als onbeschikbaar (setUnavailable)."""
        _LOGGER.debug("Laadpaal %s handmatig uitgeschakeld (onbeschikbaar gemaakt)", self.device_id)
        
        success = await self.client.set_charger_availability(self.device_id, False)
        if success:
            if self.coordinator.data and "smart_devices" in self.coordinator.data:
                for device in self.coordinator.data["smart_devices"]:
                    if device.get("id") == self.device_id:
                        device["available"] = False
                        break
                self.coordinator.async_set_updated_data(self.coordinator.data)

            await asyncio.sleep(1.5)
            await self.coordinator.async_request_refresh()

    @property
    def icon(self):
        return "mdi:ev-station" if self.is_on else "mdi:ev-station-disabled"


class SmappeeOfflineChargingSwitch(SmappeeBaseEntity, SwitchEntity):
    """Schakelaar om offline laden / failsafe modus te beheren via v11 data."""

    _attr_translation_key = "offline_charging"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, client, entry_title, device_id):
        super().__init__(coordinator, client, entry_title, device_id=device_id, device_type="charger", platform_domain="switch")

    @property
    def unique_id(self):
        return f"{self.device_id}_offline_charging_switch"

    @property
    def is_on(self) -> bool:
        """Lees de status rechtstreeks uit de nieuwe v11 charging_station_details database."""
        if self.coordinator.data and "charging_station_details" in self.coordinator.data:
            serial = getattr(self.client, "charging_station_serial", None)
            station_data = self.coordinator.data["charging_station_details"].get(serial) if serial else None
            
            if station_data and "offlineCharging" in station_data:
                # Directe koppeling: True is True, False is False!
                return bool(station_data["offlineCharging"].get("enabled", False))

        # Betrouwbare Fallback: oude v10 loadManagement methode
        data = self.smart_device_data
        if data:
            return bool(data.get("loadManagement", {}).get("active", False))
            
        return False

    async def _send_payload(self, enabled: bool):
        """Helper om de status 1-op-1 naar de Smappee v11 API te pushen."""
        serial = getattr(self.client, "charging_station_serial", None)
        current_failsafe = 3  # Veilige basis default (zoals gezien in je JSON)
        
        # Probeer de actuele failsafe stroom dynamisch uit de data te vissen
        if self.coordinator.data and "charging_station_details" in self.coordinator.data and serial:
            station_data = self.coordinator.data["charging_station_details"].get(serial)
            if station_data and "offlineCharging" in station_data:
                current_failsafe = int(station_data["offlineCharging"].get("failSafe", 3))
        else:
            # Fallback op de v10 configuratie eigenschappen
            data = self.smart_device_data
            if data:
                config_props = data.get("configurationProperties", [])
                for prop in config_props:
                    spec = prop.get("spec", {}) if "spec" in prop else prop
                    if spec.get("name") == "etc.smart.device.type.car.charger.config.max.gridassistanceamps":
                        values = prop.get("values", [{}])
                        if values:
                            current_failsafe = int(values[0].get("Integer", 3))

        _LOGGER.debug("Load Management switch gewijzigd naar %s. Sturen naar v11 API met failsafe %s A", enabled, current_failsafe)

        # De geteste v11 PATCH call via de client
        success = await self.client.set_offline_charging_config(enabled, current_failsafe)
        
        if success and self.coordinator.data:
            # Multi-layer Optimistic UI update zodat beide datastructuren direct synchroon lopen
            
            # 1. Update in de rijke v11 station details structuur
            if serial and "charging_station_details" in self.coordinator.data:
                station_data = self.coordinator.data["charging_station_details"].get(serial)
                if station_data:
                    if "offlineCharging" not in station_data:
                        station_data["offlineCharging"] = {}
                    station_data["offlineCharging"]["enabled"] = enabled

            # 2. Update in de klassieke v10 smart_devices lijst
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

    async def async_turn_on(self, **kwargs) -> None:
        """Zet load management AAN (enabled=True)."""
        await self._send_payload(True)

    async def async_turn_off(self, **kwargs) -> None:
        """Zet load management UIT (enabled=False)."""
        await self._send_payload(False)

    @property
    def icon(self):
        return "mdi:cloud-outline" if self.is_on else "mdi:cloud-off-outline"