import requests
import json
import time
from datetime import datetime

base_url = "https://raksha-sim.onrender.com/vitals"

# Normal packet
normal_payload = {
    "device_id": "test_normal",
    "timestamp": datetime.utcnow().isoformat(),
    "ecg": {"heart_rate_bpm": 75, "samples": []},
    "urine_sensor": {"red": 255, "green": 234, "blue": 112},
    "stethoscope": {"rms": 0, "min": 0, "max": 0, "samples": 0},
    "temperature": {"body_temp_c": 37.0},
    "pulse_oximeter": {"heart_rate_bpm": 75, "spo2_percent": 98, "ir_raw": 0},
    "status": "normal",
    "bp_sys": 120,
    "bp_dia": 80
}

# Abnormal packet (SpO2 80)
abnormal_payload = {
    "device_id": "test_abnormal",
    "timestamp": datetime.utcnow().isoformat(),
    "ecg": {"heart_rate_bpm": 75, "samples": []},
    "urine_sensor": {"red": 255, "green": 234, "blue": 112},
    "stethoscope": {"rms": 0, "min": 0, "max": 0, "samples": 0},
    "temperature": {"body_temp_c": 37.0},
    "pulse_oximeter": {"heart_rate_bpm": 75, "spo2_percent": 80, "ir_raw": 0},
    "status": "abnormal"
}

try:
    print("Testing Normal Packet...")
    r1 = requests.post(base_url, json=normal_payload, timeout=10)
    print("Normal Response:", r1.json())
    
    print("Testing Abnormal Packet...")
    r2 = requests.post(base_url, json=abnormal_payload, timeout=10)
    print("Abnormal Response:", r2.json())
except Exception as e:
    print("Error:", e)
