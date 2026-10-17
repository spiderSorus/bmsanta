#!/usr/bin/env python3
"""
tools/live_cells.py - Live ASCII Telemetry Dashboard for BMSanta & CATL LBM025100A
Displays real-time pack telemetry, cell voltages, thermistors, and safety limits.
Operates either by reading the live daemon status (/run/bmsanta_status.json)
or by querying the SocketCAN interface directly if the daemon is not running.
"""

import sys
import os
import time
import json
import socket
import struct
import select

CAN_EFF = 0x80000000
POLL_ID = 0x180011F4 | CAN_EFF
STATUS_FILES = ["/run/bmsanta_status.json", "/tmp/bmsanta_status.json"]


def create_can_socket(iface="can0"):
    s = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    s.bind((iface,))
    s.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_LOOPBACK, 0)
    s.setblocking(False)
    return s


def query_telemetry_direct(sock, module_id=0xAA):
    """Fallback: Directly queries CAN bus if daemon is inactive."""
    cells = [0.0] * 8
    temps = [0.0, 0.0]

    # 1. Query Cell Voltages (Cmd 0x13)
    p_v = bytes([module_id, 0x13, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
    sock.send(struct.pack("=IB3x8s", POLL_ID, 8, p_v))
    time.sleep(0.03)

    start = time.time()
    while time.time() - start < 0.12:
        r, _, _ = select.select([sock], [], [], 0.02)
        if not r:
            break
        raw = sock.recv(16)
        if len(raw) >= 16:
            r_id, r_dlc, r_data = struct.unpack("=IB3x8s", raw)
            clean_id = r_id & 0x1FFFFFFF
            if clean_id == (0x18001300 | module_id) and r_dlc == 8:
                cells[0] = struct.unpack("<H", r_data[0:2])[0] / 1000.0
                cells[1] = struct.unpack("<H", r_data[2:4])[0] / 1000.0
                cells[2] = struct.unpack("<H", r_data[4:6])[0] / 1000.0
                cells[3] = struct.unpack("<H", r_data[6:8])[0] / 1000.0
            elif clean_id == (0x18011300 | module_id) and r_dlc == 8:
                cells[4] = struct.unpack("<H", r_data[0:2])[0] / 1000.0
                cells[5] = struct.unpack("<H", r_data[2:4])[0] / 1000.0
                cells[6] = struct.unpack("<H", r_data[4:6])[0] / 1000.0
                cells[7] = struct.unpack("<H", r_data[6:8])[0] / 1000.0

    # 2. Query Temperatures (Cmd 0x14)
    p_t = bytes([module_id, 0x14, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
    sock.send(struct.pack("=IB3x8s", POLL_ID, 8, p_t))
    time.sleep(0.03)

    start = time.time()
    while time.time() - start < 0.10:
        r, _, _ = select.select([sock], [], [], 0.02)
        if not r:
            break
        raw = sock.recv(16)
        if len(raw) >= 16:
            r_id, r_dlc, r_data = struct.unpack("=IB3x8s", raw)
            clean_id = r_id & 0x1FFFFFFF
            if clean_id == (0x18001400 | module_id) and r_dlc >= 4:
                raw_t1 = struct.unpack("<H", r_data[0:2])[0]
                raw_t2 = struct.unpack("<H", r_data[2:4])[0]
                temps[0] = (raw_t1 / 10.0 - 32) * 5.0 / 9.0
                temps[1] = (raw_t2 / 10.0 - 32) * 5.0 / 9.0

    pack_v = sum(cells)
    return cells, temps, pack_v


def make_bar(v, v_min=3.0, v_max=3.45, width=20):
    clamped = max(v_min, min(v_max, v))
    ratio = (clamped - v_min) / (v_max - v_min)
    filled = int(ratio * width)
    return "█" * filled + "░" * (width - filled)


def read_daemon_status():
    """Reads daemon exported JSON if fresh (< 3.0s)."""
    for path in STATUS_FILES:
        if os.path.exists(path):
            try:
                mtime = os.path.getmtime(path)
                if time.time() - mtime < 3.0:
                    with open(path, "r") as f:
                        return json.load(f)
            except Exception:
                pass
    return None


def main():
    iface = sys.argv[1] if len(sys.argv) > 1 else "can0"
    direct_sock = None

    print("\033[2J", end="")  # Clear screen

    try:
        while True:
            daemon_data = read_daemon_status()

            if daemon_data:
                # Mode A: Live from Daemon
                pack_v = daemon_data.get("bank_voltage", 0.0)
                soc = daemon_data.get("soc_pct", 0)
                min_v = daemon_data.get("min_cell_v", 0.0)
                max_v = daemon_data.get("max_cell_v", 0.0)
                delta_mv = daemon_data.get("delta_cell_mv", 0.0)
                temps = [daemon_data.get("min_temp_c", 0.0), daemon_data.get("max_temp_c", 0.0)]
                ccl = daemon_data.get("safety", {}).get("ccl_amps", 0.0)
                dcl = daemon_data.get("safety", {}).get("dcl_amps", 0.0)
                healthy = daemon_data.get("safety", {}).get("is_healthy", True)
                status_str = "ONLINE (HEALTHY)" if healthy else "ALARM ACTIVE"
                modules = daemon_data.get("modules", {})
                mod_count = daemon_data.get("online_modules", 1)
                source_label = f"DAEMON ACTIVE ({mod_count} Module(s))"
            else:
                # Mode B: Direct CAN Query
                if not direct_sock:
                    try:
                        direct_sock = create_can_socket(iface)
                    except Exception as e:
                        print(f"Waiting for CAN or daemon... ({e})")
                        time.sleep(1.0)
                        continue

                cells, temps, pack_v = query_telemetry_direct(direct_sock, module_id=0xAA)
                valid_cells = [c for c in cells if c > 1.0]
                min_v = min(valid_cells) if valid_cells else 0.0
                max_v = max(valid_cells) if valid_cells else 0.0
                delta_mv = (max_v - min_v) * 1000.0 if valid_cells else 0.0
                soc = 35 if pack_v >= 25.8 else 20
                ccl, dcl = 50.0, 100.0
                status_str = "ONLINE (STANDALONE)"
                modules = {0xAA: {"cells": cells, "temps_c": temps, "pack_v": pack_v}}
                source_label = f"DIRECT CAN ({iface} @ 250k)"

            lines = [
                "\033[H",
                "╔═════════════════════════════════════════════════════════════════╗",
                "║             BMSanta - CATL LBM025100A 24V Live Monitor          ║",
                "╠═════════════════════════════════════════════════════════════════╣",
                f"║ Mode: {source_label:<24} Status: {status_str:<21} ║",
                f"║ Bank Voltage: {pack_v:6.3f} V                Est. SOC: ~{int(soc)}%                ║",
                f"║ Min Cell:     {min_v:6.3f} V                Max Cell: {max_v:6.3f} V         ║",
                f"║ Cell Delta:   {delta_mv:5.1f} mV               CCL/DCL:  {ccl:.0f}A / {dcl:.0f}A      ║",
                f"║ Min Temp:     {temps[0]:5.1f} °C               Max Temp: {temps[1]:5.1f} °C       ║",
                "╠═════════════════════════════════════════════════════════════════╣",
            ]

            sorted_mods = sorted(modules.items(), key=lambda x: int(x[0]) if str(x[0]).isdigit() else str(x[0]))
            for idx, (nid, m_info) in enumerate(sorted_mods, 1):
                nid_int = int(nid) if isinstance(nid, (int, str)) and str(nid).isdigit() else nid
                nid_display = f"0x{int(nid_int):02X}" if isinstance(nid_int, int) else str(nid)
                pack_num = m_info.get("pack_num", idx)
                pack_v_mod = m_info.get("pack_v", 0.0)
                delta_mod = m_info.get("delta_mv", 0.0)
                t_list = m_info.get("temps_c", [0.0, 0.0])
                t_str = f"{t_list[0]:.1f}°C, {t_list[1]:.1f}°C" if len(t_list) >= 2 else "--"
                lines.append(f"╟─────────────────────────────────────────────────────────────────╢")
                lines.append(f"║ [Pack #{pack_num} - {nid_display}] V: {pack_v_mod:6.3f} V | Δ: {delta_mod:4.1f} mV | Temps: {t_str:<15} ║")

                cells_list = m_info.get("cells", [])
                c_str1 = " ".join([f"C{i}:{v:.3f}{'*' if abs(v-min_v)<0.001 else ('^' if abs(v-max_v)<0.001 else ' ')}" for i, v in enumerate(cells_list[:4], 1)])
                c_str2 = " ".join([f"C{i}:{v:.3f}{'*' if abs(v-min_v)<0.001 else ('^' if abs(v-max_v)<0.001 else ' ')}" for i, v in enumerate(cells_list[4:8], 5)])
                lines.append(f"║   {c_str1:<60}║")
                lines.append(f"║   {c_str2:<60}║")

            lines.extend([
                "╚═════════════════════════════════════════════════════════════════╝",
                f" (* = Global Min, ^ = Global Max) | Updated: {time.strftime('%H:%M:%S')} | Ctrl+C"
            ])

            print("\n".join(lines))
            time.sleep(1.0)

    except KeyboardInterrupt:
        print("\nExiting live monitor...")
    finally:
        if direct_sock:
            direct_sock.close()


if __name__ == "__main__":
    main()
