# BMSanta System Backup & ISO Migration Guide

This directory contains the complete backup of the **BMSanta Raspberry Pi Host OS, configurations, and application files** for migration to a new SD card, NVMe drive, or custom ISO image.

---

## 1. Backup Artifacts Summary

| File | Description | Purpose |
| :--- | :--- | :--- |
| `bmsanta-pi-rootfs.tar.gz` | **Complete OS Root Filesystem Archive** | Full backup of all files, libraries, Python environment, `/etc`, and `/home/dangersorus`. |
| `pi-firmware-config.txt` | **Raspberry Pi Hardware Config** (`/boot/firmware/config.txt`) | Configures Broadcom SPI clock, MCP2515 CAN0/CAN1 device tree overlays, and interrupts. |
| `pi-bmsanta.service` | **BMSanta Daemon Service** (`/etc/systemd/system/bmsanta.service`) | Auto-starts BMSanta on boot at 1 Hz with automatic restart. |
| `pi-can.service` | **SocketCAN Initialization Service** (`/etc/systemd/system/can.service`) | Automatically configures `can0` and `can1` at 250 kbps with auto-recovery on boot. |
| `pi-installed-packages.txt` | **Debian Package Selection Inventory** | Lists all installed system packages (`can-utils`, `python3`, etc.) via `dpkg --get-selections`. |

---

## 2. Migration Method A: Restore onto Any Fresh SD Card or NVMe SSD (Recommended)

This is the fastest and cleanest method to migrate the complete system to a new SD card or NVMe drive:

### Step 1: Flash a Clean Base Raspberry Pi OS
Use **Raspberry Pi Imager** to write standard **Raspberry Pi OS Lite (64-bit Bookworm)** onto the new target drive.

### Step 2: Mount the Target Drive on your Linux/Mac Host
Insert the newly flashed drive into your computer. It will have two partitions:
- `bootfs` (or `boot` / `boot_firmware`)
- `rootfs`

### Step 3: Extract the Root Filesystem Backup
Extract the `bmsanta-pi-rootfs.tar.gz` archive directly onto the `rootfs` partition (preserving permissions):
```bash
# Example on Linux (substitute with your actual mountpoint):
sudo tar -xzpf backup/bmsanta-pi-rootfs.tar.gz -C /media/$USER/rootfs/

# Copy the Waveshare CAN overlays to the boot partition:
sudo cp backup/pi-firmware-config.txt /media/$USER/bootfs/config.txt
```

### Step 4: Boot and Verify
Eject the drive, insert it into the Raspberry Pi, and power on.
The system will boot up with the exact same hostname, IP configuration, CAN settings, and `bmsanta` daemon running automatically.

---

## 3. Migration Method B: Automated Re-Installation on a New OS

If you prefer installing onto a fresh system without overwriting system files:

```bash
# 1. Copy the BMSanta repository or distribution archive to the target Pi:
scp dist/bmsanta-v0.1.0.tar.gz dangersorus@<target-ip>:~/

# 2. Extract and run the automated installer:
ssh dangersorus@<target-ip>
tar -xzf bmsanta-v0.1.0.tar.gz
cd bmsanta
sudo ./install.sh
```
`install.sh` will automatically configure:
- SPI and MCP2515 overlays in `/boot/firmware/config.txt`
- `can.service` and `bmsanta.service` systemd units
- Udev rules for RS-485 adapters
- Service auto-start and verification

---

## 4. Migration Method C: Live SD Card Direct Clone

If you want a raw, byte-for-byte disk image (`.img`) of the live SD card streamed over the network:

```bash
# Stream full 29GB SD card into a compressed .img.gz on your Mac:
ssh dangersorus@192.168.1.217 "sudo dd if=/dev/mmcblk0 bs=4M status=progress | gzip -c" > backup/bmsanta-pi-full-disk.img.gz

# Decompress and flash directly with Raspberry Pi Imager or dd:
gzip -dc backup/bmsanta-pi-full-disk.img.gz | sudo dd of=/dev/rdiskN bs=4M status=progress
```

---

## 5. Hardware Overlay Verification Checklist

When booting on new hardware, ensure the Waveshare 2-CH CAN HAT jumpers match:
1. **SPI Clock:** Oscillator must be set to `16000000` (16 MHz) in `config.txt`:
   ```ini
   dtparam=spi=on
   dtoverlay=mcp2515-can0,oscillator=16000000,interrupt=23
   dtoverlay=mcp2515-can1,oscillator=16000000,interrupt=25
   ```
2. **Terminal Blocks:**
   - Physical terminal block **`CAN0`** on HAT = Linux **`can1`** (Battery bus, 250 kbps).
   - Physical terminal block **`CAN1`** on HAT = Linux **`can0`** (Inverter Pylontech bus, 250/500 kbps).
3. **120Ω Termination:** Both jumpers on the Waveshare HAT should be set to `ON` (connected).
