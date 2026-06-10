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
    """Stel de Smappee device tracker entiteiten dynamisch in."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    client = entry_data["client"]
    coordinator = entry_data["coordinator"]

    entities = []

    if coordinator.data and "smart_devices" in coordinator.data:
        for device in coordinator.data["smart_devices"]:
            category = device.get("type", {}).get("category")
            device_id = device.get("id")

            # Maak de tracker specifiek aan voor de CARCHARGER
            if category == "CARCHARGER" and device_id:
                _LOGGER.debug("Dynamische device tracker aanmaken voor Smappee lader: %s", device_id)
                entities.append(
                    SmappeeChargerLocationTracker(coordinator, client, entry.title, device_id)
                )

    if entities:
        async_add_entities(entities)


class SmappeeChargerLocationTracker(SmappeeBaseEntity, TrackerEntity):
    """Device tracker die de vaste geografische locatie van het laadstation weergeeft."""

    _attr_translation_key = "charger_location_tracker"

    def __init__(self, coordinator, client, entry_title, device_id):
        super().__init__(coordinator, client, entry_title, device_id=device_id, device_type="charger", platform_domain="device_tracker")

    @property
    def unique_id(self):
        return f"{self.device_id}_location_tracker"

    @property
    def source_type(self) -> SourceType:
        """Geef aan dat dit een vaste GPS-locatie betreft."""
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        """Haal de breedtegraad dynamic uit de servicelocation data."""
        if self.coordinator.data and "servicelocations" in self.coordinator.data:
            for loc in self.coordinator.data["servicelocations"]:
                # Zoek naar de locatie die de GPS coördinaten bevat
                lat = loc.get("latitude")
                if lat is not None:
                    return float(lat)
        return None

    @property
    def longitude(self) -> float | None:
        """Haal de lengtegraad dynamic uit de servicelocation data."""
        if self.coordinator.data and "servicelocations" in self.coordinator.data:
            for loc in self.coordinator.data["servicelocations"]:
                lon = loc.get("longitude")
                if lon is not None:
                    return float(lon)
        return None

    @property
    def icon(self):
        return "mdi:map-marker-radius"
