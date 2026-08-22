import socket
import json
import time

# --- CONFIGURATION ---
# Replace with your ESP32's actual Bluetooth MAC address
ESP32_MAC = "XX:XX:XX:XX:XX:XX" 
BT_PORT = 1 
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
    print(f"Connecting to ESP32 at {ESP32_MAC}...")
    
    try:
        # Initialize Bluetooth RFCOMM Socket
        sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        sock.settimeout(TIMEOUT_SECONDS)
        sock.connect((ESP32_MAC, BT_PORT))
        print("Connected successfully!\n")
    except Exception as e:
        print(f"Failed to connect: {e}")
        return

    commands = ["PING", "REQ_TEMP", "REQ_URINE", "REQ_ECG", "REQ_SPO2", "REQ_STETH"]

    for cmd in commands:
        print(f"--- Sending: {cmd} ---")
        
        # Send command with newline termination
        sock.send(f"{cmd}\n".encode('utf-8'))
        
        try:
            # Read response until newline
            response_bytes = b""
            while True:
                char = sock.recv(1)
                if not char or char == b'\n':
                    break
                if char != b'\r':  # Ignore carriage returns
                    response_bytes += char
            
            response_str = response_bytes.decode('utf-8')
            print(f"Raw Response: {response_str}")

            if cmd == "PING" and response_str == "PONG":
                print("Ping successful.\n")
                continue

            # Parse the framed packet: <SENSOR_CODE>|<json_payload>|<CRC8_hex>
            parts = response_str.split('|')
            if len(parts) != 3:
                print("Error: Malformed packet received.\n")
                continue

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

        except socket.timeout:
            print("Request timed out. The ESP32 took too long to respond.\n")
        except Exception as e:
            print(f"Communication error: {e}\n")
            
        time.sleep(2) # Brief pause before the next request

    sock.close()
    print("Session complete. Socket closed.")

if __name__ == "__main__":
    main()