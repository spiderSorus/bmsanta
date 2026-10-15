#!/usr/bin/env python3
"""
daemon/battery_bus.py - SocketCAN Driver & Telemetry Poller for CATL LBM025100A
Communicates with Huasi/ZNTech BME slave controllers over CAN 2.0B at 250 kbps.
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
    Supports auto-discovery of parallel daisy-chained modules (0xAA .. 0xB2).
    """

    def __init__(self, interface: str = "can0", candidate_nodes: Optional[List[int]] = None):
        self.interface = interface
        self.candidate_nodes = candidate_nodes or [0xAA + i for i in range(8)]  # 0xAA to 0xB1
        self.modules: Dict[int, ModuleTelemetry] = {
            nid: ModuleTelemetry(node_id=nid) for nid in self.candidate_nodes
        }
        self.active_nodes: List[int] = []
        self._sock: Optional[socket.socket] = None
        self._last_discovery_time = 0.0
        self.discovery_interval = 30.0  # seconds

    def connect(self) -> bool:
        """Opens and binds the raw SocketCAN interface."""
        try:
            if self._sock:
                self.close()
            self._sock = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
            self._sock.bind((self.interface,))
            self._sock.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_LOOPBACK, 0)
            self._sock.setblocking(False)
            logger.info("BatteryBus connected to SocketCAN interface %s", self.interface)
            return True
        except Exception as e:
            logger.error("Failed to bind SocketCAN interface %s: %s", self.interface, e)
            self._sock = None
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

    def _drain_can_rx(self, timeout_sec: float = 0.08) -> List[tuple]:
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
        Polls a specific module for Cell Voltages (0x13) and Temperatures (0x14).
        Updates telemetry in self.modules[node_id].
        Returns True if module replied with valid data.
        """
        if not self._sock:
            return False

        mod = self.modules.setdefault(node_id, ModuleTelemetry(node_id=node_id))
        got_voltages = False

        # --- 1. Query Cell Voltages (Cmd 0x13) ---
        p_v = bytes([node_id, 0x13, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
        self._send_can_frame(POLL_ID, p_v)
        time.sleep(0.02)
        v_frames = self._drain_can_rx(timeout_sec=0.10)

        for cid, dlc, data in v_frames:
            if cid == (0x18001300 | node_id) and dlc == 8:
                mod.cells[0] = struct.unpack("<H", data[0:2])[0] / 1000.0
                mod.cells[1] = struct.unpack("<H", data[2:4])[0] / 1000.0
                mod.cells[2] = struct.unpack("<H", data[4:6])[0] / 1000.0
                mod.cells[3] = struct.unpack("<H", data[6:8])[0] / 1000.0
                got_voltages = True
            elif cid == (0x18011300 | node_id) and dlc == 8:
                mod.cells[4] = struct.unpack("<H", data[0:2])[0] / 1000.0
                mod.cells[5] = struct.unpack("<H", data[2:4])[0] / 1000.0
                mod.cells[6] = struct.unpack("<H", data[4:6])[0] / 1000.0
                mod.cells[7] = struct.unpack("<H", data[6:8])[0] / 1000.0
                got_voltages = True

        # --- 2. Query Temperatures (Cmd 0x14) ---
        p_t = bytes([node_id, 0x14, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
        self._send_can_frame(POLL_ID, p_t)
        time.sleep(0.02)
        t_frames = self._drain_can_rx(timeout_sec=0.08)

        for cid, dlc, data in t_frames:
            if cid == (0x18001400 | node_id) and dlc >= 4:
                raw_t1 = struct.unpack("<H", data[0:2])[0]
                raw_t2 = struct.unpack("<H", data[2:4])[0]
                # Scale: 0.1 deg F
                t1_f = raw_t1 / 10.0
                t2_f = raw_t2 / 10.0
                mod.temps_f = [t1_f, t2_f]
                # Convert to Celsius
                mod.temps = [(t1_f - 32.0) * 5.0 / 9.0, (t2_f - 32.0) * 5.0 / 9.0]

        if got_voltages:
            mod.is_online = True
            mod.last_seen = time.time()
            mod.missed_polls = 0
            mod.pack_v = sum(mod.cells)
            valid = [c for c in mod.cells if c > 1.0]
            if valid:
                mod.min_cell = min(valid)
                mod.max_cell = max(valid)
                mod.delta_mv = (mod.max_cell - mod.min_cell) * 1000.0
            return True
        else:
            mod.missed_polls += 1
            if mod.missed_polls >= 3:
                mod.is_online = False
            return False

    def poll_node_status(self, node_id: int) -> bool:
        """Queries diagnostics and fault status (Cmd 0x12)."""
        if not self._sock:
            return False
        p_s = bytes([node_id, 0x12, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
        self._send_can_frame(POLL_ID, p_s)
        time.sleep(0.03)
        frames = self._drain_can_rx(timeout_sec=0.10)

        mod = self.modules.setdefault(node_id, ModuleTelemetry(node_id=node_id))
        for cid, dlc, data in frames:
            if cid == (0x18001200 | node_id) and dlc >= 3:
                mod.status_code = data[2]
                mod.fault_flags = data[3:8]
            elif cid == (0x18041200 | node_id) and dlc >= 5:
                # Firmware: e.g. 00 00 01 20 00 -> 1.20.0
                mod.firmware = f"{data[2]}.{data[3]:02d}.{data[4]}"
        return len(frames) > 0

    def discover_modules(self) -> List[int]:
        """Probes all candidate nodes to discover active modules."""
        logger.info("Scanning CAN bus for CATL LBM025100A modules (0x%02X..0x%02X)...",
                    self.candidate_nodes[0], self.candidate_nodes[-1])
        discovered = []
        for nid in self.candidate_nodes:
            if self.poll_node(nid):
                self.poll_node_status(nid)
                discovered.append(nid)
                logger.info("Found online module: 0x%02X (Pack V: %.3f V, FW: %s)",
                            nid, self.modules[nid].pack_v, self.modules[nid].firmware)
            time.sleep(0.02)

        self.active_nodes = discovered
        self._last_discovery_time = time.time()
        return discovered

    def poll_all_active(self) -> Dict[int, ModuleTelemetry]:
        """
        Polls all currently active modules.
        Triggers discovery if no modules are known or if interval expired.
        """
        now = time.time()
        if not self.active_nodes or (now - self._last_discovery_time > self.discovery_interval):
            self.discover_modules()

        for nid in list(self.active_nodes):
            success = self.poll_node(nid)
            if not success and self.modules[nid].missed_polls >= 5:
                logger.warning("Module 0x%02X went offline (5 missed polls)", nid)
                self.active_nodes.remove(nid)

        return {nid: self.modules[nid] for nid in self.active_nodes if self.modules[nid].is_online}
