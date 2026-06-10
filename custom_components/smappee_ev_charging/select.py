import logging
import asyncio
import json
from homeassistant.components.select import SelectEntity
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
    """Stel de Smappee select entiteiten dynamisch in op basis van ontdekte apparaten."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    client = entry_data["client"]
    coordinator = entry_data["coordinator"]

    entities = []

    if coordinator.data and "smart_devices" in coordinator.data:
        smart_devices = coordinator.data["smart_devices"]

        for device in smart_devices:
            category = device.get("type", {}).get("category")
            device_id = device.get("id")

            # Maak de dropdown-menu's aan voor apparaten van het type CARCHARGER
            if category == "CARCHARGER" and device_id:
                _LOGGER.debug("Dynamische dropdown-menu's aanmaken voor Smappee lader: %s", device_id)
                entities.extend([
                    SmappeeChargingModeSelect(coordinator, client, entry.title, device_id),
                    SmappeePhaseRotationSelect(coordinator, client, entry.title, device_id)
                ])

    if entities:
        async_add_entities(entities)


class SmappeeChargingModeSelect(SmappeeBaseEntity, SelectEntity):
    """Dropdown entiteit om de Smappee laadmodus (STANDARD, SMART, SOLAR) aan te passen."""

    _attr_translation_key = "charging_mode_select"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, client, entry_title, device_id):
        super().__init__(coordinator, client, entry_title, device_id=device_id, device_type="charger", platform_domain="select")
        
        # De ondersteunde opties uit de Smappee JSON features
        self._attr_options = ["STANDARD", "SMART", "SOLAR"]

    @property
    def unique_id(self):
        return f"{self.device_id}_charging_mode_select"

    @property
    def current_option(self) -> str | None:
        """Toon de huidige geselecteerde modus op basis van MQTT of de rijke REST details."""
        
        # 1. Prioriteit: Check de live MQTT JSON data uit de WebSocket (Loepzuiver & Real-time)
        if self.coordinator.data and "mqtt_charging_state" in self.coordinator.data:
            mqtt_payload = self.coordinator.data["mqtt_charging_state"]
            try:
                mqtt_json = json.loads(mqtt_payload)
                if isinstance(mqtt_json, dict):
                    charging_mode = str(mqtt_json.get("chargingMode", "")).upper()
                    optimization_strategy = str(mqtt_json.get("optimizationStrategy", "")).upper()

                    if charging_mode in ("NORMAL", "STANDARD"):
                        return "STANDARD"
                    if charging_mode == "SMART" and optimization_strategy == "EXCESS_ONLY":
                        return "SOLAR"
                    if charging_mode == "SMART":
                        return "SMART"
            except Exception:
                pass

        # 2. Secundair: Haal het uit de gloednieuwe charging_station_details (Rijke REST boom)
        if self.coordinator.data and "charging_station_details" in self.coordinator.data:
            # Smappee koppelt details per laadstation-serienummer (bvb. '6230010364')
            # Dit serienummer staat op de client óf we vissen het uit de base entiteit
            serial = getattr(self.client, "charging_station_serial", None)
            station_data = self.coordinator.data["charging_station_details"].get(serial) if serial else None
            
            if station_data:
                # Loop door de modules om de carCharger data te vinden
                for module in station_data.get("modules", []):
                    if "carCharger" in module and module["carCharger"]:
                        cc_data = module["carCharger"]
                        charging_mode = str(cc_data.get("chargingMode", "")).upper()
                        optimization_strategy = str(cc_data.get("optimizationStrategy", "")).upper()
                        
                        if charging_mode in ("STANDARD", "NORMAL"):
                            return "STANDARD"
                        if charging_mode == "SMART" and optimization_strategy == "EXCESS_ONLY":
                            return "SOLAR"
                        if charging_mode == "SMART":
                            return "SMART"

        # 3. Ultieme Fallback: Oude vlakke smart_devices structuur
        data = self.smart_device_data
        if data:
            charging_mode = str(data.get("chargingMode", "")).upper()
            load_management = data.get("loadManagement", {})
            optimization_strategy = str(load_management.get("optimizationStrategy", "")).upper()

            if charging_mode in ("NORMAL", "STANDARD"):
                return "STANDARD"
            if charging_mode == "SMART" and optimization_strategy == "EXCESS_ONLY":
                return "SOLAR"
            if charging_mode == "SMART":
                return "SMART"

        return None

    async def async_select_option(self, option: str) -> None:
        """Aangeroepen wanneer de gebruiker een modus kiest in de Home Assistant UI."""
        _LOGGER.debug("Laadmodus via HA dropdown voor %s gewijzigd naar: %s", self.device_id, option)
        
        # 1. Haal het serviceLocation ID dynamisch uit het apparaat-object (Geen hardcoded client status!)
        service_location_id = None
        data = self.smart_device_data
        if data:
            service_location_id = data.get("serviceLocation")

        if not service_location_id:
            _LOGGER.error("Kan laadmodus voor %s niet wijzigen: serviceLocation ID ontbreekt in apparaatdata.", self.device_id)
            return

        # 2. Stuur de geselecteerde modus EN het juiste service_location_id naar de client
        success = await self.client.set_charging_mode(service_location_id, self.device_id, option)
        
        if success:
            # Optimistic update
            if self.coordinator.data:
                serial = getattr(self.client, "charging_station_serial", None)
                api_mode = "SMART" if option in ("SMART", "SOLAR") else "STANDARD"
                api_strategy = "EXCESS_ONLY" if option == "SOLAR" else "BALANCED"

                # Update A: In de rijke charging_station_details structuur
                if serial and "charging_station_details" in self.coordinator.data:
                    station_data = self.coordinator.data["charging_station_details"].get(serial)
                    if station_data:
                        for module in station_data.get("modules", []):
                            if "carCharger" in module:
                                module["carCharger"]["chargingMode"] = api_mode
                                module["carCharger"]["optimizationStrategy"] = api_strategy

                # Update B: In de vlakke smart_devices lijst
                if "smart_devices" in self.coordinator.data:
                    for device in self.coordinator.data["smart_devices"]:
                        if device.get("id") == self.device_id:
                            device["chargingMode"] = api_mode
                            if "loadManagement" not in device:
                                device["loadManagement"] = {}
                            device["loadManagement"]["optimizationStrategy"] = api_strategy
                            break
                            
                self.coordinator.async_set_updated_data(self.coordinator.data)

            await asyncio.sleep(1.5)
            await self.coordinator.async_request_refresh()

    @property
    def icon(self):
        """Dynamisch icoon op basis van de actieve laadmodus."""
        mode = self.current_option
        if mode == "SOLAR":
            return "mdi:solar-power"
        elif mode == "SMART":
            return "mdi:brain"
        elif mode == "STANDARD":
            return "mdi:lightning-bolt"
        return "mdi:ev-station"

class SmappeePhaseRotationSelect(SmappeeBaseEntity, SelectEntity):
    """Dropdown om de exacte fysieke faserotatie van het laadstation in te stellen via v11 PUT."""

    _attr_translation_key = "phase_config_select"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, client, entry_title, device_id):
        super().__init__(coordinator, client, entry_title, device_id=device_id, device_type="charger", platform_domain="select")
        
        # Alle 6 mogelijke permutaties van de 3 fasen als duidelijke UI-opties
        self._attr_options = [
            "L1-L2-L3",  # ABC
            "L1-L3-L2",  # ACB
            "L2-L1-L3",  # BAC
            "L2-L3-L1",  # BCA
            "L3-L1-L2",  # CAB
            "L3-L2-L1"   # CBA
        ]

        # Interne mapping van HA string naar Smappee API constants
        self._mapping = {
            "L1-L2-L3": ["PHASEA", "PHASEB", "PHASEC"],
            "L1-L3-L2": ["PHASEA", "PHASEC", "PHASEB"],
            "L2-L1-L3": ["PHASEB", "PHASEA", "PHASEC"],
            "L2-L3-L1": ["PHASEB", "PHASEC", "PHASEA"],
            "L3-L1-L2": ["PHASEC", "PHASEA", "PHASEB"],
            "L3-L2-L1": ["PHASEC", "PHASEB", "PHASEA"]
        }

    @property
    def unique_id(self):
        return f"{self.device_id}_phase_rotation_select"

    @property
    def current_option(self) -> str | None:
        """Lees de actuele fasen-array uit de v11 data en match deze met de juiste UI-optie."""
        if not self.coordinator.data or "charging_station_details" not in self.coordinator.data:
            return None

        details_dict = self.coordinator.data["charging_station_details"]

        serial = getattr(self.client, "charging_station_serial", None)
        station_data = None

        if serial:
            station_data = details_dict.get(str(serial)) or details_dict.get(int(serial))

        if station_data is None and len(details_dict) > 0:
            station_data = list(details_dict.values())[0]

        if not station_data:
            return None

        if "installationConfiguration" in station_data:
            config = station_data["installationConfiguration"].get("currentlyConfigured", {})
            phases_list = config.get("phases", [])
            
            if phases_list and isinstance(phases_list, list) and len(phases_list) > 0:
                actual_phases = [str(p).strip().upper() for p in phases_list[0]]
                
                for ha_option, api_array in self._mapping.items():
                    if actual_phases == api_array:
                        return ha_option
                        
        return None

    async def async_select_option(self, option: str) -> None:
        """Aangeroepen wanneer de gebruiker een nieuwe rotatie kiest in de UI."""
        _LOGGER.debug("Faserotatie voor lader %s wordt gewijzigd naar: %s", self.device_id, option)
        
        # 1. Haal de doelfasen op via de mapping dictionary
        target_phases = self._mapping.get(option)
        if not target_phases:
            _LOGGER.error("Ongeldige fase-optie geselecteerd: %s", option)
            return

        serial = getattr(self.client, "charging_station_serial", None)
        
        # 2. Haal de actuele installatieconfiguratie op uit de coordinator
        station_data = None
        if self.coordinator.data and "charging_station_details" in self.coordinator.data and serial:
            station_data = self.coordinator.data["charging_station_details"].get(str(serial))

        if not station_data or "installationConfiguration" not in station_data:
            _LOGGER.error(
                "Kan faserotatie voor %s niet aanpassen: actuele installatieconfiguratie ontbreekt in coordinator cache.", 
                serial
            )
            return

        # Pak de op dit moment actieve configuratie-tak
        currently_configured = station_data["installationConfiguration"].get("currentlyConfigured", {})
        
        # 3. Trek de live parameters dynamic los uit de JSON
        amount_cables = currently_configured.get("amountPowerSupplyCables")
        maximum_current = currently_configured.get("maximumCurrent") # Dit is al een array van objecten, e.g. [{"value": 20, "unit": "AMPERE"}]

        # 4. Strikte validatie: als Smappee de parameters niet levert, sturen we niks op!
        if not amount_cables or not maximum_current:
            _LOGGER.error(
                "Faserotatie afgebroken. Cruciale live parameters (cables: %s, maxCurrent: %s) ontbreken in de API response.",
                amount_cables, maximum_current
            )
            return

        # 5. Bouw de payload met de loepzuivere, live data uit de laadpaal zelf
        payload = {
            "amountPowerSupplyCables": amount_cables,
            "maximumCurrent": maximum_current,
            "phases": [target_phases]
        }

        _LOGGER.debug("Versturen van dynamic installationConfiguration PUT payload: %s", payload)

        # 6. Voer de PUT call uit richting de v11 API
        success = await self.client.set_installation_configuration(payload)
        
        if success:
            # Optimistic UI update in de coordinator data structuren
            currently_configured["phases"] = [target_phases]
            self.coordinator.async_set_updated_data(self.coordinator.data)
            
            await asyncio.sleep(1.5)
            await self.coordinator.async_request_refresh()

    @property
    def icon(self):
        return "mdi:sync"