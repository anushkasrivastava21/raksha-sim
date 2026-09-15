import requests
import json

base_url = "https://raksha-sim.onrender.com/vitals"

# Garbled packet (missing all nested schemas, wrong types)
garbled_payload = {
    "device_id": "test_garbled",
    # Missing timestamp entirely
    "ecg": "this is a string, not a dict",
    # Missing urine_sensor, stethoscope, temperature, pulse_oximeter
    "status": "abnormal"
}

try:
    print("Testing Garbled Packet...")
    r = requests.post(base_url, json=garbled_payload, timeout=10)
    print("Status Code:", r.status_code)
    print("Response:", r.json())
except Exception as e:
    print("Error:", e)
