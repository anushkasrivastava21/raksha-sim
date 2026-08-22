import os
import pytest
from datetime import datetime, timezone
import pandas as pd
from unittest.mock import patch, MagicMock

import sys
sys.modules['torch'] = MagicMock()
sys.modules['transformers'] = MagicMock()
sys.modules['ecg_processor'] = MagicMock()
sys.modules['voice_processor'] = MagicMock()
sys.modules['urine_processor'] = MagicMock()

import main
import models
import schemas
from triage_engine import analyze_patient
from triage_integrator import predict_final_triage
import triage_integrator

# Inject mocked returns for ML processors
sys.modules['ecg_processor'].process_ecg.return_value = "Normal"
sys.modules['voice_processor'].extract_symptoms.return_value = []

def mock_process_urine(rgb):
    return (rgb[0] + rgb[1] + rgb[2]) / 765.0

sys.modules['urine_processor'].process_urine.side_effect = mock_process_urine

def get_valid_payload():
    return {
        "device_id": "test_device",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ecg": {"heart_rate_bpm": 72, "samples": [1000]*50},
        "urine_sensor": {"red": 255, "green": 234, "blue": 112},
        "stethoscope": {"rms": 200, "min": 1500, "max": 2500, "samples": 300},
        "temperature": {"body_temp_c": 37.0},
        "pulse_oximeter": {"heart_rate_bpm": 72, "spo2_percent": 98, "ir_raw": 135000},
        "status": "complete",
        "bp_sys": 120,
        "bp_dia": 80,
        "patient_speech_text": "feeling good"
    }

def test_app_imports_cleanly():
    assert main.app is not None

def test_post_vitals_round_trip(client):
    payload = get_valid_payload()
    resp = client.post("/vitals", json=payload)
    assert resp.status_code == 200
    
    resp_get = client.get("/patients")
    data = resp_get.json()
    assert len(data["vitals"]) == 1
    vital = data["vitals"][0]
    
    assert vital["patient_id"] == "test_device"
    assert vital["urine_r"] == 255
    assert vital["urine_g"] == 234
    assert vital["urine_b"] == 112
    assert vital["bp_sys"] == 120
    assert vital["bp_dia"] == 80

def test_post_triage_round_trip(client):
    payload = {
        "patient_id": "test_patient",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "triage": "Green",
        "confidence": 0.95
    }
    resp = client.post("/triage", json=payload)
    assert resp.status_code == 200
    
    resp_get = client.get("/patients")
    data = resp_get.json()
    assert len(data["triage_results"]) == 1
    triage = data["triage_results"][0]
    
    assert triage["patient_id"] == "test_patient"
    assert triage["triage"] == "Green"
    assert triage["confidence"] == 0.95

def test_schema_model_field_parity():
    # Because VitalsIn is nested in this branch (for ESP32), we adapt the parity test 
    # to ensure all mapping fields exist in models.Vitals
    flat_fields = [
        "bp_sys", "bp_dia", "ecg_hr", "ecg_samples", "steth_rms", "steth_min", 
        "steth_max", "steth_samples", "spo2_percent", "spo2_hr", "spo2_ir_raw", 
        "temperature", "urine_r", "urine_g", "urine_b", "patient_speech_text"
    ]
    model_columns = [c.name for c in models.Vitals.__table__.columns]
    for field in flat_fields:
        assert field in model_columns, f"Field {field} missing from models.Vitals"

def test_cors_config_is_valid():
    cors_mw = None
    for middleware in main.app.user_middleware:
        if middleware.cls.__name__ == "CORSMiddleware":
            cors_mw = middleware
            break
            
    assert cors_mw is not None
    # If wildcard origin is present, allow_credentials MUST be False
    if "*" in cors_mw.kwargs.get("allow_origins", []):
        assert cors_mw.kwargs.get("allow_credentials") == False, "CORS config invalid: wildcard origin with credentials=True"

@patch("triage_integrator.xgb_model")
def test_triage_engine_normal_vitals_green(mock_model):
    mock_model.predict_proba.return_value = [[0.85, 0.10, 0.05]]
    
    payload = get_valid_payload()
    result = predict_final_triage(payload)
    
    assert result["triage_color"] == "Green"
    assert 0.0 <= result["confidence_score"] <= 1.0

@patch("triage_integrator.xgb_model")
def test_triage_engine_redflag_speech_biases_toward_red(mock_model):
    # Mock model strongly favors Green
    mock_model.predict_proba.return_value = [[0.85, 0.10, 0.05]]
    
    # But patient has red-flag symptoms
    payload = get_valid_payload()
    payload["patient_speech_text"] = "I have severe chest pain and feel dizzy"
    
    result = predict_final_triage(payload)
    
    # Should shift toward Red (the exact threshold depends on the logic, but it shouldn't be Green anymore, or Red prob increases)
    assert result["triage_color"] in ["Yellow", "Red"]
    assert 0.0 <= result["confidence_score"] <= 1.0

@pytest.mark.parametrize("mock_probs", [
    [[1.0, 0.0, 0.0]],
    [[0.0, 1.0, 0.0]],
    [[0.0, 0.0, 1.0]],
    [[0.33, 0.33, 0.34]],
    [[0.1, 0.8, 0.1]],
])
@patch("triage_integrator.xgb_model")
def test_triage_engine_confidence_always_valid_probability(mock_model, mock_probs):
    mock_model.predict_proba.return_value = mock_probs
    
    payload = get_valid_payload()
    result = predict_final_triage(payload)
    
    assert result["triage_color"] in ["Green", "Yellow", "Red"]
    assert 0.0 <= result["confidence_score"] <= 1.0

@patch("triage_integrator.xgb_model")
def test_triage_engine_uses_real_urine_input(mock_model):
    mock_model.predict_proba.return_value = [[0.8, 0.1, 0.1]]
    
    payload1 = get_valid_payload()
    payload1["urine_sensor"] = {"red": 255, "green": 255, "blue": 255}
    
    payload2 = get_valid_payload()
    payload2["urine_sensor"] = {"red": 10, "green": 20, "blue": 30}
    
    # We spy on XGBClassifier.predict_proba to see the features dataframe
    predict_final_triage(payload1)
    df1 = mock_model.predict_proba.call_args_list[0][0][0]
    
    predict_final_triage(payload2)
    df2 = mock_model.predict_proba.call_args_list[1][0][0]
    
    # Severity should differ based on the urine rgb values
    assert df1["urine_severity"].iloc[0] != df2["urine_severity"].iloc[0]

def test_analyze_endpoint_end_to_end(client):
    payload = get_valid_payload()
    resp = client.post("/analyze", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "triage_status" in data
    assert "confidence" in data
    
    resp_get = client.get("/patients")
    assert len(resp_get.json()["triage_results"]) > 0

@patch("triage_integrator.xgb_model", new=None)
@patch("triage_integrator.MODEL_LOADED", new=False)
def test_model_load_failure_raises_clear_error(client):
    payload = get_valid_payload()
    resp = client.post("/analyze", json=payload)
    assert resp.status_code == 503
    assert "Triage engine model is not loaded" in resp.json()["detail"]

def test_s6_simulator_produces_valid_payloads():
    import s6
    for i in range(10):
        inject = (i % 2 == 0)
        packet = s6.generate_vitals_packet(inject_red_flag=inject)
        
        # Validate against schemas.VitalsIn
        validated = schemas.VitalsIn(**packet)
        assert validated.device_id is not None
        if inject:
            assert "chest pain" in validated.patient_speech_text or "dizzy" in validated.patient_speech_text
