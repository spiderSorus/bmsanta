#!/usr/bin/env python3
"""
tools/interrogate.py - Full CATL LBM025100A / Huasi Battery Interrogation Tool
Polls Module 0xAA with commands 0x10 through 0x20 and decodes all telemetry.
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

    print(f"=== BMSanta Battery Interrogator for LBM025100A (Module 0xAA) ===")

    def query(cmd_byte, data_extra=b'\x00'*6):
        # Master poll frame: 0x180011F4, Payload: [AA, cmd, ...]
        payload = bytes([0xAA, cmd_byte]) + data_extra
        frame = struct.pack("=IB3x8s", 0x180011F4 | CAN_EFF, 8, payload)
        s.send(frame)
        time.sleep(0.05)

        responses = []
        start = time.time()
        while time.time() - start < 0.2:
            r, _, _ = select.select([s], [], [], 0.02)
            if not r:
                break
            raw = s.recv(16)
            if len(raw) >= 16:
                r_id, r_dlc, r_data = struct.unpack("=IB3x8s", raw)
                clean_id = r_id & 0x1FFFFFFF
                responses.append((clean_id, r_dlc, r_data[:r_dlc]))
        return responses

    # Test all command codes from 0x01 to 0x30
    for cmd in [0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x01, 0x02, 0x03]:
        responses = query(cmd)
        if responses:
            print(f"\n--- Query [0xAA, 0x{cmd:02X}] returned {len(responses)} frame(s) ---")
            for cid, dlc, data in responses:
                hex_str = " ".join(f"{b:02X}" for b in data)
                ascii_str = "".join(chr(b) if 32 <= b <= 126 else "." for b in data)
                print(f"  RX ID: 0x{cid:08X} DLC:{dlc} [{hex_str}] | {ascii_str}")
        else:
            print(f"Query [0xAA, 0x{cmd:02X}]: No response")
        time.sleep(0.05)

    s.close()

if __name__ == "__main__":
    main()
