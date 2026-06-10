"""Set up and manage Smappee Charger button entities."""

import asyncio
import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .sensor import SmappeeBaseEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Smappee button entities dynamically based on discovered devices."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    client = entry_data["client"]
    coordinator = entry_data["coordinator"]

    entities = []

    if coordinator.data and "smart_devices" in coordinator.data:
        for device in coordinator.data["smart_devices"]:
            if device.get("type", {}).get("category") == "CARCHARGER":
                device_id = device.get("id")

                if device_id:
                    _LOGGER.debug(
                        "Dynamically creating button entities for Smappee charger: %s",
                        device_id,
                    )
                    entities.extend(
                        [
                            SmappeePauseChargingButton(
                                coordinator, client, entry.title, device_id
                            ),
                            SmappeeStopChargingButton(
                                coordinator, client, entry.title, device_id
                            ),
                            SmappeeNormalChargingModeButton(
                                coordinator, client, entry.title, device_id
                            ),
                            SmappeeSmartChargingModeButton(
                                coordinator, client, entry.title, device_id
                            ),
                        ]
                    )

    if entities:
        async_add_entities(entities)


class SmappeePauseChargingButton(SmappeeBaseEntity, ButtonEntity):
    """Trigger a temporary pause on the active charging session."""

    _attr_translation_key = "pause_charging_button"
    _attr_icon = "mdi:pause-circle"

    def __init__(self, coordinator, client, entry_title, device_id: str) -> None:
        """Initialize the Smappee pause charging button entity."""
        super().__init__(
            coordinator,
            client,
            entry_title,
            device_id=device_id,
            device_type="charger",
            platform_domain="button",
        )

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this button entity."""
        return f"{self.device_id}_pause_charging_button"

    async def async_press(self) -> None:
        """Execute the pauseCharging device action endpoint task."""
        _LOGGER.info(
            "Triggered charging session pause instruction for charger: %s",
            self.device_id,
        )

        service_location_id = (
            self.smart_device_data.get("serviceLocation")
            if self.smart_device_data
            else None
        )
        if not service_location_id:
            _LOGGER.error(
                "Failed executing pause action for %s: missing location identifier tracking.",
                self.device_id,
            )
            return

        await self.client.execute_device_action(
            device_id=self.device_id,
            action_name="pauseCharging",
            payload=[],
            service_location_id=service_location_id,
        )
        await asyncio.sleep(1.5)
        await self.coordinator.async_request_refresh()


class SmappeeStopChargingButton(SmappeeBaseEntity, ButtonEntity):
    """Permanent cleanup boundary to terminate active energy transactions completely."""

    _attr_translation_key = "stop_charging_button"
    _attr_icon = "mdi:stop-circle"

    def __init__(self, coordinator, client, entry_title, device_id: str) -> None:
        """Initialize the Smappee stop charging button entity."""
        super().__init__(
            coordinator,
            client,
            entry_title,
            device_id=device_id,
            device_type="charger",
            platform_domain="button",
        )

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this button entity."""
        return f"{self.device_id}_stop_charging_button"

    async def async_press(self) -> None:
        """Execute the stopCharging device action endpoint task."""
        _LOGGER.info(
            "Triggered charging session termination instruction for charger: %s",
            self.device_id,
        )

        service_location_id = (
            self.smart_device_data.get("serviceLocation")
            if self.smart_device_data
            else None
        )
        if not service_location_id:
            _LOGGER.error(
                "Failed executing stop action for %s: missing location identifier tracking.",
                self.device_id,
            )
            return

        await self.client.execute_device_action(
            device_id=self.device_id,
            action_name="stopCharging",
            payload=[],
            service_location_id=service_location_id,
        )
        await asyncio.sleep(1.5)
        await self.coordinator.async_request_refresh()


class SmappeeNormalChargingModeButton(SmappeeBaseEntity, ButtonEntity):
    """Switch the load configuration behavior directly over to standard charging rules."""

    _attr_translation_key = "normal_charging_mode_button"
    _attr_icon = "mdi:lightning-bolt"

    def __init__(self, coordinator, client, entry_title, device_id: str) -> None:
        """Initialize the Smappee normal charging mode button entity."""
        super().__init__(
            coordinator,
            client,
            entry_title,
            device_id=device_id,
            device_type="charger",
            platform_domain="button",
        )

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this button entity."""
        return f"{self.device_id}_normal_charging_mode_button"

    async def async_press(self) -> None:
        """Change station operational parameters back to maximum output standard delivery."""
        service_location_id = (
            self.smart_device_data.get("serviceLocation")
            if self.smart_device_data
            else None
        )
        if not service_location_id:
            _LOGGER.error(
                "Failed executing selection modification for %s: missing location identifier tracking.",
                self.device_id,
            )
            return

        success = await self.client.set_charging_mode(
            service_location_id, self.device_id, "STANDARD"
        )
        if success:
            await self.coordinator.async_request_refresh()


class SmappeeSmartChargingModeButton(SmappeeBaseEntity, ButtonEntity):
    """Switch the load configuration behavior directly over to intelligent grid balancing rules."""

    _attr_translation_key = "smart_charging_mode_button"
    _attr_icon = "mdi:brain"

    def __init__(self, coordinator, client, entry_title, device_id: str) -> None:
        """Initialize the Smappee smart charging mode button entity."""
        super().__init__(
            coordinator,
            client,
            entry_title,
            device_id=device_id,
            device_type="charger",
            platform_domain="button",
        )

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this button entity."""
        return f"{self.device_id}_smart_charging_mode_button"

    async def async_press(self) -> None:
        """Change station operational parameters back to managed dynamic balancing tracking."""
        service_location_id = (
            self.smart_device_data.get("serviceLocation")
            if self.smart_device_data
            else None
        )
        if not service_location_id:
            _LOGGER.error(
                "Failed executing selection modification for %s: missing location identifier tracking.",
                self.device_id,
            )
            return

        success = await self.client.set_charging_mode(
            service_location_id, self.device_id, "SMART"
        )
        if success:
            await self.coordinator.async_request_refresh()
