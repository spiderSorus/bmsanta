#!/usr/bin/env python3
"""
tools/test_data_queries.py - Exhaustive 29-bit Data Query Sweep
Sends active DATA frames (not RTR) for all candidate Huasi/BME formats.
"""

import sys
import time
import socket
import struct
import select

CAN_EFF = 0x80000000

def main():
    iface = "can0"
    s = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    s.bind((iface,))
    s.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_LOOPBACK, 0)
    s.setblocking(False)

    print(f"[*] Starting 29-bit Data Query Sweep on {iface}...")

    hit_count = 0

    def check_rx(sent_id_desc=""):
        nonlocal hit_count
        while True:
            r, _, _ = select.select([s], [], [], 0.003)
            if not r:
                break
            raw = s.recv(16)
            if len(raw) >= 16:
                r_id, r_dlc, r_data = struct.unpack("=IB3x8s", raw)
                clean_id = r_id & 0x1FFFFFFF
                is_ext = bool(r_id & CAN_EFF)
                payload = r_data[:r_dlc].hex()
                print(f"\n========================================================")
                print(f"[*** BATTERY RESPONSE DETECTED! ***]")
                print(f"    Triggered by: {sent_id_desc}")
                print(f"    Received ID: 0x{clean_id:08X} (EXT={is_ext}) DLC={r_dlc}")
                print(f"    Data: {payload}")
                print(f"========================================================\n")
                with open("/home/dangersorus/battery_found.txt", "a") as f:
                    f.write(f"TRIGGER={sent_id_desc} ID=0x{clean_id:08X} DATA={payload}\n")
                hit_count += 1

    candidate_ids = []

    # 1. 8-digit Format: 0x1800{cmd}{node}
    # Commands: 00..20, Nodes: AA, AB, AC, AD, 00, 01, 02, F4
    for cmd in range(0x10, 0x18):
        for node in [0xAA, 0xAB, 0xAC, 0xAD, 0x00, 0x01, 0x02, 0xF4]:
            cid = 0x18000000 | (cmd << 8) | node
            candidate_ids.append((cid, f"0x1800{cmd:02X}{node:02X}"))

    # 2. 7-digit Format: 0x180{cmd}{node}
    for cmd in range(0x10, 0x18):
        for node in [0xAA, 0xAB, 0xAC, 0xAD, 0x00, 0x01, 0x02, 0xF4]:
            cid = 0x1800000 | (cmd << 8) | node
            candidate_ids.append((cid, f"0x180{cmd:02X}{node:02X}"))

    # 3. Destination in byte 2: 0x18{cmd}{node}F4
    for cmd in [0x00, 0x11, 0x12, 0x13, 0x14, 0x16, 0xEF, 0xFF]:
        for node in [0xAA, 0xAB, 0xAC, 0xAD, 0x00, 0x01, 0x02, 0xFF]:
            cid = 0x18000000 | (cmd << 16) | (node << 8) | 0xF4
            candidate_ids.append((cid, f"0x18{cmd:02X}{node:02X}F4"))

    # 4. Master target in byte 2: 0x1800F4{node}
    for node in [0xAA, 0xAB, 0xAC, 0xAD, 0x00, 0x01, 0x02]:
        cid = 0x18000000 | (0xF4 << 8) | node
        candidate_ids.append((cid, f"0x1800F4{node:02X}"))

    print(f"Generated {len(candidate_ids)} candidate IDs. Testing each with 4 payload variations...")

    payloads = [
        b'\x00' * 8,
        b'\x01' + b'\x00' * 7,
        b'\xAA\x12' + b'\x00' * 6,
        b'\x12\xAA' + b'\x00' * 6,
        b''  # 0-byte frame (ping)
    ]

    for cid, desc in candidate_ids:
        for p in payloads:
            raw_p = p.ljust(8, b'\x00') if p else b'\x00'*8
            frame = struct.pack("=IB3x8s", cid | CAN_EFF, len(p), raw_p)
            s.send(frame)
            time.sleep(0.002)
            check_rx(f"{desc} data={p.hex()}")

    print(f"\n[*] Sweep complete. Total hits: {hit_count}")
    s.close()

if __name__ == "__main__":
    main()
