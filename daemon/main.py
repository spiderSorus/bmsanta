#!/usr/bin/env python3
"""
daemon/main.py - BMSanta Master Service
Coordinates BatteryBus polling, Multi-Pack Aggregator, Inverter Gateways,
and Embedded Web Dashboard. Exports real-time bank status to /run/bmsanta_status.json.
"""

import sys
import os
import json
import time
import signal
import logging
from dataclasses import asdict
from daemon.battery_bus import BatteryBus
from daemon.aggregator import Aggregator
from daemon.inverter_bus import ModbusRtuInverterGateway, PylontechCanGateway
from daemon.web_server import EmbeddedWebServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("bmsanta.main")

STATUS_FILE = "/run/bmsanta_status.json"
FALLBACK_STATUS_FILE = "/tmp/bmsanta_status.json"


class BMSantaService:
    def __init__(self,
                 can_interface: str = "can0",
                 rs485_port: str = "/dev/ttyUSB0",
                 inverter_can_interface: str = "can1",
                 web_port: int = 5000):
        self.can_interface = can_interface
        self.rs485_port = rs485_port
        self.inverter_can_interface = inverter_can_interface
        self.web_port = web_port

        self.battery_bus = BatteryBus(interface=self.can_interface)
        self.aggregator = Aggregator()
        self.modbus_gw = ModbusRtuInverterGateway(port=self.rs485_port)
        self.can_gw = PylontechCanGateway(interface=self.inverter_can_interface)
        self.web_server = EmbeddedWebServer(port=self.web_port, get_state_fn=self.get_latest_status_dict)

        self._latest_status_dict = {}
        self._running = False

    def get_latest_status_dict(self):
        return self._latest_status_dict

    def start(self):
        logger.info("Starting BMSanta Controller Service...")

        # 1. Connect Battery CAN Bus
        if not self.battery_bus.connect():
            logger.error("Failed to connect to Battery CAN bus %s. Exiting.", self.can_interface)
            sys.exit(1)

        # 2. Start Inverter Gateways (Non-blocking threads)
        self.modbus_gw.start()
        self.can_gw.start()

        # 3. Start Embedded Web Dashboard
        self.web_server.start()

        self._running = True
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        logger.info("Initial module discovery...")
        self.battery_bus.discover_modules()

        self._run_loop()

    def _handle_signal(self, signum, frame):
        logger.info("Received termination signal (%d). Shutting down...", signum)
        self._running = False

    def _format_status_dict(self, state):
        return {
            "timestamp": state.timestamp,
            "online_modules": state.online_modules,
            "total_capacity_ah": state.total_capacity_ah,
            "total_energy_kwh": state.total_energy_kwh,
            "bank_voltage": state.bank_voltage,
            "soc_pct": state.soc_pct,
            "min_cell_v": state.min_cell_v,
            "min_cell_location": f"Mod 0x{state.min_cell_module:02X} Cell {state.min_cell_index}",
            "max_cell_v": state.max_cell_v,
            "max_cell_location": f"Mod 0x{state.max_cell_module:02X} Cell {state.max_cell_index}",
            "delta_cell_mv": state.delta_cell_mv,
            "min_temp_c": state.min_temp_c,
            "max_temp_c": state.max_temp_c,
            "avg_temp_c": state.avg_temp_c,
            "safety": {
                "is_healthy": state.safety.is_healthy,
                "charge_enabled": state.safety.charge_enabled,
                "discharge_enabled": state.safety.discharge_enabled,
                "ccl_amps": state.safety.ccl_amps,
                "dcl_amps": state.safety.dcl_amps,
                "warnings": state.safety.warnings,
                "alarms": state.safety.alarms,
            },
            "modules": state.module_details,
        }

    def _write_status_json(self, state):
        status_dict = self._format_status_dict(state)
        self._latest_status_dict = status_dict

        content = json.dumps(status_dict, indent=2)
        for target in [STATUS_FILE, FALLBACK_STATUS_FILE]:
            try:
                temp_file = target + ".tmp"
                with open(temp_file, "w") as f:
                    f.write(content)
                os.replace(temp_file, target)
                break
            except Exception:
                pass

    def _run_loop(self):
        logger.info("BMSanta main control loop active (1 Hz poll rate).")
        while self._running:
            t0 = time.time()
            try:
                # 1. Poll all active battery packs
                modules = self.battery_bus.poll_all_active()

                # 2. Aggregate telemetry & evaluate safety
                state = self.aggregator.update(self.battery_bus.modules)

                # 3. Update inverter gateways
                self.modbus_gw.update_state(state)
                self.can_gw.update_state(state)

                # 4. Export JSON status & update web server cache
                self._write_status_json(state)

                # Periodic heartbeat log
                if state.online_modules > 0:
                    logger.info("Bank: %.3f V | SOC: %d%% | Delta: %.1f mV | CCL: %.1f A | DCL: %.1f A | %d Pack(s) Online",
                                state.bank_voltage, state.soc_pct, state.delta_cell_mv,
                                state.safety.ccl_amps, state.safety.dcl_amps, state.online_modules)
                else:
                    logger.warning("No battery modules responding on %s", self.can_interface)

            except Exception as e:
                logger.error("Exception in control loop: %s", e, exc_info=True)

            elapsed = time.time() - t0
            sleep_time = max(0.1, 1.0 - elapsed)
            time.sleep(sleep_time)

        # Cleanup on exit
        logger.info("Stopping gateways and closing bus connections...")
        self.web_server.stop()
        self.modbus_gw.stop()
        self.can_gw.stop()
        self.battery_bus.close()
        logger.info("BMSanta shutdown complete.")


def main():
    can_iface = os.environ.get("BMSANTA_CAN_IFACE", "can1")
    rs485_dev = os.environ.get("BMSANTA_RS485_DEV", "/dev/ttyUSB0")
    inverter_can = os.environ.get("BMSANTA_INVERTER_CAN", "can0")
    web_port = int(os.environ.get("BMSANTA_WEB_PORT", "5000"))

    service = BMSantaService(
        can_interface=can_iface,
        rs485_port=rs485_dev,
        inverter_can_interface=inverter_can,
        web_port=web_port
    )
    service.start()


if __name__ == "__main__":
    main()
