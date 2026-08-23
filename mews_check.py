def check_mews(vitals_dict):
    # Support both nested (hardware) and flat (VitalsIn schema) payloads
    hr = (vitals_dict.get("ecg", {}) or {}).get("heart_rate_bpm") \
         or vitals_dict.get("ecg_hr", 0)
    spo2 = (vitals_dict.get("pulse_oximeter", {}) or {}).get("spo2_percent") \
           or vitals_dict.get("spo2", 100)
    temp = (vitals_dict.get("temperature", {}) or {}).get("body_temp_c") \
           if isinstance(vitals_dict.get("temperature"), dict) \
           else vitals_dict.get("temperature", 37.0)

    reasons = []
    status = "NONE"

    # Check Heart Rate
    if hr < 40 or hr > 130:
        reasons.append(f"Critical Heart Rate ({hr} bpm)")
        status = "RED"

    # Check SpO2 (Oxygen)
    if spo2 < 90:
        reasons.append(f"Critical SpO2 ({spo2}%)")
        status = "RED"

    # Check Temperature
    if temp > 39.0 or temp < 35.0:
        reasons.append(f"Abnormal Temperature ({temp}°C)")
        if status != "RED":
            status = "YELLOW"

    # Compile result
    if reasons:
        return {
            "override": True,
            "status": status,
            "reason": " | ".join(reasons)  # Combines all triggered alerts
        }

    return {"override": False, "status": "NONE", "reason": "Vitals within normal range"}