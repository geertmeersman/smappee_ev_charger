"""Set up and manage Smappee Charger device tracker entities."""

import logging

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .sensor import SmappeeBaseEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Smappee device tracker entities dynamically based on discovered devices."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    client = entry_data["client"]
    coordinator = entry_data["coordinator"]

    entities = []

    if coordinator.data and "smart_devices" in coordinator.data:
        for device in coordinator.data["smart_devices"]:
            category = device.get("type", {}).get("category")
            device_id = device.get("id")

            # Create location tracking entity exclusively for CARCHARGER devices
            if category == "CARCHARGER" and device_id:
                _LOGGER.debug(
                    "Dynamically creating device tracker entity for Smappee charger: %s",
                    device_id,
                )
                entities.append(
                    SmappeeChargerLocationTracker(
                        coordinator, client, entry.title, device_id
                    )
                )

    if entities:
        async_add_entities(entities)


class SmappeeChargerLocationTracker(SmappeeBaseEntity, TrackerEntity):
    """Track the static geographical location coordinates of the charging station."""

    _attr_translation_key = "charger_location_tracker"

    def __init__(self, coordinator, client, entry_title, device_id: str) -> None:
        """Initialize the Smappee charger location tracker entity."""
        super().__init__(
            coordinator,
            client,
            entry_title,
            device_id=device_id,
            device_type="charger",
            platform_domain="device_tracker",
        )

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this device tracker entity."""
        return f"{self.device_id}_location_tracker"

    @property
    def source_type(self) -> SourceType:
        """Return the source type flagging static GPS tracking coordinates."""
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        """Extract the latitude coordinate dynamically from service location metadata registries."""
        if self.coordinator.data and "servicelocations" in self.coordinator.data:
            for loc in self.coordinator.data["servicelocations"]:
                lat = loc.get("latitude")
                if lat is not None:
                    return float(lat)
        return None

    @property
    def longitude(self) -> float | None:
        """Extract the longitude coordinate dynamically from service location metadata registries."""
        if self.coordinator.data and "servicelocations" in self.coordinator.data:
            for loc in self.coordinator.data["servicelocations"]:
                lon = loc.get("longitude")
                if lon is not None:
                    return float(lon)
        return None

    @property
    def icon(self) -> str:
        """Return the geographical map positioning boundary indicator icon symbol."""
        return "mdi:map-marker-radius"
