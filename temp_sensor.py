"""
temp_sensor.py
--------------
Handles the MLX90614 IR body-temperature reading (SENSOR_CODE = "TEMP").

Matches the ESP32 firmware's stepTempSequence() payload exactly:
    {"body_temp_c": <float, 1 decimal>}

Usage (from main.py):
    from sensors import temp_sensor

    reading = temp_sensor.parse(json_payload)   # -> dict or None
    temp_sensor.save(reading)                   # persist latest reading
    latest = temp_sensor.load()                 # -> dict or None
    temp_sensor.to_report_fields(latest)        # -> dict for unified report
"""

import json
import os

SENSOR_CODE = "TEMP"
_STORE_PATH = os.path.join(os.path.dirname(__file__), "_temp_latest.json")


def parse(json_payload: str):
    """
    Parse the raw JSON payload string received between the pipes.
    Returns a dict like {"body_temp_c": 36.8} or None if malformed.
    """
    try:
        data = json.loads(json_payload)
    except (json.JSONDecodeError, TypeError):
        return None

    if "body_temp_c" not in data:
        return None

    try:
        data["body_temp_c"] = float(data["body_temp_c"])
    except (TypeError, ValueError):
        return None

    return data


def save(reading: dict):
    """Persist the latest valid reading to disk so it survives restarts."""
    if reading is None:
        return
    with open(_STORE_PATH, "w") as f:
        json.dump(reading, f)


def load():
    """Load the last persisted reading, or None if none exists yet."""
    if not os.path.exists(_STORE_PATH):
        return None
    try:
        with open(_STORE_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def to_report_fields(reading: dict):
    """
    Map this sensor's stored reading into the unified JSON report's
    field names. Adjust the key names here if your final report schema
    differs from the raw ESP32 field names.
    """
    if reading is None:
        return {"body_temp_c": None}
    return {"body_temp_c": reading.get("body_temp_c")}


def is_fever(reading: dict, threshold_c: float = 37.5) -> bool:
    """Simple convenience helper: flags fever based on a threshold."""
    if reading is None or reading.get("body_temp_c") is None:
        return False
    return reading["body_temp_c"] >= threshold_c
