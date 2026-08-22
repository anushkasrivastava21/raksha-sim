from sqlalchemy import Column, String, Float, Integer, DateTime, JSON
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class Vitals(Base):
    __tablename__ = "vitals"
    id = Column(String, primary_key=True) # e.g. patient_id + timestamp
    patient_id = Column(String)
    timestamp = Column(DateTime)
    stethoscope_status = Column(String)
    ecg_hr = Column(Float)
    spo2 = Column(Float)
    temperature = Column(Float)
    urine_r = Column(Float)
    urine_g = Column(Float)
    urine_b = Column(Float)
    patient_speech_text = Column(String, nullable=True)

class Triage(Base):
    __tablename__ = "triage"
    id = Column(String, primary_key=True)
    patient_id = Column(String)
    timestamp = Column(DateTime)
    triage = Column(String) # "Green" / "Yellow" / "Red"
    confidence = Column(Float)