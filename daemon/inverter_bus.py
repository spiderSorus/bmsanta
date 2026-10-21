#!/usr/bin/env python3
"""
daemon/inverter_bus.py - Inverter Communication Gateway (RS-485 Modbus RTU & Pylontech ASCII & CAN)
Exposes aggregated battery bank state to solar hybrid inverters (PowMr, Growatt, Sol-Ark, Deye, Victron).
Supports:
1. Dual-Protocol RS-485 Serial Server on /dev/ttyUSB0:
   - Pylontech RS-485 ASCII Protocol (Used by PowMr Program 05: LIB-485, Program 43: PYL)
   - Standard Modbus RTU Function 0x03 (Used by PowMr / SRNE / Custom Modbus)
2. Pylontech CAN Emulation on secondary CAN bus (can1).
"""

import time
import socket
import struct
import threading
import logging
from typing import Optional, Tuple
from daemon.aggregator import BankState

logger = logging.getLogger("bmsanta.inverter_bus")


def calc_crc16_modbus(data: bytes) -> int:
    """Calculates standard Modbus RTU CRC-16."""
    crc = 0xFFFF
    for pos in data:
        crc ^= pos
        for _ in range(8):
            if (crc & 1) != 0:
                crc >>= 1
                crc ^= 0xA001
            else:
                crc >>= 1
    return crc


def calculate_pylon_lchksum(lenid: int) -> int:
    """Calculates 4-bit nibble checksum for Pylontech LENID."""
    s = ((lenid >> 8) & 0x0F) + ((lenid >> 4) & 0x0F) + (lenid & 0x0F)
    return (-s) & 0x0F


def build_pylon_length(lenid: int) -> str:
    """Builds 4-character hex ASCII string combining LCHKSUM and LENID."""
    lchk = calculate_pylon_lchksum(lenid)
    return f"{(lchk << 12) | (lenid & 0x0FFF):04X}"


def calculate_pylon_chksum(frame_body_ascii: str) -> str:
    """Calculates 16-bit Pylontech frame checksum across all ASCII bytes between ~ and CHKSUM."""
    total = sum(frame_body_ascii.encode("ascii")) % 0x10000
    chksum = (~total + 1) & 0xFFFF
    return f"{chksum:04X}"


class InverterSerialGateway:
    """
    Dual-protocol RS-485 serial worker for /dev/ttyUSB0 (Xiusiyt RJ45 cable).
    Automatically detects and handles both Pylontech RS-485 ASCII queries (PowMr LIB-485/PYL)
    and standard Modbus RTU requests (Function Code 0x03).
    """

    def __init__(self, port: str = "/dev/ttyUSB0", baudrate: int = 9600, slave_id: int = 1):
        self.port = port
        self.baudrate = baudrate
        self.slave_id = slave_id
        self._serial = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.latest_state: Optional[BankState] = None

    def start(self):
        try:
            import serial
            self._serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.05
            )
            self._running = True
            self._thread = threading.Thread(target=self._run_loop, name="InverterSerialWorker", daemon=True)
            self._thread.start()
            logger.info("Inverter RS-485 Gateway started on %s @ %d baud (Pylontech & Modbus RTU)",
                        self.port, self.baudrate)
        except Exception as e:
            logger.warning("Could not open RS-485 port %s: %s (Inverter RS-485 disabled)", self.port, e)

    def stop(self):
        self._running = False
        if self._serial:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None

    def update_state(self, state: BankState):
        self.latest_state = state

    # --- Pylontech RS-485 Protocol Handler ---
    def _handle_pylontech_frame(self, frame_str: str) -> Optional[bytes]:
        """
        Parses an incoming Pylontech ASCII request:
        ~VER(2) + ADR(2) + CID1(2) + CID2(2) + LENGTH(4) + [INFO] + CHKSUM(4) + \r
        """
        st = self.latest_state
        if not st or len(frame_str) < 14:
            return None

        clean = frame_str.strip("~\r\n")
        if len(clean) < 12:
            return None

        ver = clean[0:2]
        adr = clean[2:4]
        cid1 = clean[4:6]
        cid2 = clean[6:8]

        logger.debug("Pylontech RX: VER=%s ADR=%s CID1=%s CID2=%s", ver, adr, cid1, cid2)

        # CID1 = 46 (Battery telemetry / management group)
        if cid1 != "46":
            return None

        # 1. CID2 = 47: Charge & Discharge Management Info
        if cid2 == "47":
            cvl_mv = 28400  # 28.40V absorption
            ccl_da = int(st.safety.ccl_amps * 10)
            dcl_da = int(st.safety.dcl_amps * 10)
            cutoff_mv = 21000  # 21.00V low cutoff
            status_flags = 0xC0 if (st.safety.charge_enabled and st.safety.discharge_enabled) else 0x00
            if not st.safety.charge_enabled:
                status_flags &= ~0x80
            if not st.safety.discharge_enabled:
                status_flags &= ~0x40

            info = f"{cvl_mv:04X}{ccl_da:04X}{dcl_da:04X}{cutoff_mv:04X}{status_flags:02X}"
            length_str = build_pylon_length(len(info))
            body = f"{ver}{adr}{cid1}00{length_str}{info}"
            chk = calculate_pylon_chksum(body)
            logger.info("Replying Pylontech CID2=47 (CVL=%.2fV CCL=%.0fA DCL=%.0fA)",
                        cvl_mv/1000.0, ccl_da/10.0, dcl_da/10.0)
            return f"~{body}{chk}\r".encode("ascii")

        # 2. CID2 = 42: Analog Telemetry (Cell voltages, temps, V, I, Ah)
        elif cid2 == "42":
            info_flag = "01"
            pack_idx = "01"

            # 8 cell voltages in mV
            mod_data = next(iter(st.module_details.values()), None) if st.module_details else None
            cells = mod_data["cells"] if mod_data else [3.238] * 8
            cell_hex = "".join(f"{int(c * 1000):04X}" for c in cells[:8])

            # 2 temperatures in deci-Kelvin: (T_C + 273.15) * 10
            temps = mod_data["temps_c"] if mod_data else [st.avg_temp_c, st.avg_temp_c]
            temp_hex = "".join(f"{int((t + 273.15) * 10):04X}" for t in temps[:2])

            cur_hex = "0000"  # 0.1A signed
            pack_v_hex = f"{int(st.bank_voltage * 1000):04X}"
            rem_val = min(0xFFFF, max(0, int(st.total_capacity_ah * (st.soc_pct / 100.0) * 100)))
            tot_val = min(0xFFFF, max(0, int(st.total_capacity_ah * 100)))
            rem_ah_hex = f"{rem_val:04X}"
            tot_ah_hex = f"{tot_val:04X}"
            cycles_hex = "000A"

            info = f"{info_flag}{pack_idx}08{cell_hex}02{temp_hex}{cur_hex}{pack_v_hex}{rem_ah_hex}{tot_ah_hex}{cycles_hex}"
            length_str = build_pylon_length(len(info))
            body = f"{ver}{adr}{cid1}00{length_str}{info}"
            chk = calculate_pylon_chksum(body)
            logger.info("Replying Pylontech CID2=42 (V=%.3fV SOC=%d%% Delta=%.1fmV)",
                        st.bank_voltage, int(st.soc_pct), st.delta_cell_mv)
            return f"~{body}{chk}\r".encode("ascii")

        # 3. CID2 = 44: Alarms & Warnings
        elif cid2 == "44":
            # Normal / healthy = 00 across all fields
            info = "010108" + ("00" * 8) + "02" + ("00" * 2) + "0000"
            length_str = build_pylon_length(len(info))
            body = f"{ver}{adr}{cid1}00{length_str}{info}"
            chk = calculate_pylon_chksum(body)
            return f"~{body}{chk}\r".encode("ascii")

        # 4. CID2 = 51 / 92: Protocol Version & Hardware Info
        elif cid2 in ["51", "92"]:
            info = "20"
            length_str = build_pylon_length(len(info))
            body = f"{ver}{adr}{cid1}00{length_str}{info}"
            chk = calculate_pylon_chksum(body)
            return f"~{body}{chk}\r".encode("ascii")

        return None

    # --- Modbus RTU Protocol Handler ---
    def _build_holding_registers(self) -> dict:
        st = self.latest_state
        if not st:
            return {r: 0 for r in range(16)}

        v_centi = int(st.bank_voltage * 100)
        soc = int(st.soc_pct)
        soh = int(st.soh_pct)
        temp_deci = int(st.avg_temp_c * 10)
        ccl_deci = int(st.safety.ccl_amps * 10)
        dcl_deci = int(st.safety.dcl_amps * 10)
        min_cell_mv = int(st.min_cell_v * 1000)
        max_cell_mv = int(st.max_cell_v * 1000)
        delta_mv = int(st.delta_cell_mv)

        status_mask = 0
        if st.safety.is_healthy:
            status_mask |= (1 << 0)
        if st.safety.charge_enabled:
            status_mask |= (1 << 1)
        if st.safety.discharge_enabled:
            status_mask |= (1 << 2)

        return {
            0: v_centi,
            1: 0,
            2: soc,
            3: soh,
            4: temp_deci,
            5: 2840,
            6: ccl_deci,
            7: dcl_deci,
            8: 2100,
            9: min_cell_mv,
            10: max_cell_mv,
            11: delta_mv,
            12: status_mask,
            13: st.online_modules,
        }

    def _build_pylontech_broadcast(self) -> bytes:
        """Constructs proactive Pylontech broadcast frames (CID2=47 and CID2=42) to keep charger alive."""
        st = self.latest_state
        if not st or st.online_modules == 0:
            return b""
        # 1. CID2=47: Charge & Discharge Management (Absorption 28.4V, CCL 200A, DCL 400A, Cutoff 21.0V)
        cvl_mv = 28400
        ccl_da = int(st.safety.ccl_amps * 10)
        dcl_da = int(st.safety.dcl_amps * 10)
        cutoff_mv = 21000
        status_flags = 0xC0 if (st.safety.charge_enabled and st.safety.discharge_enabled) else 0x00
        info_47 = f"{cvl_mv:04X}{ccl_da:04X}{dcl_da:04X}{cutoff_mv:04X}{status_flags:02X}"
        body_47 = f"20014600{build_pylon_length(len(info_47))}{info_47}"
        f_47 = f"~{body_47}{calculate_pylon_chksum(body_47)}\r".encode("ascii")

        # 2. CID2=42: Analog Telemetry (Pack V, Current, SOC, Cell voltages, Temps)
        mod_data = next(iter(st.module_details.values()), None) if st.module_details else None
        cells = mod_data["cells"] if mod_data else [3.28] * 8
        cell_hex = "".join(f"{int(c * 1000):04X}" for c in cells[:8])
        temps = mod_data["temps_c"] if mod_data else [st.avg_temp_c, st.avg_temp_c]
        temp_hex = "".join(f"{int((t + 273.15) * 10):04X}" for t in temps[:2])
        cur_hex = "0000"
        pack_v_hex = f"{int(st.bank_voltage * 1000):04X}"
        rem_val = min(0xFFFF, max(0, int(st.total_capacity_ah * (st.soc_pct / 100.0) * 100)))
        tot_val = min(0xFFFF, max(0, int(st.total_capacity_ah * 100)))
        info_42 = f"010108{cell_hex}02{temp_hex}{cur_hex}{pack_v_hex}{rem_val:04X}{tot_val:04X}000A"
        body_42 = f"20014600{build_pylon_length(len(info_42))}{info_42}"
        f_42 = f"~{body_42}{calculate_pylon_chksum(body_42)}\r".encode("ascii")

        return f_47 + f_42

    def _run_loop(self):
        buf = bytearray()
        last_bcast = 0.0
        while self._running and self._serial:
            try:
                now = time.time()
                # Proactively broadcast telemetry every 1.0s to ensure the inverter receives continuous keep-alive
                if now - last_bcast >= 1.0:
                    bcast = self._build_pylontech_broadcast()
                    if bcast:
                        self._serial.write(bcast)
                    last_bcast = now

                data = self._serial.read(64)
                if data:
                    logger.info("RS-485 RX [%d bytes]: %s", len(data), data[:32])
                    buf.extend(data)

                # --- 1. Check for Pylontech ASCII Frame (starts with '~' / 0x7E) ---
                if 0x7E in buf:
                    idx = buf.find(0x7E)
                    if idx > 0:
                        buf = buf[idx:]  # align to start of frame

                    if 0x0D in buf:  # end of line (\r)
                        end_idx = buf.find(0x0D)
                        frame_bytes = buf[:end_idx + 1]
                        buf = buf[end_idx + 1:]
                        try:
                            frame_str = frame_bytes.decode("ascii", errors="ignore")
                            resp = self._handle_pylontech_frame(frame_str)
                            if resp:
                                self._serial.write(resp)
                        except Exception as e:
                            logger.error("Error processing Pylontech frame: %s", e)
                        continue

                # --- 2. Check for Modbus RTU Frame ---
                if len(buf) >= 8:
                    if buf[0] == self.slave_id and buf[1] in [0x03, 0x04]:
                        req_crc = buf[6] | (buf[7] << 8)
                        calc_crc = calc_crc16_modbus(buf[:6])
                        if req_crc == calc_crc:
                            start_reg = (buf[2] << 8) | buf[3]
                            qty = (buf[4] << 8) | buf[5]
                            regs = self._build_holding_registers()

                            resp_bytes = bytearray([self.slave_id, buf[1], qty * 2])
                            for r in range(start_reg, start_reg + qty):
                                val = regs.get(r, 0) & 0xFFFF
                                resp_bytes.extend(struct.pack(">H", val))

                            crc = calc_crc16_modbus(resp_bytes)
                            resp_bytes.append(crc & 0xFF)
                            resp_bytes.append((crc >> 8) & 0xFF)

                            self._serial.write(resp_bytes)
                            logger.info("Replying Modbus RTU (Start=%d Qty=%d)", start_reg, qty)
                            buf.clear()
                            continue

                    # If sync lost and not Pylontech, advance one byte
                    if not (0x7E in buf):
                        buf.pop(0)

                elif not data:
                    time.sleep(0.01)

            except Exception as e:
                logger.error("Inverter serial worker exception: %s", e)
                time.sleep(0.1)


# Maintain backward compatibility alias
ModbusRtuInverterGateway = InverterSerialGateway


class PylontechCanGateway:
    """
    Emulates standard Pylontech CAN 2.0B broadcast frames (0x359, 0x351, 0x355, 0x356)
    over CAN1 to connect directly to Inverter BMS CAN ports.
    """

    def __init__(self, interface: str = "can1"):
        self.interface = interface
        self._sock: Optional[socket.socket] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.latest_state: Optional[BankState] = None

    def start(self):
        try:
            self._sock = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
            self._sock.bind((self.interface,))
            self._running = True
            self._thread = threading.Thread(target=self._broadcast_loop, name="PylontechCanWorker", daemon=True)
            self._thread.start()
            logger.info("Pylontech CAN Gateway started on %s", self.interface)
        except Exception as e:
            logger.warning("Could not bind Inverter CAN interface %s: %s (CAN gateway disabled)", self.interface, e)

    def stop(self):
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def update_state(self, state: BankState):
        self.latest_state = state

    def _send_can(self, can_id: int, data: bytes):
        if not self._sock:
            return
        try:
            frame = struct.pack("=IB3x8s", can_id, len(data), data.ljust(8, b"\x00")[:8])
            self._sock.send(frame)
        except Exception:
            pass

    def _broadcast_loop(self):
        while self._running:
            st = self.latest_state
            if st and st.online_modules > 0:
                # 0x351: Charge & Discharge parameters
                cvl = 284  # 28.4V (0.1V)
                ccl = int(st.safety.ccl_amps * 10)
                dcl = int(st.safety.dcl_amps * 10)
                disch_cutoff = 210  # 21.0V (0.1V)
                p_351 = struct.pack("<HHHH", cvl, ccl, dcl, disch_cutoff)
                self._send_can(0x351, p_351)

                # 0x355: SOC and SOH
                soc = int(st.soc_pct)
                soh = int(st.soh_pct)
                p_355 = struct.pack("<HH", soc, soh)
                self._send_can(0x355, p_355)

                # 0x356: Voltage, Current, Temperature
                v_centi = int(st.bank_voltage * 100)
                cur_deci = 0
                temp_deci = int((st.avg_temp_c) * 10)
                p_356 = struct.pack("<hhH", v_centi, cur_deci, temp_deci)
                self._send_can(0x356, p_356)

                # 0x359: Protection & Alarms
                alarm_byte = 0
                if not st.safety.is_healthy:
                    alarm_byte |= 0x01
                p_359 = bytes([0x00, 0x00, 0x00, 0x00, alarm_byte, 0x00, 0x00, 0x00])
                self._send_can(0x359, p_359)

            time.sleep(1.0)
