import os
import json
import pandas as pd
import xgboost as xgb

import scipy.io.wavfile as wav

# Import Microservices lazily below


# --- AI Model Initialization ---
import logging
logger = logging.getLogger(__name__)

model_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'triage_xgboost.json')
xgb_model = None
MODEL_LOADED = False

try:
    if os.path.exists(model_path):
        xgb_model = xgb.XGBClassifier()
        xgb_model.load_model(model_path)
        MODEL_LOADED = True
    else:
        logger.error(f"Model file not found at {model_path}")
except Exception as e:
    logger.error(f"Error loading model: {e}")

TRIAGE_MAP = {0: "Green", 1: "Yellow", 2: "Red"}


def record_live_audio(duration=5, sample_rate=16000, output_file="recorded_audio.wav"):
    """Records live audio from the Raspberry Pi USB microphone."""
    try:
        import sounddevice as sd
    except OSError:
        raise RuntimeError(
            "Live microphone recording is not available on this server "
            "(no audio hardware / PortAudio not installed). "
            "This feature only works on the physical device (e.g. Raspberry Pi)."
        )

    print("==================================================")
    print(f"[+] Recording patient audio... Speak into the USB mic ({duration}s)")
    print("==================================================")
    
    audio_data = sd.rec(
        int(duration * sample_rate), 
        samplerate=sample_rate, 
        channels=1, 
        dtype='int16'
    )
    sd.wait() 
    wav.write(output_file, sample_rate, audio_data)
    
    print(f"[+] Recording finished! Saved as: {output_file}\n")
    return output_file


def parse_sensor_packet(sensor_packet: dict):
    """Extracts features and conditionally triggers hardware recording."""
    
    # 1. Audio & Symptoms (Triggers mic if requested)
    audio_path = sensor_packet.get('step_1_audio', None)
    
    # Integration trigger for the Raspberry Pi Mic
    if audio_path == "RECORD_MIC":
        audio_path = record_live_audio()
        
    if audio_path:
        from voice_processor import extract_symptoms
        symptoms = extract_symptoms(audio_path)
    else:
        symptoms = []
        
    # Fallback to direct text if provided
    speech_text = sensor_packet.get('patient_speech_text', '')
    if speech_text:
        RED_FLAG_KEYWORDS = ["dizzy", "chest pain", "fever", "fainting", "blood", "shortness of breath"]
        symptoms.extend([kw for kw in RED_FLAG_KEYWORDS if kw in speech_text.lower()])

    # 2. ECG & Heart Rate
    ecg_hr = None
    if 'ecg' in sensor_packet and isinstance(sensor_packet['ecg'], dict):
        ecg_data = sensor_packet['ecg']
        raw_ecg = ecg_data.get('samples', [0]*10)
        ecg_hr = ecg_data.get('heart_rate_bpm', None)
    else:
        raw_ecg = sensor_packet.get('step_2_ecg', [0]*10)

    # 3. Pulse Oximetry
    if 'pulse_oximeter' in sensor_packet and isinstance(sensor_packet['pulse_oximeter'], dict):
        po = sensor_packet['pulse_oximeter']
        spo2 = float(po.get('spo2_percent', 98.0))
        if ecg_hr is None:
            ecg_hr = float(po.get('heart_rate_bpm', 75.0))
    else:
        spo2 = float(sensor_packet.get('step_4_pulse_oximetry', {}).get('spo2', 98.0))

    if ecg_hr is None:
        ecg_hr = 75.0

    if 'urine_sensor' in sensor_packet and isinstance(sensor_packet['urine_sensor'], dict):
        u_dict = sensor_packet['urine_sensor']
        r = float(u_dict.get('red', 255.0))
        g = float(u_dict.get('green', 234.0))
        b = float(u_dict.get('blue', 112.0))
        
        max_val = max(r, g, b)
        if max_val > 255:
            r, g, b = (r/4095.0)*255.0, (g/4095.0)*255.0, (b/4095.0)*255.0
            
        urine_rgb = [r, g, b]
    else:
        urine_rgb = sensor_packet.get('urine_rgb', [255.0, 234.0, 112.0])

    from urine_processor import process_urine
    urine_severity = process_urine(urine_rgb)


    if 'temperature' in sensor_packet and isinstance(sensor_packet['temperature'], dict):
        temp = float(sensor_packet['temperature'].get('body_temp_c', 37.0))
    else:
        temp = float(sensor_packet.get('step_5_ir_temperature', 37.0))

    from ecg_processor import process_ecg
    ecg_result = process_ecg(raw_ecg)

    return {
        'ecg_hr': ecg_hr,
        'spo2': spo2,
        'temperature': temp,
        'urine_severity': urine_severity,
        'ecg_result': ecg_result,
        'symptoms': symptoms
    }
def predict_final_triage(sensor_packet: dict) -> dict:
    """Integrates all AI processors and outputs final triage payload."""
    parsed = parse_sensor_packet(sensor_packet)
    
    features = pd.DataFrame([{
        'ecg_hr': parsed['ecg_hr'],
        'bp_sys': 120.0,
        'bp_dia': 80.0,
        'spo2': parsed['spo2'],
        'temperature': parsed['temperature'],
        'urine_severity': parsed['urine_severity']
    }])
    
    if not MODEL_LOADED or xgb_model is None:
        raise RuntimeError("Triage engine model is not loaded.")
        
    raw_probs = xgb_model.predict_proba(features)[0]
    p_green, p_yellow, p_red = raw_probs[0], raw_probs[1], raw_probs[2]

    symptoms = parsed['symptoms']
    ecg_result = parsed['ecg_result']
    if symptoms or ecg_result == "Arrhythmia Detected":
        p_red += 0.45 
        p_yellow += 0.10
        p_green *= 0.10 
        total = p_green + p_yellow + p_red
        p_green, p_yellow, p_red = p_green/total, p_yellow/total, p_red/total

    final_probs = [p_green, p_yellow, p_red]
    confidence = max(final_probs)
    triage_color = TRIAGE_MAP[final_probs.index(confidence)]
    
    return {
        "triage_color": triage_color,
        "confidence_score": round(float(confidence), 2),
        "symptom_list": symptoms,
        "ecg_result": ecg_result
    }
