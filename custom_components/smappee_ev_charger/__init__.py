"""Set up and manage the Smappee Charger integration platforms."""

import json
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import paho.mqtt.client as mqtt_paho

from .client import SmappeeClient
from .const import DOMAIN, STARTUP
from .coordinator import SmappeeDataUpdateCoordinator

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

    # Instantiate the data update coordinator class
    coordinator = SmappeeDataUpdateCoordinator(hass, client, station_id)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "client": client,
    }

    # Execute first baseline refresh via REST API upon startup
    await coordinator.async_config_entry_first_refresh()

    # Initialize long/short interval timers managed inside the coordinator
    coordinator.initialize_startup_timers()

    # Register parent service location context inside the Device Registry
    _register_parent_location_device(hass, entry, coordinator)

    # Connect the persistent background WebSocket MQTT stream
    _setup_mqtt_stream(hass, entry, coordinator, client)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Dismount and clean platform entity connections matching an integration entry instance."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return bool(unload_ok)


def _register_parent_location_device(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: SmappeeDataUpdateCoordinator
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
    coordinator: SmappeeDataUpdateCoordinator,
    client: SmappeeClient,
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
            # Zorg dat de coordinator opgestart is en data bevat
            if coordinator.data is None:
                coordinator.data = {}

            if current_topic_lower == target_charging_topic:
                # Sla de charging state op in de coordinator data container
                coordinator.data["mqtt_charging_state"] = payload

                try:
                    mqtt_json = json.loads(payload)
                    if isinstance(mqtt_json, dict):
                        status_obj = mqtt_json.get("status", {})
                        state = str(
                            status_obj.get(
                                "current", mqtt_json.get("chargingState", "")
                            )
                        ).upper()
                        coordinator.timer_context["handler"](state == "CHARGING")
                except Exception as err:
                    _LOGGER.error(
                        "Failed calculating transient execution bounds from stream metrics: %s",
                        err,
                    )

                # CRUCIAL: Laat de coordinator weten dat er nieuwe data is zodat sensoren verversen!
                coordinator.async_set_updated_data(coordinator.data)

            elif current_topic_lower == target_power_topic:
                try:
                    # Sla de power data op (voor je live power én je nieuwe energiesensoren)
                    coordinator.data["mqtt_power_data"] = json.loads(payload)
                except Exception:
                    coordinator.data["mqtt_power_data"] = payload

                # CRUCIAL: Laat de coordinator weten dat er nieuwe data is zodat sensoren verversen!
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
            if coordinator.timer_context["session_interval_unsub"]:
                coordinator.timer_context["session_interval_unsub"]()
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
