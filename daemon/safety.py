#!/usr/bin/env python3
"""
daemon/safety.py - Battery Protection Matrix & Dynamic Current Derating
Enforces cell-level and pack-level safety rules for 24V CATL 8S LiFePO4 batteries.
"""

import logging
from dataclasses import dataclass, field
from typing import List, Tuple

logger = logging.getLogger("bmsanta.safety")


@dataclass
class SafetyThresholds:
    # Cell Voltage Protection (Volts)
    cell_ovp_cutoff: float = 3.65      # Absolute maximum cell voltage (stop charging)
    cell_ovp_derate: float = 3.48      # Begin tapering charge current
    cell_uvp_cutoff: float = 2.50      # Absolute minimum cell voltage (stop discharging)
    cell_uvp_derate: float = 2.85      # Begin tapering discharge current
    cell_recovery_margin: float = 0.05 # Recovery hysteresis (V)

    # Pack Voltage Limits (Volts)
    pack_ovp: float = 29.2
    pack_uvp: float = 20.0

    # Temperature Protection (Celsius)
    charge_max_temp: float = 50.0      # Stop charging
    charge_derate_temp: float = 45.0   # Derate charging
    charge_min_temp: float = 0.0       # Stop charging below freezing (LFP lithium plating risk)
    charge_cold_derate_temp: float = 5.0

    discharge_max_temp: float = 60.0   # Stop discharging
    discharge_min_temp: float = -20.0  # Stop discharging

    # Cell Balance Imbalance (mV)
    delta_warning_mv: float = 60.0
    delta_derate_mv: float = 120.0
    delta_critical_mv: float = 250.0

    # Base Module Current Limits (Amps per 100Ah 8S pack)
    max_charge_amps_per_pack: float = 50.0     # 0.5C nominal
    max_discharge_amps_per_pack: float = 100.0 # 1.0C continuous


@dataclass
class SafetyStatus:
    is_healthy: bool = True
    charge_enabled: bool = True
    discharge_enabled: bool = True
    ccl_amps: float = 50.0             # Calculated Charge Current Limit
    dcl_amps: float = 100.0            # Calculated Discharge Current Limit
    warnings: List[str] = field(default_factory=list)
    alarms: List[str] = field(default_factory=list)


class SafetyManager:
    """Evaluates telemetry against safety thresholds and computes dynamic CCL / DCL."""

    def __init__(self, thresholds: SafetyThresholds = None):
        self.cfg = thresholds or SafetyThresholds()

    def evaluate(self,
                 pack_count: int,
                 global_min_cell: float,
                 global_max_cell: float,
                 global_delta_mv: float,
                 global_min_temp: float,
                 global_max_temp: float,
                 global_pack_v: float) -> SafetyStatus:
        """
        Evaluates battery bank state and generates limits and alarms.
        """
        status = SafetyStatus()
        warnings = []
        alarms = []

        if pack_count == 0:
            status.is_healthy = False
            status.charge_enabled = False
            status.discharge_enabled = False
            status.ccl_amps = 0.0
            status.dcl_amps = 0.0
            status.alarms = ["NO_MODULES_ONLINE"]
            return status

        # Base nominal current based on number of parallel packs
        base_ccl = self.cfg.max_charge_amps_per_pack * pack_count
        base_dcl = self.cfg.max_discharge_amps_per_pack * pack_count

        ccl_factor = 1.0
        dcl_factor = 1.0

        # --- 1. Cell Over-Voltage Evaluation ---
        if global_max_cell >= self.cfg.cell_ovp_cutoff:
            alarms.append(f"CELL_OVP_CUTOFF ({global_max_cell:.3f}V >= {self.cfg.cell_ovp_cutoff}V)")
            ccl_factor = 0.0
            status.charge_enabled = False
        elif global_max_cell >= self.cfg.cell_ovp_derate:
            # Linear derating between derate threshold and cutoff
            derate_span = self.cfg.cell_ovp_cutoff - self.cfg.cell_ovp_derate
            excess = global_max_cell - self.cfg.cell_ovp_derate
            ccl_factor = max(0.05, 1.0 - (excess / derate_span))
            warnings.append(f"CELL_OVP_DERATE ({global_max_cell:.3f}V: CCL reduced to {int(ccl_factor*100)}%)")

        # --- 2. Cell Under-Voltage Evaluation ---
        if global_min_cell <= self.cfg.cell_uvp_cutoff:
            alarms.append(f"CELL_UVP_CUTOFF ({global_min_cell:.3f}V <= {self.cfg.cell_uvp_cutoff}V)")
            dcl_factor = 0.0
            status.discharge_enabled = False
        elif global_min_cell <= self.cfg.cell_uvp_derate:
            derate_span = self.cfg.cell_uvp_derate - self.cfg.cell_uvp_cutoff
            drop = self.cfg.cell_uvp_derate - global_min_cell
            dcl_factor = max(0.05, 1.0 - (drop / derate_span))
            warnings.append(f"CELL_UVP_DERATE ({global_min_cell:.3f}V: DCL reduced to {int(dcl_factor*100)}%)")

        # --- 3. Temperature Protection ---
        # A. Cold Temperature Charging (LFP lithium plating hazard)
        if global_min_temp <= self.cfg.charge_min_temp:
            alarms.append(f"CHARGE_FREEZING_CUTOFF ({global_min_temp:.1f}C <= {self.cfg.charge_min_temp}C)")
            ccl_factor = 0.0
            status.charge_enabled = False
        elif global_min_temp <= self.cfg.charge_cold_derate_temp:
            ccl_factor = min(ccl_factor, 0.20)  # Max 0.2C at low temps
            warnings.append(f"COLD_TEMP_CHARGE_DERATE ({global_min_temp:.1f}C)")

        # B. High Temperature Charging
        if global_max_temp >= self.cfg.charge_max_temp:
            alarms.append(f"CHARGE_OVERTEMP_CUTOFF ({global_max_temp:.1f}C >= {self.cfg.charge_max_temp}C)")
            ccl_factor = 0.0
            status.charge_enabled = False
        elif global_max_temp >= self.cfg.charge_derate_temp:
            ccl_factor = min(ccl_factor, 0.50)
            warnings.append(f"HIGH_TEMP_CHARGE_DERATE ({global_max_temp:.1f}C)")

        # C. Discharge Temperature
        if global_max_temp >= self.cfg.discharge_max_temp:
            alarms.append(f"DISCHARGE_OVERTEMP_CUTOFF ({global_max_temp:.1f}C)")
            dcl_factor = 0.0
            status.discharge_enabled = False
        if global_min_temp <= self.cfg.discharge_min_temp:
            alarms.append(f"DISCHARGE_UNDERTEMP_CUTOFF ({global_min_temp:.1f}C)")
            dcl_factor = 0.0
            status.discharge_enabled = False

        # --- 4. Cell Imbalance (Delta V) ---
        if global_delta_mv >= self.cfg.delta_critical_mv:
            alarms.append(f"CRITICAL_CELL_IMBALANCE ({global_delta_mv:.1f}mV >= {self.cfg.delta_critical_mv}mV)")
            ccl_factor = min(ccl_factor, 0.20)
        elif global_delta_mv >= self.cfg.delta_derate_mv:
            ccl_factor = min(ccl_factor, 0.50)
            warnings.append(f"HIGH_CELL_IMBALANCE ({global_delta_mv:.1f}mV)")
        elif global_delta_mv >= self.cfg.delta_warning_mv:
            warnings.append(f"MODERATE_CELL_IMBALANCE ({global_delta_mv:.1f}mV)")

        # --- 5. Overall Pack Voltage Sanity ---
        if global_pack_v >= self.cfg.pack_ovp:
            alarms.append(f"PACK_OVP ({global_pack_v:.2f}V >= {self.cfg.pack_ovp}V)")
            ccl_factor = 0.0
            status.charge_enabled = False
        elif global_pack_v <= self.cfg.pack_uvp:
            alarms.append(f"PACK_UVP ({global_pack_v:.2f}V <= {self.cfg.pack_uvp}V)")
            dcl_factor = 0.0
            status.discharge_enabled = False

        status.ccl_amps = round(base_ccl * ccl_factor, 1)
        status.dcl_amps = round(base_dcl * dcl_factor, 1)
        status.warnings = warnings
        status.alarms = alarms
        status.is_healthy = (len(alarms) == 0)

        return status
