# Local Development Setup

## Prerequisites
- Python 3.9+
- Arduino IDE (for firmware modifications)
- Flutter SDK (for `raksha-dash`)

## Running the Backend (`raksha-sim`)
1. Clone the repository.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Run the development server:
   ```bash
   uvicorn main:app --reload
   ```

## Running the Firmware
1. Open `ESP32_VitalsRig_BLE_final_firmware 2.ino` in Arduino IDE.
2. Select your ESP32 board and port.
3. Click **Upload**.
