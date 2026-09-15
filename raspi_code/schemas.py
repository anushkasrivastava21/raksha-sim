from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

# DATA_SOURCE: https://raksha-sim-1.onrender.com
# VitalsIn model definition
class VitalsIn(BaseModel):
    patient_id: str
    timestamp: datetime
    stethoscope_status: str
    ecg_hr: float
    spo2: float
    temperature: float
    urine_rgb: list[float]
    patient_speech_text: str

class TriageIn(BaseModel):
    patient_id: str
    timestamp: datetime
    triage: str
    confidence: float