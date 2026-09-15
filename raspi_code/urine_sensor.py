"""
urine_sensor.py

Parses, stores, and reports readings from the TCS3200 color sensor
("URINE" sensor) sent by the ESP32 vitals rig.

Wire payload (JSON, carried inside the "<SENSOR_CODE>|<json>|<CRC8>" frame
defined in the firmware / rpi_file_rpi_to_esp.py):

    {"red": int, "green": int, "blue": int}

Follows the same module pattern as temp_sensor.py / spo2_sensor.py /
ecg_sensor.py / steth_sensor.py: parse() -> save() -> load() ->
to_report_fields(), plus one sensor-specific convenience helper.

OPEN ITEM (unconfirmed by user, per handoff notes):
    classify_color() below is UNWIRED / UNCALIBRATED. The red/green/blue
    ratio thresholds are placeholder guesses, not derived from real
    TCS3200 calibration data on this rig (sensor unit, LED brightness,
    sample distance, ambient light all affect raw readings). Do not
    treat its output as clinically meaningful until it's been calibrated
    against known reference samples.
"""

import os
import json

SENSOR_CODE = "URINE"
_STORE_PATH = os.path.join(os.path.dirname(__file__), "_urine_latest.json")


def parse(json_payload: str) -> dict | None:
    """Validate + type-cast the URINE sensor JSON payload.

    Expected shape: {"red": int, "green": int, "blue": int}
    Returns a cleaned reading dict, or None if the payload is invalid.
    """
    try:
        data = json.loads(json_payload)
    except (json.JSONDecodeError, TypeError):
        return None

    required_fields = ("red", "green", "blue")
    if not isinstance(data, dict) or not all(f in data for f in required_fields):
        return None

    try:
        red = int(data["red"])
        green = int(data["green"])
        blue = int(data["blue"])
    except (TypeError, ValueError):
        return None

    # TCS3200 raw channel counts are non-negative.
    if red < 0 or green < 0 or blue < 0:
        return None

    return {"red": red, "green": green, "blue": blue}


def save(reading: dict):
    """Persist the latest URINE reading to disk as JSON."""
    with open(_STORE_PATH, "w") as f:
        json.dump(reading, f)


def load() -> dict | None:
    """Reload the last saved URINE reading, if any."""
    if not os.path.exists(_STORE_PATH):
        return None
    try:
        with open(_STORE_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def classify_color(reading: dict) -> str:
    """Rough color-band classification from raw RGB channel counts.

    *** UNWIRED / UNCALIBRATED — flagged open item, not user-confirmed. ***
    Thresholds are generic placeholder guesses only. They have not been
    calibrated against this rig's actual sensor output and should be
    treated as advisory/experimental only, never as a diagnostic signal.

    Returns one of: "pale", "normal", "deep", "reddish", "unknown"
    """
    red = reading.get("red", 0)
    green = reading.get("green", 0)
    blue = reading.get("blue", 0)
    total = red + green + blue

    if total <= 0:
        return "unknown"

    # Normalize so classification is less sensitive to overall brightness
    # (distance from sample, LED intensity, etc.) than raw counts would be.
    r_ratio = red / total
    g_ratio = green / total
    b_ratio = blue / total

    # --- placeholder thresholds; UNCALIBRATED, guesses only ---
    if r_ratio > 0.50 and g_ratio > 0.30 and b_ratio < 0.15:
        return "reddish"
    if r_ratio > 0.45 and g_ratio > 0.40 and b_ratio < 0.10:
        return "deep"
    if r_ratio < 0.40 and g_ratio < 0.40 and b_ratio > 0.20:
        return "pale"
    if r_ratio > 0.35 and g_ratio > 0.30:
        return "normal"

    return "unknown"


def to_report_fields(reading: dict) -> dict:
    """Map a URINE reading into the unified vitals report schema."""
    return {
        "urine_color_rgb": {
            "red": reading["red"],
            "green": reading["green"],
            "blue": reading["blue"],
        },
        # advisory only -- see classify_color() docstring / open items
        "urine_color_class": classify_color(reading),
    }
