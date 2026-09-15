import serial
import json
import time

# --- CONFIGURATION ---
# Configure the serial port for the ESP32 (usually /dev/ttyUSB0 or /dev/ttyACM0 on Linux/RPi)
SERIAL_PORT = "/dev/ttyUSB0" 
BAUD_RATE = 115200 
TIMEOUT_SECONDS = 75  # 75s covers the 5s delay + 50s steth read time

def calc_crc8(data_bytes):
    """Calculates CRC8 (poly 0x07) matching the ESP32 firmware."""
    crc = 0x00
    for byte in data_bytes:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x07) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return f"{crc:02X}"

def main():
    print(f"Connecting to ESP32 on {SERIAL_PORT}...")
    
    try:
        # Initialize Serial Connection
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
        time.sleep(2) # Give the ESP32 a moment to reset upon connection
        ser.reset_input_buffer()
        print("Connected successfully!\n")
    except Exception as e:
        print(f"Failed to connect: {e}")
        return

    commands = ["PING", "REQ_TEMP", "REQ_URINE", "REQ_ECG", "REQ_SPO2", "REQ_STETH"]

    for cmd in commands:
        print(f"--- Sending: {cmd} ---")
        
        # Send command with newline termination
        ser.write(f"{cmd}\n".encode('utf-8'))
        
        try:
            response_str = ""
            start_time = time.time()
            
            # Read lines until we get a valid packet or timeout
            while True:
                if time.time() - start_time > TIMEOUT_SECONDS:
                    raise serial.SerialTimeoutException("Request timed out.")
                    
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if not line:
                    continue
                    
                print(f"Raw Serial: {line}")

                if cmd == "PING" and line == "PONG":
                    print("Ping successful.\n")
                    response_str = line
                    break
                    
                parts = line.split('|')
                if len(parts) == 3:
                    response_str = line
                    break

            if cmd == "PING" and response_str == "PONG":
                continue
                
            # Parse the framed packet: <SENSOR_CODE>|<json_payload>|<CRC8_hex>
            parts = response_str.split('|')

            sensor_code, json_payload, received_crc = parts
            
            # Recompute CRC over everything before the second pipe
            data_to_verify = f"{sensor_code}|{json_payload}".encode('utf-8')
            computed_crc = calc_crc8(data_to_verify)

            if computed_crc != received_crc:
                print(f"CRC Error! Expected {received_crc}, got {computed_crc}\n")
                continue

            print(f"CRC Validated. Sensor: {sensor_code}")
            
            if sensor_code == "ERR":
                print(f"ESP32 reported an error: {json_payload}\n")
            else:
                parsed_json = json.loads(json_payload)
                print(f"Parsed Data: {json.dumps(parsed_json, indent=2)}\n")

        except serial.SerialTimeoutException:
            print("Request timed out. The ESP32 took too long to respond.\n")
        except Exception as e:
            print(f"Communication error: {e}\n")
            
        time.sleep(2) # Brief pause before the next request

    ser.close()
    print("Session complete. Serial port closed.")

if __name__ == "__main__":
    main()