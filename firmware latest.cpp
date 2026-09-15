/*
  ============================================================================
  ESP32_VitalsRig.ino
  ============================================================================
  Hackathon vitals rig firmware.

  UPDATED FOR BLE:
  This version uses ESP32 BLE (GATT Server) instead of USB Serial.
  It advertises as "Raksha_Vitals_Rig" with a standard Nordic UART Service UUID.
  It chunks large JSON payloads over BLE Notifications (TX) and receives commands
  over BLE Writes (RX).
*/

#include <Wire.h>
#include <Adafruit_MLX90614.h>
#include <MAX30105.h>
#include "spo2_algorithm.h"   // ships with SparkFun MAX3010x library examples
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>

// ----------------------------------------------------------------------------
// BLE UUIDs (Nordic UART Service)
// ----------------------------------------------------------------------------
#define SERVICE_UUID           "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
#define CHARACTERISTIC_UUID_RX "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
#define CHARACTERISTIC_UUID_TX "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

BLEServer *pServer = NULL;
BLECharacteristic * pTxCharacteristic;
bool deviceConnected = false;
bool oldDeviceConnected = false;

// ----------------------------------------------------------------------------
// PIN DEFINITIONS
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
// TUNABLE CONSTANTS
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
// GLOBAL SENSOR OBJECTS
// ----------------------------------------------------------------------------
Adafruit_MLX90614 mlx = Adafruit_MLX90614();
MAX30105 max30102;

// ----------------------------------------------------------------------------
// STATE MACHINE TYPES
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

int subStep = 0;
uint32_t subStepStartedAt = 0;

// ----------------------------------------------------------------------------
// PER-SENSOR WORKING BUFFERS
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
int estimateBpmFromEcgSamples();
void readTcsColor(uint16_t &redCount, uint16_t &greenCount, uint16_t &blueCount);

// ----------------------------------------------------------------------------
// BLE CALLBACKS
// ----------------------------------------------------------------------------
class MyServerCallbacks: public BLEServerCallbacks {
    void onConnect(BLEServer* pServer) {
      deviceConnected = true;
      Serial.println(F("[BLE] Client connected!"));
    };

    void onDisconnect(BLEServer* pServer) {
      deviceConnected = false;
      Serial.println(F("[BLE] Client disconnected."));
    }
};

class MyCallbacks: public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic *pCharacteristic) {
      String rxValue = pCharacteristic->getValue().c_str();
      if (rxValue.length() > 0) {
        String cleanCmd = rxValue;
        cleanCmd.trim();
        handleIncomingCommand(cleanCmd);
      }
    }
};

void onMax30102Interrupt() {
  // empty interrupt handler as original
}

// ============================================================================
// SETUP
// ============================================================================
void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println(F("[BOOT] ESP32 Vitals Rig starting..."));

  // Pins Setup
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

  // Sensor Init
  if (!mlx.begin()) {
    Serial.println(F("[WARN] MLX90614 not detected at boot"));
  } else {
    Serial.println(F("[OK] MLX90614 initialized"));
  }

  if (!max30102.begin(Wire, I2C_SPEED_FAST)) {
    Serial.println(F("[WARN] MAX30102 not detected at boot"));
  } else {
    max30102.setup(); 
    max30102.setPulseAmplitudeRed(0x0A);
    max30102.setPulseAmplitudeGreen(0);
    Serial.println(F("[OK] MAX30102 initialized"));
  }

  pinMode(PIN_MAX30102_INT, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(PIN_MAX30102_INT), onMax30102Interrupt, FALLING);

  // BLE Setup
  BLEDevice::init("Raksha_Vitals_Rig");
  pServer = BLEDevice::createServer();
  pServer->setCallbacks(new MyServerCallbacks());

  BLEService *pService = pServer->createService(SERVICE_UUID);

  pTxCharacteristic = pService->createCharacteristic(
                      CHARACTERISTIC_UUID_TX,
                      BLECharacteristic::PROPERTY_NOTIFY
                    );
  pTxCharacteristic->addDescriptor(new BLE2902());

  BLECharacteristic * pRxCharacteristic = pService->createCharacteristic(
                       CHARACTERISTIC_UUID_RX,
                       BLECharacteristic::PROPERTY_WRITE
                     );
  pRxCharacteristic->setCallbacks(new MyCallbacks());

  pService->start();
  pServer->getAdvertising()->start();
  Serial.println(F("[BLE] Advertising started. Waiting for connections..."));

  currentState = STATE_IDLE;
  Serial.println(F("[BOOT] Ready."));
}

// ============================================================================
// MAIN LOOP
// ============================================================================
void loop() {
  // BLE Connection management
  if (!deviceConnected && oldDeviceConnected) {
      delay(500); 
      pServer->startAdvertising(); 
      Serial.println(F("[BLE] Restarting advertising..."));
      oldDeviceConnected = deviceConnected;
  }
  if (deviceConnected && !oldDeviceConnected) {
      oldDeviceConnected = deviceConnected;
  }

  serviceSerialInput(); // Keep Serial available for debugging
  runStateMachine();
}

// ----------------------------------------------------------------------------
// Serial command intake (Debugging)
// ----------------------------------------------------------------------------
void serviceSerialInput() {
  static String lineBuf;
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (lineBuf.length() > 0) {
        handleIncomingCommand(lineBuf);
        lineBuf = "";
      }
    } else {
      lineBuf += c;
      if (lineBuf.length() > 64) lineBuf = ""; 
    }
  }
}

void handleIncomingCommand(String cmd) {
  cmd.trim();
  Serial.print(F("[CMD] Received: "));
  Serial.println(cmd);

  if (cmd == "PING") {
    sendPacket("SYS", "{\"response\":\"PONG\"}");
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
// Top-level state machine
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
// ECG
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
// TCS3200 (urine color)
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
// MAX4466 "stethoscope" 
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
// MLX90614 (temperature)
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
// MAX30102 (pulse oximeter: HR + SpO2)
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
// PACKET FRAMING / CRC & BLE TRANSMISSION
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

  String fullPacket = body + "|" + String(crcHex) + "\n"; // appended newline for EOM tracking

  Serial.print(F("[TX-DEBUG] "));
  Serial.print(fullPacket);

  // Send via BLE using MTU chunking (default BLE MTU is 23 bytes -> 20 payload)
  if (deviceConnected) {
    int maxChunk = 20;
    for (int i = 0; i < fullPacket.length(); i += maxChunk) {
      String chunk = fullPacket.substring(i, i + maxChunk);
      pTxCharacteristic->setValue(chunk.c_str());
      pTxCharacteristic->notify();
      delay(10); // Small delay to prevent queue overflow
    }
    Serial.println(F("[BLE] Payload sent successfully."));
  } else {
    Serial.println(F("[BLE] Payload NOT sent - No Client Connected."));
  }
}

void sendError(const String &sensorCode, const String &reason) {
  String json = "{\"sensor\":\"" + sensorCode + "\",\"reason\":\"" + reason + "\"}";
  sendPacket("ERR", json);
}