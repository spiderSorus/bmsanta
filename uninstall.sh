#!/usr/bin/env bash
# ==============================================================================
# BMSanta Uninstaller Script
# ==============================================================================

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "[ERROR] This uninstaller must be run as root or with sudo:"
    echo "  sudo ./uninstall.sh"
    exit 1
fi

echo "Stopping and disabling BMSanta services..."
systemctl stop bmsanta.service || true
systemctl disable bmsanta.service || true
rm -f /etc/systemd/system/bmsanta.service

systemctl stop can.service || true
systemctl disable can.service || true
rm -f /etc/systemd/system/can.service

systemctl daemon-reload
echo "[OK] BMSanta services uninstalled successfully."
