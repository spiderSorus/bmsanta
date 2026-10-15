#!/usr/bin/env python3
"""
daemon/aggregator.py - Multi-Pack Aggregator for Parallel CATL LBM025100A Modules
Combines individual 8S module telemetry into a unified battery bank representation.
"""

import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional
from daemon.battery_bus import ModuleTelemetry
from daemon.safety import SafetyManager, SafetyStatus, SafetyThresholds

logger = logging.getLogger("bmsanta.aggregator")

# Approximate LiFePO4 8S resting OCV curve for SOC estimation
# (Voltage, SOC %)
LFP_8S_OCV_POINTS = [
    (27.20, 100.0),
    (26.80, 95.0),
    (26.60, 90.0),
    (26.50, 80.0),
    (26.40, 70.0),
    (26.30, 60.0),
    (26.20, 50.0),
    (26.00, 30.0),
    (25.80, 20.0),
    (25.40, 10.0),
    (24.50, 5.0),
    (23.00, 0.0),
]


def estimate_soc_lfp(pack_v: float) -> float:
    """Estimates SOC% using linear interpolation across LFP OCV curve."""
    if pack_v >= LFP_8S_OCV_POINTS[0][0]:
        return 100.0
    if pack_v <= LFP_8S_OCV_POINTS[-1][0]:
        return 0.0

    for i in range(len(LFP_8S_OCV_POINTS) - 1):
        v_high, soc_high = LFP_8S_OCV_POINTS[i]
        v_low, soc_low = LFP_8S_OCV_POINTS[i + 1]
        if v_low <= pack_v <= v_high:
            ratio = (pack_v - v_low) / (v_high - v_low)
            return round(soc_low + ratio * (soc_high - soc_low), 1)
    return 50.0


@dataclass
class BankState:
    timestamp: float = 0.0
    online_modules: int = 0
    total_capacity_ah: float = 0.0
    total_energy_kwh: float = 0.0
    bank_voltage: float = 0.0
    soc_pct: float = 0.0
    soh_pct: float = 100.0

    min_cell_v: float = 0.0
    min_cell_module: int = 0
    min_cell_index: int = 0

    max_cell_v: float = 0.0
    max_cell_module: int = 0
    max_cell_index: int = 0

    delta_cell_mv: float = 0.0

    min_temp_c: float = 0.0
    max_temp_c: float = 0.0
    avg_temp_c: float = 0.0

    safety: SafetyStatus = field(default_factory=SafetyStatus)
    module_details: Dict[int, dict] = field(default_factory=dict)


class Aggregator:
    """Aggregates multi-pack telemetry from BatteryBus and computes bank state."""

    def __init__(self, safety_thresholds: Optional[SafetyThresholds] = None):
        self.safety_mgr = SafetyManager(safety_thresholds or SafetyThresholds())
        self.state = BankState()

    def update(self, modules: Dict[int, ModuleTelemetry]) -> BankState:
        """
        Processes current telemetry from all known modules and updates bank state.
        """
        now = time.time()
        online_mods = [m for m in modules.values() if m.is_online]
        mod_count = len(online_mods)

        if mod_count == 0:
            self.state = BankState(
                timestamp=now,
                online_modules=0,
                safety=self.safety_mgr.evaluate(0, 0, 0, 0, 0, 0, 0)
            )
            return self.state

        # Find global extremes across all modules
        all_cells: List[Tuple[float, int, int]] = []  # (v, mod_id, cell_idx)
        all_temps: List[float] = []
        pack_voltages: List[float] = []

        mod_details = {}

        for m in online_mods:
            pack_voltages.append(m.pack_v)
            all_temps.extend(m.temps)
            for idx, c in enumerate(m.cells, 1):
                if c > 1.0:
                    all_cells.append((c, m.node_id, idx))

            mod_details[m.node_id] = {
                "pack_v": round(m.pack_v, 3),
                "cells": [round(c, 3) for c in m.cells],
                "temps_c": [round(t, 1) for t in m.temps],
                "min_cell": round(m.min_cell, 3),
                "max_cell": round(m.max_cell, 3),
                "delta_mv": round(m.delta_mv, 1),
                "firmware": m.firmware,
                "last_seen": round(m.last_seen, 2),
            }

        avg_pack_v = sum(pack_voltages) / len(pack_voltages)
        total_ah = mod_count * 100.0
        total_kwh = (total_ah * 25.6) / 1000.0

        if all_cells:
            all_cells.sort(key=lambda x: x[0])
            min_cell_val, min_mod, min_idx = all_cells[0]
            max_cell_val, max_mod, max_idx = all_cells[-1]
            delta_mv = (max_cell_val - min_cell_val) * 1000.0
        else:
            min_cell_val, min_mod, min_idx = 0.0, 0, 0
            max_cell_val, max_mod, max_idx = 0.0, 0, 0
            delta_mv = 0.0

        min_temp = min(all_temps) if all_temps else 25.0
        max_temp = max(all_temps) if all_temps else 25.0
        avg_temp = sum(all_temps) / len(all_temps) if all_temps else 25.0

        soc = estimate_soc_lfp(avg_pack_v)

        # Run safety evaluation
        safety_status = self.safety_mgr.evaluate(
            pack_count=mod_count,
            global_min_cell=min_cell_val,
            global_max_cell=max_cell_val,
            global_delta_mv=delta_mv,
            global_min_temp=min_temp,
            global_max_temp=max_temp,
            global_pack_v=avg_pack_v
        )

        self.state = BankState(
            timestamp=now,
            online_modules=mod_count,
            total_capacity_ah=total_ah,
            total_energy_kwh=total_kwh,
            bank_voltage=round(avg_pack_v, 3),
            soc_pct=soc,
            soh_pct=100.0,
            min_cell_v=round(min_cell_val, 3),
            min_cell_module=min_mod,
            min_cell_index=min_idx,
            max_cell_v=round(max_cell_val, 3),
            max_cell_module=max_mod,
            max_cell_index=max_idx,
            delta_cell_mv=round(delta_mv, 1),
            min_temp_c=round(min_temp, 1),
            max_temp_c=round(max_temp, 1),
            avg_temp_c=round(avg_temp, 1),
            safety=safety_status,
            module_details=mod_details
        )

        return self.state
