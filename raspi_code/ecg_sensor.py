"""
ecg_sensor.py
-------------
Handles the AD8232 ECG reading (SENSOR_CODE = "ECG").

Matches the ESP32 firmware's stepEcgSequence() payload exactly:
    {"heart_rate_bpm": <int>, "samples": [<int> x ECG_NUM_SAMPLES]}

NOTE (carried over from the firmware's own design notes): the BPM value
is a placeholder peak-counter derived from only 20 raw ADC samples taken
20ms apart (~0.4s window total) -- it is NOT clinically robust. This
module does not attempt to "fix" that on the RPi side; it just stores
and forwards what the ESP32 computed. If you swap in the MAX30102's own
BPM later, do that at the report-assembly layer, not here.

Usage (from main.py):
    from sensors import ecg_sensor

    reading = ecg_sensor.parse(json_payload)
    ecg_sensor.save(reading)
    latest = ecg_sensor.load()
    ecg_sensor.to_report_fields(latest)
"""

import json
import os

SENSOR_CODE = "ECG"
_STORE_PATH = os.path.join(os.path.dirname(__file__), "_ecg_latest.json")

EXPECTED_SAMPLE_COUNT = 20  # must match ECG_NUM_SAMPLES in the firmware


def parse(json_payload: str):
    """
    Parse the raw JSON payload string received between the pipes.
    Returns a dict {"heart_rate_bpm": int, "samples": [int, ...]}
    or None if malformed / missing fields.
    """
    try:
        data = json.loads(json_payload)
    except (json.JSONDecodeError, TypeError):
        return None

    if "heart_rate_bpm" not in data or "samples" not in data:
        return None

    samples = data["samples"]
    if not isinstance(samples, list) or len(samples) == 0:
        return None

    try:
        data["heart_rate_bpm"] = int(data["heart_rate_bpm"])
        data["samples"] = [int(s) for s in samples]
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
    field names. By default the raw sample array is included; set
    include_samples=False at the call site's discretion if your final
    report only wants the summary BPM.
    """
    if reading is None:
        return {"heart_rate_bpm": None, "samples": []}
    return {
        "heart_rate_bpm": reading.get("heart_rate_bpm"),
        "samples": reading.get("samples", []),
    }


def sample_count_ok(reading: dict) -> bool:
    """Sanity check: did we actually get the expected number of samples?"""
    if reading is None:
        return False
    return len(reading.get("samples", [])) == EXPECTED_SAMPLE_COUNT
