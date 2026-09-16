# Raksha — VMEDITHON 3.0

> **Raksha means protection. Six vitals, one phone, no signal required.**

## 🚨 Problem Statement
One ASHA (Accredited Social Health Activist) worker is often responsible for a thousand people, yet relies entirely on her judgment with no diagnostic equipment. If she's unsure, the patient's only option is an average 5.5 km walk to a clinic that is already stretched thin. Because no device today combines multi-vital sensing with offline, on-device triage, chronic conditions go undetected until they become emergencies.

Raksha solves this: it reads six vitals and spoken symptoms, scores urgency on the spot, works without an internet connection, and fits in her bag.

## 🛠 Tech Stack
- **Hardware/Firmware:** ESP32 (Arduino C++), BLE GATT service, CRC8 checksum framing.
- **Mobile App:** Flutter/Dart (`flutter_blue_plus`, `tflite_flutter`, offline cache).
- **Vitals AI (On-Device):** ECG and urine CNNs trained in PyTorch, exported to TensorFlow Lite.
- **Voice & NLP (On-Device):** Vosk offline speech recognition (~40 MB) + Bilingual (English/Hindi) lexicon fuzzy matcher with negation handling.
- **Triage Engine:** XGBoost decision boundaries ported to a lightweight Dart rule table, gated by a MEWS safety layer.
- **Backend (Sync):** FastAPI + SQLite (for when connectivity returns).

## 🚀 Setup Steps

1. **Clone the repository**
   ```bash
   git clone https://github.com/anushkasrivastava21/raksha-sim.git
   cd raksha-sim
   ```
2. **Install Backend Dependencies**
   ```bash
   pip install -r requirements.txt
   ```
3. **Run the API / Dashboard**
   ```bash
   uvicorn main:app --reload
   ```
4. **Hardware**: Flash `ESP32_VitalsRig_BLE_final_firmware 2.ino` using the Arduino IDE.
5. **App**: Run the Flutter client from the `raksha-dash` repository.

*Note: For deeper architectural details (ADRs, API specs), check the `docs/` folder!*

## 👥 Team Members (All of us are Med)
- **Anushka Srivastava** — Backend, database, cloud integration
- **Anirudh G** — AI implementation, training, testing
- **Arnav Semwal** — Flutter app development and deployment
- **Shashwat Siddhant** — Firmware and hardware assembly
- **Archie Sinha** — Research and testing

## 📸 Demo & Screenshots
- Interactive UI design screens and clinical mockups are available in the [`pages/`](pages/) directory.
- *Insert Live Demo Link Here*

## 🔮 Future Scope
- **0–3 Months:** Extend the AI model to flag dangerous condition combinations, evaluating multi-symptom severity rather than just single-disease probabilities.
- **3 Months:** Validate model outputs against medical professional review and clinical guidelines.
- **6 Months:** Pilot deployment with an ASHA worker cohort in a targeted district.

## Folder Structure & Documentation
We use a standardized documentation structure. Start by exploring the `docs/` folder:

- [Architecture Overview](docs/architecture/overview.md) - High-level system design & Hardware BLE Firmware.
- [Architecture Decisions (ADRs)](docs/architecture/decisions/) - Historical decisions (e.g., custom BLE chunks).
- [API Endpoints](docs/api/endpoints.md) - Details on `/vitals`, `/triage`, etc.
- [Setup & Local Dev](docs/setup/local-dev.md) - Run instructions.
- [Changelog](CHANGELOG.md) - Version history.

## Scripts & Commands
- `uvicorn main:app --reload` (Start development server)
- `pytest` (Run test suite)
