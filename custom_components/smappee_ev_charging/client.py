import aiohttp
import logging
import time

_LOGGER = logging.getLogger(__name__)

# De API basissen
DASHAPI_URL = "https://dashboard.smappee.net/dashapi"
API_BASE_URL = "https://dashboard.smappee.net/api"

# Definieer hier de versies per endpoint-groep
DEFAULT_API_VERSION = "v10"
SERVICELOCATIONS_API_VERSION = "v11"

class SmappeeClient:
    """Client om te communiceren met de Smappee API voor laadpalen."""

    def __init__(self, username, password, session: aiohttp.ClientSession):
        self.username = username
        self.password = password
        self.session = session
        
        self.token = None
        self.token_expires_at = 0
        self.user_id = None
        
        self.charging_location_id = None
        self.charging_station_serial = None
        self.rfid_device_id = None

    async def authenticate(self) -> bool:
        """Log in bij Smappee via het dashapi endpoint."""
        current_time_ms = int(time.time() * 1000)
        if self.token and self.token_expires_at > (current_time_ms + 30000):
            return True

        url = f"{DASHAPI_URL}/login"
        payload = {"userName": self.username, "password": self.password}
        headers = {"content-type": "application/json"}

        try:
            async with self.session.post(url, json=payload, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    self.token = data.get("token")
                    self.token_expires_at = data.get("tokenExpirationTimestamp", 0)
                    self.user_id = data.get("userId")
                    return True
                return False
        except Exception as e:
            _LOGGER.error("Fout tijdens Smappee login: %s", e)
            return False

    async def get_headers(self) -> dict:
        """Genereer de benodigde headers met het token."""
        await self.authenticate()
        return {
            "token": self.token,
            "content-type": "application/json"
        }

    async def fetch_charging_station_info(self) -> bool:
        """Stap 1: Zoek de CHARGINGSTATION (v11)."""
        headers = await self.get_headers()
        if not self.token:
            return False

        # Gebruikt direct de servicelocations definitie (v11)
        url = f"{API_BASE_URL}/{SERVICELOCATIONS_API_VERSION}/user/servicelocations?fullDetails=true"
        
        try:
            async with self.session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    if isinstance(data, list):
                        station = next((item for item in data if item.get("functionType") == "CHARGINGSTATION"), None)
                        if station and "chargingStation" in station:
                            self.charging_station_serial = station["chargingStation"].get("serialNumber")
                            self.charging_location_id = station.get("id")
                            self.service_location_id = station.get("id")
                            return True
                return False
        except Exception as e:
            _LOGGER.error("Fout bij ophalen servicelocations: %s", e)
            return False

    async def get_charging_station_details(self, serial_number: str) -> dict | None:
        """Haal details op voor een specifiek laadstation-serienummer (v10)."""
        headers = await self.get_headers()
        if not self.token or not serial_number:
            return None

        # Geen hardcoded serienummers; we gebruiken direct de dynamic string van de coordinator
        url = f"{API_BASE_URL}/{DEFAULT_API_VERSION}/chargingstations/{serial_number}?includeDetails=true"

        try:
            _LOGGER.debug("Smappee v10 laadstation details opvragen via URL: %s", url)
            async with self.session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    modules = data.get("modules", [])
                    rfid_module = next((m for m in modules if m.get("type") == "AC_CAR_CHARGE_CONTROLLER_RFID"), None)
                    if rfid_module and "smartDevice" in rfid_module:
                        self.rfid_device_id = rfid_module["smartDevice"].get("id")
                    return data
                
                _LOGGER.error("Fout bij ophalen laadstation details. HTTP Status: %s", response.status)
                return None
        except Exception as e:
            _LOGGER.error("Uitzondering bij ophalen laadstation details voor %s: %s", serial_number, e)
            return None

    async def set_charging_mode(self, service_location_id: str | int, device_id: str, mode: str) -> bool:
        """Wijzig de laadmodus (STANDARD, SMART, SOLAR) via het v10 actie-endpoint."""
        headers = await self.get_headers()
        if not self.token:
            return False
        
        # FIXED: We gebruiken nu het dynamic en loepzuivere service_location_id uit de aanroep!
        url = f"{API_BASE_URL}/{DEFAULT_API_VERSION}/servicelocation/{service_location_id}/homecontrol/smart/devices/{device_id}/actions/setChargingMode"

        payload = [
            {
                "spec": {
                    "name": "mode",
                    "displayName": "Laadmodus",
                    "description": "Parameter die de geselecteerde laadmodus instelt",
                    "species": "String",
                    "required": True,
                    "visible": True,
                    "obfuscated": False,
                    "possibleValues": {
                        "values": [{"String": "STANDARD"}, {"String": "SMART"}, {"String": "SOLAR"}],
                        "exhaustive": True
                    }
                },
                "values": [{"String": mode.upper()}]
            }
        ]

        try:
            _LOGGER.debug("Smappee setChargingMode API call naar %s met payload: %s", url, payload)
            async with self.session.post(url, json=payload, headers=headers) as response:
                if response.status in (200, 201, 204):
                    _LOGGER.info("Laadmodus succesvol gewijzigd naar %s.", mode.upper())
                    return True
                
                raw_text = await response.text()
                _LOGGER.error("Fout bij wijzigen laadmodus. Status: %s, Response: %s", response.status, raw_text)
                return False
        except Exception as e:
            _LOGGER.error("Fout tijdens setChargingMode API call voor %s: %s", device_id, e)
            return False

    async def get_recent_sessions(self) -> list:
        """Haal de laadsessies op van de afgelopen 24 uur (Eerste in lijst = laatste/huidige sessie)."""
        if not self.charging_station_serial:
            await self.fetch_charging_station_info()

        headers = await self.get_headers()
        if not self.token or not self.charging_station_serial:
            return []

        # Bereken range: van 24 uur geleden tot nu (in milliseconden)
        now_ms = int(time.time() * 1000)
        one_day_ago_ms = now_ms - (7 * 24 * 60 * 60 * 1000)

        # CORRECTIE: api/v10 via DEFAULT_API_VERSION
        url = f"{API_BASE_URL}/{DEFAULT_API_VERSION}/chargingstations/{self.charging_station_serial}/sessions"
        params = {
            "range": f"{one_day_ago_ms},{now_ms}",
            "rangeMode": "stop_or_start"
        }

        try:
            async with self.session.get(url, headers=headers, params=params) as response:
                if response.status == 200:
                    return await response.json()
                return []
        except Exception as e:
            _LOGGER.error("Fout bij ophalen laadsessies: %s", e)
            return []

    def get_mqtt_config(self, station_details: dict = None) -> dict | None:
        """Extraheer alle MQTT-credentials en live-topics uit de rijke laadstation details."""
        if not station_details or not isinstance(station_details, dict):
            _LOGGER.error("MQTT configuratie mislukt: Geen station_details beschikbaar.")
            return None

        car_charger_data = None
        
        # Doorzoek de modules van het ontdekte laadstation
        for serial, station in station_details.items():
            modules = station.get("modules", [])
            for module in modules:
                # MATCH: We zoeken naar de RFID laadcontroller module
                if module.get("type") == "AC_CAR_CHARGE_CONTROLLER_RFID":
                    smart_device = module.get("smartDevice", {})
                    if "carCharger" in smart_device:
                        car_charger_data = smart_device["carCharger"]
                        break
            if car_charger_data:
                break

        if not car_charger_data:
            _LOGGER.error("MQTT configuratie mislukt: Geen 'carCharger' data gevonden onder AC_CAR_CHARGE_CONTROLLER_RFID.")
            return None

        # Haal de kanalen op uit de exacte JSON-locatie
        power_channel = car_charger_data.get("powerUpdateChannel", {})
        state_channel = car_charger_data.get("connectionStatusUpdateChannel", {})
        charging_state_channel = car_charger_data.get("chargingStateUpdateChannel", {})

        # Trek de inloggegevens los (Smappee gebruikt de locatie UUID als username/password)
        username = power_channel.get("userName")
        password = power_channel.get("password")

        if not username or not password:
            _LOGGER.error("MQTT configuratie mislukt: userName of password ontbreekt in carCharger kanalen.")
            return None

        # Bouw de complete configuratie-dict voor de WebSocket client in __init__.py
        return {
            "host": "dashboard.smappee.net",
            "port": 443,
            "username": username,
            "password": password,
            "power_topic": power_channel.get("name"),
            "state_topic": state_channel.get("name") if state_channel else None,
            "charging_state_topic": charging_state_channel.get("name") if charging_state_channel else None
        }

    async def set_installation_configuration(self, payload: dict) -> bool:
        """Pas de installatieconfiguratie (zoals maximale stroomlimiet) aan via een PUT call."""
        if not self.charging_station_serial:
            await self.fetch_charging_station_info()

        headers = await self.get_headers()
        if not self.token or not self.charging_station_serial:
            return False

        # Gebruik v11 zoals aangegeven in het endpoint
        url = f"{API_BASE_URL}/{SERVICELOCATIONS_API_VERSION}/chargingstations/{self.charging_station_serial}/installationconfiguration"

        try:
            async with self.session.put(url, headers=headers, json=payload) as response:
                if response.status in [200, 204]:
                    _LOGGER.info("Smappee installatieconfiguratie succesvol bijgewerkt naar %s", payload)
                    return True
                else:
                    _LOGGER.error("Fout bij bijwerken Smappee configuratie. Status: %s", response.status)
                    return False
        except Exception as e:
            _LOGGER.error("Uitzondering opgetreden bij PUT configuratie: %s", e)
            return False

    async def get_service_locations_full_details(self) -> list:
        """Haal alle servicelocaties op inclusief volledige details."""
        headers = await self.get_headers()
        url = f"{API_BASE_URL}/{SERVICELOCATIONS_API_VERSION}/user/servicelocations?fullDetails=true"
        
        async with self.session.get(url, headers=headers) as response:
            if response.status == 200:
                return await response.json()
            else:
                raise Exception(f"Fout bij ophalen servicelocations: {response.status}")            

    async def get_smart_devices(self) -> list:
        """Haal alle smart devices (station, charger, led) op voor deze locatie."""
        headers = await self.get_headers()
        url = f"{API_BASE_URL}/{DEFAULT_API_VERSION}/servicelocation/{self.service_location_id}/homecontrol/smart/devices?excludedCategories="
        
        try:
            async with self.session.get(url, headers=headers) as response:
                if response.status == 200:
                    return await response.json()
                _LOGGER.error("Fout bij ophalen smart devices. Status: %s", response.status)
                return []
        except Exception as e:
            _LOGGER.error("Uitzondering bij ophalen smart devices: %s", e)
            return []

    async def set_led_brightness(self, smart_devices: list, led_device_id: str, brightness: int) -> bool:
        """Pas de helderheid van de LED aan via het overkoepelende CHARGINGSTATION device ID."""
        headers = await self.get_headers()
        
        # 1. VIND HET JUISTE CHARGINGSTATION ID DYNAMISCH
        # We zoeken nu direct in de meegegeven lijst met apparaten uit de coordinator
        charging_station_id = None
        if smart_devices and isinstance(smart_devices, list):
            charging_station = next(
                (d for d in smart_devices if d.get("type", {}).get("category") == "CHARGINGSTATION"), 
                None
            )
            if charging_station:
                charging_station_id = charging_station.get("id")

        # Mocht hij niet gevonden worden, vallen we terug op je exact gekoppelde ID uit de eerdere log
        if not charging_station_id:
            _LOGGER.warning("CHARGINGSTATION module niet gevonden in smart_devices. Fallback naar bekende ID.")
            charging_station_id = "CHARGINGSTATION-acchargingstation-100254"

        # 2. BOUW DE KLOPPENDE URL
        url = f"{API_BASE_URL}/{DEFAULT_API_VERSION}/servicelocation/{self.service_location_id}/homecontrol/smart/devices/{charging_station_id}/actions/setBrightness"
        
        payload = [
            {
                "spec": {
                    "name": "etc.smart.device.type.car.charger.led.config.brightness",
                    "displayName": "Helderheid",
                    "description": "De helderheid van uw LED",
                    "species": "Integer",
                    "unit": "%",
                    "required": True,
                    "visible": True,
                    "obfuscated": False,
                    "possibleValues": {
                        "values": [{"Integer": 70}],
                        "range": {
                            "from": {"Integer": 0},
                            "to": {"Integer": 100}
                        },
                        "defaultValue": {"Integer": 70},
                        "exhaustive": True
                    }
                },
                "values": [{"Integer": brightness}]
            }
        ]

        try:
            _LOGGER.debug("Smappee LED helderheid aanpassen naar %s%% via POST: %s", brightness, url)
            async with self.session.post(url, headers=headers, json=payload) as response:
                if response.status in (200, 201, 204):
                    _LOGGER.info("LED helderheid succesvol aangepast naar %s%% via %s.", brightness, charging_station_id)
                    return True
                
                raw_text = await response.text()
                _LOGGER.error("Fout bij aanpassen LED helderheid. Status: %s, Response: %s", response.status, raw_text)
                return False
        except Exception as err:
            _LOGGER.error("Exception bij aanpassen LED helderheid: %s", err)
            return False

    async def set_grid_assistance_amps(self, device_id: str, amps: int) -> bool:
        """Pas de maximale grid assistance stroomsterkte aan via een PATCH call."""
        headers = await self.get_headers()
        # De PATCH call gaat rechtstreeks naar de CARCHARGER module
        url = f"{API_BASE_URL}/{DEFAULT_API_VERSION}/servicelocation/{self.service_location_id}/homecontrol/smart/devices/{device_id}"
        
        # Exact de payload structuur zoals vereist door de v10 API PATCH methode
        payload = {
            "configurationProperties": [
                {
                    "spec": {
                        "name": "etc.smart.device.type.car.charger.config.max.gridassistanceamps",
                        "displayName": "etc.smart.device.type.car.charger.config.max.gridassistanceamps",
                        "description": "etc.smart.device.type.car.charger.config.max.gridassistanceamps.description",
                        "species": "Integer",
                        "required": False,
                        "visible": True,
                        "obfuscated": False,
                        "possibleValues": {
                            "values": [],
                            "range": {
                                "from": {"Integer": 0},
                                "to": {"Integer": 6}
                            },
                            "exhaustive": True
                        },
                        "group": {
                            "name": "etc.smart.device.type.car.charger.config.current.group",
                            "displayName": "Stroom"
                        }
                    },
                    "values": [{"Integer": amps}] # <-- De nieuwe schuifbalk-waarde van HA
                }
            ]
        }

        try:
            _LOGGER.debug("Smappee Grid Assistance aanpassen naar %s A via PATCH: %s", amps, url)
            async with self.session.patch(url, headers=headers, json=payload) as response:
                if response.status in (200, 201, 204):
                    _LOGGER.info("Grid Assistance succesvol aangepast naar %s A.", amps)
                    return True
                
                raw_text = await response.text()
                _LOGGER.error("Fout bij aanpassen Grid Assistance. Status: %s, Response: %s", response.status, raw_text)
                return False
        except Exception as err:
            _LOGGER.error("Exception bij aanpassen Grid Assistance: %s", err)
            return False

    async def set_charger_availability(self, device_id: str, available: bool) -> bool:
        """Zet de beschikbaarheid van de laadpaal (true/false) via een v11 PATCH call."""
        headers = await self.get_headers()
        
        # We gebruiken het correcte, dynamic laadpaal-serienummer dat in __init__.py is gezet
        if not self.charging_station_serial:
            _LOGGER.error("Kan beschikbaarheid niet aanpassen: charging_station_serial ontbreekt op de client.")
            return False

        url = f"{API_BASE_URL}/{SERVICELOCATIONS_API_VERSION}/chargingstations/{self.charging_station_serial}"
        payload = {"available": available}

        try:
            _LOGGER.debug(
                "Smappee laadpaal beschikbaarheid voor %s (S/N: %s) aanpassen naar %s via PATCH: %s", 
                device_id, self.charging_station_serial, available, url
            )
            async with self.session.patch(url, headers=headers, json=payload) as response:
                if response.status in (200, 201, 204):
                    _LOGGER.info("Laadpaal beschikbaarheid succesvol aangepast naar %s.", available)
                    return True
                
                raw_text = await response.text()
                _LOGGER.error("Fout bij aanpassen laadpaal beschikbaarheid. Status: %s, Response: %s", response.status, raw_text)
                return False
        except Exception as err:
            _LOGGER.error("Exception bij aanpassen laadpaal beschikbaarheid voor %s: %s", device_id, err)
            return False

    async def set_offline_charging_config(self, enabled: bool, failsafe_amps: int) -> bool:
        """Pas de offline laadconfiguratie aan via een PATCH call op het v11 endpoint."""
        headers = await self.get_headers()
        url = f"{API_BASE_URL}/{SERVICELOCATIONS_API_VERSION}/chargingstations/{self.charging_station_serial}"

        payload = {
            "offlineCharging": {
                "enabled": enabled,
                "failSafe": failsafe_amps
            }
        }

        try:
            _LOGGER.debug("Smappee Offline Laden aanpassen (enabled: %s, failSafe: %s A) via PATCH: %s", enabled, failsafe_amps, url)
            async with self.session.patch(url, headers=headers, json=payload) as response:
                if response.status in (200, 201, 204):
                    _LOGGER.info("Offline laadconfiguratie succesvol aangepast via v11.")
                    return True
                
                raw_text = await response.text()
                _LOGGER.error("Fout bij aanpassen offline laden. Status: %s, Response: %s", response.status, raw_text)
                return False
        except Exception as err:
            _LOGGER.error("Exception bij aanpassen offline laadconfiguratie: %s", err)
            return False

    async def set_charge_percentage_limit(self, device_id: str, percentage: int) -> bool:
        """Stel de maximale laadsnelheid in procenten in via de v10 setPercentageLimit actie."""
        headers = await self.get_headers()
        url = f"{API_BASE_URL}/{DEFAULT_API_VERSION}/servicelocation/{self.service_location_id}/homecontrol/smart/devices/{device_id}/actions/setPercentageLimit"

        payload = [
            {
                "spec": {
                    "name": "percentageLimit",
                    "displayName": "Stroombeperkingen elektrische wagen",
                    "description": "De maximale stroom dat jouw elektrisch voertuig kan ondersteunen.",
                    "species": "Integer",
                    "unit": "%",
                    "required": True,
                    "visible": True,
                    "obfuscated": False,
                    "possibleValues": {
                        "range": {"from": {"Integer": 0}, "to": {"Integer": 100}},
                        "exhaustive": True
                    }
                },
                "values": [{"Integer": percentage}]
            }
        ]

        try:
            _LOGGER.debug("Smappee setPercentageLimit call naar %s met %s%%", url, percentage)
            async with self.session.post(url, json=payload, headers=headers) as response:
                return response.status in (200, 201, 204)
        except Exception as e:
            _LOGGER.error("Fout tijdens setPercentageLimit actie: %s", e)
            return False

    async def execute_charger_action(self, device_id: str, action_name: str) -> bool:
        """Voer een parameterloze actie uit (zoals stopCharging of pauseCharging)."""
        headers = await self.get_headers()
        url = f"{API_BASE_URL}/{DEFAULT_API_VERSION}/servicelocation/{self.service_location_id}/homecontrol/smart/devices/{device_id}/actions/{action_name}"

        # Parameterloze acties verwachten een lege JSON array [] als payload volgens de Smappee API
        payload = []

        try:
            _LOGGER.debug("Smappee actie '%s' afvuren naar: %s", action_name, url)
            async with self.session.post(url, json=payload, headers=headers) as response:
                if response.status in (200, 201, 204):
                    _LOGGER.info("Smappee actie '%s' succesvol uitgevoerd.", action_name)
                    return True
                
                raw_text = await response.text()
                _LOGGER.error("Fout bij uitvoeren actie %s. Status: %s, Response: %s", action_name, response.status, raw_text)
                return False
        except Exception as e:
            _LOGGER.error("Uitzondering bij uitvoeren van Smappee actie %s: %s", action_name, e)
            return False    

    async def set_charging_percentage_limit(self, service_location_id: str | int, device_id: str, percentage: int) -> bool:
        """Stel de maximale laadsnelheid in als een percentage (0-100%)."""
        headers = await self.get_headers()
        if not self.token:
            return False

        url = f"{API_BASE_URL}/{DEFAULT_API_VERSION}/servicelocation/{service_location_id}/homecontrol/smart/devices/{device_id}/actions/setPercentageLimit"

        payload = [
            {
                "spec": {
                    "name": "percentageLimit",
                    "displayName": "Stroombeperkingen elektrische wagen",
                    "description": "De maximale stroom dat jouw elektrisch voertuig kan ondersteunen.",
                    "species": "Integer",
                    "unit": "%",
                    "required": True,
                    "visible": True,
                    "obfuscated": False,
                    "possibleValues": {
                        "values": [],
                        "range": {
                            "from": {"Integer": 0},
                            "to": {"Integer": 100}
                        },
                        "exhaustive": True
                    }
                },
                "values": [{"Integer": int(percentage)}]
            }
        ]

        try:
            _LOGGER.debug("Smappee setPercentageLimit API call naar %s met waarde: %s%%", url, percentage)
            async with self.session.post(url, json=payload, headers=headers) as response:
                if response.status in (200, 201, 204):
                    _LOGGER.info("Laadsnelheid percentage succesvol ingesteld op %s%% voor %s.", percentage, device_id)
                    return True
                
                raw_text = await response.text()
                _LOGGER.error("Fout bij instellen laadpercentage. Status: %s, Response: %s", response.status, raw_text)
                return False
        except Exception as e:
            _LOGGER.error("Fout tijdens setPercentageLimit API call voor %s: %s", device_id, e)
            return False

    async def set_normal_charging_mode(self, service_location_id: str | int, device_id: str) -> bool:
        """Schakel slim autoladen uit (Standaard laden) via het v10 actie-endpoint."""
        headers = await self.get_headers()
        if not self.token:
            return False

        url = f"{API_BASE_URL}/{DEFAULT_API_VERSION}/servicelocation/{service_location_id}/homecontrol/smart/devices/{device_id}/actions/normalChargingMode"

        try:
            _LOGGER.debug("Smappee normalChargingMode API call naar %s", url)
            async with self.session.post(url, json=[], headers=headers) as response:
                return response.status in (200, 201, 204)
        except Exception as e:
            _LOGGER.error("Fout tijdens normalChargingMode API call voor %s: %s", device_id, e)
            return False

    async def set_smart_charging_mode(self, service_location_id: str | int, device_id: str) -> bool:
        """Schakel slim autoladen in (Slim laden) via het v10 actie-endpoint."""
        headers = await self.get_headers()
        if not self.token:
            return False

        url = f"{API_BASE_URL}/{DEFAULT_API_VERSION}/servicelocation/{service_location_id}/homecontrol/smart/devices/{device_id}/actions/smartChargingMode"

        try:
            _LOGGER.debug("Smappee smartChargingMode API call naar %s", url)
            async with self.session.post(url, json=[], headers=headers) as response:
                return response.status in (200, 201, 204)
        except Exception as e:
            _LOGGER.error("Fout tijdens smartChargingMode API call voor %s: %s", device_id, e)
            return False

    async def execute_device_action(self, device_id: str, action_name: str, payload: list[dict]) -> bool:
        """Execute a post action (like setPercentageLimit) on a specific smart device."""
        headers = await self.get_headers()
        url = (
            f"{API_BASE_URL}/{DEFAULT_API_VERSION}/servicelocation/{self.service_location_id}"
            f"/homecontrol/smart/devices/{device_id}/actions/{action_name}"
        )

        try:
            _LOGGER.debug("Sending Smappee device action to %s with payload: %s", url, payload)
            async with self.session.post(url, json=payload, headers=headers) as response:
                if response.status in (200, 201, 204):
                    _LOGGER.info("Successfully executed action '%s' for device %s", action_name, device_id)
                    return True
                
                raw_text = await response.text()
                _LOGGER.error("Failed device action '%s' (%s): %s", action_name, response.status, raw_text)
                return False
        except Exception as err:
            _LOGGER.error("Exception occurred during device action '%s': %s", action_name, err)
            return False

    async def update_configuration_property(self, device_id: str, property_name: str, value_dict: dict) -> bool:
        """Update a hardware configuration property (like min.excesspct) on a specific smart device."""
        headers = await self.get_headers()
        # Note: If your endpoint configuration path differs slightly from actions, adjust this URL structure accordingly
        url = (
            f"{API_BASE_URL}/{DEFAULT_API_VERSION}/servicelocation/{self.service_location_id}"
            f"/homecontrol/smart/devices/{device_id}/config"
        )
        
        # Generic config update payload structure
        payload = [
            {
                "spec": {"name": property_name},
                "values": [value_dict]
            }
        ]

        try:
            _LOGGER.debug("Sending Smappee configuration update to %s: %s", url, payload)
            async with self.session.put(url, json=payload, headers=headers) as response:
                if response.status in (200, 201, 204):
                    _LOGGER.info("Successfully updated property '%s' for device %s", property_name, device_id)
                    return True
                return False
        except Exception as err:
            _LOGGER.error("Exception occurred during configuration update of '%s': %s", property_name, err)
            return False