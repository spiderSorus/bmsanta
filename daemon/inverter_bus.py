#!/usr/bin/env python3
"""
daemon/inverter_bus.py - Inverter Communication Gateway (RS-485 Modbus RTU & CAN)
Exposes aggregated battery bank state to solar hybrid inverters (Growatt, Sol-Ark, Deye, Victron).
Supports:
1. RS-485 Modbus RTU Server over /dev/ttyUSB0 (RJ45 cable).
2. Pylontech CAN emulation over secondary CAN bus (can1).
"""

import time
import socket
import struct
import threading
import logging
from typing import Optional
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


class ModbusRtuInverterGateway:
    """
    Lightweight, robust Modbus RTU slave server for RS-485 serial ports (/dev/ttyUSB0).
    Answers Read Holding Registers (Function Code 0x03).
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
            self._thread = threading.Thread(target=self._run_loop, name="ModbusRtuWorker", daemon=True)
            self._thread.start()
            logger.info("Modbus RTU Inverter Gateway started on %s @ %d baud (Slave ID: %d)",
                        self.port, self.baudrate, self.slave_id)
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

    def _build_holding_registers(self) -> dict:
        """
        Maps BankState to standard solar inverter battery registers:
        0x0000: Bank Voltage (0.01 V, e.g. 2588 = 25.88V)
        0x0001: Bank Current (0.1 A, signed)
        0x0002: SOC % (0 - 100)
        0x0003: SOH % (0 - 100)
        0x0004: Average Temp (0.1 °C, e.g. 225 = 22.5°C)
        0x0005: Charge Voltage Limit CVL (0.01 V, e.g. 2840 = 28.40V)
        0x0006: Charge Current Limit CCL (0.1 A, e.g. 500 = 50.0A)
        0x0007: Discharge Current Limit DCL (0.1 A, e.g. 1000 = 100.0A)
        0x0008: Discharge Cutoff Voltage (0.01 V, e.g. 2100 = 21.00V)
        0x0009: Min Cell Voltage (mV, e.g. 3236)
        0x000A: Max Cell Voltage (mV, e.g. 3238)
        0x000B: Cell Delta (mV, e.g. 2)
        0x000C: Status Bitmask (Bit 0: Healthy, Bit 1: Charge OK, Bit 2: Discharge OK)
        0x000D: Online Modules Count
        """
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
            1: 0,           # Current (0 if no shunt installed yet)
            2: soc,
            3: soh,
            4: temp_deci,
            5: 2840,        # CVL = 28.40 V (3.55V * 8)
            6: ccl_deci,
            7: dcl_deci,
            8: 2100,        # UVP Cutoff = 21.00 V
            9: min_cell_mv,
            10: max_cell_mv,
            11: delta_mv,
            12: status_mask,
            13: st.online_modules,
        }

    def _run_loop(self):
        buf = bytearray()
        while self._running and self._serial:
            try:
                data = self._serial.read(64)
                if data:
                    buf.extend(data)

                # Standard Modbus Read Holding Registers frame is 8 bytes:
                # [SlaveID, Func(0x03), StartAddr_Hi, StartAddr_Lo, Qty_Hi, Qty_Lo, CRC_Lo, CRC_Hi]
                if len(buf) >= 8:
                    if buf[0] == self.slave_id and buf[1] == 0x03:
                        req_crc = buf[6] | (buf[7] << 8)
                        calc_crc = calc_crc16_modbus(buf[:6])
                        if req_crc == calc_crc:
                            start_reg = (buf[2] << 8) | buf[3]
                            qty = (buf[4] << 8) | buf[5]
                            regs = self._build_holding_registers()

                            resp_bytes = bytearray([self.slave_id, 0x03, qty * 2])
                            for r in range(start_reg, start_reg + qty):
                                val = regs.get(r, 0) & 0xFFFF
                                resp_bytes.extend(struct.pack(">H", val))

                            crc = calc_crc16_modbus(resp_bytes)
                            resp_bytes.append(crc & 0xFF)
                            resp_bytes.append((crc >> 8) & 0xFF)

                            self._serial.write(resp_bytes)
                            buf.clear()
                            continue

                    # If sync lost, pop one byte and re-scan
                    buf.pop(0)
                elif not data:
                    time.sleep(0.01)
            except Exception as e:
                logger.error("Modbus worker exception: %s", e)
                time.sleep(0.1)


class PylontechCanGateway:
    """
    Emulates standard Pylontech CAN 2.0B broadcast frames (0x359, 0x351, 0x355, 0x356)
    over CAN1 (or configurable CAN interface) to connect directly to Inverter BMS CAN ports.
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
                # [CVL_Lo, CVL_Hi, CCL_Lo, CCL_Hi, DCL_Lo, DCL_Hi, DischCutoff_Lo, DischCutoff_Hi]
                cvl = 284  # 28.4V (0.1V)
                ccl = int(st.safety.ccl_amps * 10)
                dcl = int(st.safety.dcl_amps * 10)
                disch_cutoff = 210  # 21.0V (0.1V)
                p_351 = struct.pack("<HHHH", cvl, ccl, dcl, disch_cutoff)
                self._send_can(0x351, p_351)

                # 0x355: SOC and SOH
                # [SOC_Lo, SOC_Hi, SOH_Lo, SOH_Hi]
                soc = int(st.soc_pct)
                soh = int(st.soh_pct)
                p_355 = struct.pack("<HH", soc, soh)
                self._send_can(0x355, p_355)

                # 0x356: Voltage, Current, Temperature
                # [V_Lo, V_Hi, I_Lo, I_Hi, T_Lo, T_Hi]
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
