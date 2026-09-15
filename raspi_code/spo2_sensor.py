"""
spo2_sensor.py
--------------
Handles the MAX30102 pulse-oximeter reading (SENSOR_CODE = "SPO2").

Matches the ESP32 firmware's stepSpo2Sequence() payload exactly:
    {"heart_rate_bpm": <int>, "spo2_percent": <int>, "ir_raw": <int>}

Note: the firmware only sends this packet when heartRateValid AND
spo2Valid are both true (otherwise it sends an ERR packet instead),
so every successfully parsed reading here is already algorithm-validated
on the ESP32 side.

Usage (from main.py):
    from sensors import spo2_sensor

    reading = spo2_sensor.parse(json_payload)
    spo2_sensor.save(reading)
    latest = spo2_sensor.load()
    spo2_sensor.to_report_fields(latest)
"""

import json
import os

SENSOR_CODE = "SPO2"
_STORE_PATH = os.path.join(os.path.dirname(__file__), "_spo2_latest.json")

_REQUIRED_KEYS = ("heart_rate_bpm", "spo2_percent", "ir_raw")


def parse(json_payload: str):
    """
    Parse the raw JSON payload string received between the pipes.
    Returns a dict with heart_rate_bpm/spo2_percent/ir_raw (ints),
    or None if malformed / missing fields.
    """
    try:
        data = json.loads(json_payload)
    except (json.JSONDecodeError, TypeError):
        return None

    if not all(key in data for key in _REQUIRED_KEYS):
        return None

    try:
        for key in _REQUIRED_KEYS:
            data[key] = int(data[key])
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
    field names. Adjust here if your final report schema uses
    different key names than the raw ESP32 fields.
    """
    if reading is None:
        return {"heart_rate_bpm": None, "spo2_percent": None, "ir_raw": None}
    return {
        "heart_rate_bpm": reading.get("heart_rate_bpm"),
        "spo2_percent": reading.get("spo2_percent"),
        "ir_raw": reading.get("ir_raw"),
    }


def is_low_spo2(reading: dict, threshold_pct: int = 92) -> bool:
    """Convenience helper: flags hypoxemia risk below a threshold."""
    if reading is None or reading.get("spo2_percent") is None:
        return False
    return reading["spo2_percent"] < threshold_pct
