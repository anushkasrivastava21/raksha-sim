# 1. Custom BLE Transport for ESP32

Date: 2026-09-16

## Context
The original firmware communicated over USB/UART (Bluetooth Classic SPP) to a Raspberry Pi. The project requirements pivoted to require direct communication with a Flutter mobile app (`raksha-dash`) via Bluetooth Low Energy (BLE).

## Decision
We implemented a custom BLE GATT Server on the ESP32.
- **Service UUID**: `6fa41660-6244-4aa0-aee4-06d9377ad51b`
- **RX UUID**: `bf9dace6-017f-4793-abf9-5d117db16e55`
- **TX UUID**: `41d5a28d-de2a-4ab4-aa6e-31ab8472925c`

To ensure payload integrity without dropping large packets (e.g., continuous ECG streams), we adopted a chunking protocol over BLE with an explicit MTU negotiation of `247`. Every packet is preceded by a 1-byte header containing the total chunk count and current index.

## Consequences
- **Positive**: Direct mobile app integration is now fully functional and performant. Android MTU packet truncation issues are bypassed.
- **Negative**: Increased complexity on the Flutter client side, which now must manually request an MTU of 247 and parse/re-assemble bit-masked chunk headers before running CRC validation.
