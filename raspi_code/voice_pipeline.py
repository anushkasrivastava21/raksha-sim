import json

# NOTE (assumption): "audio (1).py" has a space in its filename, which is not a
# valid Python module name. Rename it to "audio_recorder.py" (or similar) before
# importing, or the import below will fail with a SyntaxError/ModuleNotFoundError.
from audio_recorder import record_audio, OUTPUT_FILE
from voice_processor import extract_symptoms


def analyze_voice_input(record: bool = True, audio_file_path: str = None) -> dict:
    """
    Wrapper function for capturing/loading a patient's voice sample and
    extracting reported symptoms from it.

    Mirrors the shape of `analyze_patient()` in test_pipeline.py:
    - Combines two independent modules (recorder + NLP extractor) into one call.
    - Returns a clean, minimal dict for downstream consumers (e.g. triage_integrator).

    Args:
        record: If True, actively records a new sample via the USB mic
                (calls record_audio() from audio_recorder.py, which blocks
                until DURATION seconds have been captured).
                If False, skips recording and uses `audio_file_path` directly
                (e.g. a file already uploaded from an ESP32 device, matching
                the "step_1_audio" field seen in test_pipeline.py's payload).
        audio_file_path: Path to an existing .wav file. Required if record=False.
                          Ignored (overwritten with OUTPUT_FILE) if record=True.

    Returns:
        {
            "audio_file": str,        # path to the .wav file that was processed
            "symptom_list": list,     # symptoms extracted by voice_processor.py
            "symptom_count": int      # convenience field for quick checks/logging
        }
    """
    if record:
        record_audio()
        audio_file_path = OUTPUT_FILE

    if not audio_file_path:
        raise ValueError("audio_file_path must be provided when record=False")

    symptoms = extract_symptoms(audio_file_path)

    return {
        "audio_file": audio_file_path,
        "symptom_list": symptoms,
        "symptom_count": len(symptoms),
    }


if __name__ == "__main__":
    print("\n--- Starting Voice Symptom Extraction Pipeline Test ---")

    # Assumption: simulating an already-uploaded audio sample (e.g. from an
    # ESP32/mobile client), same style as the "step_1_audio" file path used
    # in test_pipeline.py's test_esp32_payload. Set record=True to instead
    # capture live audio from a connected USB mic.
    test_audio_file_path = "recorded_audio.wav"

    print("1. Loading Patient Voice Sample...")
    res = analyze_voice_input(record=False, audio_file_path=test_audio_file_path)

    print("\n=== FINAL VOICE PIPELINE OUTPUT ===")
    print(json.dumps(res, indent=2))
