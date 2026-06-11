"""Client to interface with the Smappee Cloud REST and WebSocket APIs."""

import logging
import time
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

DASHAPI_URL = "https://dashboard.smappee.net/dashapi"
API_BASE_URL = "https://dashboard.smappee.net/api"

DEFAULT_API_VERSION = "v10"
SERVICELOCATIONS_API_VERSION = "v11"


class SmappeeClient:
    """Client backend wrapper managing network transport loops toward Smappee endpoints."""

    def __init__(
        self, username: str, password: str, session: aiohttp.ClientSession
    ) -> None:
        """Initialize the Smappee API client instance."""
        self.username = username
        self.password = password
        self.session = session

        self.token: str | None = None
        self.token_expires_at: int = 0
        self.user_id: str | None = None

        self.charging_location_id: str | None = None
        self.service_location_id: str | None = None
        self.charging_station_serial: str | None = None
        self.rfid_device_id: str | None = None

    async def authenticate(self) -> bool:
        """Authenticate session credentials against the Smappee dashboard portal login gateway."""
        current_time_ms = int(time.time() * 1000)
        if self.token and self.token_expires_at > (current_time_ms + 30000):
            return True

        url = f"{DASHAPI_URL}/login"
        payload = {"userName": self.username, "password": self.password}
        headers = {"content-type": "application/json"}

        try:
            async with self.session.post(
                url, json=payload, headers=headers
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    self.token = data.get("token")
                    self.token_expires_at = data.get("tokenExpirationTimestamp", 0)
                    self.user_id = data.get("userId")
                    return True
                return False
        except Exception as err:
            _LOGGER.error(
                "Failed to authenticate session credentials against gateway: %s", err
            )
            return False

    async def get_headers(self) -> dict[str, str]:
        """Generate and append active token signatures into HTTP connection header arrays."""
        await self.authenticate()
        return {"token": str(self.token), "content-type": "application/json"}

    async def fetch_charging_station_info(self) -> bool:
        """Isolate active functional charging station nodes out of user service location arrays."""
        headers = await self.get_headers()
        if not self.token:
            return False

        url = f"{API_BASE_URL}/{SERVICELOCATIONS_API_VERSION}/user/servicelocations?fullDetails=true"

        try:
            async with self.session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    if isinstance(data, list):
                        station = next(
                            (
                                item
                                for item in data
                                if item.get("functionType") == "CHARGINGSTATION"
                            ),
                            None,
                        )
                        if station and "chargingStation" in station:
                            self.charging_station_serial = station[
                                "chargingStation"
                            ].get("serialNumber")
                            self.charging_location_id = station.get("id")
                            self.service_location_id = station.get("id")
                            return True
                return False
        except Exception as err:
            _LOGGER.error(
                "Failed to fetch service location infrastructure records: %s", err
            )
            return False

    async def get_charging_station_details(
        self, serial_number: str
    ) -> dict[str, Any] | None:
        """Fetch extensive operational modules and nested telemetry maps for a given station serial."""
        headers = await self.get_headers()
        if not self.token or not serial_number:
            return None

        url = f"{API_BASE_URL}/{DEFAULT_API_VERSION}/chargingstations/{serial_number}?includeDetails=true"

        try:
            _LOGGER.debug(
                "Requesting extensive v10 station configuration records via: %s", url
            )
            async with self.session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    modules = data.get("modules", [])
                    rfid_module = next(
                        (
                            m
                            for m in modules
                            if m.get("type") == "AC_CAR_CHARGE_CONTROLLER_RFID"
                        ),
                        None,
                    )
                    if rfid_module and "smartDevice" in rfid_module:
                        self.rfid_device_id = rfid_module["smartDevice"].get("id")
                    return data

                _LOGGER.error(
                    "Failed to fetch hardware detail metrics. HTTP Status: %s",
                    response.status,
                )
                return None
        except Exception as err:
            _LOGGER.error(
                "Exception occurred while retrieving module configurations for serial %s: %s",
                serial_number,
                err,
            )
            return None

    async def set_charging_mode(
        self, service_location_id: str | int, device_id: str, mode: str
    ) -> bool:
        """Modify load management profile configurations targeting standard or balanced balancing matrices."""
        option = mode.upper()
        payload = [
            {
                "spec": {
                    "name": "mode",
                    "species": "String",
                    "required": True,
                    "possibleValues": {
                        "values": [
                            {"String": "STANDARD"},
                            {"String": "SMART"},
                            {"String": "SOLAR"},
                        ],
                        "exhaustive": True,
                    },
                },
                "values": [{"String": option}],
            }
        ]

        success = await self.execute_device_action(
            device_id, "setChargingMode", payload, service_location_id
        )
        if not success:
            return False
        if option == "SOLAR":
            success = await self.update_configuration_property(
                device_id=device_id,
                property_name="etc.smart.device.type.car.charger.config.min.excesspct",
                value_dict={"Integer": 100},
                service_location_id=service_location_id,
            )
        return success

    async def get_recent_sessions(self) -> list[dict[str, Any]]:
        """Fetch historical tracking summaries logging transactions finalized across the past week."""
        if not self.charging_station_serial:
            await self.fetch_charging_station_info()

        headers = await self.get_headers()
        if not self.token or not self.charging_station_serial:
            return []

        now_ms = int(time.time() * 1000)
        one_week_ago_ms = now_ms - (7 * 24 * 60 * 60 * 1000)

        url = f"{API_BASE_URL}/{DEFAULT_API_VERSION}/chargingstations/{self.charging_station_serial}/sessions"
        params = {"range": f"{one_week_ago_ms},{now_ms}", "rangeMode": "stop_or_start"}

        try:
            async with self.session.get(
                url, headers=headers, params=params
            ) as response:
                if response.status == 200:
                    return await response.json()
                return []
        except Exception as err:
            _LOGGER.error(
                "Failed to retrieve chronological telemetry transaction logs: %s", err
            )
            return []

    def get_mqtt_config(
        self, station_details: dict[str, Any] = None
    ) -> dict[str, Any] | None:
        """Extract credentials and explicit live state telemetry channels from master cluster dictionaries."""
        if not station_details or not isinstance(station_details, dict):
            _LOGGER.error(
                "Aborted broker extraction pipeline: invalid or empty station profile footprint."
            )
            return None

        car_charger_data = None

        for _, station in station_details.items():
            modules = station.get("modules", [])
            for module in modules:
                if module.get("type") == "AC_CAR_CHARGE_CONTROLLER_RFID":
                    smart_device = module.get("smartDevice", {})
                    if "carCharger" in smart_device:
                        car_charger_data = smart_device["carCharger"]
                        break
            if car_charger_data:
                break

        if not car_charger_data:
            _LOGGER.error(
                "Aborted broker extraction pipeline: target module carCharger block is empty."
            )
            return None

        power_channel = car_charger_data.get("powerUpdateChannel", {})
        state_channel = car_charger_data.get("connectionStatusUpdateChannel", {})
        charging_state_channel = car_charger_data.get("chargingStateUpdateChannel", {})

        username = power_channel.get("userName")
        password = power_channel.get("password")

        if not username or not password:
            _LOGGER.error(
                "Aborted broker extraction pipeline: security profile credential maps are incomplete."
            )
            return None

        return {
            "host": "dashboard.smappee.net",
            "port": 443,
            "username": username,
            "password": password,
            "power_topic": power_channel.get("name"),
            "state_topic": state_channel.get("name") if state_channel else None,
            "charging_state_topic": (
                charging_state_channel.get("name") if charging_state_channel else None
            ),
        }

    async def set_installation_configuration(self, payload: dict[str, Any]) -> bool:
        """Overwrite overall physical building electrical balancing thresholds using a PUT network instruction."""
        if not self.charging_station_serial:
            await self.fetch_charging_station_info()

        headers = await self.get_headers()
        if not self.token or not self.charging_station_serial:
            return False

        url = f"{API_BASE_URL}/{SERVICELOCATIONS_API_VERSION}/chargingstations/{self.charging_station_serial}/installationconfiguration"

        try:
            async with self.session.put(url, headers=headers, json=payload) as response:
                if response.status in [200, 204]:
                    _LOGGER.info(
                        "Successfully synchronized raw site physical load capacity bounds."
                    )
                    return True
                _LOGGER.error(
                    "API rejected structural installation adjustments. Status: %s",
                    response.status,
                )
                return False
        except Exception as err:
            _LOGGER.error(
                "Exception occurred during structural setup configuration overrides: %s",
                err,
            )
            return False

    async def get_service_locations_full_details(self) -> list[dict[str, Any]]:
        """Fetch comprehensive profiles tracking metadata assignments across all service fields linked to profiles."""
        headers = await self.get_headers()
        url = f"{API_BASE_URL}/{SERVICELOCATIONS_API_VERSION}/user/servicelocations?fullDetails=true"

        async with self.session.get(url, headers=headers) as response:
            if response.status == 200:
                return await response.json()
            raise RuntimeError(
                f"API connection failed to yield location metrics. Status: {response.status}"
            )

    async def get_smart_devices(self) -> list[dict[str, Any]]:
        """Fetch all individual device modules mapped across the active location cluster registry."""
        headers = await self.get_headers()
        url = f"{API_BASE_URL}/{DEFAULT_API_VERSION}/servicelocation/{self.service_location_id}/homecontrol/smart/devices?excludedCategories="

        try:
            async with self.session.get(url, headers=headers) as response:
                if response.status == 200:
                    return await response.json()
                _LOGGER.error(
                    "Failed to query localized smart device registry tables. Status: %s",
                    response.status,
                )
                return []
        except Exception as err:
            _LOGGER.error(
                "Exception occurred while retrieving connected homecontrol endpoints: %s",
                err,
            )
            return []

    async def set_charger_availability(self, device_id: str, available: bool) -> bool:
        """Modify general online accessibility flags via structural station validation adjustments."""
        headers = await self.get_headers()
        if not self.charging_station_serial:
            _LOGGER.error(
                "Aborted accessibility processing loop: client missing active station serial parameters."
            )
            return False

        url = f"{API_BASE_URL}/{SERVICELOCATIONS_API_VERSION}/chargingstations/{self.charging_station_serial}"
        payload = {"available": available}

        try:
            async with self.session.patch(
                url, headers=headers, json=payload
            ) as response:
                if response.status in (200, 201, 204):
                    _LOGGER.info(
                        "Successfully toggled infrastructure availability flags to: %s",
                        available,
                    )
                    return True
                return False
        except Exception as err:
            _LOGGER.error(
                "Failed adjusting system visibility states for tracking identity %s: %s",
                device_id,
                err,
            )
            return False

    async def set_offline_charging_config(
        self, enabled: bool, failsafe_amps: int
    ) -> bool:
        """Overwrite offline processing limits used during local connection drop dropouts."""
        headers = await self.get_headers()
        url = f"{API_BASE_URL}/{SERVICELOCATIONS_API_VERSION}/chargingstations/{self.charging_station_serial}"
        payload = {"offlineCharging": {"enabled": enabled, "failSafe": failsafe_amps}}
        _LOGGER.critical(payload)

        try:
            async with self.session.patch(
                url, headers=headers, json=payload
            ) as response:
                return response.status in (200, 201, 204)
        except Exception as err:
            _LOGGER.error(
                "Failed negotiating local network dropout failsafe specifications: %s",
                err,
            )
            return False

    async def execute_device_action(
        self,
        device_id: str,
        action_name: str,
        payload: list[dict[str, Any]],
        service_location_id: str | int | None = None,
    ) -> bool:
        """Execute an instantaneous action request toward a specific target device endpoint block."""
        headers = await self.get_headers()
        loc_id = service_location_id or self.service_location_id

        if not loc_id:
            _LOGGER.error(
                "Aborted action execution '%s': no valid service location ID available.",
                action_name,
            )
            return False

        url = (
            f"{API_BASE_URL}/{DEFAULT_API_VERSION}/servicelocation/{loc_id}"
            f"/homecontrol/smart/devices/{device_id}/actions/{action_name}"
        )

        try:
            _LOGGER.debug(
                "Dispatching operational action payload toward %s: %s", url, payload
            )
            async with self.session.post(
                url, json=payload, headers=headers
            ) as response:
                if response.status in (200, 201, 204):
                    _LOGGER.info(
                        "Successfully executed action payload rule '%s' for device %s",
                        action_name,
                        device_id,
                    )
                    return True

                raw_text = await response.text()
                _LOGGER.error(
                    "API rejected dynamic instruction task '%s' (%s): %s",
                    action_name,
                    response.status,
                    raw_text,
                )
                return False
        except Exception as err:
            _LOGGER.error(
                "Exception occurred during task loop processing for execution '%s': %s",
                action_name,
                err,
            )
            return False

    async def update_configuration_property(
        self,
        device_id: str,
        property_name: str,
        value_dict: dict[str, Any],
        service_location_id: str | int | None = None,
    ) -> bool:
        """Update a persistent hardware profile parameter configuration property inside database registries."""
        headers = await self.get_headers()
        loc_id = service_location_id or self.service_location_id

        if not loc_id:
            _LOGGER.error(
                "Aborted configuration update '%s': no valid service location ID available.",
                property_name,
            )
            return False

        url = (
            f"{API_BASE_URL}/{DEFAULT_API_VERSION}/servicelocation/{loc_id}"
            f"/homecontrol/smart/devices/{device_id}/config"
        )
        payload = [{"spec": {"name": property_name}, "values": [value_dict]}]

        _LOGGER.critical(payload)
        _LOGGER.critical(url)

        try:
            _LOGGER.debug(
                "Dispatching static config block updates toward %s: %s", url, payload
            )
            async with self.session.put(url, json=payload, headers=headers) as response:
                if response.status in (200, 201, 204):
                    _LOGGER.info(
                        "Successfully updated database profile registry property '%s' for device %s",
                        property_name,
                        device_id,
                    )
                    return True
                return False
        except Exception as err:
            _LOGGER.error(
                "Exception occurred during database property synchronization for attribute '%s': %s",
                property_name,
                err,
            )
            return False

    async def get_high_level_configuration(self) -> dict[str, Any]:
        """Fetch the explicit high-level configuration schema layout mapping parameters for raw data arrays."""
        headers = await self.get_headers()
        url = f"{API_BASE_URL}/{DEFAULT_API_VERSION}/servicelocation/{self.service_location_id}/highlevelconfiguration"

        try:
            async with self.session.get(url, headers=headers) as response:
                if response.status == 200:
                    return await response.json()
                _LOGGER.error(
                    "Failed to query localized high-level tracking configuration blocks. Status: %s",
                    response.status,
                )
                return {}
        except Exception as err:
            _LOGGER.error(
                "Exception occurred while retrieving layout blueprint mapping constraints: %s",
                err,
            )
            return {}
