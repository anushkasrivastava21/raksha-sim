# Architecture Overview

Raksha Simulator consists of a FastAPI backend to ingest hardware telemetry, a Flutter front-end for UI, and an ESP32 firmware serving as a BLE GATT Server.

## System Components
1. **ESP32 Firmware**: Reads analog/I2C sensors (ECG, MAX30102, MLX90614, Stethoscope, Urine Color) and broadcasts via BLE.
2. **Flutter App (`raksha-dash`)**: Subscribes to the ESP32 BLE TX characteristic, parses chunks, and proxies requests to the backend.
3. **FastAPI Backend (`raksha-sim`)**: Ingests vitals via POST, evaluates triage using an XGBoost model, and persists to SQLite.

## UI Design Screens & Review Flow
UI mockups are located under the `pages/` directory:
- **Base Dashboard** (`pages/BASE`): Main vital monitoring dashboard layout.
- **Patient Registration** (`pages/REGISTER`): Patient intake and onboarding interface.
- **ECG Monitoring** (`pages/ECG/1`, `pages/ECG/2`): Electrocardiogram waveform visualization.
- **SPO2 & Temperature** (`pages/SPO2 + TEMP`): Pulse oximetry saturation and thermal monitoring.
- **Stethoscope Audio** (`pages/STETH/1`, `pages/STETH/2`): Auscultation interface.
- **Urine RGB Analysis** (`pages/URINE/1`, `pages/URINE/2`): Urinalysis colorimetry test screens.
- **Final Summary** (`pages/FINAL`): Comprehensive patient report.

Each screen folder contains:
- `code.html`: Full interactive HTML/CSS implementation.
- `screen.png`: High-resolution visual mockup image.
- `DESIGN.md`: Structural design guidelines.
