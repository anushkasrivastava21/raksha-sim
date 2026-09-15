/*
  ============================================================================
  ESP32_VitalsRig_BLE.ino
  ============================================================================
  Hackathon vitals rig firmware -- PhoneEdge pivot, BLE transport.

  WHAT CHANGED FROM firmware_latest.cpp / ESP32_VitalsRig.ino
  -------------------------------------------------------------
  ONLY the radio/transport layer changed. Everything else -- every sensor
  read sequence, every JSON field, the CRC8 algorithm, the command set, the
  state machine, the pin map, the timing constants -- is byte-for-byte
  identical to the original firmware.

  IMPORTANT CORRECTION vs. the master pivot PRD: the original firmware was
  NOT talking to the RPi4 over USB/UART. It was already using Bluetooth --
  specifically Bluetooth CLASSIC SPP (BluetoothSerial.h), which behaves like
  a plain serial cable (unlimited-length streaming, no packet-size limit).
  The master PRD's BLE requirement (to match Arnav's flutter_blue_plus,
  which speaks BLE, not Classic SPP) means the real transport change here is
  Bluetooth Classic SPP -> Bluetooth Low Energy (BLE), not USB -> BLE.

  This distinction matters mechanically:
    - Classic SPP: stream any length, byte-by-byte, no framing needed beyond
      the newline-terminated lines the original code already used.
    - BLE: message-based. Data moves through GATT "characteristics" with a
      negotiated MTU size ceiling, and incoming data arrives via an
      asynchronous write-callback instead of a byte-by-byte polled buffer.

  To keep the wire protocol itself identical, this file models the BLE link
  as a "serial pipe over BLE" with two characteristics (one for phone->ESP32
  commands, one for ESP32->phone data), using freshly generated custom
  128-bit UUIDs (not a standard BLE SIG UUID, not a reused well-known
  service like Nordic UART) per the PRD's explicit instruction. These UUIDs
  are final -- do not regenerate them once shared with Arnav.

  PROTOCOL -- UNCHANGED IN CONTENT, NEW CARRIER
  -----------------------------------------------
  Phone -> ESP32 (write to RX characteristic, same ASCII commands as before):
      "REQ_ECG", "REQ_URINE", "REQ_STETH", "REQ_TEMP", "REQ_SPO2", "PING"
      (trailing newline no longer required -- BLE writes are already
      discrete messages -- but tolerated/stripped if present.)

  ESP32 -> Phone (notify on TX characteristic, exact same framing as before):
      "<SENSOR_CODE>|<json_payload>|<CRC8_hex>"
      Same SENSOR_CODE values, same JSON field names/units, same CRC8
      (poly 0x07) computed over "<SENSOR_CODE>|<json_payload>" exactly as
      before. The only difference: this string is now the value of a BLE
      notification instead of a line written to a serial buffer, so the
      trailing "\n" is dropped (BLE messages are already length-delimited;
      Arnav's app should treat each notification as one complete packet,
      not concatenate a byte stream looking for '\n').

  MTU / CHUNKING -- NOW IMPLEMENTED (previously a pending action item)
  --------------------------------------------------------
  This firmware still requests a preferred MTU of 247 bytes in setup(), but
  no longer assumes the phone will grant it. The actual negotiated MTU is
  captured via BLEServerCallbacks::onMtuChanged() and stored in the global
  `negotiatedMtu` (starts at 23, the BLE-spec-guaranteed minimum, until the
  MTU exchange completes -- so even a packet sent before negotiation
  finishes is still handled correctly).

  Every outgoing line now goes through bleSendRaw(), which:
    1. Computes how many bytes actually fit in one notification at the
       CURRENT negotiated MTU (MTU minus 3 bytes ATT overhead minus 1 byte
       for this firmware's own chunk header -- see below).
    2. If the line fits in one notification, sends it as a single chunk
       (header byte declares total=1, index=0).
    3. If not (this only matters for the ECG packet, ~140-160 bytes, on a
       phone that negotiates a small MTU), splits it into up to 15 chunks
       per the framing below and sends them back-to-back with a short delay
       between each so the phone's BLE stack doesn't drop consecutive
       notifications.

  CHUNK HEADER FORMAT (every notification, always -- see note below):
    Byte 0 of every notification = 1 header byte:
        top 4 bits    = total number of chunks in this packet (1-15)
        bottom 4 bits = this chunk's index (0-based)
    Bytes 1..N        = that chunk's slice of the packet text.

  IMPORTANT WIRE-FORMAT NOTE FOR ARNAV: every single notification -- even
  the common case of a short packet like TEMP or URINE that fits in one
  chunk -- now carries this 1-byte header first. This is a deliberate,
  unambiguous design choice: rather than have the phone guess whether a
  given notification is "raw" or "chunked," every notification is always
  chunk-framed (trivially, as a 1-of-1 chunk when it's short). Arnav's app
  must always read byte 0, extract total/index, and reassemble by index
  before running CRC8 on the reassembled text -- for a 1-of-1 packet that's
  just "strip the first byte and use the rest immediately."

  DROPPED-PACKET COUNTING -- SCOPE CORRECTION vs. the master PRD's §5
  --------------------------------------------------------
  The master PRD asked for a dropped-packet counter for CRC validation
  failures. That doesn't apply on this device: the ESP32 only ever RECEIVES
  short plain-text commands with no CRC attached (REQ_ECG, PING, etc.) --
  it generates and sends the CRC on the way OUT. A "corrupted CRC" is
  therefore something a receiver of ESP32 data could detect (i.e. Arnav's
  phone app, checking the CRC on a packet it just received), never
  something the ESP32 itself could detect about its own outgoing data.
  That counter belongs on the phone side, not here -- flag this at Review 1
  as a scope clarification, not a missed requirement.

  What IS meaningful on this side, and is now implemented: two on-device
  counters, printed to Serial whenever they increment, for failure modes
  that genuinely happen at the ESP32:
    - txDroppedNoClient: an outgoing line was ready to send but no BLE
      client was connected/subscribed, so nothing could be sent.
    - txDroppedOversized: an outgoing line was too large to fit even after
      chunking into 15 pieces at the current negotiated MTU (should not
      happen in practice for any packet this firmware produces, but guarded
      rather than silently truncated).
  Report both counts at Review 1 as an honest data point, same spirit as
  the CRC-drop count the original master PRD asked for.

  EVENT MODEL DIFFERENCE (transport-inherent, not a logic change)
  --------------------------------------------------------------------
  The original loop() polled the Bluetooth Classic buffer every iteration
  via serviceBluetoothInput(). BLE writes instead arrive via an asynchronous
  characteristic-write callback (onWrite), which is how the ESP32 BLE stack
  works -- there is no equivalent "poll a byte buffer" step needed or
  possible. loop() is simplified accordingly; runStateMachine() is
  unchanged and still called every iteration.

  REQUIRED ARDUINO LIBRARIES (Library Manager)
  ---------------------------------------------
  - "SparkFun MAX3010x Pulse and Proximity Sensor Library" (MAX30105.h)
      -> also pulls in heartRate.h. The full SpO2 algorithm needs
         spo2_algorithm.h/.cpp, which SparkFun ships inside that library's
         examples (Example5_HeartRateAndSpO2). If it isn't auto-included,
         copy spo2_algorithm.h/.cpp into this sketch folder.
  - "Adafruit MLX90614 Library" (+ Adafruit BusIO dependency)
  - ESP32 core's built-in BLE Arduino library (BLEDevice.h, BLEServer.h,
    BLEUtils.h, BLE2902.h) -- no install needed, ships with the ESP32
    Arduino core. Unlike Classic SPP, BLE is available on ESP32-S3/C3 boards
    too, not just plain ESP32 -- one incidental benefit of this pivot.

  BOARD SETTINGS
  --------------
  Tools > Board: "ESP32 Dev Module" (or your specific ESP32 board)
  Partition scheme: default (BLE needs BT enabled at build, same as before)

  ============================================================================
  PIN MAP -- UNCHANGED, taken verbatim from ESP32_Medical_Hardware_Pin_Configuration.docx
  ============================================================================
  Shared I2C bus (MAX30102 + MLX90614):
      SDA  -> GPIO 21
      SCL  -> GPIO 22

  MAX30102 (pulse oximeter):
      INT  -> GPIO 16
      VCC/GND -> 3.3V, GND
      (I2C address 0x57, on the shared bus above)

  MLX90614 (IR temperature):
      Shares I2C bus above (SDA=21, SCL=22). VCC/GND -> 3.3V, GND.

  MAX4466 (electret mic / "stethoscope"):
      OUT  -> GPIO 35  (ADC1_CH7, input-only pin)
      GAIN -> left floating, set via onboard trim pot (no GPIO)
      VCC/GND -> 3.3V, GND

  AD8232 ECG module:
      OUTPUT -> GPIO 34  (ADC1_CH6, input-only pin)
      LO+  -> GPIO 32  (leads-off detect)
      LO-  -> GPIO 33  (leads-off detect)
      3.3V/GND -> 3.3V, GND

  TCS3200 (color sensor):
      S0   -> GPIO 13
      S1   -> GPIO 17
      S2   -> GPIO 18
      S3   -> GPIO 19
      OUT  -> GPIO 23
      OE   -> tied directly to GND on the board (no ESP32 GPIO used)
      VCC/GND -> 3.3V, GND

  All modules share a common GND rail. BLE is the ESP32's onboard radio --
  no wired UART/USB link to the phone, same as the Classic SPP link it
  replaces.

  ============================================================================
  BLE GATT PROFILE (replaces the Bluetooth Classic SPP link)
  ============================================================================
  Custom 128-bit UUIDs, freshly generated for this project (per PRD §6.1 --
  not a standard BLE SIG UUID, not a reused well-known service). Share
  these three with Arnav verbatim; they are final, do not regenerate:

      Service UUID:                      6fa41660-6244-4aa0-aee4-06d9377ad51b
      RX characteristic (phone writes
        commands to this -- ESP32 receives):
                                          bf9dace6-017f-4793-abf9-5d117db16e55
      TX characteristic (ESP32 notifies
        packets on this -- phone receives):
                                          41d5a28d-de2a-4ab4-aa6e-31ab8472925c

  RX characteristic properties: WRITE (phone -> ESP32 commands)
  TX characteristic properties: NOTIFY + READ (ESP32 -> phone packets; READ
    also lets a generic BLE scanner like nRF Connect pull the last packet
    on demand for manual debugging without triggering a new sample)

  ============================================================================
  DESIGN NOTES / ASSUMPTIONS (unchanged from original firmware)
  ============================================================================
  1. Stethoscope modified per user request: takes exactly 50 samples, one sample
     every 1 second, without inhale/exhale prompts, displays each reading every second,
     then computes min, max, and rms values.
  2. BPM from only 20 raw ECG samples is not physiologically robust (a real
     beat only needs ~1 QRS peak per ~0.6-1s, and 20 raw ADC points is a
     very short window). Simple threshold peak-counter placeholder, unchanged.
  3. TCS3200 color classification (pale/deep/reddish yellow) is NOT done on
     the ESP32 -- it just ships raw R/G/B frequency counts.
  4. "Non-blocking" is implemented as: the BLE link is serviced by the BLE
     stack's own callback/event mechanism (not polled), and the state
     machine is serviced every loop() iteration, same intent as before.
  ============================================================================
*/

#include <Wire.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>
#include <Adafruit_MLX90614.h>
#include <MAX30105.h>
#include "spo2_algorithm.h"   // ships with SparkFun MAX3010x library examples

#if !defined(CONFIG_BT_ENABLED) || !defined(CONFIG_BLUEDROID_ENABLED)
#error Bluetooth is not enabled! Please run `make menuconfig` / select an ESP32 board that supports BLE.
#endif

// ----------------------------------------------------------------------------
// PIN DEFINITIONS (unchanged)
// ----------------------------------------------------------------------------
#define PIN_I2C_SDA        21
#define PIN_I2C_SCL        22

#define PIN_MAX30102_INT   16

#define PIN_MIC_ANALOG     35   // MAX4466 "stethoscope" OUT (ADC1_CH7)

#define PIN_ECG_ANALOG     34   // AD8232 OUTPUT (ADC1_CH6)
#define PIN_ECG_LO_PLUS    32
#define PIN_ECG_LO_MINUS   33

#define PIN_TCS_S0         13
#define PIN_TCS_S1         17
#define PIN_TCS_S2         18
#define PIN_TCS_S3         19
#define PIN_TCS_OUT        23

// ----------------------------------------------------------------------------
// BLE UUIDS -- custom 128-bit, freshly generated for this project (PRD
// §6.1). Final -- share verbatim with Arnav, do not regenerate.
// ----------------------------------------------------------------------------
#define SERVICE_UUID           "6fa41660-6244-4aa0-aee4-06d9377ad51b"
#define RX_CHARACTERISTIC_UUID "bf9dace6-017f-4793-abf9-5d117db16e55" // phone -> ESP32 (write)
#define TX_CHARACTERISTIC_UUID "41d5a28d-de2a-4ab4-aa6e-31ab8472925c" // ESP32 -> phone (notify/read)

#define BLE_DEVICE_NAME        "ESP32_VitalsRig_01"  // same name as original SPP advertisement
#define BLE_PREFERRED_MTU      247                    // requested MTU; actual negotiated value is tracked separately, see negotiatedMtu
#define ATT_HEADER_OVERHEAD    3                       // fixed BLE ATT protocol overhead per notification
#define CHUNK_HEADER_SIZE      1                       // this firmware's own 1-byte chunk header, see chunking note at top of file
#define MAX_CHUNKS             15                      // fits in the header's 4-bit "total chunks" nibble

// ----------------------------------------------------------------------------
// TUNABLE CONSTANTS (unchanged)
// ----------------------------------------------------------------------------
static const uint32_t POST_REQUEST_DELAY_MS   = 5000;
static const uint32_t ECG_SAMPLE_INTERVAL_MS  = 20;
static const int      ECG_NUM_SAMPLES         = 20;

static const uint32_t MLX_PLACEMENT_DELAY_MS  = 5000;

static const uint32_t URINE_PLACEMENT_DELAY_MS = 3000;
static const uint32_t TCS_PULSE_TIMEOUT_US     = 50000UL;

static const uint32_t STETH_SAMPLE_INTERVAL_MS = 1000; // 1 sample every 1 second
static const int      STETH_NUM_SAMPLES       = 50;    // exactly 50 samples

static const uint32_t SPO2_PLACE_FINGER_MSG_MS = 1500;
static const uint32_t SPO2_BUFFER_LEN         = 100;
static const uint32_t SPO2_SAMPLE_TIMEOUT_MS  = 8000;

static const uint32_t SENSOR_STAGE_TIMEOUT_MS = 65000; // increased for 50s steth reading

// ----------------------------------------------------------------------------
// GLOBAL OBJECTS
// ----------------------------------------------------------------------------
Adafruit_MLX90614 mlx = Adafruit_MLX90614();
MAX30105 max30102;

BLEServer* pServer = nullptr;
BLECharacteristic* pTxCharacteristic = nullptr; // notify -- ESP32 -> phone
BLECharacteristic* pRxCharacteristic = nullptr; // write  -- phone -> ESP32

volatile bool max30102InterruptFlag = false;
void IRAM_ATTR onMax30102Interrupt() {
  max30102InterruptFlag = true;
}

bool bleClientConnected = false; // replaces btClientConnected

// MTU starts at the BLE-spec-guaranteed minimum and is only raised once the
// stack confirms what was actually negotiated with this specific phone.
uint16_t negotiatedMtu = 23;

// On-device drop counters -- see "DROPPED-PACKET COUNTING" note at top of
// file for why this replaces the CRC-drop counter the master PRD requested.
uint32_t txDroppedNoClient  = 0;
uint32_t txDroppedOversized = 0;

// ----------------------------------------------------------------------------
// STATE MACHINE TYPES (unchanged)
// ----------------------------------------------------------------------------
enum SystemState {
  STATE_IDLE,
  STATE_POST_REQUEST_DELAY,
  STATE_RUN_ECG,
  STATE_RUN_URINE,
  STATE_RUN_STETH,
  STATE_RUN_TEMP,
  STATE_RUN_SPO2
};

enum SensorRequest {
  REQ_NONE,
  REQ_ECG,
  REQ_URINE,
  REQ_STETH,
  REQ_TEMP,
  REQ_SPO2
};

SystemState currentState = STATE_IDLE;
SensorRequest pendingRequest = REQ_NONE;
uint32_t stateEnteredAt = 0;

// Generic sub-step counter
int subStep = 0;
uint32_t subStepStartedAt = 0;

// ----------------------------------------------------------------------------
// PER-SENSOR WORKING BUFFERS (unchanged)
// ----------------------------------------------------------------------------
int ecgSamples[ECG_NUM_SAMPLES];
int ecgSampleCount = 0;
uint32_t lastEcgSampleAt = 0;

int stethSamples[STETH_NUM_SAMPLES];
int stethSampleCount = 0;
uint32_t lastStethSampleAt = 0;

uint32_t irBuffer[SPO2_BUFFER_LEN];
uint32_t redBuffer[SPO2_BUFFER_LEN];
int32_t spo2Value = 0;
int8_t spo2Valid = 0;
int32_t heartRateValue = 0;
int8_t heartRateValid = 0;
int spo2SampleCount = 0;

// ----------------------------------------------------------------------------
// FORWARD DECLARATIONS
// ----------------------------------------------------------------------------
void handleIncomingCommand(String cmd);
void sendPacket(const String &sensorCode, const String &jsonPayload);
void sendError(const String &sensorCode, const String &reason);
void bleSendRaw(const String &line);
uint8_t crc8(const uint8_t *data, size_t len);

void beginEcgSequence();
void stepEcgSequence();
void beginUrineSequence();
void stepUrineSequence();
void beginStethSequence();
void stepStethSequence();
void beginTempSequence();
void stepTempSequence();
void beginSpo2Sequence();
void stepSpo2Sequence();

void readTcsColor(uint16_t &redCount, uint16_t &greenCount, uint16_t &blueCount);

// ============================================================================
// BLE SERVER / CHARACTERISTIC CALLBACKS
// ----------------------------------------------------------------------------
// These replace the Classic SPP register_callback() connect/disconnect
// handler and the byte-polling serviceBluetoothInput()/handleIncomingCommand
// intake path. handleIncomingCommand() itself -- the actual command logic --
// is 100% unchanged; only how a command string arrives at that function
// changes (asynchronous BLE write callback instead of polled serial buffer).
// ============================================================================
class VitalsServerCallbacks: public BLEServerCallbacks {
  void onConnect(BLEServer* server) {
    bleClientConnected = true;
    Serial.println(F("[BLE] Client connected"));
  }

  void onDisconnect(BLEServer* server) {
    bleClientConnected = false;
    Serial.println(F("[BLE] Client disconnected -- awaiting reconnection"));
    currentState = STATE_IDLE;
    pendingRequest = REQ_NONE;
    subStep = 0;
    negotiatedMtu = 23; // reset to guaranteed minimum until the next connection re-negotiates
    // ESP32 BLE stack stops advertising on disconnect by default --
    // restart it so the phone can reconnect without a firmware reset.
    server->getAdvertising()->start();
  }

  // Captures the ACTUAL negotiated MTU for this connection, so chunking
  // decisions in bleSendRaw() are based on reality, not the value this
  // firmware merely requested. See MTU/CHUNKING note at top of file.
  void onMtuChanged(BLEServer* server, esp_ble_gatts_cb_param_t* param) {
    // Clamp to what we actually allocated a buffer for in bleSendRaw(),
    // even though a phone should never negotiate above what we requested.
    uint16_t mtu = param->mtu.mtu;
    negotiatedMtu = (mtu > BLE_PREFERRED_MTU) ? BLE_PREFERRED_MTU : mtu;
    Serial.print(F("[BLE] MTU negotiated: "));
    Serial.println(negotiatedMtu);
  }
};

class VitalsRxCallbacks: public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic* characteristic) {
    String value = String(characteristic->getValue().c_str());
    handleIncomingCommand(value); // same function, same command semantics as before
  }
};

// ============================================================================
// SETUP
// ============================================================================
void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println(F("[BOOT] ESP32 Vitals Rig starting (BLE build)..."));

  pinMode(PIN_ECG_LO_PLUS, INPUT);
  pinMode(PIN_ECG_LO_MINUS, INPUT);

  analogReadResolution(12);

  pinMode(PIN_TCS_S0, OUTPUT);
  pinMode(PIN_TCS_S1, OUTPUT);
  pinMode(PIN_TCS_S2, OUTPUT);
  pinMode(PIN_TCS_S3, OUTPUT);
  pinMode(PIN_TCS_OUT, INPUT);
  digitalWrite(PIN_TCS_S0, HIGH);
  digitalWrite(PIN_TCS_S1, LOW);

  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL);
  Wire.setClock(400000);

  if (!mlx.begin()) {
    Serial.println(F("[WARN] MLX90614 not detected at boot -- will retry error on request"));
  } else {
    Serial.println(F("[OK] MLX90614 initialized"));
  }

  if (!max30102.begin(Wire, I2C_SPEED_FAST)) {
    Serial.println(F("[WARN] MAX30102 not detected at boot -- will retry error on request"));
  } else {
    max30102.setup();
    max30102.setPulseAmplitudeRed(0x0A);
    max30102.setPulseAmplitudeGreen(0);
    Serial.println(F("[OK] MAX30102 initialized"));
  }

  pinMode(PIN_MAX30102_INT, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(PIN_MAX30102_INT), onMax30102Interrupt, FALLING);

  // ---------------------------------------------------------------------
  // BLE init -- replaces SerialBT.register_callback(...) + SerialBT.begin(...)
  // ---------------------------------------------------------------------
  BLEDevice::init(BLE_DEVICE_NAME);
  BLEDevice::setMTU(BLE_PREFERRED_MTU); // see MTU assumption note at top of file

  pServer = BLEDevice::createServer();
  pServer->setCallbacks(new VitalsServerCallbacks());

  BLEService* pService = pServer->createService(SERVICE_UUID);

  pTxCharacteristic = pService->createCharacteristic(
      TX_CHARACTERISTIC_UUID,
      BLECharacteristic::PROPERTY_NOTIFY | BLECharacteristic::PROPERTY_READ);
  pTxCharacteristic->addDescriptor(new BLE2902()); // required for notifications to work

  pRxCharacteristic = pService->createCharacteristic(
      RX_CHARACTERISTIC_UUID,
      BLECharacteristic::PROPERTY_WRITE);
  pRxCharacteristic->setCallbacks(new VitalsRxCallbacks());

  pService->start();

  BLEAdvertising* pAdvertising = BLEDevice::getAdvertising();
  pAdvertising->addServiceUUID(SERVICE_UUID);
  pAdvertising->setScanResponse(true);
  pAdvertising->start();

  Serial.print(F("[BLE] Advertising as "));
  Serial.println(BLE_DEVICE_NAME);
  Serial.print(F("[BLE] Service UUID: "));
  Serial.println(SERVICE_UUID);

  currentState = STATE_IDLE;
  Serial.println(F("[BOOT] Ready."));
}

// ============================================================================
// MAIN LOOP
// ----------------------------------------------------------------------------
// serviceBluetoothInput() is gone: BLE command intake is now driven by
// VitalsRxCallbacks::onWrite (an interrupt/task-driven BLE stack callback),
// not a polled byte buffer. There is nothing to poll for commands anymore --
// this is an inherent property of the BLE API, not a protocol change.
// runStateMachine() is unchanged and still drives every sensor sequence.
// ============================================================================
void loop() {
  runStateMachine();
}

void handleIncomingCommand(String cmd) {
  cmd.trim(); // strips any stray whitespace/newline, same as original .trim()
  Serial.print(F("[BLE] Received: "));
  Serial.println(cmd);

  if (cmd == "PING") {
    bleSendRaw("PONG");
    return;
  }

  if (currentState != STATE_IDLE) {
    Serial.println(F("[WARN] Request received while busy -- ignoring"));
    return;
  }

  if (cmd == "REQ_ECG")        pendingRequest = REQ_ECG;
  else if (cmd == "REQ_URINE") pendingRequest = REQ_URINE;
  else if (cmd == "REQ_STETH") pendingRequest = REQ_STETH;
  else if (cmd == "REQ_TEMP")  pendingRequest = REQ_TEMP;
  else if (cmd == "REQ_SPO2")  pendingRequest = REQ_SPO2;
  else {
    Serial.println(F("[WARN] Unknown command"));
    return;
  }

  currentState = STATE_POST_REQUEST_DELAY;
  stateEnteredAt = millis();
}

// ----------------------------------------------------------------------------
// Top-level state machine (unchanged)
// ----------------------------------------------------------------------------
void runStateMachine() {
  uint32_t now = millis();

  switch (currentState) {
    case STATE_IDLE:
      break;

    case STATE_POST_REQUEST_DELAY:
      if (now - stateEnteredAt >= POST_REQUEST_DELAY_MS) {
        switch (pendingRequest) {
          case REQ_ECG:   currentState = STATE_RUN_ECG;   beginEcgSequence();   break;
          case REQ_URINE: currentState = STATE_RUN_URINE; beginUrineSequence(); break;
          case REQ_STETH: currentState = STATE_RUN_STETH; beginStethSequence(); break;
          case REQ_TEMP:  currentState = STATE_RUN_TEMP;  beginTempSequence();  break;
          case REQ_SPO2:  currentState = STATE_RUN_SPO2;  beginSpo2Sequence();  break;
          default:        currentState = STATE_IDLE;      break;
        }
        pendingRequest = REQ_NONE;
        stateEnteredAt = now;
      }
      break;

    case STATE_RUN_ECG:   stepEcgSequence();   break;
    case STATE_RUN_URINE: stepUrineSequence(); break;
    case STATE_RUN_STETH: stepStethSequence(); break;
    case STATE_RUN_TEMP:  stepTempSequence();  break;
    case STATE_RUN_SPO2:  stepSpo2Sequence();  break;
  }

  if (currentState != STATE_IDLE && currentState != STATE_POST_REQUEST_DELAY) {
    if (now - stateEnteredAt > SENSOR_STAGE_TIMEOUT_MS) {
      const char* code = "ERR";
      switch (currentState) {
        case STATE_RUN_ECG:   code = "ECG"; break;
        case STATE_RUN_URINE: code = "URINE"; break;
        case STATE_RUN_STETH: code = "STETH"; break;
        case STATE_RUN_TEMP:  code = "TEMP"; break;
        case STATE_RUN_SPO2:  code = "SPO2"; break;
        default: break;
      }
      sendError(code, "timeout");
      currentState = STATE_IDLE;
      subStep = 0;
    }
  }
}

// ============================================================================
// ECG (unchanged)
// ============================================================================
void beginEcgSequence() {
  subStep = 0;
  ecgSampleCount = 0;
  subStepStartedAt = millis();
  Serial.println(F("[ECG] Starting acquisition..."));
}

void stepEcgSequence() {
  uint32_t now = millis();
  bool leadsOff = (digitalRead(PIN_ECG_LO_PLUS) == HIGH) || (digitalRead(PIN_ECG_LO_MINUS) == HIGH);
  if (leadsOff && ecgSampleCount == 0 && (now - subStepStartedAt) > 3000) {
    sendError("ECG", "leads_off");
    currentState = STATE_IDLE;
    return;
  }

  if (now - lastEcgSampleAt >= ECG_SAMPLE_INTERVAL_MS) {
    lastEcgSampleAt = now;
    ecgSamples[ecgSampleCount++] = analogRead(PIN_ECG_ANALOG);
  }

  if (ecgSampleCount >= ECG_NUM_SAMPLES) {
    int bpm = estimateBpmFromEcgSamples();
    String json = "{\"heart_rate_bpm\":" + String(bpm) + ",\"samples\":[";
    for (int i = 0; i < ECG_NUM_SAMPLES; i++) {
      json += String(ecgSamples[i]);
      if (i < ECG_NUM_SAMPLES - 1) json += ",";
    }
    json += "]}";

    sendPacket("ECG", json);
    currentState = STATE_IDLE;
  }
}

int estimateBpmFromEcgSamples() {
  int mean = 0;
  for (int i = 0; i < ECG_NUM_SAMPLES; i++) mean += ecgSamples[i];
  mean /= ECG_NUM_SAMPLES;

  int peaks = 0;
  for (int i = 1; i < ECG_NUM_SAMPLES - 1; i++) {
    if (ecgSamples[i] > mean && ecgSamples[i] >= ecgSamples[i - 1] && ecgSamples[i] >= ecgSamples[i + 1]) {
      peaks++;
    }
  }
  float windowSeconds = (ECG_NUM_SAMPLES * ECG_SAMPLE_INTERVAL_MS) / 1000.0f;
  if (windowSeconds <= 0) return 0;
  int bpm = (int)((peaks / windowSeconds) * 60.0f);
  return bpm;
}

// ============================================================================
// TCS3200 (urine color) -- unchanged
// ============================================================================
void beginUrineSequence() {
  subStep = 0;
  subStepStartedAt = millis();
  Serial.println(F("[URINE] Please place the urine color strip in the sensor housing."));
}

void stepUrineSequence() {
  uint32_t now = millis();

  if (subStep == 0) {
    if (now - subStepStartedAt >= URINE_PLACEMENT_DELAY_MS) {
      subStep = 1;
    }
    return;
  }

  if (subStep == 1) {
    uint16_t r, g, b;
    readTcsColor(r, g, b);

    String json = "{\"red\":" + String(r) + ",\"green\":" + String(g) + ",\"blue\":" + String(b) + "}";
    sendPacket("URINE", json);
    currentState = STATE_IDLE;
  }
}

void readTcsColor(uint16_t &redCount, uint16_t &greenCount, uint16_t &blueCount) {
  digitalWrite(PIN_TCS_S2, LOW);
  digitalWrite(PIN_TCS_S3, LOW);
  unsigned long redPulse = pulseIn(PIN_TCS_OUT, LOW, TCS_PULSE_TIMEOUT_US);
  redCount = redPulse > 0 ? (uint16_t)(1000000UL / redPulse) : 0;

  digitalWrite(PIN_TCS_S2, HIGH);
  digitalWrite(PIN_TCS_S3, HIGH);
  unsigned long greenPulse = pulseIn(PIN_TCS_OUT, LOW, TCS_PULSE_TIMEOUT_US);
  greenCount = greenPulse > 0 ? (uint16_t)(1000000UL / greenPulse) : 0;

  digitalWrite(PIN_TCS_S2, LOW);
  digitalWrite(PIN_TCS_S3, HIGH);
  unsigned long bluePulse = pulseIn(PIN_TCS_OUT, LOW, TCS_PULSE_TIMEOUT_US);
  blueCount = bluePulse > 0 ? (uint16_t)(1000000UL / bluePulse) : 0;
}

// ============================================================================
// MAX4466 "stethoscope" -- unchanged
// ============================================================================
void beginStethSequence() {
  subStep = 0;
  stethSampleCount = 0;
  lastStethSampleAt = millis();
  Serial.println(F("[STETH] Taking readings..."));
}

void stepStethSequence() {
  uint32_t now = millis();

  if (now - lastStethSampleAt >= STETH_SAMPLE_INTERVAL_MS) {
    lastStethSampleAt = now;
    int val = analogRead(PIN_MIC_ANALOG);
    stethSamples[stethSampleCount] = val;

    Serial.print(F("[STETH] Sample "));
    Serial.print(stethSampleCount + 1);
    Serial.print(F("/50: "));
    Serial.println(val);

    stethSampleCount++;

    if (stethSampleCount >= STETH_NUM_SAMPLES) {
      int minVal = stethSamples[0];
      int maxVal = stethSamples[0];
      double sumSquares = 0;

      for (int i = 0; i < stethSampleCount; i++) {
        int v = stethSamples[i];
        if (v < minVal) minVal = v;
        if (v > maxVal) maxVal = v;
        sumSquares += (double)v * (double)v;
      }
      int rms = (int)sqrt(sumSquares / stethSampleCount);

      String json = "{\"rms\":" + String(rms) + ",\"min\":" + String(minVal) +
                     ",\"max\":" + String(maxVal) + ",\"samples\":" + String(stethSampleCount) + "}";
      sendPacket("STETH", json);
      currentState = STATE_IDLE;
    }
  }
}

// ============================================================================
// MLX90614 (temperature) -- unchanged
// ============================================================================
void beginTempSequence() {
  subStep = 0;
  subStepStartedAt = millis();
  Serial.println(F("[TEMP] Please hold the sensor about 3 cm from the forehead."));
}

void stepTempSequence() {
  uint32_t now = millis();

  if (subStep == 0) {
    if (now - subStepStartedAt >= MLX_PLACEMENT_DELAY_MS) {
      subStep = 1;
    }
    return;
  }

  if (subStep == 1) {
    double tempC = mlx.readObjectTempC();

    if (isnan(tempC)) {
      sendError("TEMP", "i2c_read_failed");
    } else {
      String json = "{\"body_temp_c\":" + String(tempC, 1) + "}";
      sendPacket("TEMP", json);
    }
    currentState = STATE_IDLE;
  }
}

// ============================================================================
// MAX30102 (pulse oximeter: HR + SpO2) -- unchanged
// ============================================================================
void beginSpo2Sequence() {
  subStep = 0;
  spo2SampleCount = 0;
  subStepStartedAt = millis();
  Serial.println(F("[SPO2] Please place your finger on the sensor."));
}

void stepSpo2Sequence() {
  uint32_t now = millis();

  switch (subStep) {
    case 0:
      if (now - subStepStartedAt >= SPO2_PLACE_FINGER_MSG_MS) {
        subStep = 1;
        subStepStartedAt = now;
      }
      break;

    case 1: {
      if (now - subStepStartedAt > SPO2_SAMPLE_TIMEOUT_MS) {
        sendError("SPO2", "no_finger_detected");
        currentState = STATE_IDLE;
        return;
      }

      if (max30102.available()) {
        redBuffer[spo2SampleCount] = max30102.getRed();
        irBuffer[spo2SampleCount] = max30102.getIR();
        max30102.nextSample();
        spo2SampleCount++;
      } else {
        max30102.check();
      }

      if (spo2SampleCount > 0 && irBuffer[spo2SampleCount - 1] < 5000) {
        spo2SampleCount = 0;
      }

      if (spo2SampleCount >= SPO2_BUFFER_LEN) {
        maxim_heart_rate_and_oxygen_saturation(
          irBuffer, SPO2_BUFFER_LEN, redBuffer,
          &spo2Value, &spo2Valid, &heartRateValue, &heartRateValid);
        subStep = 2;
      }
      break;
    }

    case 2: {
      if (!heartRateValid || !spo2Valid) {
        sendError("SPO2", "algorithm_low_confidence");
      } else {
        String json = "{\"heart_rate_bpm\":" + String(heartRateValue) +
                       ",\"spo2_percent\":" + String(spo2Value) +
                       ",\"ir_raw\":" + String(irBuffer[SPO2_BUFFER_LEN - 1]) + "}";
        sendPacket("SPO2", json);
      }
      currentState = STATE_IDLE;
      break;
    }
  }
}

// ============================================================================
// PACKET FRAMING / CRC -- algorithm and format byte-for-byte unchanged.
// Only sendPacket()'s final transmit line changes (BLE notify instead of
// SerialBT.println), and it no longer appends a trailing "\n" since BLE
// notifications are already discrete, length-delimited messages.
// ============================================================================
uint8_t crc8(const uint8_t *data, size_t len) {
  uint8_t crc = 0x00;
  for (size_t i = 0; i < len; i++) {
    crc ^= data[i];
    for (uint8_t bit = 0; bit < 8; bit++) {
      if (crc & 0x80) crc = (crc << 1) ^ 0x07;
      else crc <<= 1;
    }
  }
  return crc;
}

void sendPacket(const String &sensorCode, const String &jsonPayload) {
  String body = sensorCode + "|" + jsonPayload;
  uint8_t crc = crc8((const uint8_t *)body.c_str(), body.length());

  char crcHex[3];
  snprintf(crcHex, sizeof(crcHex), "%02X", crc);

  String fullPacket = body + "|" + String(crcHex);

  bleSendRaw(fullPacket);

  Serial.print(F("[TX] "));
  Serial.println(fullPacket);
}

void sendError(const String &sensorCode, const String &reason) {
  String json = "{\"sensor\":\"" + sensorCode + "\",\"reason\":\"" + reason + "\"}";
  sendPacket("ERR", json);
}

// ----------------------------------------------------------------------------
// bleSendRaw() -- the one true transmit primitive, replacing
// SerialBT.println(). Every outgoing line (packets AND the PONG reply) goes
// through here, same as every outgoing line went through SerialBT before.
//
// Now chunking-aware (PRD §6.3): computes how many bytes fit in one
// notification at the CURRENT negotiated MTU and splits into multiple
// chunks if needed. Every notification -- even a 1-chunk one -- carries the
// 1-byte header described in the MTU/CHUNKING note at the top of this file.
// ----------------------------------------------------------------------------
void bleSendRaw(const String &line) {
  if (!bleClientConnected || pTxCharacteristic == nullptr) {
    txDroppedNoClient++;
    Serial.print(F("[BLE] No client connected -- dropping outgoing line (total dropped: "));
    Serial.print(txDroppedNoClient);
    Serial.println(F(")"));
    return;
  }

  size_t totalLen = line.length();

  int capacitySigned = (int)negotiatedMtu - ATT_HEADER_OVERHEAD - CHUNK_HEADER_SIZE;
  size_t capacity = (capacitySigned > 1) ? (size_t)capacitySigned : 1; // pathological-MTU guard

  uint8_t totalChunks = (uint8_t)((totalLen + capacity - 1) / capacity);
  if (totalChunks == 0) totalChunks = 1; // empty line edge case

  if (totalChunks > MAX_CHUNKS) {
    txDroppedOversized++;
    Serial.print(F("[BLE][ERROR] Packet needs "));
    Serial.print(totalChunks);
    Serial.print(F(" chunks at MTU "));
    Serial.print(negotiatedMtu);
    Serial.print(F(", max is "));
    Serial.print(MAX_CHUNKS);
    Serial.print(F(". Dropping packet (total oversized drops: "));
    Serial.print(txDroppedOversized);
    Serial.println(F(")"));
    return;
  }

  uint8_t buf[BLE_PREFERRED_MTU]; // fixed upper bound; negotiatedMtu never exceeds this, see setup()

  for (uint8_t i = 0; i < totalChunks; i++) {
    size_t start = (size_t)i * capacity;
    size_t len = min(capacity, totalLen - start);

    buf[0] = (uint8_t)((totalChunks << 4) | i); // top nibble = total chunks, bottom nibble = index
    memcpy(buf + 1, line.c_str() + start, len);

    pTxCharacteristic->setValue(buf, len + 1);
    pTxCharacteristic->notify();

    if (totalChunks > 1) {
      delay(15); // brief spacing so consecutive notifications aren't dropped by the phone's BLE stack
    }
  }
}
