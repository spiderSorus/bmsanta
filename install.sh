#!/usr/bin/env bash
# ==============================================================================
# BMSanta Installer & Configuration Script
# Automated deployment of BMSanta CAN Driver, System Services, and WebUI
# ==============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}"
echo "╔═════════════════════════════════════════════════════════════════╗"
echo "║                  BMSanta Automated Installer                    ║"
echo "║    CATL LBM025100A 24V ESS Controller & Inverter Gateway        ║"
echo "╚═════════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# 1. Root check
if [ "$(id -u)" -ne 0 ]; then
    echo -e "${RED}[ERROR] This installer must be run as root or with sudo:${NC}"
    echo "  sudo ./install.sh"
    exit 1
fi

REAL_USER="${SUDO_USER:-$(logname 2>/dev/null || echo 'dangersorus')}"
USER_HOME=$(eval echo "~${REAL_USER}")
INSTALL_DIR="${USER_HOME}/bmsanta"

echo -e "${BLUE}[*] Target user: ${GREEN}${REAL_USER}${NC}"
echo -e "${BLUE}[*] Install directory: ${GREEN}${INSTALL_DIR}${NC}"

# 2. Install System Dependencies
echo -e "\n${BLUE}[1/6] Installing required system packages...${NC}"
apt-get update -y
apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-serial \
    python3-can \
    can-utils \
    iproute2 \
    curl \
    git

# 3. Add user permissions
echo -e "\n${BLUE}[2/6] Configuring user permissions...${NC}"
usermod -aG dialout,gpio,spi,i2c "${REAL_USER}" || true

# 4. Configure Raspberry Pi Kernel Overlays (/boot/firmware/config.txt)
echo -e "\n${BLUE}[3/6] Checking Waveshare CAN HAT kernel overlays...${NC}"
BOOT_CONFIG="/boot/firmware/config.txt"
if [ ! -f "${BOOT_CONFIG}" ]; then
    BOOT_CONFIG="/boot/config.txt"
fi

OVERLAY_BLOCK="
# --- BMSanta Hardware Overlays (Waveshare 2-CH CAN HAT) ---
dtparam=spi=on
dtoverlay=mcp2515-can0,oscillator=16000000,interrupt=23
dtoverlay=mcp2515-can1,oscillator=16000000,interrupt=25
"

if grep -q "mcp2515-can0" "${BOOT_CONFIG}"; then
    echo -e "${GREEN}[OK] MCP2515 CAN overlays already present in ${BOOT_CONFIG}${NC}"
else
    echo -e "${YELLOW}[+] Appending MCP2515 CAN overlays to ${BOOT_CONFIG}...${NC}"
    echo "${OVERLAY_BLOCK}" >> "${BOOT_CONFIG}"
    NEED_REBOOT=1
fi

# 5. Install systemd services
echo -e "\n${BLUE}[4/6] Installing systemd services...${NC}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# A. can.service
cp "${SCRIPT_DIR}/systemd/can.service" /etc/systemd/system/can.service
chmod 644 /etc/systemd/system/can.service

# B. bmsanta.service (adjusting user if needed)
sed "s|User=dangersorus|User=${REAL_USER}|g; s|/home/dangersorus/bmsanta|${INSTALL_DIR}|g" \
    "${SCRIPT_DIR}/systemd/bmsanta.service" > /etc/systemd/system/bmsanta.service
chmod 644 /etc/systemd/system/bmsanta.service

systemctl daemon-reload
systemctl enable can.service
systemctl enable bmsanta.service

# 6. Bring up interfaces and restart services
echo -e "\n${BLUE}[5/6] Activating CAN interfaces and starting BMSanta daemon...${NC}"
systemctl restart can.service || true
sleep 1
systemctl restart bmsanta.service || true

# 7. Summary & Verification
echo -e "\n${BLUE}[6/6] Verifying services...${NC}"
CAN_STATUS=$(systemctl is-active can.service || echo "failed")
BMS_STATUS=$(systemctl is-active bmsanta.service || echo "failed")

HOST_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")

echo -e "\n${GREEN}═════════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}               BMSanta Installation Complete!                    ${NC}"
echo -e "${GREEN}═════════════════════════════════════════════════════════════════${NC}"
echo -e "  * CAN Service:        ${GREEN}${CAN_STATUS}${NC}"
echo -e "  * BMSanta Daemon:     ${GREEN}${BMS_STATUS}${NC}"
echo -e "  * Web Dashboard:      ${BLUE}http://${HOST_IP}:5000/${NC}"
echo -e "  * Terminal Monitor:   ${YELLOW}python3 ${INSTALL_DIR}/tools/live_cells.py${NC}"
echo -e "  * Live Daemon Logs:   ${YELLOW}journalctl -u bmsanta -f${NC}"
echo -e "${GREEN}═════════════════════════════════════════════════════════════════${NC}\n"

if [ "${NEED_REBOOT:-0}" -eq 1 ]; then
    echo -e "${YELLOW}[NOTE] A reboot is recommended to initialize the MCP2515 hardware overlays:${NC}"
    echo "  sudo reboot"
fi
