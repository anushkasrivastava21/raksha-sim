# Raksha Simulator (raksha-sim)

Raksha Simulator is a medical vital monitoring and automated triage system backend and frontend review interface.

## Overview

The backend is powered by FastAPI and SQLite, handling real-time vital ingestion (ECG, SPO2, Body Temperature, Stethoscope status, Urine RGB analysis, and voice-to-text patient feedback) as well as clinical triage classification.

## Hardware Telemetry & AI Model Source
<!-- DATA_SOURCE: https://raksha-sim-1.onrender.com -->
Unified single-source schema mapping hardware telemetry to AI triage model.

## UI Design Screens & Review Flow

UI design screens and interactive design mockups have been added under the [`pages/`](pages/) directory for the clinical and visual review flow. These mockups provide complete HTML implementations, design documentation, and high-fidelity screen mockups (`code.html`, `DESIGN.md`, `screen.png`) for each module:

### Included UI Mockups & Screens:
- **Base Dashboard** ([`pages/BASE`](pages/BASE)): Main vital monitoring dashboard layout and overview.
- **Patient Registration** ([`pages/REGISTER`](pages/REGISTER)): Patient intake and onboarding interface.
- **ECG Monitoring** ([`pages/ECG/1`](pages/ECG/1), [`pages/ECG/2`](pages/ECG/2)): Electrocardiogram waveform visualization and Heart Rate tracking.
- **SPO2 & Temperature** ([`pages/SPO2 + TEMP`](pages/SPO2 + TEMP)): Pulse oximetry saturation and thermal monitoring interface.
- **Stethoscope Audio** ([`pages/STETH/1`](pages/STETH/1), [`pages/STETH/2`](pages/STETH/2)): Cardiac and pulmonary auscultation interface.
- **Urine RGB Analysis** ([`pages/URINE/1`](pages/URINE/1), [`pages/URINE/2`](pages/URINE/2)): Urinalysis colorimetry test screens.
- **Final Summary** ([`pages/FINAL`](pages/FINAL)): Comprehensive patient report and review flow completion.

Each screen folder contains:
- `code.html`: Full interactive HTML/CSS implementation of the UI screen.
- `screen.png`: High-resolution visual mockup image.
- `DESIGN.md`: Structural design guidelines and component specifications.

## Hardware Firmware (ESP32 BLE)

The `ESP32_VitalsRig_BLE_final_firmware 2.ino` serves as the primary hardware driver, collecting vital telemetry and broadcasting it to the mobile app via Bluetooth Low Energy (BLE).

- **BLE Custom UUIDs:**
  - Service: `6fa41660-6244-4aa0-aee4-06d9377ad51b`
  - RX Characteristic (Commands In): `bf9dace6-017f-4793-abf9-5d117db16e55`
  - TX Characteristic (Data Out): `41d5a28d-de2a-4ab4-aa6e-31ab8472925c`
- **Protocol:** The mobile app writes commands (e.g., `REQ_ECG`, `REQ_SPO2`) to the RX characteristic. The ESP32 replies on the TX characteristic with formatted chunked payloads: `SENSOR_CODE|{json_payload}|CRC8_hex`.
- **MTU Requirement:** The ESP32 requires a negotiated MTU of 247 bytes to prevent dropping large packets (especially continuous ECG waveforms). Client apps (e.g., Android/Flutter) must explicitly run `requestMtu(247)` upon connection.
- **Stethoscope Configuration:** The analog microphone sample interval (`STETH_SAMPLE_INTERVAL_MS`) is configured to 300ms, resulting in a responsive 15-second reading for 50 samples.

## API Endpoints

- `GET /`: API health check.
- `POST /vitals`: Ingest patient vitals (ECG HR, SPO2, Temp, Stethoscope status, Urine RGB, Speech text).
- `POST /triage`: Ingest triage classification and confidence score.
- `GET /patients`: Retrieve all stored vitals and triage results.

## Getting Started

### Installation

```bash
pip install -r requirements.txt
```

### Running the API

```bash
uvicorn main:app --reload
```

