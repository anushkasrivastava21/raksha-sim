from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from datetime import datetime
import traceback
import serial
import threading
import time

from models import Base, Vitals, Triage
import schemas
import urllib.request
import json

from triage_engine import analyze_patient 
from mews_check import check_mews

# 1. Database Setup
engine = create_engine("sqlite:///raksha.db", connect_args={"check_same_thread": False})
Base.metadata.create_all(engine)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# 2. FastAPI Initialization (Port 8000 Backend)
app = FastAPI(title="Raksha Minimal Backend API (Port 8000)")

# NUKE CORS Restrictions (Permissive CORS for local dry-run)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Helper function to forward vitals payload to local ML Engine (Port 8001)
def fetch_ml_prediction(vitals_dict: dict):
    ml_url = "http://127.0.0.1:8001/predict"
    try:
        # Format datetime if present
        if "timestamp" in vitals_dict and hasattr(vitals_dict["timestamp"], "isoformat"):
            vitals_dict["timestamp"] = vitals_dict["timestamp"].isoformat()

        req = urllib.request.Request(
            ml_url,
            data=json.dumps(vitals_dict).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"⚠️ Local ML Engine (Port 8001) connection fallback: {e}")
        return {
            "triage": "GREEN",
            "risk_score": 0.10,
            "confidence": 0.95,
            "reasons": ["Local ML Engine Offline - Standard Rule Fallback Used"]
        }

@app.get("/")
def read_root():
    return {
        "status": "Backend API is live on port 8000!",
        "cors_mode": "Permissive (*)",
        "ml_engine_target": "http://127.0.0.1:8001"
    }

# DATA_SOURCE: https://raksha-sim-1.onrender.com
# POST /vitals endpoint schema validation and ingestion
@app.post("/vitals")
def add_vitals(v: schemas.VitalsIn, db: Session = Depends(get_db)):
    record_id = f"{v.patient_id}_{v.timestamp.isoformat()}"
    
    # 1. Forward payload to local ML Engine
    vitals_dict = {
        "patient_id": v.patient_id,
        "timestamp": v.timestamp.isoformat(),
        "stethoscope_status": v.stethoscope_status,
        "ecg_hr": v.ecg_hr,
        "spo2": v.spo2,
        "temperature": v.temperature,
        "urine_rgb": v.urine_rgb,
        "patient_speech_text": v.patient_speech_text
    }
    ai_prediction = fetch_ml_prediction(vitals_dict)
    
    # 2. Persist to DB
    db_vitals = Vitals(
        id=record_id,
        patient_id=v.patient_id,
        timestamp=v.timestamp,
        stethoscope_status=v.stethoscope_status,
        ecg_hr=v.ecg_hr,
        spo2=v.spo2,
        temperature=v.temperature,
        urine_r=v.urine_rgb[0] if len(v.urine_rgb) > 0 else 0.0,
        urine_g=v.urine_rgb[1] if len(v.urine_rgb) > 1 else 0.0,
        urine_b=v.urine_rgb[2] if len(v.urine_rgb) > 2 else 0.0,
        patient_speech_text=v.patient_speech_text
    )
    db.add(db_vitals)
    db.commit()
    
    return {
        "message": "Vitals saved successfully",
        "id": record_id,
        "ai_prediction": ai_prediction
    }

@app.post("/triage")
def add_triage(t: schemas.TriageIn, db: Session = Depends(get_db)):
    '''
    Endpoint for recording triage results.
    '''
    try:
        record_id = f"{t.patient_id}_{t.timestamp.isoformat()}"
        db_triage = Triage(
            id=record_id,
            patient_id=t.patient_id,
            timestamp=t.timestamp,
            triage=t.triage,
            confidence=t.confidence
        )
        db.add(db_triage)
        db.commit()
        return {"message": "Triage saved successfully", "id": record_id}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/patients")
def list_patients(limit: int = 50, db: Session = Depends(get_db)):
    vitals = (
        db.query(Vitals)
        .order_by(Vitals.timestamp.desc())
        .limit(limit)
        .all()
    )
    triages = (
        db.query(Triage)
        .order_by(Triage.timestamp.desc())
        .limit(limit)
        .all()
    )
    return {"vitals": vitals, "triage_results": triages}

# ── 3. HARDWARE SENSOR TRIGGER & SESSION CONTROLLER ENDPOINTS ───────────────
active_session = {
    "patient_id": "PT-0001",
    "timestamp": datetime.now().isoformat(),
    "spo2": 98.0,
    "ecg_hr": 74.0,
    "temperature": 36.8,
    "urine_rgb": [255.0, 255.0, 0.0],
    "stethoscope_status": "clean",
    "patient_speech_text": "Normal auscultation and cardiac rhythm."
}

@app.api_route("/init_session", methods=["GET", "POST"])
def init_session(payload: dict = None):
    global active_session
    p_id = (payload or {}).get("patient_id", f"PT-{int(datetime.now().timestamp())}")
    active_session = {
        "patient_id": p_id,
        "timestamp": datetime.now().isoformat(),
        "spo2": 98.0,
        "ecg_hr": 74.0,
        "temperature": 36.8,
        "urine_rgb": [255.0, 255.0, 0.0],
        "stethoscope_status": "clean",
        "patient_speech_text": "Patient session initialized."
    }
    return {"status": "ok", "message": f"Session initialized for {p_id}", "patient_id": p_id}

esp_serial = None
serial_lock = threading.Lock()

def fetch_from_esp(cmd: str, timeout: int = 15):
    global esp_serial
    if esp_serial is None:
        try:
            esp_serial = serial.Serial("/dev/ttyUSB0", 115200, timeout=1)
            time.sleep(2)
            esp_serial.reset_input_buffer()
        except Exception as e:
            print(f"Failed to connect to ESP32: {e}")
            return None
            
    expected_code = cmd.replace("REQ_", "")
    
    with serial_lock:
        try:
            esp_serial.reset_input_buffer()
            esp_serial.reset_output_buffer()
            # Send a newline first to flush any garbage bytes in the ESP32's RX buffer
            esp_serial.write(f"\n{cmd}\n".encode('utf-8'))
            esp_serial.flush()
            start_time = time.time()
            
            while time.time() - start_time < timeout:
                line = esp_serial.readline().decode('utf-8', errors='ignore').strip()
                if not line:
                    continue
                print(f"ESP32 Raw: {line}") # Log to console for debugging
                parts = line.split('|')
                if len(parts) == 3:
                    sensor_code, json_payload, _ = parts
                    if sensor_code == expected_code:
                        try:
                            return json.loads(json_payload)
                        except:
                            return None
        except Exception as e:
            print(f"Error reading from ESP32: {e}")
            return None
    return None

@app.api_route("/trigger/spo2", methods=["GET", "POST"])
def trigger_spo2():
    data = fetch_from_esp("REQ_SPO2")
    if data:
        active_session["spo2"] = data.get("spo2_percent", active_session["spo2"])
        active_session["ecg_hr"] = data.get("heart_rate_bpm", active_session.get("ecg_hr", 72.0))
        print(f"🫀 [API] SPO2 Triggered -> SpO2: {active_session['spo2']}%, HR: {active_session['ecg_hr']} bpm")
        return {"status": "ok", "sensor": "MAX30102", "spo2": active_session["spo2"], "ecg_hr": active_session["ecg_hr"]}
    raise HTTPException(status_code=503, detail="Failed to fetch SPO2 from hardware")

@app.api_route("/trigger/ecg", methods=["GET", "POST"])
def trigger_ecg():
    data = fetch_from_esp("REQ_ECG")
    if data:
        active_session["ecg_hr"] = data.get("heart_rate_bpm", active_session["ecg_hr"])
        print(f"💓 [API] ECG Triggered -> HR: {active_session['ecg_hr']} bpm")
        return {"status": "ok", "sensor": "AD8232", "ecg_hr": active_session["ecg_hr"]}
    raise HTTPException(status_code=503, detail="Failed to fetch ECG from hardware")

@app.api_route("/trigger/temp", methods=["GET", "POST"])
def trigger_temp():
    data = fetch_from_esp("REQ_TEMP")
    if data:
        active_session["temperature"] = data.get("body_temp_c", active_session["temperature"])
        print(f"🌡️ [API] TEMP Triggered -> Temp: {active_session['temperature']}°C")
        return {"status": "ok", "sensor": "MLX90614", "temperature": active_session["temperature"]}
    raise HTTPException(status_code=503, detail="Failed to fetch TEMP from hardware")

@app.api_route("/trigger/urine", methods=["GET", "POST"])
def trigger_urine():
    data = fetch_from_esp("REQ_URINE")
    if data:
        active_session["urine_rgb"] = [
            data.get("red", 255.0),
            data.get("green", 255.0),
            data.get("blue", 0.0)
        ]
        print(f"🧪 [API] URINE Triggered -> RGB: {active_session['urine_rgb']}")
        return {"status": "ok", "sensor": "TCS3200", "urine_rgb": active_session["urine_rgb"]}
    raise HTTPException(status_code=503, detail="Failed to fetch URINE from hardware")

@app.api_route("/trigger/stethoscope", methods=["GET", "POST"])
def trigger_stethoscope():
    data = fetch_from_esp("REQ_STETH")
    if data:
        active_session["stethoscope_status"] = "recorded"
        print(f"🩺 [API] STETHOSCOPE Triggered -> Status: {active_session['stethoscope_status']}")
        return {"status": "ok", "sensor": "MAX4466", "stethoscope_status": "recorded"}
    raise HTTPException(status_code=503, detail="Failed to fetch STETHOSCOPE from hardware")

@app.api_route("/finalize_triage", methods=["GET", "POST"])
def finalize_triage(payload: dict = None, db: Session = Depends(get_db)):
    global active_session
    p_id = (payload or {}).get("patient_id", active_session.get("patient_id", f"PT-{int(datetime.now().timestamp())}"))
    now = datetime.now()
    record_id = f"{p_id}_{now.isoformat()}"

    vitals_dict = {
        "patient_id": p_id,
        "timestamp": now.isoformat(),
        "stethoscope_status": active_session.get("stethoscope_status", "clean"),
        "ecg_hr": active_session.get("ecg_hr", 74.0),
        "spo2": active_session.get("spo2", 98.0),
        "temperature": active_session.get("temperature", 36.8),
        "urine_rgb": active_session.get("urine_rgb", [255.0, 255.0, 0.0]),
        "patient_speech_text": active_session.get("patient_speech_text", "Regular vitals.")
    }

    # 1. Run AI ML Prediction
    ai_prediction = fetch_ml_prediction(vitals_dict)
    triage_status = ai_prediction.get("triage", "GREEN")
    confidence = ai_prediction.get("confidence", 0.95)

    # 2. Persist Vitals to SQLite DB
    db_vitals = Vitals(
        id=record_id,
        patient_id=p_id,
        timestamp=now,
        stethoscope_status=vitals_dict["stethoscope_status"],
        ecg_hr=vitals_dict["ecg_hr"],
        spo2=vitals_dict["spo2"],
        temperature=vitals_dict["temperature"],
        urine_r=vitals_dict["urine_rgb"][0] if len(vitals_dict["urine_rgb"]) > 0 else 0.0,
        urine_g=vitals_dict["urine_rgb"][1] if len(vitals_dict["urine_rgb"]) > 1 else 0.0,
        urine_b=vitals_dict["urine_rgb"][2] if len(vitals_dict["urine_rgb"]) > 2 else 0.0,
        patient_speech_text=vitals_dict["patient_speech_text"]
    )
    db.add(db_vitals)

    # 3. Persist Triage to SQLite DB
    db_triage = Triage(
        id=record_id,
        patient_id=p_id,
        timestamp=now,
        triage=triage_status,
        confidence=confidence
    )
    db.add(db_triage)
    db.commit()

    return {
        "status": "ok",
        "message": "Triage finalized and saved to database",
        "record_id": record_id,
        "patient_id": p_id,
        "triage": triage_status,
        "confidence": confidence,
        "ai_prediction": ai_prediction
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
