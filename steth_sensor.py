"""
steth_sensor.py
---------------
Handles the MAX4466 "stethoscope" mic reading (SENSOR_CODE = "STETH").

Matches the ESP32 firmware's stepStethSequence() payload exactly:
    {"rms": <int>, "min": <int>, "max": <int>, "samples": <int count>}

NOTE: firmware currently takes 50 samples, 1/sec, no inhale/exhale
prompts (per DESIGN NOTES #1) -- NOT the older "20 readings in a loop"
spec. This module just stores/forwards whatever the ESP32 sends, so it
will work unchanged if you revert that behavior later.

Usage (from main.py):
    from sensors import steth_sensor

    reading = steth_sensor.parse(json_payload)
    steth_sensor.save(reading)
    latest = steth_sensor.load()
    steth_sensor.to_report_fields(latest)
"""

import json
import os

SENSOR_CODE = "STETH"
_STORE_PATH = os.path.join(os.path.dirname(__file__), "_steth_latest.json")

_REQUIRED_KEYS = ("rms", "min", "max", "samples")


def parse(json_payload: str):
    """
    Parse the raw JSON payload string received between the pipes.
    Returns a dict {"rms": int, "min": int, "max": int, "samples": int}
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
    field names.
    """
    if reading is None:
        return {"rms": None, "min": None, "max": None, "samples": None}
    return {
        "rms": reading.get("rms"),
        "min": reading.get("min"),
        "max": reading.get("max"),
        "samples": reading.get("samples"),
    }


def signal_range(reading: dict):
    """Convenience helper: peak-to-peak amplitude (max - min)."""
    if reading is None or reading.get("max") is None or reading.get("min") is None:
        return None
    return reading["max"] - reading["min"]
