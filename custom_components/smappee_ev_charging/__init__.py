"""The Smappee Charger integration."""
import logging
import json
from datetime import timedelta
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, STARTUP
from .client import SmappeeClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = ["sensor", "binary_sensor", "switch", "number", "select", "light", "button", "device_tracker"]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Smappee Charger integration from a config entry."""
    _LOGGER.info(STARTUP)

    username = entry.data["username"]
    password = entry.data["password"]
    station_id = entry.data["station_id"]

    # 1. Initialize the API client
    session = async_get_clientsession(hass)
    client = SmappeeClient(username, password, session)
    
    # Ensure the charging station serial number is directly available on the client
    client.charging_station_serial = entry.data.get("serial")

    async def async_update_data():
        """Fetch topology, charging station details, and sessions from the Smappee API."""
        try:
            # Fetch ALL service locations
            servicelocations = await client.get_service_locations_full_details()
            
            all_smart_devices = []
            charging_station_details = {}

            # Search each location objectively based on the presence of a charging station serial number
            for loc in servicelocations:
                # Dive directly into the nested chargingStation object
                charging_station_obj = loc.get("chargingStation")
                
                # Check if the object exists and contains a serial number
                if charging_station_obj and isinstance(charging_station_obj, dict):
                    raw_serial = charging_station_obj.get("serialNumber")
                else:
                    raw_serial = None

                # ONLY fetch details if a valid serial number is found
                if raw_serial is not None:
                    serial_str = str(raw_serial).strip()
                    
                    _LOGGER.debug("Valid charging station serial discovered: %s. Fetching details...", serial_str)
                    
                    # Sync the correct serial number to the client
                    client.charging_station_serial = serial_str
                    
                    # Fetch details via the correct charging station serial
                    station_data = await client.get_charging_station_details(serial_str)
                    if station_data:
                        charging_station_details[serial_str] = station_data
                        
                        # Extract the linked smart_devices directly from the modules
                        modules = station_data.get("modules", [])
                        for module in modules:
                            if "smartDevice" in module:
                                smart_device = module["smartDevice"]
                                
                                if "configurationProperties" in module:
                                    smart_device["configurationProperties"] = module["configurationProperties"]
                                
                                if smart_device not in all_smart_devices:
                                    all_smart_devices.append(smart_device)

            # Fallback: If module readout is empty, fall back to the standard smart devices list
            if not all_smart_devices and servicelocations:
                _LOGGER.debug("No devices found via modules, falling back to get_smart_devices")
                client.service_location_id = servicelocations[0].get("id")
                
                fallback_devices = await client.get_smart_devices()
                for d in fallback_devices:
                    if d not in all_smart_devices:
                        all_smart_devices.append(d)

            recent_sessions = await client.get_recent_sessions()
            
            new_data = {
                "servicelocations": servicelocations,
                "smart_devices": all_smart_devices,
                "charging_station_details": charging_station_details,
                "recent_sessions": recent_sessions
            }

            # --- PRESERVE LIVE MQTT CLOUD DATA DURING REST REFRESH ---
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
            raise UpdateFailed(f"Error communicating with Smappee API: {err}")

    # Main coordinator handles heavy API topology data once every hour to safeguard token
    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"Smappee Charger {station_id}",
        update_method=async_update_data,
        update_interval=timedelta(hours=1),
    )

    async def async_refresh_sessions_only():
        """Fetch the latest sessions directly outside the regular polling interval."""
        try:
            _LOGGER.debug("Executing Smappee API session update...")
            recent_sessions = await client.get_recent_sessions()
            
            if coordinator.data:
                coordinator.data["recent_sessions"] = recent_sessions
                coordinator.async_set_updated_data(coordinator.data)
                _LOGGER.info("Smappee session data successfully refreshed.")
        except Exception as err:
            _LOGGER.warning("Could not refresh session data dynamically: %s", err)

    coordinator.async_refresh_sessions_only = async_refresh_sessions_only

    # ---- TIMING FIX: REGISTER COLD DATA STORE BEFORE ANY PLATFORM OR REFRESH FIRES ----
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "client": client,
    }
    # --------------------------------------------------------------------------------

    # Variables to track dynamic charging session timers live
    session_interval_unsub = None
    was_charging = False

    def handle_charging_session_timers(is_charging: bool):
        """Manage dynamic timers based strictly on the CHARGING status of the vehicle."""
        nonlocal session_interval_unsub, was_charging
        
        # Scenario A: Vehicle enters CHARGING state -> Start the 5-minute loop timer
        if is_charging and not session_interval_unsub:
            _LOGGER.info("Vehicle started charging. Dynamic 5-minute session timer activated.")
            
            async def run_periodic_session_update(_now):
                await coordinator.async_refresh_sessions_only()

            from homeassistant.helpers.event import async_track_time_interval
            session_interval_unsub = async_track_time_interval(
                hass, run_periodic_session_update, timedelta(minutes=5)
            )
            was_charging = True

        # Scenario B: Vehicle stopped CHARGING -> Stop loop timer and execute final settlement call after 5s
        elif not is_charging and was_charging:
            _LOGGER.info("Vehicle stopped charging. Stopping periodic timer and scheduling final session update in 5 seconds...")
            
            if session_interval_unsub:
                session_interval_unsub()
                session_interval_unsub = None
            
            async def finalize_charging_session(_now):
                _LOGGER.info("Executing final Smappee session call (5s post-charging).")
                await coordinator.async_refresh_sessions_only()

            from homeassistant.helpers.event import async_call_later
            async_call_later(hass, 5, finalize_charging_session)
            
            was_charging = False

    # 2. Execute first baseline refresh via REST API
    await coordinator.async_config_entry_first_refresh()

    # ---- COLD-START VALIDATION BASED ON CHARGING STATUS UPON HA REBOOT ----
    _LOGGER.debug("Running cold-start validation on current charger connectivity...")
    currently_charging = False

    # 1. Primary: Validate via rich details if cached
    serial = client.charging_station_serial
    if serial and "charging_station_details" in coordinator.data:
        station_data = coordinator.data["charging_station_details"].get(str(serial))
        if station_data:
            for module in station_data.get("modules", []):
                if "carCharger" in module and module["carCharger"]:
                    cc_data = module["carCharger"]
                    status_dict = cc_data.get("status", {})
                    rest_state = str(status_dict.get("current", "")).upper()

                    if rest_state == "CHARGING":
                        currently_charging = True

    # 2. Fallback: Validate via flat smart devices list
    if not currently_charging and "smart_devices" in coordinator.data:
        for device in coordinator.data["smart_devices"]:
            if device.get("type", {}).get("category") == "CARCHARGER":
                cc_data = device.get("carCharger", {})
                status_dict = cc_data.get("status", {}) if cc_data else {}
                flat_state = str(status_dict.get("current", device.get("chargingState", ""))).upper()
                if not cc_data and "status" in device and isinstance(device["status"], dict):
                    flat_state = str(device["status"].get("current", "")).upper()

                if flat_state == "CHARGING":
                    currently_charging = True

    if currently_charging:
        _LOGGER.info("Smappee charger detected as actively CHARGING upon startup. Activating periodic session timer...")
        handle_charging_session_timers(True)
    else:
        _LOGGER.debug("Charger is not actively charging upon startup. Standby monitoring active.")
    # ------------------------------------------------------------------------------

    # ---- REGISTER PARENT LOCATION IN DEVICE REGISTRY ----
    servicelocations = coordinator.data.get("servicelocations", [])
    smart_devices = coordinator.data.get("smart_devices", [])

    parent_loc_id = None
    for device in smart_devices:
        child_loc_id = device.get("serviceLocation")
        current_loc = next((loc for loc in servicelocations if loc.get("id") == child_loc_id), None)
        if current_loc and current_loc.get("parentId"):
            parent_loc_id = current_loc.get("parentId")
            break

    if parent_loc_id:
        parent_loc_data = next((loc for loc in servicelocations if loc.get("id") == parent_loc_id), None)
        parent_name = parent_loc_data.get("name") if parent_loc_data else f"Location {parent_loc_id}"

        device_registry = dr.async_get(hass)
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, f"location_{parent_loc_id}")},
            name=f"Smappee {parent_name}",
            manufacturer="Smappee",
            model="Central Gateway / Service Location",
            entry_type=dr.DeviceEntryType.SERVICE,
        )
        _LOGGER.info("Parent location %s (%s) registered successfully in Device Registry.", parent_name, parent_loc_id)
    # -----------------------------------------------------

    # ---- SECURE MQTT WEBSOCKET CONNECTION AND LIVE MONITORING ----
    charging_station_details_live = coordinator.data.get("charging_station_details", {})
    mqtt_config = client.get_mqtt_config(charging_station_details_live)

    if mqtt_config:
        import paho.mqtt.client as mqtt_paho

        _LOGGER.info("Smappee Cloud MQTT configuration acquired. Establishing secure WebSocket stream...")

        mqtt_client = mqtt_paho.Client(transport="websockets")
        mqtt_client.username_pw_set(mqtt_config["username"], mqtt_config["password"])
        
        await hass.async_add_executor_job(mqtt_client.tls_set)
        mqtt_client.reconnect_delay_set(min_delay=1, max_delay=120)

        def on_connect(paho_client, userdata, flags, rc):
            """Callback triggered upon establishing Cloud MQTT Broker connection."""
            if rc == 0:
                _LOGGER.info("Successfully connected to Smappee Cloud MQTT Broker via WebSockets.")
                for topic_key in ["power_topic", "charging_state_topic"]:
                    topic_name = mqtt_config.get(topic_key)
                    if topic_name:
                        paho_client.subscribe(topic_name)
                        _LOGGER.debug("Subscribed to Smappee Cloud topic: %s", topic_name)
            else:
                _LOGGER.error("Smappee Cloud MQTT connection error occurred (code: %s)", rc)

        def on_disconnect(paho_client, userdata, rc):
            """Callback triggered upon connection drops."""
            if rc != 0:
                _LOGGER.warning("Lost connection to Smappee Cloud MQTT. Reconnection loop running...")

        def on_message(paho_client, userdata, msg):
            """Callback triggered when an asynchronous message arrives from the cloud thread."""
            payload = msg.payload.decode("utf-8")
            topic = msg.topic
            
            target_charging_topic = str(mqtt_config.get("charging_state_topic", "")).lower()
            target_power_topic = str(mqtt_config.get("power_topic", "")).lower()
            current_topic_lower = str(topic).lower()

            # FIXED: Log ONLY charging state changes, ignore high-frequency power data to avoid log spam
            if current_topic_lower == target_charging_topic:
                _LOGGER.debug("Smappee Cloud MQTT charging state push received on %s", topic)
            elif current_topic_lower != target_power_topic:
                _LOGGER.debug("Smappee Cloud MQTT push received on %s", topic)

            async def async_update_coordinator_data():
                if current_topic_lower == target_charging_topic:
                    if coordinator.data:
                        coordinator.data["mqtt_charging_state"] = payload
                        # This triggers async_set_updated_data, which has its own internal log we want to bypass
                        coordinator.async_set_updated_data(coordinator.data)
                    
                    try:
                        mqtt_json = json.loads(payload)
                        if isinstance(mqtt_json, dict):
                            status_obj = mqtt_json.get("status", {})
                            state = str(status_obj.get("current", mqtt_json.get("chargingState", ""))).upper()
                            
                            is_active_charging = (state == "CHARGING")
                            handle_charging_session_timers(is_active_charging)
                    except Exception as e:
                        _LOGGER.error("Error parsing charging state for timer runtime: %s", e)
                
                elif current_topic_lower == target_power_topic:
                    if coordinator.data:
                        try:
                            coordinator.data["mqtt_power_data"] = json.loads(payload)
                            # FIXED: Use async_set_updated_data without manual logging triggers here
                            coordinator.async_set_updated_data(coordinator.data)
                        except Exception:
                            coordinator.data["mqtt_power_data"] = payload
                            coordinator.async_set_updated_data(coordinator.data)

            def schedule_in_main_loop():
                hass.async_create_task(async_update_coordinator_data())

            hass.loop.call_soon_threadsafe(schedule_in_main_loop)

        mqtt_client.on_connect = on_connect
        mqtt_client.on_disconnect = on_disconnect
        mqtt_client.on_message = on_message

        try:
            broker_host = mqtt_config.get("host", "dashboard.smappee.net")
            broker_port = mqtt_config.get("port", 443)
            
            mqtt_client.connect_async(broker_host, broker_port, keepalive=60)
            mqtt_client.loop_start()
            
            def stop_mqtt_loop(_event=None):
                _LOGGER.info("Permanently closing Smappee Cloud MQTT background loop...")
                if session_interval_unsub:
                    session_interval_unsub()
                mqtt_client.loop_stop()
                mqtt_client.disconnect()

            entry.async_on_unload(stop_mqtt_loop)
            entry.async_on_unload(
                hass.bus.async_listen_once("homeassistant_stop", stop_mqtt_loop)
            )
            
        except Exception as conn_err:
            _LOGGER.error("Critical error starting Smappee Cloud MQTT link: %s", conn_err)
    else:
        _LOGGER.warning("Smappee Cloud MQTT link aborted: get_mqtt_config yielded no profile.")
    # -------------------------------------------------------------------

    # 5. Initialize individual entity platforms asynchronously
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry (triggered on core restarts or integration deletion)."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok