import time
import random
import requests
import argparse
from datetime import datetime
from triage_engine import analyze_patient

def generate_vitals_packet(inject_red_flag=False):
    """
    Generates a mock sensor packet matching the nested VitalsIn schema.
    """
    # Base normal values
    hr = random.randint(60, 90)
    spo2 = random.randint(95, 100)
    temp = round(random.uniform(36.1, 37.2), 1)
    bp_sys = random.randint(110, 120)
    bp_dia = random.randint(70, 80)
    speech = "I am feeling okay"
    urine = {"red": 255, "green": 234, "blue": 112} # Normal pale yellow

    if inject_red_flag:
        # Inject abnormal values
        hr = random.randint(110, 140)
        spo2 = random.randint(85, 90)
        temp = round(random.uniform(38.5, 40.0), 1)
        bp_sys = random.randint(150, 180)
        speech = "I have severe chest pain and feel dizzy"
        urine = {"red": 150, "green": 50, "blue": 20} # Dark/abnormal

    # Match the nested VitalsIn schema
    packet = {
        "device_id": "Sim_RaspberryPi_01",
        "timestamp": datetime.now(datetime.timezone.utc).isoformat(),
        "ecg": {
            "heart_rate_bpm": hr,
            "samples": [random.randint(1800, 2000) for _ in range(50)]
        },
        "urine_sensor": urine,
        "stethoscope": {
            "rms": random.randint(200, 300),
            "min": random.randint(1400, 1600),
            "max": random.randint(2400, 2600),
            "samples": 300
        },
        "temperature": {
            "body_temp_c": temp
        },
        "pulse_oximeter": {
            "heart_rate_bpm": hr,
            "spo2_percent": spo2,
            "ir_raw": 135000
        },
        "status": "complete",
        "bp_sys": bp_sys,
        "bp_dia": bp_dia,
        "patient_speech_text": speech
    }
    return packet

def run_simulator(host, port, interval, iterations):
    base_url = f"http://{host}:{port}"
    count = 0
    
    while True:
        if iterations > 0 and count >= iterations:
            break
            
        count += 1
        inject_red_flag = (count % 5 == 0) # Inject anomaly every 5 iterations
        
        print(f"\n--- Iteration {count} ---")
        packet = generate_vitals_packet(inject_red_flag)
        
        # 1. Local AI inference
        print("Running local triage engine...")
        try:
            ai_result = analyze_patient(packet)
            triage_color = ai_result["triage"]
            confidence = ai_result["confidence"]
            print(f"Result: {triage_color} (Conf: {confidence})")
        except Exception as e:
            print(f"Model inference failed: {e}")
            triage_color = "Unknown"
            confidence = 0.0

        # 2. POST to /vitals
        print("POSTing to /vitals...")
        try:
            r = requests.post(f"{base_url}/vitals", json=packet)
            r.raise_for_status()
            print("Successfully posted vitals.")
        except requests.exceptions.RequestException as e:
            print(f"Failed to post vitals: {e}")

        # 3. POST to /triage
        if triage_color != "Unknown":
            print("POSTing to /triage...")
            triage_payload = {
                "patient_id": packet["device_id"],
                "timestamp": packet["timestamp"],
                "triage": triage_color,
                "confidence": confidence
            }
            try:
                r = requests.post(f"{base_url}/triage", json=triage_payload)
                r.raise_for_status()
                print("Successfully posted triage result.")
            except requests.exceptions.RequestException as e:
                print(f"Failed to post triage: {e}")

        if iterations <= 0 or count < iterations:
            print(f"Sleeping for {interval} seconds...")
            time.sleep(interval)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Raksha Hardware Simulator")
    parser.add_argument("--host", default="localhost", help="API Host")
    parser.add_argument("--port", default=8000, type=int, help="API Port")
    parser.add_argument("--interval", default=5, type=int, help="Interval between sends (seconds)")
    parser.add_argument("--iterations", default=0, type=int, help="Number of iterations (0 for infinite)")
    
    args = parser.parse_args()
    run_simulator(args.host, args.port, args.interval, args.iterations)
