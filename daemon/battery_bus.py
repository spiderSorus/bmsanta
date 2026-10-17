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
    Manages low-level CAN communication with one or more CATL LBM025100A modules.
    Supports daisy-chained parallel packs sharing an interface and CAN ID.
    """

    def __init__(self, interface: str = "can1", candidate_nodes: Optional[List[int]] = None):
        self.interface = interface
        self.candidate_nodes = candidate_nodes or [0xAA + i for i in range(16)]  # 0xAA to 0xB9 (up to 16 packs)
        self.modules: Dict[int, ModuleTelemetry] = {}
        self.active_nodes: List[int] = []
        self._sock: Optional[socket.socket] = None
        self._last_discovery_time = 0.0
        self.discovery_interval = 30.0  # seconds

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
        except Exception as e:
            logger.error("CAN send failed: %s", e)
            return False

    def _drain_can_rx(self, timeout_sec: float = 0.20) -> List[tuple]:
        """Reads all pending CAN messages until silence or timeout."""
        frames = []
        if not self._sock:
            return frames

        t_end = time.time() + timeout_sec
        while time.time() < t_end:
            remaining = max(0.005, t_end - time.time())
            r, _, _ = select.select([self._sock], [], [], remaining)
            if not r:
                break
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

    def poll_node(self, node_id: int) -> bool:
        """
        Polls for Cell Voltages (0x13) and Temperatures (0x14).
        Seamlessly supports up to 8+ daisy-chained packs answering to node ID.
        """
        if not self._sock:
            return False

        # --- 1. Query Cell Voltages (Cmd 0x13) ---
        p_v = bytes([node_id, 0x13, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
        self._send_can_frame(POLL_ID, p_v)
        time.sleep(0.03)
        v_frames = self._drain_can_rx(timeout_sec=0.25)

        # --- 2. Query Temperatures (Cmd 0x14) ---
        p_t = bytes([node_id, 0x14, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
        self._send_can_frame(POLL_ID, p_t)
        time.sleep(0.03)
        t_frames = self._drain_can_rx(timeout_sec=0.15)

        # Collect all response frames for this query
        lower_halves = [data for cid, dlc, data in v_frames if cid == (0x18001300 | node_id) and dlc == 8]
        upper_halves = [data for cid, dlc, data in v_frames if cid == (0x18011300 | node_id) and dlc == 8]
        temp_packets = [data for cid, dlc, data in t_frames if cid == (0x18001400 | node_id) and dlc >= 4]

        num_packs = max(len(lower_halves), len(upper_halves))
        if num_packs == 0:
            mod = self.modules.get(node_id)
            if mod:
                mod.missed_polls += 1
                if mod.missed_polls >= 3:
                    mod.is_online = False
                    if node_id in self.active_nodes:
                        self.active_nodes.remove(node_id)
            return False

        any_online = False
        for i in range(num_packs):
            pack_node_id = node_id if i == 0 else (node_id + i)
            mod = self.modules.setdefault(pack_node_id, ModuleTelemetry(node_id=pack_node_id))
            got_v = False

            if i < len(lower_halves):
                d = lower_halves[i]
                mod.cells[0] = struct.unpack("<H", d[0:2])[0] / 1000.0
                mod.cells[1] = struct.unpack("<H", d[2:4])[0] / 1000.0
                mod.cells[2] = struct.unpack("<H", d[4:6])[0] / 1000.0
                mod.cells[3] = struct.unpack("<H", d[6:8])[0] / 1000.0
                got_v = True

            if i < len(upper_halves):
                d = upper_halves[i]
                mod.cells[4] = struct.unpack("<H", d[0:2])[0] / 1000.0
                mod.cells[5] = struct.unpack("<H", d[2:4])[0] / 1000.0
                mod.cells[6] = struct.unpack("<H", d[4:6])[0] / 1000.0
                mod.cells[7] = struct.unpack("<H", d[6:8])[0] / 1000.0
                got_v = True

            if i < len(temp_packets):
                d = temp_packets[i]
                t1_f = struct.unpack("<H", d[0:2])[0] / 10.0
                t2_f = struct.unpack("<H", d[2:4])[0] / 10.0
                mod.temps_f = [t1_f, t2_f]
                mod.temps = [(t1_f - 32.0) * 5.0 / 9.0, (t2_f - 32.0) * 5.0 / 9.0]

            if got_v and any(c > 1.0 for c in mod.cells):
                mod.is_online = True
                mod.last_seen = time.time()
                mod.missed_polls = 0
                mod.pack_v = sum(mod.cells)
                valid = [c for c in mod.cells if c > 1.0]
                if valid:
                    mod.min_cell = min(valid)
                    mod.max_cell = max(valid)
                    mod.delta_mv = (mod.max_cell - mod.min_cell) * 1000.0
                if pack_node_id not in self.active_nodes:
                    self.active_nodes.append(pack_node_id)
                any_online = True

        return any_online

    def poll_node_status(self, node_id: int) -> bool:
        """Queries diagnostics, fault status, and firmware (Cmd 0x12) for all daisy-chained packs."""
        if not self._sock:
            return False
        p_s = bytes([node_id, 0x12, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
        self._send_can_frame(POLL_ID, p_s)
        time.sleep(0.04)
        frames = self._drain_can_rx(timeout_sec=0.30)

        status_frames = [data for cid, dlc, data in frames if cid == (0x18001200 | node_id) and dlc >= 3]
        fw_frames = [data for cid, dlc, data in frames if cid == (0x18041200 | node_id) and dlc >= 5]

        num_packs = max(len(status_frames), len(fw_frames), 1)
        for i in range(num_packs):
            pack_nid = node_id if i == 0 else (node_id + i)
            mod = self.modules.setdefault(pack_nid, ModuleTelemetry(node_id=pack_nid))
            if i < len(status_frames):
                d = status_frames[i]
                mod.status_code = d[2]
                mod.fault_flags = d[3:8]
            if i < len(fw_frames):
                d = fw_frames[i]
                mod.firmware = f"{d[2]}.{d[3]:02d}.{d[4]}"

        return len(frames) > 0

    def discover_modules(self) -> List[int]:
        """Probes candidate nodes to discover active modules."""
        logger.info("Scanning CAN bus %s for CATL modules (0x%02X..0x%02X)...",
                    self.interface, self.candidate_nodes[0], self.candidate_nodes[-1])
        self.active_nodes = []
        for nid in self.candidate_nodes:
            if self.poll_node(nid):
                self.poll_node_status(nid)
            time.sleep(0.02)

        for nid in sorted(self.active_nodes):
            m = self.modules[nid]
            logger.info("Found online module: 0x%02X (Pack V: %.3f V, Min: %.3f V, Max: %.3f V)",
                        nid, m.pack_v, m.min_cell, m.max_cell)

        self._last_discovery_time = time.time()
        return self.active_nodes

    def poll_all_active(self) -> Dict[int, ModuleTelemetry]:
        """
        Polls all currently active modules.
        Triggers discovery if no modules are known or if interval expired.
        """
        now = time.time()
        if not self.active_nodes or (now - self._last_discovery_time > self.discovery_interval):
            self.discover_modules()

        # Poll base nodes
        base_nodes = set(nid for nid in self.candidate_nodes if nid in self.active_nodes or nid == 0xAA)
        for nid in base_nodes:
            self.poll_node(nid)

        return {nid: self.modules[nid] for nid in self.active_nodes if self.modules[nid].is_online}
