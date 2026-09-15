import requests

base_url = "https://raksha-api-7ie6.onrender.com"

print("--- 1. Testing /docs ---")
try:
    r_docs = requests.get(f"{base_url}/docs", timeout=10)
    print("Status:", r_docs.status_code)
except Exception as e:
    print("Error:", e)

print("\n--- 2. Testing /patients?limit=50 ---")
try:
    r_pat = requests.get(f"{base_url}/patients?limit=50", timeout=10)
    print("Status:", r_pat.status_code)
    data = r_pat.json()
    print("Vitals Count:", len(data.get("vitals", [])))
except Exception as e:
    print("Error:", e)

print("\n--- 3. Testing Garbled Packet ---")
garbled = {"device_id": "test", "status": "abnormal"} # Missing ecg, temp, etc.
try:
    r_garb = requests.post(f"{base_url}/vitals", json=garbled, timeout=10)
    print("Status:", r_garb.status_code)
    print("Response:", r_garb.text[:200])
except Exception as e:
    print("Error:", e)

print("\n--- 4. Testing Disconnected Sensor (Zeros/Nulls) ---")
disconnected = {
    "device_id": "test_disconnected",
    "timestamp": "2023-10-01T12:00:00",
    "ecg": {"heart_rate_bpm": 0, "samples": []},
    "urine_sensor": {"red": 0, "green": 0, "blue": 0},
    "stethoscope": {"rms": 0, "min": 0, "max": 0, "samples": 0},
    "temperature": {"body_temp_c": 0.0},
    "pulse_oximeter": {"heart_rate_bpm": 0, "spo2_percent": 0, "ir_raw": 0},
    "status": "abnormal",
    "bp_sys": 0,
    "bp_dia": 0
}
try:
    r_disc = requests.post(f"{base_url}/vitals", json=disconnected, timeout=10)
    print("Status:", r_disc.status_code)
    print("Response:", r_disc.text[:200])
except Exception as e:
    print("Error:", e)
