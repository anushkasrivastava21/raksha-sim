import serial
import json
import time
import requests
from datetime import datetime
from triage_integrator import predict_final_triage

# --- CONFIGURATION ---
SERIAL_PORT = "/dev/ttyACM0" 
BAUD_RATE = 115200 
TIMEOUT_SECONDS = 75

# Set this to True if you want to push data to the local FastAPI backend
POST_TO_BACKEND = True
BACKEND_URL = "http://127.0.0.1:8000/vitals"

def main():
    print(f"Connecting to ESP32 on {SERIAL_PORT}...")
    
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
        time.sleep(2) 
        ser.reset_input_buffer()
        print("Connected successfully!\n")
    except Exception as e:
        print(f"Failed to connect to ESP32: {e}")
        return

    # Ping to check connection
    ser.write(b"PING\n")
    start = time.time()
    ping_ok = False
    while time.time() - start < 5:
        line = ser.readline().decode('utf-8', errors='ignore').strip()
        if line == "PONG":
            ping_ok = True
            break
    
    if ping_ok:
        print("Ping successful.\n")
    else:
        print("Ping failed or timed out. Continuing anyway...\n")

    # This packet will accumulate all the sensor readings
    sensor_packet = {
        "step_1_audio": "RECORD_MIC",  # Triggers live recording in triage_integrator
        "patient_speech_text": ""
    }

    commands = ["REQ_TEMP", "REQ_URINE", "REQ_ECG", "REQ_SPO2", "REQ_STETH"]

    for cmd in commands:
        print(f"--- Sending: {cmd} ---")
        ser.write(f"{cmd}\n".encode('utf-8'))
        
        try:
            start_time = time.time()
            while True:
                if time.time() - start_time > TIMEOUT_SECONDS:
                    raise serial.SerialTimeoutException("Request timed out.")
                    
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if not line:
                    continue
                    
                print(f"Raw Serial: {line}")
                
                parts = line.split('|')
                if len(parts) == 3:
                    sensor_code, json_payload, received_crc = parts
                    
                    if sensor_code == "ERR":
                        print(f"ESP32 reported an error: {json_payload}\n")
                    else:
                        parsed_json = json.loads(json_payload)
                        print(f"Parsed Data: {json.dumps(parsed_json, indent=2)}\n")
                        
                        # Map to expected keys for triage_integrator
                        if sensor_code == "TEMP":
                            sensor_packet['temperature'] = parsed_json
                        elif sensor_code == "URINE":
                            sensor_packet['urine_sensor'] = parsed_json
                        elif sensor_code == "ECG":
                            sensor_packet['ecg'] = parsed_json
                        elif sensor_code == "SPO2":
                            sensor_packet['pulse_oximeter'] = parsed_json
                        elif sensor_code == "STETH":
                            sensor_packet['stethoscope'] = parsed_json
                            
                    break # Move to next command
                    
        except serial.SerialTimeoutException:
            print(f"Request {cmd} timed out. The ESP32 took too long to respond.\n")
        except Exception as e:
            print(f"Communication error on {cmd}: {e}\n")
            
        time.sleep(2) 

    ser.close()
    print("ESP32 Sensor Collection Complete.\n")
    
    print("==================================================")
    print("Starting ML Triage Analysis...")
    print("==================================================")
    
    try:
        triage_result = predict_final_triage(sensor_packet)
        print("\n--- FINAL TRIAGE RESULT ---")
        print(json.dumps(triage_result, indent=2))
        
        if POST_TO_BACKEND:
            # Construct payload for FastAPI backend
            payload = {
                "patient_id": "P-RASPI-LIVE",
                "timestamp": datetime.utcnow().isoformat(),
                "stethoscope_status": "Normal" if not "stethoscope" in sensor_packet else "Recorded",
                "ecg_hr": sensor_packet.get("ecg", {}).get("heart_rate_bpm", 75),
                "spo2": sensor_packet.get("pulse_oximeter", {}).get("spo2_percent", 98.0),
                "temperature": sensor_packet.get("temperature", {}).get("body_temp_c", 37.0),
                "urine_rgb": [
                    sensor_packet.get("urine_sensor", {}).get("red", 255.0),
                    sensor_packet.get("urine_sensor", {}).get("green", 234.0),
                    sensor_packet.get("urine_sensor", {}).get("blue", 112.0)
                ],
                "patient_speech_text": " ".join(triage_result.get("symptom_list", []))
            }
            
            print("\nPushing to local backend...")
            resp = requests.post(BACKEND_URL, json=payload)
            if resp.status_code == 200:
                print("Successfully synced with backend API!")
            else:
                print(f"Backend returned status {resp.status_code}: {resp.text}")
                
    except Exception as e:
        print(f"\nError running triage ML pipeline: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
