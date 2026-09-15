"""Raksha ML Engine (port 8001) - real inference, no hardcoded outputs.

Previously /predict returned fixed confidences (0.88/0.96) and risk scores
(0.85/0.12) from four if-statements. Now every value comes from:
  * the trained XGBoost triage model (exact rule evaluation of triage_xgboost.json)
  * urine_cnn.tflite / ecg_cnn.tflite when their model cards allow it
  * the lexicon symptom extractor on patient_speech_text
  * a named safety override (the old thresholds, now in config.py)
See triage_service.py.

Response keeps the old fields (triage, risk_score, confidence, is_abnormal,
reasons, model_version) and adds decided_by, probabilities, ecg, urine,
symptoms, imputed. NOTE: triage can now also be "YELLOW" - check the
dashboard handles it (case-sensitive, upper-case as before).

    pip install fastapi uvicorn numpy scipy ai-edge-litert
    uvicorn ml_engine:app --port 8001
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import triage_service

log = logging.getLogger("ml_engine")
app = FastAPI(title="Raksha AI ML Engine (Port 8001)")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # tighten to the dashboard origin before deployment
    allow_credentials=False,      # "*" with credentials is rejected by browsers anyway
    allow_methods=["*"],
    allow_headers=["*"],
)


class VitalsIn(BaseModel):
    patient_id: str
    timestamp: Optional[datetime] = None
    # None means "not measured". The old defaults (HR 72, SpO2 98, ...) silently
    # turned a missing sensor into a healthy reading.
    ecg_hr: Optional[float] = Field(None, ge=0, le=350)
    bp_systolic: Optional[float] = Field(None, ge=0, le=350)
    bp_diastolic: Optional[float] = Field(None, ge=0, le=250)
    spo2: Optional[float] = Field(None, ge=0, le=100)
    temperature: Optional[float] = Field(None, ge=20, le=46)
    urine_rgb: Optional[List[float]] = None
    ecg_samples: Optional[List[float]] = None
    stethoscope_status: Optional[str] = None
    patient_speech_text: Optional[str] = ""


@app.on_event("startup")
def _warmup() -> None:
    triage_service.triage_model()          # fail loudly at boot, not on patient #1
    triage_service.urine_model()
    triage_service.ecg_model()


@app.get("/")
def read_root():
    return {"status": "ML Engine AI Server is live on port 8001!"}


@app.get("/health/models")
def model_health():
    ecg, urine = triage_service.ecg_model(), triage_service.urine_model()
    return {
        "triage": {"source": "triage_xgboost.json (exact rules)",
                   "sha256": triage_service.triage_model().source_sha256},
        "ecg": {"loaded": ecg is not None, "status": getattr(ecg, "status", None)},
        "urine": {"loaded": urine is not None, "status": getattr(urine, "status", "python-rules")},
    }


@app.post("/predict")
def predict_risk(v: VitalsIn):
    try:
        return triage_service.assess(
            patient_id=v.patient_id, ecg_hr=v.ecg_hr, bp_systolic=v.bp_systolic,
            bp_diastolic=v.bp_diastolic, spo2=v.spo2, temperature=v.temperature,
            urine_rgb=v.urine_rgb, ecg_samples=v.ecg_samples,
            stethoscope_status=v.stethoscope_status,
            patient_speech_text=v.patient_speech_text,
        )
    except FileNotFoundError as exc:
        log.error("model missing: %s", exc)
        raise HTTPException(status_code=503, detail=f"Triage model unavailable: {exc}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8001)
