# Raksha Simulator (raksha-sim)

Raksha Simulator is a medical vital monitoring and automated triage system backend and frontend review interface.

## Tech Stack
- **Backend**: FastAPI (Python), SQLite
- **AI/ML**: XGBoost (Triage engine)
- **Hardware**: ESP32 (BLE GATT Server)
- **Frontend**: Flutter / HTML mockups

## Quick Start
Get the backend API running locally in under 2 minutes:

```bash
# 1. Clone the repository
git clone https://github.com/anushkasrivastava21/raksha-sim.git
cd raksha-sim

# 2. Install dependencies
pip install -r requirements.txt

# 3. Start the server
uvicorn main:app --reload
```
API runs at `http://localhost:8000`.

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
