"""Set up and manage the Smappee Charger integration platforms."""

from datetime import timedelta
import json
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
import paho.mqtt.client as mqtt_paho

from .client import SmappeeClient
from .const import DOMAIN, STARTUP

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = [
    "sensor",
    "binary_sensor",
    "switch",
    "number",
    "select",
    "light",
    "button",
    "device_tracker",
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the Smappee Charger integration entry from a configuration profile."""
    _LOGGER.info(STARTUP)

    username = entry.data["username"]
    password = entry.data["password"]
    station_id = entry.data["station_id"]

    session = async_get_clientsession(hass)
    client = SmappeeClient(username, password, session)
    client.charging_station_serial = entry.data.get("serial")

    async def async_update_data() -> dict[str, Any]:
        """Fetch infrastructure registries and session states from the Smappee cloud API."""
        return await _async_fetch_topology_data(client, hass, entry)

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"Smappee Charger {station_id}",
        update_method=async_update_data,
        update_interval=timedelta(hours=1),
    )

    async def async_refresh_sessions_only() -> None:
        """Fetch updated transactional summaries outside regular polling constraints."""
        await _async_refresh_sessions_data(client, coordinator)

    coordinator.async_refresh_sessions_only = async_refresh_sessions_only

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "client": client,
    }

    # Execute first baseline refresh via REST API
    await coordinator.async_config_entry_first_refresh()

    # Set up and validate dynamic charging session update timers
    timer_context = _setup_charging_timers(hass, coordinator, client)

    # Register parent service location context inside the Device Registry
    _register_parent_location_device(hass, entry, coordinator)

    # Secure the Cloud MQTT WebSocket stream
    _setup_mqtt_stream(hass, entry, coordinator, client, timer_context)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Dismount and clean platform entity connections matching an integration entry instance."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return bool(unload_ok)


async def _async_fetch_topology_data(
    client: SmappeeClient, hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Fetch raw infrastructure maps and session states directly from cloud endpoints."""
    try:
        servicelocations = await client.get_service_locations_full_details()
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

                client.charging_station_serial = serial_str
                station_data = await client.get_charging_station_details(serial_str)

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
            client.service_location_id = servicelocations[0].get("id")
            fallback_devices = await client.get_smart_devices()
            for device in fallback_devices:
                if device not in all_smart_devices:
                    all_smart_devices.append(device)

        recent_sessions = await client.get_recent_sessions()

        new_data = {
            "servicelocations": servicelocations,
            "smart_devices": all_smart_devices,
            "charging_station_details": charging_station_details,
            "recent_sessions": recent_sessions,
        }

        if DOMAIN in hass.data and entry.entry_id in hass.data[DOMAIN]:
            existing_coordinator = hass.data[DOMAIN][entry.entry_id].get("coordinator")
            if existing_coordinator and existing_coordinator.data:
                old_data = existing_coordinator.data
                if "mqtt_charging_state" in old_data:
                    new_data["mqtt_charging_state"] = old_data["mqtt_charging_state"]
                if "mqtt_power_data" in old_data:
                    new_data["mqtt_power_data"] = old_data["mqtt_power_data"]

        return new_data

    except Exception as err:
        raise UpdateFailed(
            f"Network transport disruption encountered during Smappee API synchronization: {err}"
        )


async def _async_refresh_sessions_data(
    client: SmappeeClient, coordinator: DataUpdateCoordinator
) -> None:
    """Fetch updated transactional summaries outside regular polling constraints."""
    try:
        _LOGGER.debug("Executing isolated Smappee API transactional data sweep...")
        recent_sessions = await client.get_recent_sessions()

        if coordinator.data:
            coordinator.data["recent_sessions"] = recent_sessions
            coordinator.async_set_updated_data(coordinator.data)
            _LOGGER.info(
                "Smappee operational transaction registers successfully stepped."
            )
    except Exception as err:
        _LOGGER.warning(
            "Isolated session synchronization tracking query dropped: %s", err
        )


def _setup_charging_timers(
    hass: HomeAssistant, coordinator: DataUpdateCoordinator, client: SmappeeClient
) -> dict[str, Any]:
    """Evaluate and set up dynamic polling schedules matching vehicle charging states."""
    timer_context: dict[str, Any] = {
        "session_interval_unsub": None,
        "was_charging": False,
    }

    def handle_charging_session_timers(is_charging: bool) -> None:
        """Manage interval update schedules based on active output load tracking cycles."""
        if is_charging and not timer_context["session_interval_unsub"]:
            _LOGGER.info(
                "Vehicle power absorption state verified. Registering short-cycle interval polling tasks."
            )

            async def run_periodic_session_update(_now: Any) -> None:
                await coordinator.async_refresh_sessions_only()

            from homeassistant.helpers.event import async_track_time_interval

            timer_context["session_interval_unsub"] = async_track_time_interval(
                hass, run_periodic_session_update, timedelta(minutes=5)
            )
            timer_context["was_charging"] = True

        elif not is_charging and timer_context["was_charging"]:
            _LOGGER.info(
                "Vehicle power delivery collapsed. Unregistering short-cycle tracking handlers."
            )

            if timer_context["session_interval_unsub"]:
                timer_context["session_interval_unsub"]()
                timer_context["session_interval_unsub"] = None

            async def finalize_charging_session(_now: Any) -> None:
                _LOGGER.info("Executing ultimate post-session validation query.")
                await coordinator.async_refresh_sessions_only()

            from homeassistant.helpers.event import async_call_later

            async_call_later(hass, 5, finalize_charging_session)
            timer_context["was_charging"] = False

    timer_context["handler"] = handle_charging_session_timers

    # Evaluate current active charging status upon startup
    _LOGGER.debug(
        "Evaluating initial power delivery boundaries upon instance startup..."
    )
    currently_charging = False
    serial = client.charging_station_serial

    if serial and coordinator.data and "charging_station_details" in coordinator.data:
        station_data = coordinator.data["charging_station_details"].get(str(serial))
        if station_data:
            for module in station_data.get("modules", []):
                if "carCharger" in module and module["carCharger"]:
                    status_dict = module["carCharger"].get("status", {})
                    if str(status_dict.get("current", "")).upper() == "CHARGING":
                        currently_charging = True

    if (
        not currently_charging
        and coordinator.data
        and "smart_devices" in coordinator.data
    ):
        for device in coordinator.data["smart_devices"]:
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
        handle_charging_session_timers(True)

    return timer_context


def _register_parent_location_device(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: DataUpdateCoordinator
) -> None:
    """Register the main location parent context inside the Home Assistant Device Registry."""
    if not coordinator.data:
        return

    servicelocations = coordinator.data.get("servicelocations", [])
    smart_devices = coordinator.data.get("smart_devices", [])

    parent_loc_id = None
    for device in smart_devices:
        child_loc_id = device.get("serviceLocation")
        current_loc = next(
            (loc for loc in servicelocations if loc.get("id") == child_loc_id), None
        )
        if current_loc and current_loc.get("parentId"):
            parent_loc_id = current_loc.get("parentId")
            break

    if parent_loc_id:
        parent_loc_data = next(
            (loc for loc in servicelocations if loc.get("id") == parent_loc_id), None
        )
        parent_name = (
            parent_loc_data.get("name")
            if parent_loc_data
            else f"Location {parent_loc_id}"
        )

        device_registry = dr.async_get(hass)
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, f"location_{parent_loc_id}")},
            name=f"Smappee {parent_name}",
            manufacturer="Smappee",
            model="Central Gateway / Service Location",
            entry_type=dr.DeviceEntryType.SERVICE,
        )
        _LOGGER.info(
            "Parent service location site context %s (%s) successfully mounted.",
            parent_name,
            parent_loc_id,
        )


def _setup_mqtt_stream(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: DataUpdateCoordinator,
    client: SmappeeClient,
    timer_context: dict[str, Any],
) -> None:
    """Initialize and connect the persistent background WebSocket MQTT streaming client."""
    if not coordinator.data:
        return

    charging_station_details_live = coordinator.data.get("charging_station_details", {})
    mqtt_config = client.get_mqtt_config(charging_station_details_live)

    if not mqtt_config:
        _LOGGER.warning(
            "Asynchronous push streaming monitoring aborted: telemetry map extraction yielded no tokens."
        )
        return

    _LOGGER.info(
        "Smappee cloud streaming telemetry specifications verified. Attaching WebSocket transport links..."
    )

    mqtt_client = mqtt_paho.Client(
        callback_api_version=mqtt_paho.CallbackAPIVersion.VERSION1,
        transport="websockets",
    )
    mqtt_client.username_pw_set(mqtt_config["username"], mqtt_config["password"])

    hass.async_add_executor_job(mqtt_client.tls_set)
    mqtt_client.reconnect_delay_set(min_delay=1, max_delay=120)

    target_charging_topic = str(mqtt_config.get("charging_state_topic", "")).lower()
    target_power_topic = str(mqtt_config.get("power_topic", "")).lower()

    def on_connect(paho_client: Any, userdata: Any, flags: Any, rc: int) -> None:
        if rc == 0:
            _LOGGER.info(
                "Successfully established connection loops toward Smappee cloud streaming servers."
            )
            for topic_key in ["power_topic", "charging_state_topic"]:
                topic_name = mqtt_config.get(topic_key)
                if topic_name:
                    paho_client.subscribe(topic_name)
                    _LOGGER.debug(
                        "Mounted persistent subscription panel tracking: %s", topic_name
                    )
        else:
            _LOGGER.error(
                "Smappee cloud streaming broker rejected authentication check. Code: %s",
                rc,
            )

    def on_disconnect(paho_client: Any, userdata: Any, rc: int) -> None:
        if rc != 0:
            _LOGGER.warning(
                "Smappee cloud streaming socket transport dropped. Instantiating auto-reconnect loops..."
            )

    def on_message(paho_client: Any, userdata: Any, msg: Any) -> None:
        payload = msg.payload.decode("utf-8")
        current_topic_lower = str(msg.topic).lower()

        if current_topic_lower == target_charging_topic:
            _LOGGER.debug(
                "Asynchronous streaming charger operational state update encountered on: %s",
                msg.topic,
            )
        elif current_topic_lower != target_power_topic:
            _LOGGER.debug(
                "Asynchronous streaming telemetry element received on topic: %s",
                msg.topic,
            )

        async def async_update_coordinator_data() -> None:
            if not coordinator.data:
                return

            if current_topic_lower == target_charging_topic:
                coordinator.data["mqtt_charging_state"] = payload
                coordinator.async_set_updated_data(coordinator.data)

                try:
                    mqtt_json = json.loads(payload)
                    if isinstance(mqtt_json, dict):
                        status_obj = mqtt_json.get("status", {})
                        state = str(
                            status_obj.get(
                                "current", mqtt_json.get("chargingState", "")
                            )
                        ).upper()
                        timer_context["handler"](state == "CHARGING")
                except Exception as err:
                    _LOGGER.error(
                        "Failed calculating transient execution bounds from stream metrics: %s",
                        err,
                    )

            elif current_topic_lower == target_power_topic:
                try:
                    coordinator.data["mqtt_power_data"] = json.loads(payload)
                except Exception:
                    coordinator.data["mqtt_power_data"] = payload
                coordinator.async_set_updated_data(coordinator.data)

        hass.loop.call_soon_threadsafe(
            lambda: hass.async_create_task(async_update_coordinator_data())
        )

    mqtt_client.on_connect = on_connect
    mqtt_client.on_disconnect = on_disconnect
    mqtt_client.on_message = on_message

    try:
        broker_host = mqtt_config.get("host", "dashboard.smappee.net")
        broker_port = mqtt_config.get("port", 443)

        mqtt_client.connect_async(broker_host, broker_port, keepalive=60)
        mqtt_client.loop_start()

        def stop_mqtt_loop(_event: Any = None) -> None:
            _LOGGER.info(
                "Tearing down open Smappee streaming network sockets and tracking loops..."
            )
            if timer_context["session_interval_unsub"]:
                timer_context["session_interval_unsub"]()
            mqtt_client.loop_stop()
            mqtt_client.disconnect()

        entry.async_on_unload(stop_mqtt_loop)
        entry.async_on_unload(
            hass.bus.async_listen_once("homeassistant_stop", stop_mqtt_loop)
        )

    except Exception as conn_err:
        _LOGGER.error(
            "Critical failure during websocket socket transport initialization: %s",
            conn_err,
        )
