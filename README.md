# 🎅 BMSanta: 24V CATL Multi-Module ESS Controller & Inverter Gateway

An open, offline, zero-dependency Battery Management System (BMS) controller and hybrid solar inverter gateway designed for second-life **CATL LBM025100A** 24V LiFePO4 (LFP) battery modules running in parallel.

---

## 🚀 Current System Status (As-Built)

* **Host Device:** Raspberry Pi Zero W (`192.168.1.217` / `bmsanta.local`), running 64-bit Debian Bookworm Lite.
* **Active Battery Bank:** **8x CATL 8S LBM025100A Modules** (64 cells total, 800 Ah capacity, 20.48 kWh energy).
* **CAN Bus Communication:** Online at 250 kbps via dual-channel Waveshare MCP2515 SPI HAT. All 8 packs continuously discovered and aggregated on a **low-drain 30-second polling interval** (configurable via `BMSANTA_POLL_INTERVAL`) with ~17–20 mV cell balance delta and a calibrated LFP OCV curve.
* **Web Telemetry Dashboard:** Active at `http://192.168.1.217:5000` with **30-second sampling**, multi-threaded Gzip HTTP delivery, delta-sync (`/api/history?since=...`), and non-volatile disk persistence.
* **System Backup:** Full system rootfs archive, package inventories, and configuration backups saved in [`backup/`](backup/) and tagged at `v0.1.2-10s-backup`.

---

## ⚡ Hardware Wiring & Pinout Reference

### 1. Waveshare 2-CH CAN HAT Port Inversion
> [!IMPORTANT]
> The Waveshare 2-CH CAN HAT assigns SPI Chip Enables such that Linux SocketCAN device numbers are reversed relative to the silkscreen labels:
> * Physical **`CAN0`** Terminal on HAT = Linux **`can1`** (Battery bus @ 250 kbps)
> * Physical **`CAN1`** Terminal on HAT = Linux **`can0`** (Inverter CAN bus @ 250/500 kbps)
> * Both 120Ω termination jumpers must be set to `ON`. VIO jumper must be set to `3V3`.

### 2. Battery COM Cable (CATL LBM025100A 6-Pin Amphenol)
| 6-Pin COM Pin | Signal | HAT Terminal Connection | Notes |
| :--- | :--- | :--- | :--- |
| **Pin 3** | `CAN_H` | Waveshare **CAN0 Terminal** (Linux `can1`) | High differential line |
| **Pin 4** | `CAN_L` | Waveshare **CAN0 Terminal** (Linux `can1`) | Low differential line |

### 3. Multi-Pack CAN Addressing Topology
During CAN bus scanning, packs respond according to their hardware position and internal CMU daisy-chain controller:
| Battery Modules | Responding CAN ID | Aggregation Method |
| :--- | :--- | :--- |
| **Packs #1 – #5** | `0xAA` | Auto-multiplexed across frames by internal CMU daisy-chain controller |
| **Pack #6** | `0xAB` | Direct node address |
| **Pack #7 – #8** | `0xAD` | Direct node address |

`daemon/battery_bus.py` scans candidate IDs `0xAA..0xB1` with a `quiet_timeout` so that all 8 packs are dynamically gathered and balanced without manual configuration.

---

## 🔌 Inverter Interface & PowMr RS-485 Diagnosis

### Current Status
* The system includes closed-loop Pylontech CAN emulation on `can0` and Modbus RTU / Pylontech serial protocol emulation on `/dev/ttyUSB0`.
* The user's PowMr inverter is configured for **`LIB-485`** (Pylontech protocol over RS-485) and displays **`Fault 04`** (Under-voltage cutoff / no BMS packet communication).

### Cable Pinout Issue & Solution
* **Current Cable:** Xiusiyt USB-to-RS485 cable for Seplos PUSUNG batteries (`RJ45 Pin 1 = B-, Pin 2 = A+`).
* **PowMr Inverter RJ45 COM Port:** PowMr/SRNE inverters use **Pin 7 (A+)** and **Pin 8 (B-)** for RS-485 data, while **Pin 1 is +5V** and **Pin 2 is GND**.
* **Fix required:** To enable closed-loop communication between the Pi and the PowMr inverter, adapt or re-crimp the RJ45 cable:
  * Connect FTDI **A+ (Yellow / Pin 2)** $\to$ PowMr RJ45 **Pin 7**
  * Connect FTDI **B- (Orange / Pin 1)** $\to$ PowMr RJ45 **Pin 8**
  * Connect FTDI **GND** $\to$ PowMr RJ45 **Pin 2** (Ground reference)

---

## 📊 Web Dashboard & Telemetry Persistence

The embedded dashboard runs directly from [`daemon/web_server.py`](daemon/web_server.py) (`ThreadingHTTPServer`) with zero external dependencies:
* **Live Refresh:** 10-second browser status polling via `/api/status`, paired with the 30-second BMS interrogation cycle (`BMSANTA_POLL_INTERVAL=30`).
* **Historical Trend Graph:** Canvas-based charge and voltage trajectory updated **every 30 seconds** via `/api/history` (with incremental `?since=<timestamp>` delta fetching and pre-compressed Gzip responses).
* **Calibrated LFP SOC Curve:** [`daemon/aggregator.py`](daemon/aggregator.py) uses a calibrated 8S LiFePO4 voltage-to-SOC curve tailored for CATL LBM025100A cells under light inverter tare load (~20–25°C) with exponential moving average (EMA) filtering (`alpha=0.25`).
* **Precision Plotting:** Plots raw, unrounded millivolt-level bank voltages for granular trend analysis, while maintaining 0.1V rounded digital pack panes to eliminate visual jitter.
* **Non-Volatile Storage:** Telemetry points are saved to `/home/dangersorus/bmsanta/data/history.json` (holds up to 8,640 samples = up to 72 hours at 30s resolution).
* **SD Card Flash Wear Mitigation:** Live points stay in RAM and are atomically flushed to disk every 4 samples (~2 minutes at 30s polling) and on daemon shutdown.
* **Browser Caching:** History is mirrored to browser `localStorage` (`bmsanta_graph_history`) for instant graph rendering upon page refresh.

---

## 📦 Installation & Quick Start

Clone the repository and run the automated installer on a Raspberry Pi:
```bash
git clone https://github.com/spiderSorus/bmsanta.git ~/bmsanta
cd ~/bmsanta
sudo ./install.sh
```

---

## 🛠 Operational Runbook & Commands

### Managing the Daemon on the Pi
```bash
# SSH into the controller:
ssh dangersorus@192.168.1.217

# View real-time service logs:
journalctl -u bmsanta.service -f

# Check CAN interface status:
ip -s link show can0
ip -s link show can1

# Restart service after code changes:
sudo systemctl restart bmsanta.service
```

### Deploying Changes from Host
```bash
# Rsync codebase to Pi:
rsync -avz --exclude='.git' --exclude='backup' --exclude='dist' --exclude='data' --exclude='__pycache__' ./ dangersorus@192.168.1.217:/home/dangersorus/bmsanta/

# Restart daemon:
ssh dangersorus@192.168.1.217 'sudo systemctl restart bmsanta.service'
```

### Testing Battery Communication
```bash
# Dump raw CAN frames from battery bus:
candump can1

# Run diagnostic interrogator script:
python3 -m tools.scan_ids
python3 -m tools.live_cells
```

---

## 💾 Backups & Disaster Recovery

Snapshots are stored in [`backup/`](backup/):
* **`bmsanta-pi-rootfs.tar.gz`:** Complete root filesystem archive (91 MB).
* **`pi-firmware-config.txt`:** Pi hardware config with 16 MHz SPI clock and dual MCP2515 overlays.
* **`pi-bmsanta.service` & `pi-can.service`:** Systemd service definitions.
* **`pi-history.json`:** Historical battery voltage data points.
* **`pi-installed-packages.txt`:** Complete Debian package selection inventory.
* **`dist/bmsanta-v0.1.0.tar.gz`:** Offline installer package with SHA-256 checksum.

To restore onto a fresh Raspberry Pi OS install, extract `backup/bmsanta-pi-rootfs.tar.gz` or run `sudo ./install.sh`. See [`backup/README.md`](backup/README.md) for step-by-step restoration procedures.
