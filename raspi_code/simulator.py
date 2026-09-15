import time
import requests
import json
from datetime import datetime
import random

# Point this to your local or Render URL
API_URL = "https://raksha-sim.onrender.com/vitals"

def get_normal_payload():
    return {
        "device_id": "esp32_sim_01",
        "timestamp": datetime.utcnow().isoformat(),
        "ecg": {
            "heart_rate_bpm": random.randint(60, 90),
            "samples": [random.randint(0, 1024) for _ in range(10)]
        },
        "urine_sensor": {
            "red": 255,
            "green": 234,
            "blue": 112
        },
        "stethoscope": {
            "rms": 15,
            "min": 0,
            "max": 30,
            "samples": 100
        },
        "temperature": {
            "body_temp_c": round(random.uniform(36.5, 37.2), 1)
        },
        "pulse_oximeter": {
            "heart_rate_bpm": random.randint(60, 90),
            "spo2_percent": random.randint(95, 100),
            "ir_raw": 1500
        },
        "status": "active",
        "patient_id": "patient_normal",
        "patient_speech_text": "I feel perfectly fine today."
    }

def get_edge_case_payload():
    # SpO2 < 90 and HR > 130 to trigger MEWS "RED" override
    return {
        "device_id": "esp32_sim_02",
        "timestamp": datetime.utcnow().isoformat(),
        "ecg": {
            "heart_rate_bpm": random.randint(135, 150),
            "samples": [random.randint(0, 1024) for _ in range(10)]
        },
        "urine_sensor": {
            "red": 255,
            "green": 234,
            "blue": 112
        },
        "stethoscope": {
            "rms": 15,
            "min": 0,
            "max": 30,
            "samples": 100
        },
        "temperature": {
            "body_temp_c": round(random.uniform(38.5, 39.5), 1)
        },
        "pulse_oximeter": {
            "heart_rate_bpm": random.randint(135, 150),
            "spo2_percent": random.randint(80, 88),
            "ir_raw": 1500
        },
        "status": "critical",
        "patient_id": "patient_edge_case",
        "patient_speech_text": "I feel very dizzy and short of breath."
    }

def run_simulation():
    print(f"Starting simulation targeting: {API_URL}")
    while True:
        try:
            # 1. Post a normal payload
            print("\n--- Sending NORMAL Payload ---")
            normal_data = get_normal_payload()
            res = requests.post(API_URL, json=normal_data)
            print(f"Status Code: {res.status_code}")
            print(f"Response: {res.json()}")
            time.sleep(2)

            # 2. Post an edge-case payload (MEWS Override)
            print("\n--- Sending EDGE-CASE (RED) Payload ---")
            edge_data = get_edge_case_payload()
            res = requests.post(API_URL, json=edge_data)
            print(f"Status Code: {res.status_code}")
            print(f"Response: {res.json()}")
            time.sleep(2)

        except requests.exceptions.ConnectionError:
            print(f"Connection Error: Is the API running at {API_URL}?")
            time.sleep(5)
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(2)

if __name__ == "__main__":
    run_simulation()
