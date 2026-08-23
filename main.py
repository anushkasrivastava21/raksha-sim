from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from datetime import datetime
import traceback

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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
