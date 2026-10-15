# Huasi / Kohler / Electriq (ZNTech) BME CAN Protocol Specification

**Target Hardware:** CATL LBM025100A 24V (8S1P LiFePO4) Battery Module  
**Internal Slave Board:** Huasi / Zhongneng BME (Battery Monitoring Electronics)  
**CAN Layer:** CAN 2.0B Extended (29-bit Identifier), 250 kbps, 11-bit standard frames ignored.  
**Architecture:** Passive Master-Query / Slave-Response (Slaves never transmit unsolicited frames).

---

## 1. Physical Interface & Wakeup Requirements

The battery module provides an external 6-pin Amphenol / industrial rectangular connector:

```
           +-------------------------+
   Row 1   |  CAN1H (Yel) | CAN1L (Grn) |
   Row 2   |  CL+   (Red) | CL-   (Blk) |
   Row 3   |  24V+  (Red) | 24V-  (Blk) |
           +-------------------------+
```

### 1.1 Pin Functions & Wiring
| Row / Pin | Signal | Wire Color | Description | Connection on BMSanta |
|:---:|:---|:---|:---|:---|
| **Row 1 - Pin 1** | `CAN1H` | Yellow | CAN Differential High | Waveshare CAN HAT CAN0 `H` |
| **Row 1 - Pin 2** | `CAN1L` | Green | CAN Differential Low | Waveshare CAN HAT CAN0 `L` |
| **Row 2 - Pin 3** | `CL+` | Red | Cascade Loop Wakeup (+) | Jumped to `24V+` |
| **Row 2 - Pin 4** | `CL-` | Black | Cascade Loop Wakeup (-) | Jumped to `24V-` |
| **Row 3 - Pin 5** | `24V+` | Red | Auxiliary Module Power (+) | +24V DC Bench Supply / Bus |
| **Row 3 - Pin 6** | `24V-` | Black | Auxiliary Module Power (-) | 24V DC Return (GND) |

### 1.2 Wakeup Interlock Circuit
- The internal BME microcontrollers are galvanically isolated and powered via the 24V auxiliary power lines.
- **Interlock Loop:** The `CL+` and `CL-` pins form a high-impedance cascade loop (measured at ~1.4 MΩ). 
- In factory operation, the master controller daisy-chains this loop through all modules to detect pack presence and enforce physical cable connection.
- **Activation:** Tying `CL+` to `24V+` and `CL-` to `24V-` closes the loop, enabling the BME CAN transceiver and MCU to answer queries.

---

## 2. Bus Characteristics & Master Polling

- **Bus Bitrate:** 250,000 baud (250 kbps)
- **Arbitration ID Format:** 29-bit Extended (`EFF`)
- **Master Query Identifier:** `0x180011F4`
- **Query Frame Payload:** 8 bytes

```
+------------+------------+------------+------------+------------+------------+------------+------------+
|   Byte 0   |   Byte 1   |   Byte 2   |   Byte 3   |   Byte 4   |   Byte 5   |   Byte 6   |   Byte 7   |
+------------+------------+------------+------------+------------+------------+------------+------------+
|  Node ID   | Command ID |    0x00    |    0x00    |    0x00    |    0x00    |    0x00    |    0x00    |
+------------+------------+------------+------------+------------+------------+------------+------------+
```

### 2.1 Node Addressing
- Primary / Standalone Module: `0xAA`
- In multi-module parallel daisy-chained configurations, nodes increment sequentially:
  - Module 1: `0xAA`
  - Module 2: `0xAB`
  - Module 3: `0xAC`
  - Module 4: `0xAD`
  - Module $N$: `0xAA + (N - 1)`

---

## 3. Command & Telemetry Mappings

### 3.1 Command `0x13` - Cell Voltages
Queries the individual cell voltages for the specified module.

- **Request:** `[<Node_ID>, 0x13, 00, 00, 00, 00, 00, 00]` to `0x180011F4`
- **Response Frames:** 2 multi-packets (4 cells each, little-endian unsigned 16-bit integers in millivolts).

#### Frame 1: Cells 1 to 4
- **CAN ID:** `0x180013<Node_ID>` (e.g., `0x180013AA`)
- **DLC:** 8 bytes

| Bytes | Signal | Type | Scaling / Unit | Example Hex | Decoded Value |
|:---:|:---|:---:|:---:|:---:|:---:|
| 0–1 | Cell 1 Voltage | uint16 LE | 1 mV (0.001 V) | `A4 0C` (0x0CA4) | 3,236 mV (3.236 V) |
| 2–3 | Cell 2 Voltage | uint16 LE | 1 mV (0.001 V) | `A5 0C` (0x0CA5) | 3,237 mV (3.237 V) |
| 4–5 | Cell 3 Voltage | uint16 LE | 1 mV (0.001 V) | `A4 0C` (0x0CA4) | 3,236 mV (3.236 V) |
| 6–7 | Cell 4 Voltage | uint16 LE | 1 mV (0.001 V) | `A4 0C` (0x0CA4) | 3,236 mV (3.236 V) |

#### Frame 2: Cells 5 to 8
- **CAN ID:** `0x180113<Node_ID>` (e.g., `0x180113AA`)
- **DLC:** 8 bytes

| Bytes | Signal | Type | Scaling / Unit | Example Hex | Decoded Value |
|:---:|:---|:---:|:---:|:---:|:---:|
| 0–1 | Cell 5 Voltage | uint16 LE | 1 mV (0.001 V) | `A6 0C` (0x0CA6) | 3,238 mV (3.238 V) |
| 2–3 | Cell 6 Voltage | uint16 LE | 1 mV (0.001 V) | `A6 0C` (0x0CA6) | 3,238 mV (3.238 V) |
| 4–5 | Cell 7 Voltage | uint16 LE | 1 mV (0.001 V) | `97 0C` (0x0C97) | 3,223 mV (3.223 V) |
| 6–7 | Cell 8 Voltage | uint16 LE | 1 mV (0.001 V) | `A6 0C` (0x0CA6) | 3,238 mV (3.238 V) |

---

### 3.2 Command `0x14` - Temperatures
Queries internal NTC thermistor temperatures.

- **Request:** `[<Node_ID>, 0x14, 00, 00, 00, 00, 00, 00]` to `0x180011F4`
- **Response Frame:** 1 packet
- **CAN ID:** `0x180014<Node_ID>` (e.g., `0x180014AA`)
- **DLC:** 4 bytes

| Bytes | Signal | Type | Scaling / Unit | Example Hex | Decoded Value |
|:---:|:---|:---:|:---:|:---:|:---:|
| 0–1 | Temp Sensor 1 | uint16 LE | 0.1 °F | `D8 02` (0x02D8 = 728) | 72.8 °F (22.67 °C) |
| 2–3 | Temp Sensor 2 | uint16 LE | 0.1 °F | `D4 02` (0x02D4 = 724) | 72.4 °F (22.44 °C) |

**Conversion Formula to Celsius:**
$$T_{\text{Celsius}} = \left(\frac{\text{raw}}{10} - 32\right) \times \frac{5}{9}$$

---

### 3.3 Command `0x15` - Geometry & Topology
Queries module configuration (cell count and temperature channel count).

- **Request:** `[<Node_ID>, 0x15, 00, 00, 00, 00, 00, 00]` to `0x180011F4`
- **Response Frame:** 1 packet
- **CAN ID:** `0x180015<Node_ID>` (e.g., `0x180015AA`)
- **DLC:** 2 bytes

| Byte | Field | Value | Meaning |
|:---:|:---|:---:|:---|
| 0 | Cell Count | `0x08` | 8 Cells in series (8S) |
| 1 | Temperature Channels | `0x02` | 2 Thermistor probes |

---

### 3.4 Command `0x12` - Status, Balancing & Fault Diagnostics
Queries module health, balancing status, alarm bitmasks, and firmware revision.

- **Request:** `[<Node_ID>, 0x12, 00, 00, 00, 00, 00, 00]` to `0x180011F4`
- **Response Frames:** 5 multi-packets

| CAN ID | DLC | Raw Payload (Normal) | Description |
|:---|:---:|:---|:---|
| `0x180012AA` | 8 | `08 02 01 00 00 00 00 00` | Byte 0: 8 cells, Byte 1: 2 temps, Byte 2: Status (0x01 = Online/Normal), Bytes 3-7: Fault bits |
| `0x180112AA` | 8 | `00 00 00 00 00 00 00 00` | Cell Passive Balancing Status (Bitmask per cell) |
| `0x180212AA` | 8 | `00 00 00 00 00 00 00 00` | Warning & Protection Bitmasks (OVP, UVP, OTP, UTP) |
| `0x180312AA` | 8 | `F5 02 00 00 00 00 00 00` | Internal Diagnostic / Calibration Register |
| `0x180412AA` | 5 | `00 00 01 20 00` | Firmware Version (`v1.20.00`) |

---

## 4. Python Polling Reference Implementation

```python
import socket
import struct
import select

CAN_EFF = 0x80000000
POLL_ID = 0x180011F4 | CAN_EFF

def query_node_voltages(sock, node_id=0xAA):
    # Send Command 0x13
    payload = bytes([node_id, 0x13, 0, 0, 0, 0, 0, 0])
    sock.send(struct.pack("=IB3x8s", POLL_ID, 8, payload))
    
    cells = [0.0] * 8
    # Read responses
    for _ in range(5):
        r, _, _ = select.select([sock], [], [], 0.05)
        if not r: break
        raw = sock.recv(16)
        if len(raw) >= 16:
            r_id, r_dlc, r_data = struct.unpack("=IB3x8s", raw)
            clean_id = r_id & 0x1FFFFFFF
            if clean_id == (0x18001300 | node_id):
                cells[0:4] = [struct.unpack("<H", r_data[i:i+2])[0] / 1000.0 for i in range(0, 8, 2)]
            elif clean_id == (0x18011300 | node_id):
                cells[4:8] = [struct.unpack("<H", r_data[i:i+2])[0] / 1000.0 for i in range(0, 8, 2)]
    return cells
```
