# BMSanta: Battery Management System & Gateway Controller

An open, modular Battery Management System (BMS) controller and inverter gateway designed for second-life **CATL LBM025100A** 24V battery modules running in a parallel configuration.

---

## 1. System Overview & Objective

The primary objective of **BMSanta** is to provide active monitoring, cell-level safety, multi-pack aggregation, and inverter communication for surplus **Kohler / Electriq Power (Huasi/ZNTECH) LBM025100A** battery modules.

```
                          +-------------------------------------------------+
                          |             Raspberry Pi 2 (v1.1)               |
                          |            (Quad-core ARMv7, 1GB RAM)           |
                          +-----------------------+-------------------------+
                                                  |
                     +----------------------------+----------------------------+
                     | 40-Pin GPIO Header                                      | USB 2.0 Port
                     v                                                         v
   +------------------------------------+                    +------------------------------------+
   |         Waveshare CAN HAT          |                    |    Xiusiyt USB-to-RS485 Cable      |
   |      (MCP2515 SPI Controller)      |                    |     (FTDI FT232R, RJ45 Port)       |
   +-----------------+------------------+                    +-----------------+------------------+
                     | CAN_H / CAN_L (250 kbps)                                | RS-485 (A+ / B- / GND)
                     v                                                         v
   +------------------------------------+                    +------------------------------------+
   |    LBM025100A 24V Battery Bus      |                    |     Solar Inverter / Modbus        |
   |    (Parallel 8S CATL LFP Packs)    |                    |   (Growatt, Deye, Sol-Ark, etc.)   |
   +------------------------------------+                    +------------------------------------+
```

---

## 2. Target Battery Specifications (LBM025100A)

* **Manufacturer / Origin:** Huasi / Zhongneng (ZNTECH) for Kohler / Electriq Power residential ESS.
* **Cell Type:** Contemporary Amperex Technology Co. Limited (CATL) Prismatic LiFePO4 (LFP).
* **Module Configuration:** **8S1P** (8 cells in series per physical module).
* **Nominal Voltage:** **25.6 V** (Discharge cutoff: ~20.0 V, Full charge: ~28.8 V – 29.2 V).
* **Nominal Capacity:** **100 Ah** (2.56 kWh per module).
* **System Topology:** **24V Parallel Stack** ($N \times 1\text{P}8\text{S}$).
  * 1 Module: 25.6V nominal, 100Ah, 2.56 kWh
  * 2 Modules: 25.6V nominal, 200Ah, 5.12 kWh
  * 4 Modules: 25.6V nominal, 400Ah, 10.24 kWh
* **Internal Cell Monitoring Unit (CMU):**
  * Factory slave board inside each module monitors 8 cell taps and internal NTC thermistors.
  * Internal communication bus: **CAN 2.0B (Extended 29-bit IDs) @ 250 kbps**.
* **Module External COM Port (6-Pin Amphenol Rectangular):**
  * **Pin 3:** `CAN High` (`CAN_H`)
  * **Pin 4:** `CAN Low` (`CAN_L`)
  * Other pins: Internal power distribution and auxiliary signals.

---

## 3. Controller Hardware Architecture

### 3.1 Bill of Materials (BOM)

| Component | Part / Model | Function |
| :--- | :--- | :--- |
| **Host Controller** | Raspberry Pi 2 Model B (v1.1) | Central controller, protocol decoder, and telemetry host. |
| **Battery CAN Interface** | Waveshare CAN HAT | Connects to Pi 40-pin header; MCP2515 SPI controller + isolated transceiver. Interfaces with battery at 250 kbps. |
| **Inverter RS-485 Interface** | Xiusiyt USB-to-RS485 Cable | Plugs into Pi USB port; FTDI FT232R chipset with RJ45 connector. Interfaces with Inverter Modbus/RS-485 (`/dev/ttyUSB0`). |
| **System Power Supply** | Wide-Input DC-DC Buck (18V–36V $\to$ 5V/3A) | Steps down 24V DC battery bus to regulated 5V via Micro-USB to power the Pi. |
| **Networking** | Ethernet Cable or USB 2.4GHz Wi-Fi Dongle | Local network access, telemetry streaming, and WebUI dashboard. |

### 3.2 Pinouts & Connection Tables

#### A. Waveshare CAN HAT to LBM025100A Module
| CAN HAT Terminal | LBM025100A 6-Pin COM Pin | Wire / Signal | Notes |
| :--- | :--- | :--- | :--- |
| **CAN_H** | **Pin 3** | Yellow (internal harness) / CAN High | Ensure 120Ω termination jumper is placed on HAT |
| **CAN_L** | **Pin 4** | Green (internal harness) / CAN Low | Differential return |
| **GND** | Module Signal Ground | Common Ground | Optional reference |

#### B. Xiusiyt USB-to-RS485 (RJ45 Plug to Inverter)
| RJ45 Pin | Standard Signal | Function | Notes |
| :--- | :--- | :--- | :--- |
| **Pin 1** | `RS-485 B-` | Inverting data line (Data -) | Verify inverter pinout |
| **Pin 2** | `RS-485 A+` | Non-inverting data line (Data +) | Verify inverter pinout |
| **Pin 3 / 6** | `GND` | Signal Ground | Common ground reference |

---

## 4. Software Stack & Operating System Configuration

### 4.1 Base OS
* **Operating System:** Raspberry Pi OS Lite (32-bit ARMv7).
* **Alternative OS:** Victron Venus OS (Raspberry Pi 2/3 image) for native Victron GX integration.

### 4.2 Linux SocketCAN Setup (`/boot/firmware/config.txt`)
Add the following to enable the MCP2515 CAN controller over SPI:

```ini
# Enable SPI bus
dtparam=spi=on

# Waveshare CAN HAT (MCP2515 on SPI CE0, Interrupt on GPIO 25, 16MHz oscillator)
dtoverlay=mcp2515-can0,oscillator=16000000,interrupt=25
```

Bring up the CAN interface automatically at **250 kbps**:
```bash
sudo ip link set can0 up type can bitrate 250000 restart-ms 100
```

### 4.3 RS-485 Serial Interface
* FTDI drivers (`ftdi_sio`) load natively.
* The cable mounts as `/dev/ttyUSB0`.
* Accessible via Python using standard serial libraries (`pyserial`, `pymodbus`).

---

## 5. Software Architecture

```
bmsanta/
├── daemon/
│   ├── battery_bus.py      # SocketCAN driver: polls & decodes 250k CAN frames from LBM025100A
│   ├── aggregator.py       # Multi-module parallel logic: aggregates cell V, min/max, delta-V
│   ├── inverter_bus.py     # Modbus RTU / RS-485 transmitter to the solar inverter
│   ├── safety.py           # Protection thresholds: OVP, UVP, OTP, UTP, dynamic CCL/DCL derating
│   └── main.py             # Main asynchronous event loop
├── web/                    # Lightweight Flask/FastAPI dashboard for browser telemetry
├── tools/                  # Diagnostic utilities and test scripts
│   ├── sniff_can.py        # Dumps and parses raw CAN frames
│   └── modbus_test.py      # Verifies inverter serial communication
├── docs/                   # Datasheets, pinouts, and protocol documentation
└── project.md              # Project outline and roadmap tracker (this file)
```

### 5.1 Key Software Responsibilities
1. **Module Discovery & Polling:** Emulate the master control box wakeup sequence to discover all parallel LBM025100A nodes.
2. **Cell Telemetry Decoding:** Parse individual cell voltages (8 cells per module), thermistor readings, and internal fault registers.
3. **Parallel Fleet Aggregation:**
   * Global Minimum Cell Voltage ($V_{min}$)
   * Global Maximum Cell Voltage ($V_{max}$)
   * Maximum Cell Imbalance Delta ($\Delta V = V_{max} - V_{min}$)
   * Total Bank Usable Capacity ($N \times 100\text{Ah}$)
4. **Dynamic Current Derating (CCL / DCL):**
   * Reduce Charge Current Limit (CCL) as any individual cell approaches 3.55V–3.65V.
   * Reduce Discharge Current Limit (DCL) as any cell drops below 2.70V.
   * Derate during high cell temperature or excessive cell delta.
5. **Inverter Communication:** Stream pack status over RS-485 via Modbus RTU or emulate standard inverter protocols.

---

## 6. Project Roadmap & Action Plan

### Phase 1: Hardware Preparation & Bench Setup
- [ ] Receive Waveshare CAN HAT and Xiusiyt USB-to-RS485 cable.
- [ ] Mount CAN HAT on Raspberry Pi 2 v1.1 GPIO header and install 120Ω termination jumper.
- [ ] Flash Raspberry Pi OS Lite (32-bit) onto MicroSD card.
- [ ] Configure `config.txt` for `mcp2515-can0` and verify interface with `can-utils`.
- [ ] Verify USB-to-RS485 cable recognition via `dmesg | grep ttyUSB`.

### Phase 2: CAN Bus Sniffing & Protocol Verification
- [ ] Wire CAN HAT `CAN_H` / `CAN_L` to LBM025100A 6-pin COM port (Pins 3 and 4).
- [ ] Power module electronics and run `candump can0` to observe initial handshake and telemetry packets.
- [ ] Map out CAN IDs for cell voltages, module temperatures, and balance states.
- [ ] Document decoded message dictionary in `docs/protocols/`.

### Phase 3: Core Daemon & Multi-Module Aggregation
- [ ] Implement `battery_bus.py` with Python SocketCAN listeners.
- [ ] Build `aggregator.py` to support $N$ parallel modules simultaneously.
- [ ] Implement safety protection matrix (`safety.py`) with configurable cutoffs and hysteresis.

### Phase 4: Inverter Gateway & Field Deployment
- [ ] Connect RS-485 cable to solar inverter RJ45 communication port.
- [ ] Implement Modbus RTU / inverter protocol driver in `inverter_bus.py`.
- [ ] Verify closed-loop inverter control (dynamic charging voltage, current limits).
- [ ] Deploy local WebUI or Home Assistant MQTT bridge.
