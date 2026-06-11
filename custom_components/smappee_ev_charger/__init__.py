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
    """Initialize dedicated MQTT connection loops for each unique Smappee hub location."""
    if (
        not hasattr(coordinator, "high_level_configs")
        or not coordinator.high_level_configs
    ):
        _LOGGER.error(
            "MQTT streaming aborted: No high-level configuration payloads cached in coordinator."
        )
        return

    entry_loc_id = str(entry.data.get("station_id"))
    charging_station_details_live = coordinator.data.get("charging_station_details", {})

    # Fetch baseline MQTT configuration parameters via client definitions (contains the long charging_state_topic path)
    client_mqtt_config = client.get_mqtt_config(charging_station_details_live) or {}
    target_charging_topic = str(
        client_mqtt_config.get("charging_state_topic", "")
    ).lower()

    active_mqtt_clients = []

    # Iterate through all location payloads (e.g., 317418 for Grid/PV and 317443 for the Charger)
    for loc_id, config_payload in coordinator.high_level_configs.items():
        mqtt_cfg = None

        # 1. Search for explicit MQTT channel targets inside the measurements collection block (e.g., Grid/PV configurations)
        if "measurements" in config_payload:
            for meas in config_payload.get("measurements", []):
                ch = meas.get("updateChannels", {}).get("activePower", {})
                if ch.get("protocol") == "MQTT" and ch.get("userName"):
                    mqtt_cfg = {
                        "username": ch["userName"],
                        "password": ch["password"],
                        "topic": ch["name"],
                    }
                    break

        # 2. Fallback routing sequence: Inspect root updateChannels definitions (e.g., matching local charger contexts)
        if not mqtt_cfg:
            channels = (
                config_payload.get("updateChannels", {}) if config_payload else {}
            )
            active_power_cfg = channels.get("activePower", {})
            if (
                active_power_cfg
                and active_power_cfg.get("protocol") == "MQTT"
                and active_power_cfg.get("userName")
            ):
                mqtt_cfg = {
                    "username": active_power_cfg["userName"],
                    "password": active_power_cfg["password"],
                    "topic": active_power_cfg["name"],
                }

        # Skip out if no operational MQTT authentication specifications could be resolved for this hub instance
        if not mqtt_cfg:
            _LOGGER.warning(
                "No explicit MQTT parameters found in high-level payload for location %s",
                loc_id,
            )
            continue

        # Aggregate unique tracking topics intended for THIS explicit socket worker context
        local_topics = set()
        local_topics.add(str(mqtt_cfg["topic"]).lower())

        # If this location context matches the core configuration entity hub (the charger proxy registry),
        # attach the dynamic long-path charging state topic straight into this connection instance
        if loc_id == entry_loc_id and target_charging_topic:
            local_topics.add(target_charging_topic)

        _LOGGER.info(
            "Establishing isolated WebSocket connection for Smappee Location %s",
            loc_id,
        )

        # Instantiate a unique Paho client runner mapped strictly toward this isolated location signature profile
        mqtt_client = mqtt_paho.Client(
            callback_api_version=mqtt_paho.CallbackAPIVersion.VERSION1,
            transport="websockets",
        )
        mqtt_client.username_pw_set(mqtt_cfg["username"], mqtt_cfg["password"])
        hass.async_add_executor_job(mqtt_client.tls_set)
        mqtt_client.reconnect_delay_set(min_delay=1, max_delay=120)

        # Functional callback factories built to block state leakage across distinct background connection scopes
        def make_on_connect(topics_list: set, l_id: str):
            def on_connect(
                paho_client: Any, userdata: Any, flags: Any, rc: int
            ) -> None:
                if rc == 0:
                    _LOGGER.info(
                        "MQTT Connection successful for Location Hub: %s", l_id
                    )
                    for topic_name in topics_list:
                        paho_client.subscribe(topic_name)
                        _LOGGER.info("Successfully subscribed to topic: %s", topic_name)
                else:
                    _LOGGER.error(
                        "Smappee broker rejected authorization for location %s. Code: %s",
                        l_id,
                        rc,
                    )

            return on_connect

        def make_on_message(l_id: str, charging_topic: str):
            def on_message(paho_client: Any, userdata: Any, msg: Any) -> None:
                payload = msg.payload.decode("utf-8")
                current_topic = str(msg.topic).lower()

                _LOGGER.debug(
                    "Smappee MQTT [Incoming] | Hub: %s | Topic: %s | Payload: %s",
                    l_id,
                    current_topic,
                    payload,
                )

                async def async_update_coordinator_data() -> None:
                    if not coordinator.data:
                        return

                    # Guarantee structural map partitions exist for each separate location identity
                    if "mqtt_locations" not in coordinator.data:
                        coordinator.data["mqtt_locations"] = {}
                    if l_id not in coordinator.data["mqtt_locations"]:
                        coordinator.data["mqtt_locations"][l_id] = {
                            "power": {},
                            "state": {},
                        }

                    # CHANNEL A: Targeted charger hardware status update streaming payload context
                    if current_topic == charging_topic:
                        coordinator.data["mqtt_locations"][l_id]["state"] = payload
                        _LOGGER.critical(
                            "Charging state target topic match identified!"
                        )

                        # Execute quick validation parsing strictly to satisfy dynamic execution timer context parameters
                        try:
                            mqtt_json = json.loads(payload)
                            if isinstance(mqtt_json, dict):
                                status_obj = mqtt_json.get("status", {})
                                state = str(
                                    status_obj.get(
                                        "current", mqtt_json.get("chargingState", "")
                                    )
                                ).upper()
                                coordinator.timer_context["handler"](
                                    state == "CHARGING"
                                )
                        except Exception as err:
                            _LOGGER.error(
                                "Failed calculating transient execution bounds: %s", err
                            )

                    # CHANNEL B: Dense sequential matrix telemetry registers (Power, Currents, and Voltages arrays)
                    else:
                        try:
                            parsed_json = json.loads(payload)
                            if isinstance(parsed_json, dict) and (
                                "activePowerData" in parsed_json
                                or "importActiveEnergyData" in parsed_json
                            ):
                                coordinator.data["mqtt_locations"][l_id][
                                    "power"
                                ] = parsed_json
                            else:
                                coordinator.data["mqtt_locations"][l_id][
                                    "power"
                                ] = payload
                        except Exception:
                            coordinator.data["mqtt_locations"][l_id]["power"] = payload

                    # Forward state modifications directly downstream into core platform tracker classes
                    coordinator.async_set_updated_data(coordinator.data)

                hass.loop.call_soon_threadsafe(
                    lambda: hass.async_create_task(async_update_coordinator_data())
                )

            return on_message

        mqtt_client.on_connect = make_on_connect(local_topics, loc_id)
        mqtt_client.on_message = make_on_message(loc_id, target_charging_topic)

        try:
            mqtt_client.connect_async("dashboard.smappee.net", 443, keepalive=60)
            mqtt_client.loop_start()
            active_mqtt_clients.append(mqtt_client)
        except Exception as conn_err:
            _LOGGER.error(
                "Failed mounting WebSocket loop for location %s: %s", loc_id, conn_err
            )

    def stop_all_mqtt_loops(_event: Any = None) -> None:
        _LOGGER.info("Tearing down all active Smappee background network channels...")
        for client_instance in active_mqtt_clients:
            try:
                client_instance.loop_stop()
                client_instance.disconnect()
            except Exception as err:
                _LOGGER.warn(
                    "Error encountered while disconnecting MQTT client instance safely: %s",
                    err,
                )

    entry.async_on_unload(stop_all_mqtt_loops)
