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
Add the following to enable both MCP2515 CAN controllers over SPI (Waveshare 2-CH CAN HAT):

```ini
# Enable SPI bus
dtparam=spi=on

# Waveshare 2-CH CAN HAT
# CAN 0: SPI CE0, Interrupt GPIO 23, 16MHz oscillator
# CAN 1: SPI CE1, Interrupt GPIO 25, 16MHz oscillator
# Note: VIO jumper MUST be set to 3V3!
dtoverlay=mcp2515-can0,oscillator=16000000,interrupt=23
dtoverlay=mcp2515-can1,oscillator=16000000,interrupt=25
```

Interfaces are automatically brought up at **250 kbps** on boot via `/etc/systemd/system/can.service`:
```bash
sudo ip link set can0 up type can bitrate 250000 restart-ms 100
sudo ip link set can1 up type can bitrate 250000 restart-ms 100
```

### 4.3 RS-485 Serial Interface
* FTDI drivers (`ftdi_sio`) load natively.
* The cable mounts as `/dev/ttyUSB0` (dialout group enabled).
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
│   ├── hardware/           # Schematics and HAT jumper photos
│   └── protocols/          # Decoded CAN frames and register mappings
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
- [x] Receive Waveshare CAN HAT and Xiusiyt USB-to-RS485 cable.
- [x] Mount CAN HAT on Raspberry Pi GPIO header, verify 3V3 VIO jumper and 120Ω termination.
- [x] Flash Raspberry Pi OS Lite (32-bit) onto MicroSD card.
- [x] Configure `config.txt` for `mcp2515-can0` (GPIO 23) and `mcp2515-can1` (GPIO 25) with `can.service`.
- [x] Verify USB-to-RS485 cable recognition via `/dev/ttyUSB0` and dialout permissions.

### Phase 2: CAN Bus Sniffing & Protocol Verification
- [x] Wire CAN HAT `CAN_H` / `CAN_L` to LBM025100A 6-pin COM port (CAN0).
- [x] Power module electronics and verify CAN bus electrical ACK on transmit.
- [x] Map out CAN IDs for cell voltages, module temperatures, and balance states.
- [x] Document decoded message dictionary in `docs/protocols/huasi_bme.md`.

### Phase 3: Core Daemon & Multi-Module Aggregation
- [x] Implement `battery_bus.py` with Python SocketCAN listeners & auto-discovery.
- [x] Build `aggregator.py` to support $N$ parallel modules simultaneously.
- [x] Implement safety protection matrix (`safety.py`) with configurable cutoffs and dynamic CCL/DCL derating.
- [x] Create `systemd/bmsanta.service` running daemon at 1 Hz on boot.

### Phase 4: Inverter Gateway & Field Deployment
- [x] Implement Modbus RTU / RS-485 inverter server in `inverter_bus.py` (`/dev/ttyUSB0`).
- [x] Implement Pylontech CAN broadcast gateway in `inverter_bus.py` (`can0`).
- [x] Deploy embedded dark-mode WebUI dashboard on port 5000 (`http://<ip>:5000`).
- [x] Expand CAN hardware polling to 8 parallel packs (64 cells, 800 Ah, 20.48 kWh).
- [x] Implement unrounded millivolt chart plotting + rounded 0.1V digital panes.
- [x] Transition history sampling to 10-second intervals with 24-hour buffer (8,640 points).
- [x] Non-volatile telemetry persistence in `/home/dangersorus/bmsanta/data/history.json` and `localStorage`.
- [ ] Connect RS-485 / CAN cable to physical solar hybrid inverter RJ45 communication port:
  - *Current Diagnosis (PowMr Inverter):* The inverter is throwing `Fault 04` under `LIB-485`. The current cable (Xiusiyt USB-RS485 for Seplos PUSUNG) wires `Pin 1 = B-, Pin 2 = A+`. PowMr RJ45 COM ports use `Pin 7 = A+, Pin 8 = B-` (`Pin 1 = +5V, Pin 2 = GND`). An RJ45 breakout / adapter re-pinning Pin 1/2 to Pin 7/8 is needed for serial communication.
- [ ] Verify closed-loop inverter control with live PowMr inverter once cable pinout is adapted.
- [ ] Optional: Home Assistant MQTT auto-discovery bridge.

---

## 7. Operational Topology & Hardware Mapping (As-Built)

### 7.1 Live System Information
* **Host:** Raspberry Pi Zero W (`192.168.1.217` / `bmsanta.local`)
* **OS:** Debian GNU/Linux 12 (Bookworm, 64-bit Lite)
* **User:** `dangersorus` (passwordless sudo enabled)
* **CAN Interfaces:**
  * Physical **`CAN0`** terminal on Waveshare HAT = Linux **`can1`** (Battery bus @ 250 kbps)
  * Physical **`CAN1`** terminal on Waveshare HAT = Linux **`can0`** (Inverter CAN bus @ 250/500 kbps)
* **Active Battery Bank:** 8x CATL 8S LBM025100A modules in parallel (64 cells, 800 Ah, 20.48 kWh)

### 7.2 Multi-Module CAN Addressing Map
During CAN bus interrogation, the 8 connected packs respond across 3 distinct node IDs:
| Packs | Responding CAN ID | Multiplexing / Behavior |
| :--- | :--- | :--- |
| **Pack #1 – #5** | `0xAA` | Auto-multiplexed across frames by internal CMU daisy-chain controller |
| **Pack #6** | `0xAB` | Direct node address |
| **Pack #7 – #8** | `0xAD` | Direct node address |

The discovery poller in `daemon/battery_bus.py` scans `0xAA..0xB1` with a `quiet_timeout` to ensure all 8 packs are fully populated into `aggregator.py` on every 1 Hz cycle.

### 7.3 Telemetry & Running Graph Persistence
* **Sampling Rate:** Every 10 seconds (8,640 samples = full 24-hour history window).
* **Disk Storage:** Stored atomically at `/home/dangersorus/bmsanta/data/history.json`.
* **Flash Wear Mitigation:** In-memory rolling deque (`maxlen=8640`), flushed to SD card every 60 seconds (every 6th sample) and on daemon shutdown.
* **Browser Caching:** Mirrored to browser `localStorage` (`bmsanta_graph_history`) for instant graph rendering on page load.
* **Plotting Precision:** Raw unrounded millivolt values plotted on Canvas; UI digital cards rounded to 0.1V to eliminate digital jitter.

---

## 8. Deployment, Packaging & Backup Strategy

### 8.1 Automated Installation
The repository includes an all-in-one automated installer [`install.sh`](install.sh):
```bash
git clone https://github.com/<user>/bmsanta.git ~/bmsanta
cd ~/bmsanta
sudo ./install.sh
```

### 8.2 Standalone Release Packaging
Run [`package.sh`](package.sh) to generate an offline, self-contained distribution archive:
```bash
./package.sh
# Outputs: dist/bmsanta-v0.1.0.tar.gz + SHA-256 checksum
```

### 8.3 System Backups
The `backup/` directory contains complete snapshots of the live system:
* `backup/bmsanta-pi-rootfs.tar.gz`: Complete rootfs archive of `/home/dangersorus/bmsanta`, systemd units, hardware overlays, and sudo configuration.
* `backup/pi-firmware-config.txt`: Hardware config with 16 MHz SPI clock and dual MCP2515 overlays.
* `backup/pi-bmsanta.service`: Daemon systemd unit.
* `backup/pi-can.service`: SocketCAN initialization systemd unit.
* `backup/pi-history.json`: Snapshot of 3,000+ points of non-volatile 10-second telemetry.
* `backup/pi-installed-packages.txt`: Complete `dpkg --get-selections` inventory.
* `bmsanta-baseline.img.gz`: 1.0 GB compressed raw base OS image.
* Git tag: `v0.1.2-10s-backup`.

