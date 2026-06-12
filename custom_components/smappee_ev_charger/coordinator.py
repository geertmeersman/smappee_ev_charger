"""DataUpdateCoordinator for the Smappee Charger integration."""

from datetime import timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import SmappeeClient

_LOGGER = logging.getLogger(__name__)


class SmappeeDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Class to manage fetching Smappee Charger data from cloud endpoints."""

    def __init__(
        self, hass: HomeAssistant, client: SmappeeClient, station_id: str
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"Smappee Charger {station_id}",
            update_interval=timedelta(hours=1),
        )
        self.client = client
        self.power_mapping: dict[str, Any] = {}
        self.parent_location_id: str | None = None
        self.high_level_configs: dict[str, Any] = (
            {}
        )  # Cache cache for multi-location MQTT tokens

        # Timer context for charging cycles
        self.timer_context: dict[str, Any] = {
            "session_interval_unsub": None,
            "was_charging": False,
            "handler": self._handle_charging_session_timers,
        }

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch infrastructure registries and session states from the Smappee cloud API."""
        try:
            # 1. Fetch service location details to secure the core identifier context
            servicelocations = await self.client.get_service_locations_full_details()

            # 2. Build or verify the array coordinate maps using the V10 endpoint mapping tables
            if servicelocations and not self.power_mapping:
                if not self.client.service_location_id:
                    self.client.service_location_id = servicelocations[0].get("id")

                try:
                    _LOGGER.debug(
                        "Fetching Smappee v10 high-level tracking configuration schemas..."
                    )
                    # Force the client onto the correct base ID from the main registry call first
                    self.client.service_location_id = servicelocations[0].get("id")

                    # Correctly called with await since it is an asynchronous coroutine
                    high_level_cfg = await self.client.get_high_level_configuration()

                    if high_level_cfg:
                        self.power_mapping = self._parse_high_level_configuration(
                            high_level_cfg
                        )
                        _LOGGER.info(
                            "Smappee array offset map successfully initialized: %s",
                            self.power_mapping,
                        )
                except Exception as err:
                    _LOGGER.error(
                        "Failed building coordinate matrix array masks from high-level schemas: %s",
                        err,
                    )

            all_smart_devices: list[dict[str, Any]] = []
            charging_station_details: dict[str, Any] = {}

            for loc in servicelocations:
                charging_station_obj = loc.get("chargingStation")
                raw_serial = (
                    charging_station_obj.get("serialNumber")
                    if isinstance(charging_station_obj, dict)
                    else None
                )

                if raw_serial is not None:
                    serial_str = str(raw_serial).strip()
                    _LOGGER.debug(
                        "Valid charging station serial discovered: %s. Querying metrics...",
                        serial_str,
                    )

                    self.client.charging_station_serial = serial_str
                    station_data = await self.client.get_charging_station_details(
                        serial_str
                    )

                    if station_data:
                        charging_station_details[serial_str] = station_data
                        modules = station_data.get("modules", [])
                        for module in modules:
                            if "smartDevice" in module:
                                smart_device = module["smartDevice"]
                                if "configurationProperties" in module:
                                    smart_device["configurationProperties"] = module[
                                        "configurationProperties"
                                    ]
                                if smart_device not in all_smart_devices:
                                    all_smart_devices.append(smart_device)

            if not all_smart_devices and servicelocations:
                _LOGGER.debug(
                    "No active smart devices discovered via modules. Triggering registry fallbacks..."
                )
                self.client.service_location_id = servicelocations[0].get("id")
                fallback_devices = await self.client.get_smart_devices()
                for device in fallback_devices:
                    if device not in all_smart_devices:
                        all_smart_devices.append(device)

            for device in all_smart_devices:
                child_loc_id = device.get("serviceLocation")
                current_loc = next(
                    (loc for loc in servicelocations if loc.get("id") == child_loc_id),
                    None,
                )
                if current_loc and current_loc.get("parentId"):
                    self.parent_location_id = str(current_loc.get("parentId"))
                    break

            # 3. MULTI-LOCATION CONFIG CACHING (With proper Asynchronous Context Switches)
            entry_loc_id = str(self.config_entry.data.get("station_id"))
            parent_loc_id = (
                str(self.parent_location_id) if self.parent_location_id else None
            )

            target_ids = {entry_loc_id}
            if parent_loc_id:
                target_ids.add(parent_loc_id)

            # Store the original ID to clean up and restore the client's context status after the loop
            original_service_location_id = self.client.service_location_id

            for loc_id in target_ids:
                try:
                    _LOGGER.debug(
                        "Switching client location context to %s before calling high-level configurations",
                        loc_id,
                    )
                    # Update the target ID internally inside the client right before executing the network tracking request
                    self.client.service_location_id = (
                        int(loc_id) if loc_id.isdigit() else loc_id
                    )

                    config_res = await self.client.get_high_level_configuration()

                    if config_res:
                        self.high_level_configs[loc_id] = config_res
                        _LOGGER.info(
                            "Successfully cached high-level configuration via client context switch for location %s",
                            loc_id,
                        )
                except Exception as err:
                    _LOGGER.error(
                        "Failed fetching highlevelconfiguration via client switch for %s: %s",
                        loc_id,
                        err,
                    )

            # Restore the original client state context for any subsequent background REST calls
            self.client.service_location_id = original_service_location_id

            recent_sessions = await self.client.get_recent_sessions()

            new_data = {
                "servicelocations": servicelocations,
                "smart_devices": all_smart_devices,
                "charging_station_details": charging_station_details,
                "recent_sessions": recent_sessions,
                "mqtt_locations": {},
            }

            if self.data and "mqtt_locations" in self.data:
                new_data["mqtt_locations"] = self.data["mqtt_locations"]

            return new_data

        except Exception as err:
            raise UpdateFailed(
                f"Network transport disruption encountered during Smappee API synchronization: {err}"
            )

    async def async_refresh_sessions_only(self) -> None:
        """Fetch updated transactional summaries outside regular polling constraints."""
        try:
            _LOGGER.debug("Executing isolated Smappee API transactional data sweep...")
            recent_sessions = await self.client.get_recent_sessions()

            if self.data:
                self.data["recent_sessions"] = recent_sessions
                self.async_set_updated_data(self.data)
                _LOGGER.info(
                    "Smappee operational transaction registers successfully stepped."
                )
        except Exception as err:
            _LOGGER.warning(
                "Isolated session synchronization tracking query dropped: %s", err
            )

    def _handle_charging_session_timers(self, is_charging: bool) -> None:
        """Manage interval update schedules based on active output load tracking cycles."""
        if is_charging and not self.timer_context["session_interval_unsub"]:
            _LOGGER.info(
                "Vehicle power absorption state verified. Registering short-cycle interval polling tasks."
            )

            async def run_periodic_session_update(_now: Any) -> None:
                await self.async_refresh_sessions_only()

            from homeassistant.helpers.event import async_track_time_interval

            self.timer_context["session_interval_unsub"] = async_track_time_interval(
                self.hass, run_periodic_session_update, timedelta(minutes=5)
            )
            self.timer_context["was_charging"] = True

        elif not is_charging and self.timer_context["was_charging"]:
            _LOGGER.info(
                "Vehicle power delivery collapsed. Unregistering short-cycle tracking handlers."
            )

            if self.timer_context["session_interval_unsub"]:
                self.timer_context["session_interval_unsub"]()
                self.timer_context["session_interval_unsub"] = None

            async def finalize_charging_session(_now: Any) -> None:
                _LOGGER.info("Executing ultimate post-session validation query.")
                await self.async_refresh_sessions_only()

            from homeassistant.helpers.event import async_call_later

            async_call_later(self.hass, 5, finalize_charging_session)
            self.timer_context["was_charging"] = False

    def initialize_startup_timers(self) -> None:
        """Evaluate current active charging status upon startup."""
        currently_charging = False
        serial = self.client.charging_station_serial

        if serial and self.data and "charging_station_details" in self.data:
            station_data = self.data["charging_station_details"].get(str(serial))
            if station_data:
                for module in station_data.get("modules", []):
                    if "carCharger" in module and module["carCharger"]:
                        status_dict = module["carCharger"].get("status", {})
                        if str(status_dict.get("current", "")).upper() == "CHARGING":
                            currently_charging = True

        if not currently_charging and self.data and "smart_devices" in self.data:
            for device in self.data["smart_devices"]:
                if device.get("type", {}).get("category") == "CARCHARGER":
                    cc_data = device.get("carCharger", {})
                    status_dict = cc_data.get("status", {}) if cc_data else {}
                    flat_state = str(
                        status_dict.get("current", device.get("chargingState", ""))
                    ).upper()
                    if (
                        not cc_data
                        and "status" in device
                        and isinstance(device["status"], dict)
                    ):
                        flat_state = str(device["status"].get("current", "")).upper()

                    if flat_state == "CHARGING":
                        currently_charging = True

        if currently_charging:
            _LOGGER.info(
                "Smappee infrastructure registered as actively charging during integration initialization."
            )
            self._handle_charging_session_timers(True)

    def _parse_high_level_configuration(self, cfg: dict) -> dict[str, Any]:
        mapping: dict[str, Any] = {
            "grid": {
                "energy": [],
                "array_key": "importActiveEnergyData",
            },
            "pv": {"energy": [], "array_key": "importActiveEnergyData"},
            "cars": {},
        }

        measurements = cfg.get("measurements") or []
        for meas in measurements:
            mtype = str(meas.get("type", "")).upper()
            channels = meas.get("updateChannels", {})

            # Extract the raw indices using meterReadings if available
            reading_source = (
                channels.get("meterReadings") or channels.get("activePower") or {}
            )
            aspect_paths = reading_source.get("aspectPaths") or []

            indices = []
            array_source = "importActiveEnergyData"

            for path_obj in aspect_paths:
                path_str = path_obj.get("path", "")
                if "importActiveEnergyData" in path_str:
                    array_source = "importActiveEnergyData"
                    break
                elif "channelData" in path_str:
                    array_source = "channelData"
                elif "activePowerData" in path_str:
                    array_source = "activePowerData"

            # Parse indices
            for path_obj in aspect_paths:
                path_str = path_obj.get("path", "")
                if "[" in path_str and "]" in path_str:
                    try:
                        idx_str = path_str.split("[")[-1].split("]")[0]
                        indices.append(int(idx_str))
                    except (ValueError, IndexError):
                        pass

            clean_indices = list(dict.fromkeys(indices))

            if mtype == "GRID":
                mapping["grid"]["energy"] = clean_indices
                mapping["grid"]["array_key"] = array_source
            elif mtype == "PRODUCTION":
                mapping["pv"]["energy"] = clean_indices
                mapping["pv"]["array_key"] = array_source
            elif (
                mtype == "APPLIANCE"
                and meas.get("appliance", {}).get("type") == "CAR_CHARGER"
            ):
                if not clean_indices:
                    clean_indices = [0, 1, 2]
                meas_id = str(meas.get("id", "charger"))
                mapping["cars"][meas_id] = {
                    "energy": clean_indices,
                    "array_key": array_source,
                }

        # Fallback
        if not mapping["grid"]["energy"]:
            mapping["grid"]["energy"] = [0, 1, 2]
            mapping["grid"]["array_key"] = "importActiveEnergyData"

        return mapping
