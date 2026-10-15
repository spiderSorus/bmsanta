#!/usr/bin/env python3
"""
tools/scan_ids.py - Fast CAN ID Scanner for Battery Interrogation
Sweeps 11-bit IDs, RTR requests, and J1939 PGNs on can0.
"""

import sys
import time
import socket
import struct
import select

CAN_EFF_FLAG = 0x80000000
CAN_RTR_FLAG = 0x40000000
CAN_EFF_MASK = 0x1FFFFFFF
CAN_SFF_MASK = 0x000007FF

def main():
    iface = "can0"
    s = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    s.bind((iface,))
    # Disable local loopback so we ONLY receive frames from the battery!
    s.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_LOOPBACK, 0)
    s.setblocking(False)

    print(f"[*] Fast CAN Scanner running on {iface} (Loopback disabled)")

    def drain_rx():
        found = False
        while True:
            ready, _, _ = select.select([s], [], [], 0.005)
            if not ready:
                break
            raw_bytes = s.recv(16)
            if len(raw_bytes) >= 16:
                raw_id, dlc, raw_data = struct.unpack("=IB3x8s", raw_bytes)
                is_ext = bool(raw_id & CAN_EFF_FLAG)
                clean_id = (raw_id & CAN_EFF_MASK) if is_ext else (raw_id & CAN_SFF_MASK)
                payload = raw_data[:dlc].hex()
                print(f"\n[!!! BATTERY RESPONSE RECEIVED !!!]")
                print(f"    ID: 0x{clean_id:08X} (EXT={is_ext}) DLC={dlc} DATA={payload}\n")
                with open("/home/dangersorus/battery_found.txt", "a") as f:
                    f.write(f"ID=0x{clean_id:08X} EXT={is_ext} DLC={dlc} DATA={payload}\n")
                found = True
        return found

    # 1. Sweep all standard 11-bit IDs with RTR (Remote Transmission Request)
    print("\n--- PHASE 1: Sweeping 11-bit RTR (0x001 - 0x7FF) ---")
    for cid in range(1, 0x800):
        frame = struct.pack("=IB3x8s", cid | CAN_RTR_FLAG, 8, b'\x00'*8)
        s.send(frame)
        if drain_rx():
            print(f"Hit on 11-bit RTR ID 0x{cid:03X}")
        time.sleep(0.002)

    # 2. Sweep common 11-bit inquiry frames
    print("\n--- PHASE 2: Sweeping 11-bit Data Queries ---")
    for cid in range(0x100, 0x7FF, 0x10):
        for payload in [b'\x01\x00\x00\x00\x00\x00\x00\x00', b'\xAA\x12\x00\x00\x00\x00\x00\x00']:
            frame = struct.pack("=IB3x8s", cid, len(payload), payload.ljust(8, b'\x00'))
            s.send(frame)
            if drain_rx():
                print(f"Hit on 11-bit Query ID 0x{cid:03X}")
            time.sleep(0.003)

    # 3. Sweep J1939 Address Claims & PGN requests
    print("\n--- PHASE 3: Sweeping J1939 Requests (0x18EAxxxx) ---")
    # PGN 59904 (ISO Request) to destination DA
    for da in [0x00, 0x01, 0x02, 0xAA, 0xAB, 0xAC, 0xAD, 0xEF, 0xF4, 0xFF]:
        req_id = 0x18EA00F4 | (da << 8) | CAN_EFF_FLAG
        for pgn in [0xEE00, 0xFEEE, 0xFEFF, 0x0000, 0x1200, 0x1600, 0xFF50]:
            pgn_bytes = struct.pack("<I", pgn)[:3]
            frame = struct.pack("=IB3x8s", req_id, 3, pgn_bytes.ljust(8, b'\x00'))
            s.send(frame)
            if drain_rx():
                print(f"Hit on J1939 DA=0x{da:02X} PGN=0x{pgn:04X}")
            time.sleep(0.01)

    # 4. Sweep 29-bit RTR on common Huasi / CATL IDs
    print("\n--- PHASE 4: Sweeping 29-bit RTR on candidate IDs ---")
    for prefix in [0x18000000, 0x18010000, 0x18FF0000, 0x18EF0000]:
        for mod in [0xAA, 0xAB, 0xAC, 0xAD, 0x00, 0x01, 0xF4]:
            cid = prefix | (mod << 8) | 0xF4
            frame = struct.pack("=IB3x8s", cid | CAN_EFF_FLAG | CAN_RTR_FLAG, 8, b'\x00'*8)
            s.send(frame)
            if drain_rx():
                print(f"Hit on 29-bit RTR ID 0x{cid:08X}")
            time.sleep(0.005)

    print("\n[*] Scan sequence complete.")
    s.close()

if __name__ == "__main__":
    main()
