from triage_integrator import predict_final_triage

def analyze_patient(sensor_packet: dict) -> dict:
    """Wrapper function for analyzing patient vitals and returning triage decision."""
    result = predict_final_triage(sensor_packet)
    # Output schema matching locked contract expected by frontend / local_server
    return {
        "triage": result["triage_color"],
        "confidence": result["confidence_score"],
        "ecg_result": result["ecg_result"],
        "symptoms": result["symptom_list"]
    }

if __name__ == "__main__":
    test_esp32 = {
      "device_id": "ESP32_VitalsRig_01",
      "timestamp": "123456",
      "ecg": {
        "heart_rate_bpm": 72,
        "samples": [
          1850, 1880, 1905, 1870, 1845,
          1860, 1890, 1910, 1885, 1860,
          1840, 1870, 1900, 1940, 2100,
          2350, 2600, 2950, 3400, 3750,
          3900, 3650, 3250, 2800, 2400,
          2150, 1950, 1870, 1845, 1860,
          1890, 1910, 1880, 1850, 1835,
          1860, 1900, 1940, 2100, 2350,
          2600, 2950, 3400, 3750, 3900,
          3650, 3250, 2800, 2400, 2100,
          1900, 1860, 1845, 1870, 1900
        ]
      },
      "urine_sensor": {
        "red": 3300,
        "green": 3250,
        "blue": 3400
      },
      "stethoscope": {
        "rms": 220,
        "min": 1500,
        "max": 2600,
        "samples": 300
      },
      "temperature": {
        "body_temp_c": 36.6
      },
      "pulse_oximeter": {
        "heart_rate_bpm": 72,
        "spo2_percent": 98,
        "ir_raw": 135420
      },
      "status": "complete"
    }

    res = analyze_patient(test_esp32)
    print("ESP32 Test Analysis Result:")
    print(res)
