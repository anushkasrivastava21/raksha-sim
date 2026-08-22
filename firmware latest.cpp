/*
  ============================================================================
  ESP32_VitalsRig.ino
  ============================================================================
  Hackathon vitals rig firmware.

  ROLE OF THIS FILE
  ------------------
  The ESP32 owns all five sensors. The Raspberry Pi 4 (RPi4) is the master:
  it sends a short text command over Bluetooth Classic SPP asking for ONE
  sensor's reading. The ESP32 waits 5s (per your spec), runs that sensor's
  full read sequence (with its own messages/delays), then sends back a
  single framed packet containing a JSON fragment + CRC8 checksum. The RPi4
  is responsible for stashing each fragment into its own per-sensor Python
  file and assembling the final unified JSON once all sensors are done.

  REQUIRED ARDUINO LIBRARIES (Library Manager)
  ---------------------------------------------
  - "SparkFun MAX3010x Pulse and Proximity Sensor Library" (MAX30105.h)
      -> also pulls in heartRate.h. The full SpO2 algorithm needs
         spo2_algorithm.h/.cpp, which SparkFun ships inside that library's
         examples (Example5_HeartRateAndSpO2). If it isn't auto-included,
         copy spo2_algorithm.h/.cpp into this sketch folder.
  - "Adafruit MLX90614 Library" (+ Adafruit BusIO dependency)
  - ESP32 core's built-in "BluetoothSerial.h" (Classic SPP) -- no install
    needed, just make sure Tools > Board is an ESP32 board and that
    Bluetooth Classic is enabled for it (regular ESP32 dev boards are fine;
    ESP32-S3/C3 do NOT support Classic BT -- use a plain ESP32 for this).

  BOARD SETTINGS
  --------------
  Tools > Board: "ESP32 Dev Module" (or your specific ESP32 board)
  Partition scheme: default (Bluetooth Classic needs BT enabled at build)

  ============================================================================
  PIN MAP -- taken verbatim from ESP32_Medical_Hardware_Pin_Configuration.docx
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

  All modules share a common GND rail. Bluetooth is ESP32's onboard
  Classic SPP radio -- no wired UART to the RPi4.

  ============================================================================
  WIRE PROTOCOL (ESP32 <-> RPi4 over Bluetooth SPP)
  ============================================================================
  RPi4 -> ESP32 (ASCII, newline terminated):
      "REQ_ECG\n"
      "REQ_URINE\n"
      "REQ_STETH\n"
      "REQ_TEMP\n"
      "REQ_SPO2\n"
      "PING\n"        -> ESP32 replies "PONG\n" (liveness check, no sensor)

  ESP32 -> RPi4 (ASCII, newline terminated), fixed field order:
      "<SENSOR_CODE>|<json_payload>|<CRC8_hex>\n"

      SENSOR_CODE is one of: ECG, URINE, STETH, TEMP, SPO2, ERR
      json_payload is a single line of compact JSON matching the field
        names used in your unified JSON schema (no spaces needed, but a
        few are fine -- the CRC covers exactly what's between the pipes).
      CRC8_hex is a 2-character uppercase hex CRC8 (poly 0x07) computed
        over the raw bytes of "<SENSOR_CODE>|<json_payload>" (i.e.
        everything before the second pipe). The RPi4 side should
        recompute the same CRC8 over that substring to validate.

      On error/timeout the ESP32 sends, e.g.:
        "ERR|{\"sensor\":\"TEMP\",\"reason\":\"i2c_timeout\"}|4F\n"

  ============================================================================
  DESIGN NOTES / ASSUMPTIONS 
  ============================================================================
  1. Stethoscope modified per user request: takes exactly 50 samples, one sample 
     every 1 second, without inhale/exhale prompts, displays each reading every second,
     then computes min, max, and rms values.
  2. BPM from only 20 raw ECG samples is not physiologically robust (a real
     beat only needs ~1 QRS peak per ~0.6-1s, and 20 raw ADC points is a
     very short window). I implemented a simple threshold peak-counter as a
     placeholder (clearly marked).
  3. TCS3200 color classification (pale/deep/reddish yellow) is NOT done on
     the ESP32 -- it just ships raw R/G/B frequency counts. 
  4. "Non-blocking" is implemented as: the Bluetooth link and command
     parser are always serviced every loop() iteration.
  ============================================================================
*/

#include <Wire.h>
#include <BluetoothSerial.h>
#include <Adafruit_MLX90614.h>
#include <MAX30105.h>
#include "spo2_algorithm.h"   // ships with SparkFun MAX3010x library examples

#if !defined(CONFIG_BT_ENABLED) || !defined(CONFIG_BLUEDROID_ENABLED)
#error Bluetooth is not enabled! Please run `make menuconfig` / select an ESP32 board that supports Classic BT SPP.
#endif

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
// GLOBAL OBJECTS
// ----------------------------------------------------------------------------
BluetoothSerial SerialBT;
Adafruit_MLX90614 mlx = Adafruit_MLX90614();
MAX30105 max30102;

volatile bool max30102InterruptFlag = false;
void IRAM_ATTR onMax30102Interrupt() {
  max30102InterruptFlag = true;
}

bool btClientConnected = false;

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

// Generic sub-step counter 
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

void readTcsColor(uint16_t &redCount, uint16_t &greenCount, uint16_t &blueCount);

// ============================================================================
// SETUP
// ============================================================================
void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println(F("[BOOT] ESP32 Vitals Rig starting..."));

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

  SerialBT.register_callback([](esp_spp_cb_event_t event, esp_spp_cb_param_t *param) {
    if (event == ESP_SPP_SRV_OPEN_EVT || event == ESP_SPP_OPEN_EVT) {
      btClientConnected = true;
      Serial.println(F("[BT] Client connected"));
    } else if (event == ESP_SPP_CLOSE_EVT) {
      btClientConnected = false;
      Serial.println(F("[BT] Client disconnected -- awaiting reconnection"));
      currentState = STATE_IDLE;
      pendingRequest = REQ_NONE;
    }
  });

  SerialBT.begin("ESP32_VitalsRig_01"); 
  Serial.println(F("[BT] SPP advertising as ESP32_VitalsRig_01"));

  currentState = STATE_IDLE;
  Serial.println(F("[BOOT] Ready."));
}

// ============================================================================
// MAIN LOOP
// ============================================================================
void loop() {
  serviceBluetoothInput();
  runStateMachine();
}

// ----------------------------------------------------------------------------
// Bluetooth command intake 
// ----------------------------------------------------------------------------
void serviceBluetoothInput() {
  static String lineBuf;
  while (SerialBT.available()) {
    char c = (char)SerialBT.read();
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
  Serial.print(F("[BT] Received: "));
  Serial.println(cmd);

  if (cmd == "PING") {
    SerialBT.println("PONG");
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
// PACKET FRAMING / CRC
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

  if (btClientConnected) {
    SerialBT.println(fullPacket);
  }
  Serial.print(F("[TX] "));
  Serial.println(fullPacket);
}

void sendError(const String &sensorCode, const String &reason) {
  String json = "{\"sensor\":\"" + sensorCode + "\",\"reason\":\"" + reason + "\"}";
  sendPacket("ERR", json);
}