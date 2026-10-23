#!/usr/bin/env python3
"""
daemon/battery_bus.py - SocketCAN Driver & Telemetry Poller for CATL LBM025100A
Communicates with Huasi/ZNTech BME slave controllers over CAN 2.0B at 250 kbps.
Supports single-bus daisy chains with identical (0xAA) or distinct (0xAA..0xB1) node IDs.
"""

import time
import socket
import struct
import select
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable

logger = logging.getLogger("bmsanta.battery_bus")

CAN_EFF = 0x80000000
POLL_ID = 0x180011F4 | CAN_EFF


@dataclass
class ModuleTelemetry:
    node_id: int
    is_online: bool = False
    last_seen: float = 0.0
    hw_node_id: int = 0xAA
    pack_index: int = 1
    cells: List[float] = field(default_factory=lambda: [0.0] * 8)
    temps: List[float] = field(default_factory=lambda: [0.0, 0.0])  # in Celsius
    temps_f: List[float] = field(default_factory=lambda: [0.0, 0.0])  # in Fahrenheit
    pack_v: float = 0.0
    min_cell: float = 0.0
    max_cell: float = 0.0
    delta_mv: float = 0.0
    status_code: int = 0
    fault_flags: bytes = b"\x00" * 5
    firmware: str = ""
    missed_polls: int = 0


class BatteryBus:
    """
    Manages low-level CAN communication with CATL LBM025100A modules.
    Supports single-bus daisy chains with any mix of shared (0xAA) or distinct (0xAA..0xB9) node IDs.
    """

    def __init__(self, interface: str = "can1", candidate_nodes: Optional[List[int]] = None):
        self.interface = interface
        self.candidate_nodes = candidate_nodes or [0xAA, 0xAB, 0xAC, 0xAD, 0xAE, 0xAF, 0xB0, 0xB1]
        self.active_hw_nodes: List[int] = [0xAA]  # physical query IDs returning responses
        self.modules: Dict[int, ModuleTelemetry] = {}
        self.active_nodes: List[int] = []
        self._hw_status_cache: Dict[int, List[dict]] = {}
        self._sock: Optional[socket.socket] = None
        self._last_discovery_time = 0.0
        self.discovery_interval = 60.0  # seconds

    def connect(self) -> bool:
        """Opens and binds the raw SocketCAN interface with auto-detection."""
        candidates = [self.interface]
        alt = "can0" if self.interface == "can1" else "can1"
        if alt not in candidates:
            candidates.append(alt)

        for iface in candidates:
            try:
                if self._sock:
                    self.close()
                s = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
                s.bind((iface,))
                s.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_LOOPBACK, 0)
                s.setblocking(False)
                self._sock = s
                self.interface = iface
                logger.info("BatteryBus successfully connected to SocketCAN interface %s", self.interface)
                return True
            except Exception as e:
                logger.warning("Failed to bind SocketCAN interface %s: %s", iface, e)
                self._sock = None

        logger.error("Could not bind any CAN interface (%s)", ", ".join(candidates))
        return False

    def close(self):
        """Closes the SocketCAN interface."""
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def is_connected(self) -> bool:
        return self._sock is not None

    def _send_can_frame(self, can_id: int, payload: bytes) -> bool:
        """Encodes and transmits an extended CAN 2.0B frame."""
        if not self._sock:
            return False
        try:
            padded_payload = payload.ljust(8, b"\x00")[:8]
            frame = struct.pack("=IB3x8s", can_id | CAN_EFF, len(payload), padded_payload)
            self._sock.send(frame)
            return True
        except OSError as e:
            if getattr(e, 'errno', None) == 105:
                # No buffer space available: CAN bus has no ACKs (batteries disconnected or unpowered)
                logger.debug("CAN TX buffer full (waiting for battery ACK on %s)", self.interface)
            else:
                logger.error("CAN send failed on %s: %s", self.interface, e)
            return False
        except Exception as e:
            logger.error("CAN send failed: %s", e)
            return False

    def _drain_can_rx(self, timeout_sec: float = 0.08, quiet_timeout: float = 0.015) -> List[tuple]:
        """Reads pending CAN messages until quiet_timeout silence or overall timeout."""
        frames = []
        if not self._sock:
            return frames

        t_end = time.time() + timeout_sec
        while time.time() < t_end:
            rem = min(quiet_timeout, max(0.002, t_end - time.time()))
            r, _, _ = select.select([self._sock], [], [], rem)
            if not r:
                if frames or time.time() >= t_end:
                    break
                continue
            try:
                raw = self._sock.recv(16)
                if len(raw) >= 16:
                    r_id, r_dlc, r_data = struct.unpack("=IB3x8s", raw)
                    clean_id = r_id & 0x1FFFFFFF
                    frames.append((clean_id, r_dlc, r_data[:r_dlc]))
            except BlockingIOError:
                break
            except Exception as e:
                logger.error("Error receiving CAN frame: %s", e)
                break
        return frames

    def _query_hw_pack_data(self, hw_nid: int) -> List[dict]:
        """Reads cell voltages (0x13) and temperatures (0x14) by actively querying hw_nid."""
        if not self._sock:
            return []

        # 1. Send voltage query (0x13)
        p_v = bytes([hw_nid, 0x13, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
        self._send_can_frame(POLL_ID, p_v)
        v_frames = self._drain_can_rx(timeout_sec=0.06, quiet_timeout=0.015)

        # 2. Send temperature query (0x14)
        p_t = bytes([hw_nid, 0x14, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
        self._send_can_frame(POLL_ID, p_t)
        t_frames = self._drain_can_rx(timeout_sec=0.05, quiet_timeout=0.015)

        all_rx = v_frames + t_frames
        lower_halves = [data for cid, dlc, data in all_rx if cid == (0x18001300 | hw_nid) and dlc == 8]
        upper_halves = [data for cid, dlc, data in all_rx if cid == (0x18011300 | hw_nid) and dlc == 8]
        temp_packets = [data for cid, dlc, data in all_rx if cid == (0x18001400 | hw_nid) and dlc >= 4]

        # Only pair complete cell halves (cells 1-4 and cells 5-8), capped to 8 packs max
        num_packs = min(len(lower_halves), len(upper_halves), 8)
        results = []

        for i in range(num_packs):
            cells = [0.0] * 8
            if i < len(lower_halves):
                d = lower_halves[i]
                cells[0] = struct.unpack("<H", d[0:2])[0] / 1000.0
                cells[1] = struct.unpack("<H", d[2:4])[0] / 1000.0
                cells[2] = struct.unpack("<H", d[4:6])[0] / 1000.0
                cells[3] = struct.unpack("<H", d[6:8])[0] / 1000.0
            if i < len(upper_halves):
                d = upper_halves[i]
                cells[4] = struct.unpack("<H", d[0:2])[0] / 1000.0
                cells[5] = struct.unpack("<H", d[2:4])[0] / 1000.0
                cells[6] = struct.unpack("<H", d[4:6])[0] / 1000.0
                cells[7] = struct.unpack("<H", d[6:8])[0] / 1000.0

            temps = [0.0, 0.0]
            temps_f = [0.0, 0.0]
            if i < len(temp_packets):
                d = temp_packets[i]
                t1_f = struct.unpack("<H", d[0:2])[0] / 10.0
                t2_f = struct.unpack("<H", d[2:4])[0] / 10.0
                temps_f = [t1_f, t2_f]
                temps = [(t1_f - 32.0) * 5.0 / 9.0, (t2_f - 32.0) * 5.0 / 9.0]

            if any(c > 1.0 for c in cells):
                pack_v = sum(cells)
                valid = [c for c in cells if c > 1.0]
                min_c = min(valid) if valid else 0.0
                max_c = max(valid) if valid else 0.0
                delta_mv = (max_c - min_c) * 1000.0 if valid else 0.0
                results.append({
                    "hw_nid": hw_nid,
                    "sub_index": i,
                    "cells": cells,
                    "temps": temps,
                    "temps_f": temps_f,
                    "pack_v": pack_v,
                    "min_cell": min_c,
                    "max_cell": max_c,
                    "delta_mv": delta_mv,
                })

        return results

    def _query_hw_status(self, hw_nid: int) -> List[dict]:
        """Queries diagnostics, fault status, and firmware (Cmd 0x12) for a hardware CAN ID."""
        if not self._sock:
            return []
        p_s = bytes([hw_nid, 0x12, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
        self._send_can_frame(POLL_ID, p_s)
        frames = self._drain_can_rx(timeout_sec=0.10, quiet_timeout=0.015)

        status_frames = [data for cid, dlc, data in frames if cid == (0x18001200 | hw_nid) and dlc >= 3]
        fw_frames = [data for cid, dlc, data in frames if cid == (0x18041200 | hw_nid) and dlc >= 5]

        num_packs = max(len(status_frames), len(fw_frames), 1)
        results = []
        for i in range(num_packs):
            st_code = status_frames[i][2] if i < len(status_frames) else 0
            faults = status_frames[i][3:8] if i < len(status_frames) else b"\x00" * 5
            fw = ""
            if i < len(fw_frames):
                d = fw_frames[i]
                fw = f"{d[2]}.{d[3]:02d}.{d[4]}"
            results.append({"status_code": st_code, "fault_flags": faults, "firmware": fw})
        return results

    def poll_node(self, node_id: int) -> bool:
        """Polls a single node for backwards compatibility."""
        packs = self._query_hw_pack_data(node_id)
        return len(packs) > 0

    def discover_modules(self) -> List[int]:
        """Probes all candidate CAN IDs to discover active hardware nodes."""
        logger.info("Scanning CAN bus %s for CATL modules (0x%02X..0x%02X)...",
                    self.interface, self.candidate_nodes[0], self.candidate_nodes[-1])
        active_hw = []
        hw_status_map = {}

        for nid in self.candidate_nodes:
            self._drain_can_rx(timeout_sec=0.01)  # flush buffer before probe
            packs = self._query_hw_pack_data(nid)
            if packs:
                active_hw.append(nid)
                st_list = self._query_hw_status(nid)
                hw_status_map[nid] = st_list
                logger.info("Found %d pack(s) responding to CAN ID 0x%02X", len(packs), nid)
            time.sleep(0.01)

        if 0xAA not in active_hw and not active_hw:
            active_hw.append(0xAA)

        self.active_hw_nodes = active_hw
        self._hw_status_cache = hw_status_map
        self._last_discovery_time = time.time()
        return self.active_hw_nodes

    def poll_all_active(self) -> Dict[int, ModuleTelemetry]:
        """
        Polls all active hardware nodes and updates unified multi-pack telemetry.
        Triggers discovery if interval expired or no hardware nodes known.
        """
        now = time.time()
        if not self.active_hw_nodes or (now - self._last_discovery_time > self.discovery_interval):
            self.discover_modules()

        all_packs = []
        for hw_nid in self.active_hw_nodes:
            pack_list = self._query_hw_pack_data(hw_nid)
            for p in pack_list:
                all_packs.append(p)

        self.active_nodes = []
        for i, p in enumerate(all_packs):
            key = 0xAA + i
            self.active_nodes.append(key)
            mod = self.modules.setdefault(key, ModuleTelemetry(node_id=key))
            mod.is_online = True
            mod.last_seen = now
            mod.missed_polls = 0
            mod.hw_node_id = p["hw_nid"]
            mod.pack_index = i + 1
            mod.cells = p["cells"]
            mod.temps = p["temps"]
            mod.temps_f = p["temps_f"]
            mod.pack_v = p["pack_v"]
            mod.min_cell = p["min_cell"]
            mod.max_cell = p["max_cell"]
            mod.delta_mv = p["delta_mv"]

            # Attach cached firmware/status
            cached_st = self._hw_status_cache.get(p["hw_nid"], [])
            sub_idx = p["sub_index"]
            if sub_idx < len(cached_st):
                st = cached_st[sub_idx]
                if st.get("firmware"):
                    mod.firmware = st["firmware"]
                mod.status_code = st.get("status_code", 0)
                mod.fault_flags = st.get("fault_flags", b"\x00" * 5)
            elif not mod.firmware:
                mod.firmware = "1.32.0"

        # Mark any previously known nodes that were not in this poll as offline
        for nid, m in list(self.modules.items()):
            if nid not in self.active_nodes:
                m.missed_polls += 1
                if m.missed_polls >= 3:
                    m.is_online = False

        return {nid: self.modules[nid] for nid in self.active_nodes if self.modules[nid].is_online}
