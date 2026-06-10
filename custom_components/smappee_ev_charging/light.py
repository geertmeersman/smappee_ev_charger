import logging
import asyncio
from homeassistant.components.light import LightEntity, ColorMode, ATTR_BRIGHTNESS
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .sensor import SmappeeBaseEntity
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Stel de Smappee LED-ring in als een dimbare lamp."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    client = entry_data["client"]
    coordinator = entry_data["coordinator"]

    entities = []

    if coordinator.data and "smart_devices" in coordinator.data:
        smart_devices = coordinator.data["smart_devices"]

        for device in smart_devices:
            category = device.get("type", {}).get("category")
            device_id = device.get("id")

            # Maak de lamp-entiteit aan zodra de LED module wordt gevonden
            if category == "LED" and device_id:
                _LOGGER.debug("Dynamische dimbare lamp aanmaken voor LED module: %s", device_id)
                entities.append(
                    SmappeeLedLight(coordinator, client, entry.title, device_id)
                )

    if entities:
        async_add_entities(entities)


class SmappeeLedLight(SmappeeBaseEntity, LightEntity):
    """Dimbare lamp entiteit voor de Smappee EV Wall LED-ring."""

    _attr_translation_key = "led_light"
    
    # We configureren de lamp als een dimbare lamp (helderheid control)
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, coordinator, client, entry_title, device_id):
        super().__init__(coordinator, client, entry_title, device_id=device_id, device_type="led", platform_domain="light")

    @property
    def unique_id(self):
        return f"{self.device_id}_led_light"

    @property
    def current_brightness_percentage(self) -> int:
        """Helper om het actuele percentage (0-100) uit de coordinator te vissen."""
        data = self.smart_device_data
        config_props = data.get("configurationProperties", [])
        for prop in config_props:
            spec = prop.get("spec", {}) if "spec" in prop else prop
            if spec.get("name") == "etc.smart.device.type.car.charger.led.config.brightness":
                values = prop.get("values", [{}])
                if values:
                    return int(values[0].get("Integer", 0))
        return 0

    @property
    def is_on(self) -> bool:
        """De lamp is aan als de helderheid groter is dan 0%."""
        return self.current_brightness_percentage > 0

    @property
    def brightness(self) -> int | None:
        """Geef de helderheid terug in Home Assistant formaat (0 - 255)."""
        # Smappee (0-100%) omrekenen naar HA (0-255)
        pct = self.current_brightness_percentage
        if pct == 0:
            return None
        return round((pct / 100.0) * 255)

    async def async_turn_on(self, **kwargs) -> None:
        """Zet de lamp aan of verander de helderheid."""
        # 1. Bepaal het doel-percentage
        if ATTR_BRIGHTNESS in kwargs:
            # Gebruiker verschuift de slider in HA (0-255 -> 0-100%)
            ha_brightness = kwargs[ATTR_BRIGHTNESS]
            target_percentage = int(round((ha_brightness / 255.0) * 100))
        else:
            # Gebruiker klikt gewoon op 'AAN': we pakken de 25% default
            target_percentage = 25

        target_percentage = max(1, min(100, target_percentage))

        _LOGGER.debug("Smappee LED %s inschakelen op %s%%", self.device_id, target_percentage)
        
        # HAAL DE LIJST MET APPARATEN OP UIT DE COORDINATOR
        smart_devices = self.coordinator.data.get("smart_devices", []) if self.coordinator.data else []
        
        # PAS DE AANROEP AAN: Geef nu netjes alle 3 de argumenten mee!
        success = await self.client.set_led_brightness(smart_devices, self.device_id, target_percentage)
        if success:
            self._update_local_coordinator_brightness(target_percentage)
            await asyncio.sleep(1.0)
            await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        """Zet de lamp uit (helderheid naar 0%)."""
        _LOGGER.debug("Smappee LED %s uitschakelen (0%%)", self.device_id)
        
        # HAAL DE LIJST MET APPARATEN OP UIT DE COORDINATOR
        smart_devices = self.coordinator.data.get("smart_devices", []) if self.coordinator.data else []
        
        # PAS DE AANROEP AAN: Geef ook hier de 3 vereiste argumenten mee!
        success = await self.client.set_led_brightness(smart_devices, self.device_id, 0)
        if success:
            self._update_local_coordinator_brightness(0)
            await asyncio.sleep(1.0)
            await self.coordinator.async_request_refresh()

    def _update_local_coordinator_brightness(self, value: int):
        """Kleine interne helper om de stand direct in het geheugen aan te passen."""
        if self.coordinator.data and "smart_devices" in self.coordinator.data:
            for device in self.coordinator.data["smart_devices"]:
                if device.get("id") == self.device_id:
                    for prop in device.get("configurationProperties", []):
                        spec = prop.get("spec", {}) if "spec" in prop else prop
                        if spec.get("name") == "etc.smart.device.type.car.charger.led.config.brightness":
                            prop["values"] = [{"Integer": value}]
                            break
            self.coordinator.async_set_updated_data(self.coordinator.data)