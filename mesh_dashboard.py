#!/usr/bin/env python3
import time
import json
import ast
import logging
from logging.handlers import RotatingFileHandler
import threading
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, Response
from pubsub import pub
import paho.mqtt.client as mqtt
from meshtastic import serial_interface
import os
import re
from datetime import datetime

# -------------------- CONFIG --------------------
CONFIG_FILE = "config.json"
STATE_FILE = "state.json"
BRIDGE_TAG = "[BRIDGE]"
LOG_FILE = "meshtastic_bridge.log"
MAX_LOG_SIZE = 5 * 1024 * 1024
BACKUP_COUNT = 20
RECONNECT_DELAY = 5
LOCK_FILE = "/tmp/mesh_dashboard.lock"
SERIAL_PORT = "/dev/ttyUSB0"
LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL
}

# -------------------- LOGGING --------------------
logger = logging.getLogger("MeshDashboard")
logger.setLevel(logging.DEBUG)  # Default level, will be adjusted by config
handler = RotatingFileHandler(LOG_FILE, maxBytes=MAX_LOG_SIZE, backupCount=BACKUP_COUNT)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)

# Load initial config to set logging level
def load_config():
    global logger
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            config = json.load(f)
            # Ensure log_level exists, default to "INFO" if missing
            if "log_level" not in config:
                config["log_level"] = "INFO"
            log_level = config.get("log_level", "INFO")
            logger.setLevel(LOG_LEVELS.get(log_level, logging.INFO))
            return config
    # Default config if file doesn't exist
    config = {
        "mqtt_broker": "",
        "mqtt_port": 1883,
        "mqtt_username": "",
        "mqtt_password": "",
        "mqtt_topic_prefix": "Mesh/feeds",
        "log_level": "INFO"  # Default log level
    }
    logger.setLevel(logging.INFO)
    return config

# -------------------- FLASK --------------------
app = Flask(__name__)
app.secret_key = "supersecret"

# Register custom Jinja2 filter for strftime
@app.template_filter('strftime')
def _jinja2_filter_datetime(date, fmt='%Y-%m-%d %H:%M:%S'):
    return datetime.fromtimestamp(float(date)).strftime(fmt)

# -------------------- GLOBALS --------------------
iface = None
mqtt_client = None
bridge_status = "Disconnected"
mqtt_status = "Disconnected"
known_nodes = {}  # node_id -> set(keys)
node_messages = {}  # node_id -> list of {'time': timestamp, 'type': 'sent' or 'received', 'message': text}
enabled_nodes = set()  # Set of enabled node_ids for HA
custom_sensors = {}  # node_id: list of {'sensor_name': str, 'pattern': str, 'topic': str, 'device_class': str, 'value_type': str, 'delay_off': int}
command_sensors = {}  # node_id: list of {'command_name': str, 'single_press': str or None, 'on_message': str, 'off_message': str}
node_info = {}  # node_id -> {'short_name': str, 'long_name': str, 'last_heard': timestamp}
last_heard = {}  # node_id -> last rxTime
sensor_states = {}  # node_id -> {sensor_name: {'value': str, 'last_update': float, 'timer': Timer or None}}
update_event = threading.Event()
node_info_lock = threading.Lock()

# -------------------- CONFIG AND STATE HANDLING --------------------
def load_state():
    global known_nodes, enabled_nodes, node_info, last_heard, custom_sensors, sensor_states, command_sensors
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                state = json.load(f)
                known_nodes = {k: set(v) for k, v in state.get("known_nodes", {}).items()}
                enabled_nodes = set(state.get("enabled_nodes", []))
                node_info = {k: v for k, v in state.get("node_info", {}).items()}
                last_heard = {k: v for k, v in state.get("last_heard", {}).items()}
                custom_sensors = state.get("custom_sensors", {})
                sensor_states = state.get("sensor_states", {})
                # Initialize sensor_states with default structure if missing
                for node_id in sensor_states:
                    for sensor_name, state_data in sensor_states[node_id].items():
                        if "timer" not in state_data:
                            state_data["timer"] = None
                        if "value" not in state_data:
                            state_data["value"] = "OFF"
                        if "last_update" not in state_data:
                            state_data["last_update"] = 0
                        if "delay_off" not in state_data:
                            state_data["delay_off"] = 0
                command_sensors = state.get("command_sensors", {})
                # Sync known_nodes with custom_sensors and command_sensors on load
                for node_id in known_nodes:
                    if node_id in custom_sensors:
                        current_custom_sensors = {s["sensor_name"] for s in custom_sensors[node_id]}
                        known_nodes[node_id] = {k for k in known_nodes[node_id] if k in current_custom_sensors or k in NUMERIC_KEYS}
                    if node_id in command_sensors:
                        current_commands = {c["command_name"] for c in command_sensors[node_id]}
                        known_nodes[node_id] = {k for k in known_nodes[node_id] if k in current_commands or k in NUMERIC_KEYS}
        except Exception as e:
            logger.warning(f"Failed to load state: {e}")
    # Reset binary sensor states on startup
    clear_binary_sensor_states()
    if mqtt_client and hasattr(mqtt_client, "is_connected") and mqtt_client.is_connected():
        for node_id in enabled_nodes:
            trigger_ha_discovery(node_id, enable=True)

def save_state():
    state = {
        "known_nodes": {k: list(v) for k, v in known_nodes.items()},
        "enabled_nodes": list(enabled_nodes),
        "node_info": node_info,
        "last_heard": last_heard,
        "custom_sensors": custom_sensors,
        "sensor_states": {k: {sk: {'value': sv.get('value', 'OFF'), 'last_update': sv.get('last_update', 0), 'delay_off': sv.get('delay_off', 0)} for sk, sv in v.items()} for k, v in sensor_states.items()},
        "command_sensors": command_sensors
    }
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=4)
    except Exception as e:
        logger.error(f"Failed to save state: {e}")

def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)

# Reset binary sensor states to OFF
def clear_binary_sensor_states():
    global sensor_states, custom_sensors, mqtt_client
    for node_id in custom_sensors:
        if node_id not in sensor_states:
            sensor_states[node_id] = {}
        for sensor in custom_sensors[node_id]:
            if sensor["value_type"] == "binary":
                sensor_name = sensor["sensor_name"]
                topic = f"{config['mqtt_topic_prefix']}/{node_id}/{sensor['topic']}/{sensor_name}"
                if sensor_name in sensor_states[node_id] and sensor_states[node_id][sensor_name].get("timer"):
                    sensor_states[node_id][sensor_name]["timer"].cancel()
                sensor_states[node_id][sensor_name] = {
                    "value": "OFF",
                    "last_update": time.time(),
                    "timer": None,
                    "delay_off": sensor.get("delay_off", 0)
                }
                if mqtt_client and mqtt_client.is_connected():
                    mqtt_client.publish(topic, "OFF", retain=True)
                    logger.info(f"📊 Reset binary sensor {sensor_name} for {node_id} to OFF on topic {topic}")
    save_state()

config = load_config()
load_state()

# -------------------- SINGLE INSTANCE CHECK --------------------
if os.path.exists(LOCK_FILE):
    logger.error("Another instance is already running. Exiting.")
    exit(1)
with open(LOCK_FILE, "w") as f:
    f.write(str(os.getpid()))
import atexit
atexit.register(lambda: os.remove(LOCK_FILE))

# -------------------- MESHTASTIC --------------------
def connect_meshtastic():
    global iface, bridge_status
    while True:
        try:
            iface = serial_interface.SerialInterface(SERIAL_PORT)
            bridge_status = "Connected"
            logger.info(f"🔗 Connected to Meshtastic node on {SERIAL_PORT}")
            break
        except Exception as e:
            bridge_status = f"Error: {e}"
            logger.warning(f"Failed to connect to Meshtastic node: {e}")
            time.sleep(RECONNECT_DELAY)

def meshtastic_thread():
    global iface, bridge_status
    while True:
        try:
            if iface is None:
                connect_meshtastic()
            time.sleep(30)
        except Exception as e:
            bridge_status = f"Error: {e}"
            logger.warning(f"Meshtastic thread error: {e}")
            time.sleep(RECONNECT_DELAY)

# -------------------- MQTT --------------------
def on_mqtt_message(client, userdata, msg):
    global iface
    try:
        payload = msg.payload.decode("utf-8")
        logger.debug(f"📩 MQTT message received: topic={msg.topic}, payload={payload}")
        if payload.startswith(BRIDGE_TAG):
            return
        topic_parts = msg.topic.split("/")
        if len(topic_parts) < 4 or topic_parts[0] != "Mesh" or topic_parts[1] != "feeds":
            return
        node_id = topic_parts[2]
        sub_type = topic_parts[3]
        if sub_type == "text":
            if iface and hasattr(iface, "sendText"):
                if node_id == "broadcast":
                    iface.sendText(payload)
                else:
                    iface.sendText(payload, destinationId=node_id)
                logger.info(f"💬 MQTT {msg.topic}: {payload} ➜ Mesh node {node_id}")
        elif sub_type == "command" and len(topic_parts) >= 5:
            command_name = topic_parts[4]
            if node_id in command_sensors:
                command_config = next((c for c in command_sensors[node_id] if c["command_name"] == command_name), None)
                if command_config:
                    text_topic = f"{config['mqtt_topic_prefix']}/{node_id}/text"
                    if command_config["single_press"] and payload == "PRESS":
                        message = command_config["single_press"]
                        if iface and hasattr(iface, "sendText"):
                            iface.sendText(message, destinationId=node_id)
                            mqtt_client.publish(text_topic, message, retain=False)
                            logger.info(f"🔔 Command {command_name} triggered single press: {message} sent to {node_id}")
                            if node_id not in node_messages:
                                node_messages[node_id] = []
                            node_messages[node_id].append({'time': time.time(), 'type': 'sent', 'message': message})
                            save_state()
                    elif command_config["on_message"] and command_config["off_message"]:
                        if payload == command_config["on_message"]:
                            if iface and hasattr(iface, "sendText"):
                                iface.sendText(payload, destinationId=node_id)
                                mqtt_client.publish(text_topic, payload, retain=False)
                                state_topic = f"{config['mqtt_topic_prefix']}/{node_id}/command/{command_name}/state"
                                mqtt_client.publish(state_topic, payload, retain=True)
                                logger.info(f"🔔 Command {command_name} triggered on: {payload} sent to {node_id}")
                                if node_id not in node_messages:
                                    node_messages[node_id] = []
                                node_messages[node_id].append({'time': time.time(), 'type': 'sent', 'message': payload})
                                save_state()
                        elif payload == command_config["off_message"]:
                            if iface and hasattr(iface, "sendText"):
                                iface.sendText(payload, destinationId=node_id)
                                mqtt_client.publish(text_topic, payload, retain=False)
                                state_topic = f"{config['mqtt_topic_prefix']}/{node_id}/command/{command_name}/state"
                                mqtt_client.publish(state_topic, payload, retain=True)
                                logger.info(f"🔔 Command {command_name} triggered off: {payload} sent to {node_id}")
                                if node_id not in node_messages:
                                    node_messages[node_id] = []
                                node_messages[node_id].append({'time': time.time(), 'type': 'sent', 'message': payload})
                                save_state()
    except Exception as e:
        logger.error(f"Error in on_mqtt_message: {e}")

def connect_mqtt():
    global mqtt_client, mqtt_status, config
    try:
        mqtt_client = mqtt.Client(client_id="MeshBridge", callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        mqtt_client.username_pw_set(config.get("mqtt_username"), config.get("mqtt_password"))
        mqtt_client.on_message = on_mqtt_message
        logger.info(f"Attempting to connect to MQTT broker: {config.get('mqtt_broker')}:{config.get('mqtt_port')}")
        mqtt_client.connect(config["mqtt_broker"], int(config["mqtt_port"]), 60)
        mqtt_client.loop_start()
        mqtt_status = "Connected"
        mqtt_client.subscribe(f"{config.get('mqtt_topic_prefix','Mesh/feeds')}/+/+/text")
        mqtt_client.subscribe(f"{config.get('mqtt_topic_prefix','Mesh/feeds')}/+/command/+")
        logger.info("🔗 Connected to MQTT broker")
        # Clear binary sensor states after connection
        clear_binary_sensor_states()
    except Exception as e:
        logger.error(f"Failed to initialize MQTT client: {e}")
        mqtt_status = f"Error: {e}"
        time.sleep(RECONNECT_DELAY)
        threading.Thread(target=lambda: connect_mqtt(), daemon=True).start()

# -------------------- TELEMETRY HANDLING --------------------
NUMERIC_KEYS = {
    "batterylevel", "battery_level", "voltage", "temperature",
    "humidity", "pressure", "airutiltx", "air_util_tx", "channelutilization",
    "channel_utilization", "uptimeseconds", "uptime_seconds", "altitude",
    "groundspeed", "groundtrack", "satsinview", "pdop"
}

TELEMETRY_DEVICE_CLASSES = {
    "batterylevel": {"device_class": "battery", "unit": "%"},
    "battery_level": {"device_class": "battery", "unit": "%"},
    "voltage": {"device_class": "voltage", "unit": "V"},
    "temperature": {"device_class": "temperature", "unit": "°C"},
    "humidity": {"device_class": "humidity", "unit": "%"},
    "pressure": {"device_class": "pressure", "unit": "hPa"},
    "airutiltx": {"device_class": "signal_strength", "unit": "%"},
    "air_util_tx": {"device_class": "signal_strength", "unit": "%"},
    "channelutilization": {"device_class": "signal_strength", "unit": "%"},
    "channel_utilization": {"device_class": "signal_strength", "unit": "%"},
    "uptimeseconds": {"device_class": "duration", "unit": "s"},
    "uptime_seconds": {"device_class": "duration", "unit": "s"},
    "altitude": {"device_class": "distance", "unit": "m"},
    "groundspeed": {"device_class": "speed", "unit": "km/h"},
    "groundtrack": {"device_class": "angle", "unit": "°"},
    "satsinview": {"device_class": "count", "unit": ""},
    "pdop": {"device_class": "signal_strength", "unit": ""}
}

def parse_telemetry_raw(raw, node_id):
    if not raw:
        logger.debug(f"Telemetry raw for {node_id} is None or empty")
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try: return json.loads(raw)
        except: pass
        try: return ast.literal_eval(raw)
        except: pass
        try: return json.loads(raw.replace("\n", " ").replace("'", '"'))
        except: logger.warning(f"Failed to parse telemetry payload for {node_id}: {raw!r}")
    logger.warning(f"Could not parse telemetry payload for {node_id}: {raw!r}, type: {type(raw)}")
    return {}

# -------------------- HOME ASSISTANT AUTO-DISCOVERY --------------------
def publish_ha_discovery(node_id, sensor_key, entity_type="sensor", remove=False):
    global mqtt_client, node_info, custom_sensors, command_sensors
    if not mqtt_client or not mqtt_client.is_connected():
        logger.warning(f"MQTT client not connected, skipping HA discovery for {node_id}/{sensor_key}")
        return
    if node_id not in enabled_nodes and not remove:
        return  # Skip discovery if node is not enabled
    node_id_safe = node_id.replace("!", "").replace(".", "_").replace(" ", "_")
    last_three = node_id_safe[-3:]
    node_info_data = node_info.get(node_id, {})
    short_name = node_info_data.get("short_name", node_id_safe)
    long_name = node_info_data.get("long_name", f"{short_name} {node_id_safe}")
    display_name = f"{short_name}.{last_three}"
    sensor_config = next((s for s in custom_sensors.get(node_id, []) if s["sensor_name"] == sensor_key), None)
    command_config = next((c for c in command_sensors.get(node_id, []) if c["command_name"] == sensor_key), None)
    topic_prefix = sensor_config["topic"] if sensor_config else ("position" if entity_type == "device_tracker" else "telemetry")
    discovery_topic = f"homeassistant/{entity_type}/meshtastic_{node_id_safe}_{sensor_key.lower().replace(' ', '_')}/config"
    state_topic = f"{config['mqtt_topic_prefix']}/{node_id}/{topic_prefix}/{sensor_key}"

    if remove:
        mqtt_client.publish(discovery_topic, "", retain=True)
        mqtt_client.publish(state_topic, "", retain=True)
        # Cleanup existing button if it exists
        button_topic = f"homeassistant/button/meshtastic_{node_id_safe}_refresh_data/config"
        mqtt_client.publish(button_topic, "", retain=True)
        logger.info(f"🗑️ Removed HA discovery, state, and button for {node_id}/{sensor_key} from {discovery_topic}")
        return

    payload = {
        "name": f"{display_name} {sensor_key.replace('_', ' ').title()}",
        "state_topic": state_topic,
        "unique_id": f"meshtastic_{display_name}_{sensor_key.lower().replace(' ', '_')}",
        "device": {"identifiers": [f"meshtastic_{node_id_safe}"], "name": long_name, "model": node_id_safe, "manufacturer": "Meshtastic"},
        "force_update": True
    }

    if entity_type == "device_tracker" and sensor_key == "position":
        payload.update({
            "json_attributes_topic": f"{config['mqtt_topic_prefix']}/{node_id}/position/location",
            "source_type": "gps",
            "value_template": "{{ value_json.latitude ~ ',' ~ value_json.longitude if value_json.latitude is not none and value_json.longitude is not none else '' }}"
        })
    elif entity_type == "binary_sensor" and sensor_config:
        payload.update({
            "payload_on": "ON",
            "payload_off": "OFF",
            "value_template": "{{ value }}",
            "device_class": sensor_config["device_class"] if sensor_config["device_class"] in ["battery", "carbon_monoxide", "gas", "moisture", "motion", "occupancy", "smoke", "sound", "tamper", "vibration", "battery_charging", "cold", "connectivity", "door", "garage_door", "heat", "light", "lock", "moving", "opening", "plug", "power", "presence", "problem", "running", "safety", "update", "window"] else "binary_sensor"
        })
    elif entity_type == "sensor":
        if sensor_config:
            payload.update({
                "value_template": "{{ value | float(0) }}",
                "device_class": sensor_config["device_class"],
                "unit_of_measurement": {"battery": "%", "voltage": "V", "temperature": "°C", "humidity": "%", "pressure": "hPa", "distance": "m", "speed": "km/h", "angle": "°", "current": "A", "energy": "kWh", "power": "W", "duration": "s", "illuminance": "lx", "signal_strength": "dBm"}.get(sensor_config["device_class"], "")
            })
        elif sensor_key.lower() in TELEMETRY_DEVICE_CLASSES:
            device_class_data = TELEMETRY_DEVICE_CLASSES[sensor_key.lower()]
            payload.update({
                "value_template": "{{ value | float(0) }}",
                "device_class": device_class_data["device_class"],
                "unit_of_measurement": device_class_data["unit"]
            })
    elif entity_type == "button" and command_config:
        payload.update({
            "command_topic": f"{config['mqtt_topic_prefix']}/{node_id}/command/{command_config['command_name']}",
            "payload_press": "PRESS"
        })
    elif entity_type == "switch" and command_config and command_config["on_message"] and command_config["off_message"]:
        payload.update({
            "command_topic": f"{config['mqtt_topic_prefix']}/{node_id}/command/{command_config['command_name']}",
            "state_topic": f"{config['mqtt_topic_prefix']}/{node_id}/command/{command_config['command_name']}/state",
            "payload_on": command_config["on_message"],
            "payload_off": command_config["off_message"],
            "value_template": "{{ 'ON' if value == '" + command_config["on_message"] + "' else 'OFF' }}"
        })

    mqtt_client.publish(discovery_topic, json.dumps(payload), retain=True)
    if not remove and entity_type == "binary_sensor" and sensor_config:
        initial_state = sensor_states.get(node_id, {}).get(sensor_key, {"value": "OFF"})["value"]
        mqtt_client.publish(state_topic, initial_state, retain=True)
    logger.info(f"✅ Published HA discovery for {node_id}/{sensor_key} as {entity_type} with topic {state_topic}")

def trigger_ha_discovery(node_id, enable=True):
    if node_id not in known_nodes:
        known_nodes[node_id] = set()
    if enable:
        # Publish standard telemetry sensors and position data
        for sensor_key in {k.lower() for k in NUMERIC_KEYS if k.lower() in known_nodes[node_id]}:
            entity_type = "device_tracker" if sensor_key in ("position", "latitude", "longitude") else "sensor"
            publish_ha_discovery(node_id, sensor_key, entity_type)
        # Publish a single position tracker
        if "position" not in known_nodes.get(node_id, set()):
            known_nodes[node_id].add("position")
        publish_ha_discovery(node_id, "position", "device_tracker")
        # Publish only current custom sensors
        if node_id in custom_sensors:
            for sensor in custom_sensors[node_id]:
                sensor_key = sensor["sensor_name"]
                if sensor_key not in known_nodes.get(node_id, set()):
                    known_nodes[node_id].add(sensor_key)
                entity_type = "binary_sensor" if sensor["value_type"] == "binary" else "sensor"
                publish_ha_discovery(node_id, sensor_key, entity_type)
        # Publish command sensors as buttons or switches
        if node_id in command_sensors:
            for command in command_sensors[node_id]:
                command_key = command["command_name"]
                if command_key not in known_nodes.get(node_id, set()):
                    known_nodes[node_id].add(command_key)
                entity_type = "switch" if command.get("on_message") and command.get("off_message") else "button"
                publish_ha_discovery(node_id, command_key, entity_type)
        # Prune known_nodes to match current custom_sensors, command_sensors, and NUMERIC_KEYS
        if node_id in custom_sensors:
            current_custom_sensors = {s["sensor_name"] for s in custom_sensors[node_id]}
            current_commands = {c["command_name"] for c in command_sensors.get(node_id, [])}
            known_nodes[node_id] = {k for k in known_nodes[node_id] if k in current_custom_sensors or k in current_commands or k.lower() in NUMERIC_KEYS or k == "position"}
    else:
        # Remove all sensors and commands when disabling
        for sensor_key in known_nodes[node_id]:
            entity_type = "device_tracker" if sensor_key in ("position", "latitude", "longitude") else "sensor"
            publish_ha_discovery(node_id, sensor_key, entity_type, remove=True)
        if node_id in custom_sensors:
            for sensor in custom_sensors[node_id]:
                entity_type = "binary_sensor" if sensor["value_type"] == "binary" else "sensor"
                publish_ha_discovery(node_id, sensor["sensor_name"], entity_type, remove=True)
        if node_id in command_sensors:
            for command in command_sensors[node_id]:
                entity_type = "switch" if command.get("on_message") and command.get("off_message") else "button"
                publish_ha_discovery(node_id, command["command_name"], entity_type, remove=True)

# -------------------- NODE INFO FETCH HELPERS --------------------
def _extract_user_from_node_data(node_data):
    if not node_data: return None
    try: return node_data.get("user") or (node_data.get("data") or {}).get("user") or getattr(node_data, "user", None)
    except: return None

def fetch_node_info(node_id, max_retries=3):
    global iface, node_info
    if not iface or not hasattr(iface, "getNode"): return False
    for attempt in range(max_retries):
        try:
            node_data = iface.getNode(node_id) or iface.getNode(str(node_id)[1:] if str(node_id).startswith("!") else "!" + str(node_id))
            user = _extract_user_from_node_data(node_data)
            if user:
                short = user.get("shortName") or user.get("short_name") or node_id
                longn = user.get("longName") or user.get("long_name") or node_id
                with node_info_lock:
                    node_info[node_id] = {"short_name": short, "long_name": longn, "last_heard": last_heard.get(node_id, time.time())}
                    save_state()
                logger.info(f"ℹ️ Fetched node info for {node_id}: {short} / {longn}")
                return True
            if attempt < max_retries - 1: time.sleep(2 ** attempt)
        except Exception as e: logger.debug(f"Could not fetch node info for {node_id} (attempt {attempt + 1}/{max_retries}): {e}")
    logger.warning(f"Failed to fetch node info for {node_id} after {max_retries} attempts")
    return False

def node_info_refresher():
    while True:
        try:
            for nid in list(known_nodes.keys()):
                ni = node_info.get(nid, {})
                if ni.get("short_name") in [None, nid, "unknown"]:
                    fetch_node_info(nid)
            time.sleep(30)
        except Exception as e: logger.warning(f"node_info_refresher error: {e}"); time.sleep(10)

# -------------------- HANDLE PACKET --------------------
def handle_packet(packet, interface=None):
    global known_nodes, node_messages, node_info, last_heard, mqtt_client, custom_sensors, sensor_states
    try:
        logger.debug(f"Packet received raw: {packet}")
        decoded = packet.get("decoded", {})
        node_id = str(packet.get("fromId", "unknown"))
        rx_time = packet.get("rxTime", time.time())

        if node_id not in known_nodes:
            known_nodes[node_id] = set()
        last_heard[node_id] = rx_time
        if node_id not in node_info:
            node_info[node_id] = {"short_name": node_id, "long_name": node_id, "last_heard": rx_time}

        if decoded.get("portnum") == "NODEINFO_APP":
            node_info_data = decoded.get("data", {}).get("user") or decoded.get("user")
            if isinstance(node_info_data, dict):
                with node_info_lock:
                    node_info[node_id] = {"short_name": node_info_data.get("shortName") or node_id, "long_name": node_info_data.get("longName") or node_id, "last_heard": rx_time}
                    save_state()
                if node_id in enabled_nodes and mqtt_client:
                    for sensor_key in known_nodes[node_id]:
                        entity_type = "device_tracker" if sensor_key in ("position", "latitude", "longitude") else "sensor"
                        publish_ha_discovery(node_id, sensor_key, entity_type)
                    for sensor in custom_sensors.get(node_id, []):
                        entity_type = "binary_sensor" if sensor["value_type"] == "binary" else "sensor"
                        publish_ha_discovery(node_id, sensor["sensor_name"], entity_type)
                    for command in command_sensors.get(node_id, []):
                        entity_type = "switch" if command.get("on_message") and command.get("off_message") else "button"
                        publish_ha_discovery(node_id, command["command_name"], entity_type)
                update_event.set()

        if "text" in decoded:
            text = decoded["text"]
            if node_id not in node_messages:
                node_messages[node_id] = []
            node_messages[node_id].append({'time': rx_time, 'type': 'received', 'message': text})
            logger.info(f"💬 Received text from {node_id}: {text}")
            if mqtt_client:
                topic = f"{config['mqtt_topic_prefix']}/{node_id}/text"
                mqtt_client.publish(topic, text, retain=True)
                logger.info(f"🔔 Text from {node_id}: {text} ➜ {topic}")
            if mqtt_client and node_id in custom_sensors:
                if node_id not in sensor_states:
                    sensor_states[node_id] = {}
                for sensor in custom_sensors[node_id]:
                    pattern = sensor["pattern"].lower()
                    topic = f"{config['mqtt_topic_prefix']}/{node_id}/{sensor['topic']}/{sensor['sensor_name']}"
                    if sensor["value_type"] == "numeric":
                        match = re.search(rf'\b{re.escape(pattern)}\s*([-]?\d+\.?\d*)', text.lower(), re.IGNORECASE)
                        if match:
                            try:
                                value = float(match.group(1))
                                mqtt_client.publish(topic, str(value), retain=True)
                                logger.info(f"📊 Published numeric sensor {sensor['sensor_name']} for {node_id}: {value} ➜ {topic}")
                                if sensor["sensor_name"] not in known_nodes.get(node_id, set()):
                                    known_nodes[node_id].add(sensor["sensor_name"])
                                    if node_id in enabled_nodes:
                                        publish_ha_discovery(node_id, sensor["sensor_name"], "sensor")
                                        # Publish initial state for numeric sensor
                                        mqtt_client.publish(topic, "0", retain=True)
                            except ValueError:
                                logger.warning(f"Invalid numeric value for {sensor['sensor_name']} in {node_id}: {match.group(1)}")
                    elif sensor["value_type"] == "binary":
                        current_state = sensor_states[node_id].get(sensor["sensor_name"], {"value": "OFF", "last_update": 0, "timer": None, "delay_off": sensor.get("delay_off", 0)})
                        match = re.search(rf'\b{re.escape(pattern)}\b', text.lower(), re.IGNORECASE) or "detected" in text.lower()
                        cleared = "cleared" in text.lower()
                        new_state = current_state["value"]
                        if match or cleared:
                            new_state = "ON" if match else "OFF"
                            if current_state.get("timer"):
                                current_state["timer"].cancel()
                            mqtt_client.publish(topic, new_state, retain=True)
                            logger.info(f"📊 Published binary sensor {sensor['sensor_name']} for {node_id}: {new_state} ➜ {topic}")
                            current_state["value"] = new_state
                            current_state["last_update"] = rx_time
                            if sensor["sensor_name"] not in known_nodes.get(node_id, set()):
                                known_nodes[node_id].add(sensor["sensor_name"])
                                if node_id in enabled_nodes:
                                    publish_ha_discovery(node_id, sensor["sensor_name"], "binary_sensor")
                        if sensor.get("delay_off", 0) > 0 and new_state == "ON":
                            if current_state.get("timer"):
                                current_state["timer"].cancel()
                            def auto_off(topic=topic, sensor_name=sensor["sensor_name"], node_id=node_id):
                                if node_id in sensor_states and sensor_name in sensor_states[node_id]:
                                    current = sensor_states[node_id][sensor_name]
                                    if current["value"] == "ON" and time.time() - current["last_update"] >= sensor["delay_off"]:
                                        mqtt_client.publish(topic, "OFF", retain=True)
                                        logger.info(f"📊 Auto-off triggered for {sensor_name} after {sensor['delay_off']} seconds")
                                        current["value"] = "OFF"
                                        current["timer"] = None
                                        sensor_states[node_id][sensor_name] = current
                                        save_state()
                            timer = threading.Timer(sensor["delay_off"], auto_off)
                            timer.start()
                            current_state["timer"] = timer
                        sensor_states[node_id][sensor["sensor_name"]] = current_state
                        save_state()
            update_event.set()

        if "telemetry" in decoded:
            telemetry = parse_telemetry_raw(decoded.get("telemetry"), node_id)
            if mqtt_client:
                for key, val in telemetry.get("deviceMetrics", {}).items():
                    key_str = key.lower()
                    topic = f"{config['mqtt_topic_prefix']}/{node_id}/telemetry/{key_str}"
                    value = float(val) if val is not None and key_str in NUMERIC_KEYS else str(val) if val is not None else ""
                    mqtt_client.publish(topic, str(value), retain=True)
                    logger.info(f"📊 Telemetry {node_id}/{key_str}: {value} ➜ {topic}")
                    if key_str not in known_nodes.get(node_id, set()):
                        known_nodes[node_id].add(key_str)
                        if node_id in enabled_nodes:
                            publish_ha_discovery(node_id, key_str, "sensor")
            update_event.set()

        if "position" in decoded:
            position = decoded.get("position", {})
            if mqtt_client:
                location_topic = f"{config['mqtt_topic_prefix']}/{node_id}/position/location"
                position_data = {
                    "latitude": position.get("latitude"),
                    "longitude": position.get("longitude")
                }
                mqtt_client.publish(location_topic, json.dumps(position_data), retain=True)
                logger.info(f"📍 Position {node_id}/location ➜ {location_topic}")
                if "position" not in known_nodes.get(node_id, set()):
                    known_nodes[node_id].add("position")
                    if node_id in enabled_nodes:
                        publish_ha_discovery(node_id, "position", "device_tracker")
                for key, val in {"altitude": position.get("altitude"), "groundSpeed": position.get("groundSpeed"), "groundTrack": position.get("groundTrack"), "satsInView": position.get("satsInView"), "PDOP": position.get("PDOP")}.items():
                    if val is not None:
                        key_str = key.lower()
                        topic = f"{config['mqtt_topic_prefix']}/{node_id}/position/{key_str}"
                        mqtt_client.publish(topic, str(val), retain=True)
                        logger.info(f"📍 Position {node_id}/{key_str}: {val} ➜ {topic}")
                        if key_str not in known_nodes.get(node_id, set()):
                            known_nodes[node_id].add(key_str)
                            if node_id in enabled_nodes:
                                publish_ha_discovery(node_id, key_str, "sensor")
            update_event.set()

        save_state()

    except Exception as e:
        logger.error(f"Critical error handling packet: {e}, Packet: {packet}")

pub.subscribe(handle_packet, "meshtastic.receive")

# -------------------- FLASK ROUTES --------------------
@app.route("/", methods=["GET", "POST"])
def index():
    global enabled_nodes
    if request.method == "POST":
        if "toggle_ha" in request.form:
            node_id = request.form["node_id"]
            is_enabled = request.form.get("toggle_ha") == "on"
            if is_enabled and node_id not in enabled_nodes:
                enabled_nodes.add(node_id)
                trigger_ha_discovery(node_id, enable=True)
            elif not is_enabled and node_id in enabled_nodes:
                enabled_nodes.remove(node_id)
                trigger_ha_discovery(node_id, enable=False)
            save_state()
            update_event.set()
        elif "clear_node" in request.form:
            node_id = request.form.get("node_id")
            if node_id and node_id in known_nodes:
                for sensor_key in known_nodes[node_id]:
                    entity_type = "device_tracker" if sensor_key in ("position", "latitude", "longitude") else "sensor"
                    publish_ha_discovery(node_id, sensor_key, entity_type, remove=True)
                if node_id in custom_sensors:
                    for sensor in custom_sensors[node_id]:
                        entity_type = "binary_sensor" if sensor["value_type"] == "binary" else "sensor"
                        publish_ha_discovery(node_id, sensor["sensor_name"], entity_type, remove=True)
                if node_id in command_sensors:
                    for command in command_sensors[node_id]:
                        entity_type = "switch" if command.get("on_message") and command.get("off_message") else "button"
                        publish_ha_discovery(node_id, command["command_name"], entity_type, remove=True)
                del known_nodes[node_id]
                if node_id in node_info:
                    del node_info[node_id]
                if node_id in last_heard:
                    del last_heard[node_id]
                if node_id in enabled_nodes:
                    enabled_nodes.remove(node_id)
                if node_id in sensor_states:
                    del sensor_states[node_id]
                if node_id in node_messages:
                    del node_messages[node_id]
                if node_id in custom_sensors:
                    del custom_sensors[node_id]
                if node_id in command_sensors:
                    del command_sensors[node_id]
                save_state()
                logger.info(f"🗑️ Cleared node {node_id} from interface")
                update_event.set()
            else:
                logger.warning(f"Invalid or missing node_id for clear operation: {node_id}")
                return jsonify({"error": "Invalid node ID"}), 400
    return render_template("index.html", bridge_status=bridge_status, mqtt_status=mqtt_status, nodes=known_nodes, enabled_nodes=enabled_nodes, node_info=node_info, last_heard=last_heard)

@app.route("/get_nodes", methods=["GET"])
def get_nodes():
    return jsonify(list(known_nodes.keys()))

@app.route("/node/<node_id>", methods=["GET", "POST"])
def node_view(node_id):
    global node_messages
    if request.method == "POST" and "message" in request.form:
        text = request.form.get("message")
        if text:
            if node_id == "broadcast":
                iface.sendText(text)
            else:
                iface.sendText(text, destinationId=node_id)
            if node_id not in node_messages:
                node_messages[node_id] = []
            node_messages[node_id].append({'time': time.time(), 'type': 'sent', 'message': text})
            logger.info(f"🔔 Sent message to {node_id}: {text}")
            save_state()
    messages = node_messages.get(node_id, [])
    return render_template("node_view.html", node_id=node_id, messages=messages, node_info=node_info.get(node_id, {}))

@app.route("/mqtt_settings", methods=["GET", "POST"])
def mqtt_settings():
    global config, mqtt_client, mqtt_status
    if request.method == "POST":
        config["mqtt_broker"] = request.form["broker"]
        config["mqtt_port"] = int(request.form["port"])
        config["mqtt_username"] = request.form["username"]
        config["mqtt_password"] = request.form["password"]
        config["mqtt_topic_prefix"] = request.form["topic_prefix"]
        save_config(config)
        if mqtt_client:
            try:
                mqtt_client.loop_stop()
                mqtt_client.disconnect()
            except Exception:
                pass
        threading.Thread(target=connect_mqtt, daemon=True).start()
        flash("MQTT settings saved and reconnecting")
        return redirect(url_for("mqtt_settings"))
    return render_template("mqtt_settings.html", config=config, mqtt_status=mqtt_status)

@app.route("/update_nodes", methods=["POST"])
def update_nodes():
    global enabled_nodes
    new_enabled = set(request.form.getlist("enabled_nodes"))
    for node_id in enabled_nodes - new_enabled:
        trigger_ha_discovery(node_id, enable=False)
    for node_id in new_enabled - enabled_nodes:
        trigger_ha_discovery(node_id, enable=True)
    enabled_nodes = new_enabled
    save_state()
    update_event.set()
    return redirect(url_for("index"))

@app.route("/configure_sensor/<node_id>", methods=["GET", "POST"])
def configure_sensor(node_id):
    global custom_sensors, sensor_states, known_nodes
    messages = node_messages.get(node_id, [])
    if request.method == "POST":
        if "remove_sensor" in request.form:
            sensor_name = request.form.get("remove_sensor")
            if node_id in custom_sensors and sensor_name in [s["sensor_name"] for s in custom_sensors[node_id]]:
                # Determine entity_type before modifying custom_sensors
                sensor_config = next((s for s in custom_sensors[node_id] if s["sensor_name"] == sensor_name), None)
                entity_type = "binary_sensor" if sensor_config and sensor_config["value_type"] == "binary" else "sensor"
                if node_id in sensor_states and sensor_name in sensor_states[node_id]:
                    current_state = sensor_states[node_id][sensor_name]
                    if current_state.get("timer"):
                        current_state["timer"].cancel()
                    topic = f"{config['mqtt_topic_prefix']}/{node_id}/custom/{sensor_name}"
                    mqtt_client.publish(topic, "OFF", retain=True)
                    logger.info(f"📊 Removed sensor state for {sensor_name} on {topic}")
                    del sensor_states[node_id][sensor_name]
                custom_sensors[node_id] = [s for s in custom_sensors[node_id] if s["sensor_name"] != sensor_name]
                if not custom_sensors[node_id]:
                    del custom_sensors[node_id]
                # Remove sensor key from known_nodes
                if node_id in known_nodes and sensor_name in known_nodes[node_id]:
                    known_nodes[node_id].discard(sensor_name)
                # Remove HA entity
                node_id_safe = node_id.replace("!", "").replace(".", "_").replace(" ", "_")
                discovery_topic = f"homeassistant/{entity_type}/meshtastic_{node_id_safe}_{sensor_name.lower().replace(' ', '_')}/config"
                state_topic = f"{config['mqtt_topic_prefix']}/{node_id}/custom/{sensor_name}"
                mqtt_client.publish(state_topic, "", retain=True)
                time.sleep(1)  # Allow state change to propagate
                mqtt_client.publish(discovery_topic, "", retain=True)
                logger.info(f"🗑️ Removed HA entity for {node_id}/{sensor_name} from {discovery_topic} and {state_topic}")
                # Remove button only if no sensors remain
                if not custom_sensors.get(node_id, []):
                    button_topic = f"homeassistant/button/meshtastic_{node_id_safe}_refresh_data/config"
                    mqtt_client.publish(button_topic, "", retain=True)
                    logger.info(f"🗑️ Removed HA button for {node_id} from {button_topic}")
                save_state()
            return redirect(url_for("configure_sensor", node_id=node_id))
        sensor_name = request.form.get("sensor_name")
        pattern = request.form.get("pattern")
        topic = request.form.get("topic", "custom")
        device_class = request.form.get("device_class", "sensor")
        value_type = request.form.get("value_type", "numeric")
        delay_off = int(request.form.get("delay_off", 0)) if request.form.get("delay_off") else 0
        if sensor_name and pattern:  # Ensure all required fields are present
            if node_id not in custom_sensors:
                custom_sensors[node_id] = []
            custom_sensors[node_id].append({"sensor_name": sensor_name, "pattern": pattern, "topic": topic, "device_class": device_class, "value_type": value_type, "delay_off": delay_off})
            if sensor_name not in known_nodes.get(node_id, set()):
                known_nodes[node_id].add(sensor_name)
            save_state()
            if node_id in enabled_nodes and mqtt_client:
                entity_type = "binary_sensor" if value_type == "binary" else "sensor"
                publish_ha_discovery(node_id, sensor_name, entity_type)
                logger.info(f"📊 Configured and published custom sensor {sensor_name} for {node_id}")
            return redirect(url_for("configure_sensor", node_id=node_id))
        else:
            logger.warning(f"Invalid sensor configuration for {node_id}: sensor_name={sensor_name}, pattern={pattern}")
            flash("Sensor name and pattern are required.", "error")
    return render_template("configure_sensor.html", node_id=node_id, messages=messages, custom_sensors=custom_sensors.get(node_id, []), node_info=node_info.get(node_id, {}))

@app.route("/configure_command/<node_id>", methods=["GET", "POST"])
def configure_command(node_id):
    global command_sensors, known_nodes
    messages = node_messages.get(node_id, [])
    if request.method == "POST":
        if "remove_command" in request.form:
            command_name = request.form.get("remove_command")
            if node_id in command_sensors and command_name in [c["command_name"] for c in command_sensors[node_id]]:
                command_config = next((c for c in command_sensors[node_id] if c["command_name"] == command_name), None)
                if node_id in known_nodes and command_name in known_nodes[node_id]:
                    known_nodes[node_id].discard(command_name)
                node_id_safe = node_id.replace("!", "").replace(".", "_").replace(" ", "_")
                discovery_topic = f"homeassistant/{('switch' if command_config.get('on_message') and command_config.get('off_message') else 'button')}/meshtastic_{node_id_safe}_{command_name.lower().replace(' ', '_')}/config"
                command_topic = f"{config['mqtt_topic_prefix']}/{node_id}/command/{command_name}"
                mqtt_client.publish(command_topic, "", retain=True)
                time.sleep(1)  # Allow state change to propagate
                mqtt_client.publish(discovery_topic, "", retain=True)
                logger.info(f"🗑️ Removed HA command entity for {node_id}/{command_name} from {discovery_topic}")
                command_sensors[node_id] = [c for c in command_sensors[node_id] if c["command_name"] != command_name]
                if not command_sensors[node_id]:
                    del command_sensors[node_id]
                save_state()
            return redirect(url_for("configure_command", node_id=node_id))
        command_name = request.form.get("command_name")
        single_press = request.form.get("single_press")
        on_message = request.form.get("on_message")
        off_message = request.form.get("off_message")
        if command_name and (single_press or (on_message and off_message)):  # Ensure required fields are present
            if node_id not in command_sensors:
                command_sensors[node_id] = []
            command_sensors[node_id].append({"command_name": command_name, "single_press": single_press if single_press else None, "on_message": on_message, "off_message": off_message})
            if command_name not in known_nodes.get(node_id, set()):
                known_nodes[node_id].add(command_name)
            save_state()
            if node_id in enabled_nodes and mqtt_client:
                entity_type = "switch" if on_message and off_message else "button"
                publish_ha_discovery(node_id, command_name, entity_type)
                logger.info(f"📊 Configured and published command sensor {command_name} for {node_id}")
            return redirect(url_for("configure_command", node_id=node_id))
        else:
            logger.warning(f"Invalid command configuration for {node_id}: command_name={command_name}, single_press={single_press}, on_message={on_message}, off_message={off_message}")
            flash("Command name and either a single press message or both on/off messages are required.", "error")
    return render_template("configure_command.html", node_id=node_id, messages=messages, command_sensors=command_sensors.get(node_id, []), node_info=node_info.get(node_id, {}))

@app.route('/execute_command/<node_id>/<command_name>', methods=['POST'])
def execute_command(node_id, command_name):
    global iface, mqtt_client
    logger.debug(f"Executing command for node {node_id}, command {command_name}")
    if not iface or not hasattr(iface, "sendText"):
        logger.error(f"Meshtastic interface not available for node {node_id}")
        return jsonify({'success': False}), 500
    if not mqtt_client or not mqtt_client.is_connected():
        logger.error(f"MQTT client not connected for node {node_id}")
        return jsonify({'success': False}), 500
    if node_id not in command_sensors:
        logger.warning(f"No command sensors configured for node {node_id}")
        return jsonify({'success': False}), 404
    command_config = next((c for c in command_sensors[node_id] if c["command_name"] == command_name), None)
    if not command_config:
        logger.warning(f"Command {command_name} not found for node {node_id}")
        return jsonify({'success': False}), 404

    data = request.get_json()
    action = data.get('action', 'press')
    text_topic = f"{config['mqtt_topic_prefix']}/{node_id}/text"
    if command_config["single_press"]:
        if action == 'press':
            message = command_config["single_press"]
            try:
                iface.sendText(message, destinationId=node_id)
                mqtt_client.publish(text_topic, message, retain=False)
                logger.info(f"🔔 Sent single press command {command_name} to {node_id}: {message} via text channel {text_topic}")
                if node_id not in node_messages:
                    node_messages[node_id] = []
                node_messages[node_id].append({'time': time.time(), 'type': 'sent', 'message': message})
                save_state()
                return jsonify({'success': True})
            except Exception as e:
                logger.error(f"Failed to send single press command for {node_id}: {e}")
                return jsonify({'success': False}), 500
    elif command_config["on_message"] and command_config["off_message"]:
        if action == 'on':
            message = command_config["on_message"]
            try:
                iface.sendText(message, destinationId=node_id)
                mqtt_client.publish(text_topic, message, retain=False)
                logger.info(f"🔔 Sent on command {command_name} to {node_id}: {message} via text channel {text_topic}")
                state_topic = f"{config['mqtt_topic_prefix']}/{node_id}/command/{command_name}/state"
                mqtt_client.publish(state_topic, message, retain=True)
                if node_id not in node_messages:
                    node_messages[node_id] = []
                node_messages[node_id].append({'time': time.time(), 'type': 'sent', 'message': message})
                save_state()
                return jsonify({'success': True})
            except Exception as e:
                logger.error(f"Failed to send on command for {node_id}: {e}")
                return jsonify({'success': False}), 500
        elif action == 'off':
            message = command_config["off_message"]
            try:
                iface.sendText(message, destinationId=node_id)
                mqtt_client.publish(text_topic, message, retain=False)
                logger.info(f"🔔 Sent off command {command_name} to {node_id}: {message} via text channel {text_topic}")
                state_topic = f"{config['mqtt_topic_prefix']}/{node_id}/command/{command_name}/state"
                mqtt_client.publish(state_topic, message, retain=True)
                if node_id not in node_messages:
                    node_messages[node_id] = []
                node_messages[node_id].append({'time': time.time(), 'type': 'sent', 'message': message})
                save_state()
                return jsonify({'success': True})
            except Exception as e:
                logger.error(f"Failed to send off command for {node_id}: {e}")
                return jsonify({'success': False}), 500
    logger.warning(f"Invalid action {action} for command {command_name} on node {node_id}")
    return jsonify({'success': False}), 400

@app.route('/stream')
def stream():
    def event_stream():
        while True:
            update_event.wait()
            update_event.clear()
            data = {
                'nodes': {k: list(v) for k, v in known_nodes.items()},
                'enabled': list(enabled_nodes),
                'node_info': node_info,
                'last_heard': last_heard
            }
            yield f"data: {json.dumps(data)}\n\n"
            time.sleep(1)  # Small delay to prevent overwhelming the client
    return Response(event_stream(), mimetype="text/event-stream")

@app.route('/toggle/<node_id>', methods=['POST'])
def toggle_node(node_id):
    global enabled_nodes
    data = request.get_json()
    if node_id in known_nodes:
        toggle_state = data.get('toggle_ha', 'off')
        if toggle_state == 'on' and node_id not in enabled_nodes:
            enabled_nodes.add(node_id)
            trigger_ha_discovery(node_id, enable=True)
        elif toggle_state == 'off' and node_id in enabled_nodes:
            enabled_nodes.remove(node_id)
            trigger_ha_discovery(node_id, enable=False)
        last_heard[node_id] = time.time()
        save_state()
        update_event.set()
        return jsonify({'success': True})
    return jsonify({'success': False}), 404

@app.route("/logs", methods=["GET", "POST"])
def logs():
    global config, logger
    log_lines = []
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r") as f:
            log_lines = f.readlines()[-100:]  # Show last 100 lines
    if request.method == "POST":
        new_level = request.form.get("log_level")
        if new_level in LOG_LEVELS:
            config["log_level"] = new_level
            logger.setLevel(LOG_LEVELS[new_level])
            save_config(config)
            flash(f"Logging level set to {new_level}")
            return redirect(url_for("logs"))
    return render_template("logs.html", log_lines=log_lines, current_level=config["log_level"], levels=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])

# -------------------- MAIN ----------------
if __name__ == "__main__":
    # Start Meshtastic connection thread
    threading.Thread(target=meshtastic_thread, daemon=True).start()

    # Start MQTT connection thread
    threading.Thread(target=connect_mqtt, daemon=True).start()

    # Start node info refresher thread
    threading.Thread(target=node_info_refresher, daemon=True).start()

    # Run Flask app
    try:
        app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        if iface:
            iface.close()
        if mqtt_client:
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
        os.remove(LOCK_FILE)
        save_state()
        exit(0)
    except Exception as e:
        logger.error(f"Fatal error in main: {e}")
        if iface:
            iface.close()
        if mqtt_client:
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
        os.remove(LOCK_FILE)
        save_state()
        exit(1)
