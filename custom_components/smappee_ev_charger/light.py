"""Set up and manage Smappee Charger light entities."""

import asyncio
import logging
from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .sensor import SmappeeBaseEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Smappee light entities dynamically based on discovered devices."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    client = entry_data["client"]
    coordinator = entry_data["coordinator"]

    entities = []

    if coordinator.data and "smart_devices" in coordinator.data:
        smart_devices = coordinator.data["smart_devices"]

        for device in smart_devices:
            category = device.get("type", {}).get("category")
            device_id = device.get("id")

            # Create light entry panels exclusively for LED module components
            if category == "LED" and device_id:
                _LOGGER.debug(
                    "Dynamically creating dimmable light entity for LED module: %s",
                    device_id,
                )
                entities.append(
                    SmappeeLedLight(coordinator, client, entry.title, device_id)
                )

    if entities:
        async_add_entities(entities)


class SmappeeLedLight(SmappeeBaseEntity, LightEntity):
    """Control the dimmable illumination panel on the Smappee station casing assembly."""

    _attr_translation_key = "led_light"
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, coordinator, client, entry_title, device_id: str) -> None:
        """Initialize the Smappee LED light entity."""
        super().__init__(
            coordinator,
            client,
            entry_title,
            device_id=device_id,
            device_type="led",
            platform_domain="light",
        )

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this light entity."""
        return f"{self.device_id}_led_light"

    @property
    def current_brightness_percentage(self) -> int:
        """Extract the baseline scaling index integer from configuration property caches."""
        data = self.smart_device_data
        config_props = data.get("configurationProperties", [])
        for prop in config_props:
            spec = prop.get("spec", {}) if "spec" in prop else prop
            if (
                spec.get("name")
                == "etc.smart.device.type.car.charger.led.config.brightness"
            ):
                values = prop.get("values", [{}])
                if values:
                    return int(values[0].get("Integer", 0))
        return 0

    @property
    def is_on(self) -> bool:
        """Return True if the active luminosity registration scales above zero."""
        return self.current_brightness_percentage > 0

    @property
    def brightness(self) -> int | None:
        """Return the current light output intensity mapped inside Home Assistant scaling space (0-255)."""
        pct = self.current_brightness_percentage
        if pct == 0:
            return None
        return round((pct / 100.0) * 255)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Expose standard intensity modifications and transmit luminosity scaling limits."""
        if ATTR_BRIGHTNESS in kwargs:
            ha_brightness = kwargs[ATTR_BRIGHTNESS]
            target_percentage = int(round((ha_brightness / 255.0) * 100))
        else:
            target_percentage = 25

        target_percentage = max(1, min(100, target_percentage))

        _LOGGER.debug(
            "Modifying Smappee housing casing LED ring %s intensity to %s%%",
            self.device_id,
            target_percentage,
        )

        success = await self.client.update_configuration_property(
            device_id=self.device_id,
            property_name="etc.smart.device.type.car.charger.led.config.brightness",
            value_dict={"Integer": target_percentage},
        )

        if success:
            self._update_local_coordinator_brightness(target_percentage)
            await asyncio.sleep(1.0)
            await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Extinguish localized case panel accents by dropping target levels to zero."""
        _LOGGER.debug(
            "Extinguishing active illumination fields for Smappee LED module %s",
            self.device_id,
        )

        success = await self.client.update_configuration_property(
            device_id=self.device_id,
            property_name="etc.smart.device.type.car.charger.led.config.brightness",
            value_dict={"Integer": 0},
        )

        if success:
            self._update_local_coordinator_brightness(0)
            await asyncio.sleep(1.0)
            await self.coordinator.async_request_refresh()

    def _update_local_coordinator_brightness(self, value: int) -> None:
        """Step active database memory properties inside caching arrays to stabilize transitions."""
        if self.coordinator.data and "smart_devices" in self.coordinator.data:
            for device in self.coordinator.data["smart_devices"]:
                if device.get("id") == self.device_id:
                    for prop in device.get("configurationProperties", []):
                        spec = prop.get("spec", {}) if "spec" in prop else prop
                        if (
                            spec.get("name")
                            == "etc.smart.device.type.car.charger.led.config.brightness"
                        ):
                            prop["values"] = [{"Integer": value}]
                            break
            self.coordinator.async_set_updated_data(self.coordinator.data)
