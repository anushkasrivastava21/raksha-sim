# API Endpoints

The Raksha Simulator backend (FastAPI) exposes the following REST endpoints for telemetry ingestion and data retrieval.

## `GET /`
**Purpose**: Health check endpoint.
- **Request Body**: None
- **Response**: `{"status": "ok"}`

## `POST /vitals`
**Purpose**: Ingests patient vitals directly from the mobile app (which proxies the ESP32 hardware telemetry).
- **Request Body** (JSON):
  ```json
  {
    "ecg_hr": 75,
    "ecg_samples": [ ... ],
    "spo2_percent": 98,
    "temp_c": 36.5,
    "steth_rms": 1981,
    "urine_rgb": {"r": 100, "g": 100, "b": 100}
  }
  ```
- **Response**: `200 OK`
- **Error Codes**: `422 Unprocessable Entity` (if schema validation fails)

## `POST /triage`
**Purpose**: Ingests automated triage classification from the ML/XGBoost engine.
- **Request Body** (JSON): `{"classification": "Red", "confidence": 0.95}`
- **Response**: `200 OK`

## `GET /patients`
**Purpose**: Retrieves all stored vitals and historical triage results from the SQLite database.
- **Response**: `[{...patient_data...}]`
