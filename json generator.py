"""
report_builder.py
------------------
Assembles the unified vitals JSON report ON DEMAND, by pulling each
sensor module's most recently *stored* reading (via that module's own
load() function, which reads back whatever the last save() call wrote
to disk) and mapping it into the shared report schema.

This module does NOT talk to the ESP32 directly and does NOT trigger a
new sensor read. It only reports what's already been persisted by
ecg_sensor.py / spo2_sensor.py / steth_sensor.py / temp_sensor.py /
urine_sensor.py elsewhere in the pipeline (e.g. main.py, as each frame
comes in over serial and calls that sensor's save()).

Call generate_report() / save_report() whenever the user asks for the
current result -- each call re-reads all five _*_latest.json files
fresh, so it always reflects the latest stored values at call time.

Usage:
    from report_builder import generate_report, save_report

    report = generate_report()   # -> dict built from latest saved readings
    save_report()                # -> writes report to disk, also returns the dict

Command line:
    python report_builder.py                    # save + print
    python report_builder.py --no-save           # print only
    python report_builder.py --out custom.json   # save to a custom path

Any sensor that hasn't reported yet comes back with its fields set to
None (that module's own to_report_fields(None) behavior) rather than
raising, and is listed under "missing_sensors" with "status": "partial".
"""

import os
import json
import argparse
from datetime import datetime, timezone

try:
    from sensors import ecg_sensor, spo2_sensor, steth_sensor, temp_sensor, urine_sensor
except ImportError:
    # Fallback for flat layouts where the sensor modules sit next to this
    # file instead of inside a sensors/ package.
    import ecg_sensor, spo2_sensor, steth_sensor, temp_sensor, urine_sensor

DEVICE_ID = "ESP32_VitalsRig_01"
_OUTPUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vitals_report.json")


def _build_urine_section() -> dict:
    """urine_sensor.to_report_fields() nests red/green/blue under
    urine_color_rgb; flatten it here so it matches the other sections'
    shape. urine_color_class is UNCALIBRATED/advisory -- see
    urine_sensor.classify_color() docstring."""
    reading = urine_sensor.load()
    if reading is None:
        return {"red": None, "green": None, "blue": None, "urine_color_class": None}
    fields = urine_sensor.to_report_fields(reading)
    rgb = fields["urine_color_rgb"]
    return {
        "red": rgb["red"],
        "green": rgb["green"],
        "blue": rgb["blue"],
        "urine_color_class": fields["urine_color_class"],
    }


def generate_report(device_id: str = DEVICE_ID) -> dict:
    """Build the unified vitals report from each sensor module's current
    on-disk reading. Safe to call repeatedly -- it always re-reads fresh."""
    sections = {
        "ecg": ecg_sensor.to_report_fields(ecg_sensor.load()),
        "urine_sensor": _build_urine_section(),
        "stethoscope": steth_sensor.to_report_fields(steth_sensor.load()),
        "temperature": temp_sensor.to_report_fields(temp_sensor.load()),
        "pulse_oximeter": spo2_sensor.to_report_fields(spo2_sensor.load()),
    }

    required_field_by_section = {
        "ecg": "heart_rate_bpm",
        "urine_sensor": "red",
        "stethoscope": "rms",
        "temperature": "body_temp_c",
        "pulse_oximeter": "heart_rate_bpm",
    }
    missing = [
        name for name, key in required_field_by_section.items()
        if sections[name].get(key) is None
    ]

    report = {
        "device_id": device_id,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **sections,
        "status": "complete" if not missing else "partial",
    }
    if missing:
        report["missing_sensors"] = missing

    return report


def save_report(path: str = _OUTPUT_PATH, device_id: str = DEVICE_ID) -> dict:
    """Generate the report and write it to disk as JSON. Returns the dict
    that was written so callers can also print/use it directly."""
    report = generate_report(device_id=device_id)
    with open(path, "w") as f:
        json.dump(report, f, indent=2)
    return report


def _main():
    parser = argparse.ArgumentParser(
        description="Build (and by default save) the unified vitals JSON "
                     "report from the sensor modules' latest stored readings."
    )
    parser.add_argument("--out", "-o", default=_OUTPUT_PATH,
                         help=f"Where to write the report JSON (default: {_OUTPUT_PATH})")
    parser.add_argument("--no-save", action="store_true",
                         help="Only print the report to stdout; don't write it to disk.")
    args = parser.parse_args()

    if args.no_save:
        report = generate_report()
    else:
        report = save_report(path=args.out)
        print(f"Report saved to {args.out}")

    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    _main()
