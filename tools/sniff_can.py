#!/usr/bin/env python3
"""
tools/sniff_can.py - BMSanta CAN Bus Diagnostic & Battery Module Interrogation Tool
Designed for probing CATL LBM025100A / Huasi CMU modules over SocketCAN.
"""

import sys
import os
import time
import socket
import struct
import select
import threading

CAN_EFF_FLAG = 0x80000000  # Extended frame format
CAN_RTR_FLAG = 0x40000000  # Remote transmission request
CAN_ERR_FLAG = 0x20000000  # Error frame
CAN_EFF_MASK = 0x1FFFFFFF  # Extended frame ID mask
CAN_SFF_MASK = 0x000007FF  # Standard frame ID mask

def create_can_socket(interface="can0"):
    s = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    s.bind((interface,))
    return s

def build_can_frame(can_id, data_bytes, is_extended=True):
    raw_id = can_id | CAN_EFF_FLAG if is_extended else can_id
    raw_data = bytes(data_bytes).ljust(8, b'\x00')[:8]
    return struct.pack("=IB3x8s", raw_id, len(data_bytes), raw_data)

def parse_can_frame(raw_bytes):
    raw_id, dlc, raw_data = struct.unpack("=IB3x8s", raw_bytes)
    is_extended = bool(raw_id & CAN_EFF_FLAG)
    is_error = bool(raw_id & CAN_ERR_FLAG)
    is_rtr = bool(raw_id & CAN_RTR_FLAG)
    clean_id = (raw_id & CAN_EFF_MASK) if is_extended else (raw_id & CAN_SFF_MASK)
    return {
        "id": clean_id,
        "is_extended": is_extended,
        "is_error": is_error,
        "is_rtr": is_rtr,
        "dlc": dlc,
        "data": raw_data[:dlc]
    }

class CANBusSniffer:
    def __init__(self, interface="can0", logfile="can_capture.log"):
        self.interface = interface
        self.logfile = logfile
        self.sock = create_can_socket(interface)
        self.running = True
        self.rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
        self.log_lock = threading.Lock()
        self.seen_ids = set()

    def start(self):
        self.rx_thread.start()
        print(f"[*] CAN sniffer started on {self.interface}. Logging to {self.logfile}")

    def stop(self):
        self.running = False
        try:
            self.sock.close()
        except Exception:
            pass

    def send(self, can_id, data_bytes, is_extended=True):
        frame = build_can_frame(can_id, data_bytes, is_extended)
        self.sock.send(frame)
        ts = time.strftime("%H:%M:%S") + f".{int(time.time()*1000)%1000:03d}"
        id_str = f"0x{can_id:08X}" if is_extended else f"0x{can_id:03X}"
        payload_str = " ".join(f"{b:02X}" for b in data_bytes)
        print(f"[{ts}] TX >>> ID: {id_str} [{len(data_bytes)}] {payload_str}")

    def _rx_loop(self):
        with open(self.logfile, "a") as f_log:
            while self.running:
                try:
                    ready, _, _ = select.select([self.sock], [], [], 0.2)
                    if not ready:
                        continue
                    raw_bytes = self.sock.recv(16)
                    if len(raw_bytes) < 16:
                        continue
                    frame = parse_can_frame(raw_bytes)
                    ts = time.strftime("%H:%M:%S") + f".{int(time.time()*1000)%1000:03d}"
                    id_hex = f"0x{frame['id']:08X}" if frame['is_extended'] else f"0x{frame['id']:03X}"
                    payload_hex = " ".join(f"{b:02X}" for b in frame['data'])
                    ascii_rep = "".join(chr(b) if 32 <= b <= 126 else "." for b in frame['data'])

                    msg = f"[{ts}] RX <<< ID: {id_hex} DLC: {frame['dlc']} DATA: {payload_hex} | {ascii_rep}"
                    print(msg)
                    f_log.write(msg + "\n")
                    f_log.flush()

                    if frame['id'] not in self.seen_ids:
                        self.seen_ids.add(frame['id'])
                        print(f"    --> [NEW CAN ID DISCOVERED: {id_hex}]")
                except Exception as e:
                    if not self.running:
                        break

def run_probe_sequence(sniffer):
    """
    Sweeps through known Huasi / CATL / Electriq polling patterns:
    Module IDs: 0xAA, 0xAB, 0xAC, 0xAD, 0x01, 0x00, 0xF4
    Command IDs: 0x180016F4, 0x180011F4, 0x1800F4AA, 0x1801F4AA, 0x18FFAA00
    """
    print("\n" + "="*60)
    print("=== STARTING HUASI / CATL BATTERY MODULE PROBE SWEEP ===")
    print("="*60 + "\n")

    # 1. Sweep Huasi 29-bit Master Query frames
    # Pattern A: 0x180016F4 with module address byte
    for mod_id in [0xAA, 0xAB, 0xAC, 0xAD, 0x00, 0x01, 0x02, 0xF4]:
        for cmd in [0x12, 0x11, 0x16, 0x03, 0x01]:
            sniffer.send(0x180016F4, [mod_id, cmd, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00], is_extended=True)
            time.sleep(0.08)

    # Pattern B: 0x180011F4 (Config / Serial Query)
    for mod_id in [0xAA, 0xAB, 0x00, 0x01]:
        sniffer.send(0x180011F4, [mod_id, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00], is_extended=True)
        time.sleep(0.08)

    # Pattern C: J1939-style Node addressed requests (0x1800 + Target + Source)
    for target in [0xAA, 0xAB, 0x01, 0xF4]:
        can_id = 0x18EA00F4 | (target << 8)
        sniffer.send(can_id, [0x00, 0xEE, 0x00], is_extended=True)
        time.sleep(0.08)

    # Pattern D: Standard 11-bit queries (0x450, 0x7E0 OBD/UDS TesterPresent)
    sniffer.send(0x7E0, [0x02, 0x3E, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00], is_extended=False)
    time.sleep(0.08)
    sniffer.send(0x7E0, [0x03, 0x22, 0xF1, 0x90, 0x00, 0x00, 0x00, 0x00], is_extended=False)
    time.sleep(0.08)

    # Pattern E: Master Heartbeat broadcast (0x1800F4AA / 0x1801F4AA)
    sniffer.send(0x1800F4AA, [0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00], is_extended=True)
    time.sleep(0.1)

    print("\n[*] Probe sweep completed. Listening for delayed responses...")
    time.sleep(2.0)

if __name__ == "__main__":
    interface = sys.argv[1] if len(sys.argv) > 1 else "can0"
    sniffer = CANBusSniffer(interface=interface)
    sniffer.start()

    try:
        if "--sweep" in sys.argv:
            run_probe_sequence(sniffer)
        else:
            print("Options:")
            print("  Press [Enter] to run the automated Huasi / CATL probe sweep")
            print("  Or type raw frame: e.g. '180016F4#AA12000000000000'")
            print("  Press Ctrl+C to exit\n")
            while True:
                user_input = input().strip()
                if not user_input or user_input == "sweep":
                    run_probe_sequence(sniffer)
                elif "#" in user_input:
                    parts = user_input.split("#")
                    cid = int(parts[0], 16)
                    is_ext = len(parts[0]) > 3
                    data = bytes.fromhex(parts[1]) if len(parts) > 1 and parts[1] else b""
                    sniffer.send(cid, list(data), is_extended=is_ext)
                else:
                    print("Unrecognized command. Use format: <CANID>#<HEXDATA> or hit Enter for sweep.")
    except KeyboardInterrupt:
        print("\nExiting...")
    finally:
        sniffer.stop()
