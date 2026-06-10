import logging
import asyncio
from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .sensor import SmappeeBaseEntity
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Stel de Smappee knoppen dynamisch in."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    client = entry_data["client"]
    coordinator = entry_data["coordinator"]

    entities = []

    if coordinator.data and "smart_devices" in coordinator.data:
        for device in coordinator.data["smart_devices"]:
            if device.get("type", {}).get("category") == "CARCHARGER":
                device_id = device.get("id")
                entities.extend([
                    SmappeePauseChargingButton(coordinator, client, entry.title, device_id),
                    SmappeeStopChargingButton(coordinator, client, entry.title, device_id),
                    SmappeeNormalChargingModeButton(coordinator, client, entry.title, device_id),
                    SmappeeSmartChargingModeButton(coordinator, client, entry.title, device_id),
                ])

    if entities:
        async_add_entities(entities)


class SmappeePauseChargingButton(SmappeeBaseEntity, ButtonEntity):
    """Knop om de actieve laadsessie te pauzeren."""

    _attr_translation_key = "pause_charging_button"
    _attr_icon = "mdi:pause-circle"

    def __init__(self, coordinator, client, entry_title, device_id):
        super().__init__(coordinator, client, entry_title, device_id=device_id, device_type="charger", platform_domain="button")

    @property
    def unique_id(self):
        return f"{self.device_id}_pause_charging_button"

    async def async_press(self) -> None:
        """Vuur de pauseCharging actie af."""
        _LOGGER.info("Pauzeren van laadsessie getriggerd voor lader: %s", self.device_id)
        await self.client.execute_charger_action(self.device_id, "pauseCharging")
        await asyncio.sleep(1.5)
        await self.coordinator.async_request_refresh()


class SmappeeStopChargingButton(SmappeeBaseEntity, ButtonEntity):
    """Knop om de actieve laadsessie permanent te stoppen."""

    _attr_translation_key = "stop_charging_button"
    _attr_icon = "mdi:stop-circle"

    def __init__(self, coordinator, client, entry_title, device_id):
        super().__init__(coordinator, client, entry_title, device_id=device_id, device_type="charger", platform_domain="button")

    @property
    def unique_id(self):
        return f"{self.device_id}_stop_charging_button"

    async def async_press(self) -> None:
        """Vuur de stopCharging actie af."""
        _LOGGER.info("Stoppen van laadsessie getriggerd voor lader: %s", self.device_id)
        await self.client.execute_charger_action(self.device_id, "stopCharging")
        await asyncio.sleep(1.5)
        await self.coordinator.async_request_refresh()

class SmappeeNormalChargingModeButton(SmappeeBaseEntity, ButtonEntity):
    """Knop om direct over te schakelen naar Standaard Laden."""

    _attr_translation_key = "normal_charging_mode_button"

    def __init__(self, coordinator, client, entry_title, device_id):
        super().__init__(coordinator, client, entry_title, device_id=device_id, device_type="charger", platform_domain="button")

    @property
    def unique_id(self):
        return f"{self.device_id}_normal_charging_mode_button"

    async def async_press(self) -> None:
        """Aangeroepen wanneer de knop wordt ingedrukt."""
        service_location_id = self.smart_device_data.get("serviceLocation") if self.smart_device_data else None
        if not service_location_id:
            _LOGGER.error("Kan laadmodus niet wijzigen: serviceLocation ontbreekt.")
            return

        success = await self.client.set_normal_charging_mode(service_location_id, self.device_id)
        if success:
            await self.coordinator.async_request_refresh()

    @property
    def icon(self):
        return "mdi:lightning-bolt"


class SmappeeSmartChargingModeButton(SmappeeBaseEntity, ButtonEntity):
    """Knop om direct over te schakelen naar Slim Laden."""

    _attr_translation_key = "smart_charging_mode_button"

    def __init__(self, coordinator, client, entry_title, device_id):
        super().__init__(coordinator, client, entry_title, device_id=device_id, device_type="charger", platform_domain="button")

    @property
    def unique_id(self):
        return f"{self.device_id}_smart_charging_mode_button"

    async def async_press(self) -> None:
        """Aangeroepen wanneer de knop wordt ingedrukt."""
        service_location_id = self.smart_device_data.get("serviceLocation") if self.smart_device_data else None
        if not service_location_id:
            _LOGGER.error("Kan laadmodus niet wijzigen: serviceLocation ontbreekt.")
            return

        success = await self.client.set_smart_charging_mode(service_location_id, self.device_id)
        if success:
            await self.coordinator.async_request_refresh()

    @property
    def icon(self):
        return "mdi:brain"