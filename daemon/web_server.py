#!/usr/bin/env python3
"""
daemon/web_server.py - Zero-Dependency Embedded Web Dashboard for BMSanta
Provides an offline, dark-mode real-time telemetry dashboard on port 5000.
Optimized for multi-pack daisy chains (up to 8+ CATL LBM025100A 8S modules).
"""

import json
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional, Callable

logger = logging.getLogger("bmsanta.web")

HTML_DASHBOARD = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>BMSanta - 24V CATL Multi-Module ESS Monitor</title>
  <style>
    :root {
      --bg: #0b0f19;
      --card-bg: #151c2c;
      --card-inner: #0f1523;
      --card-border: #232f48;
      --text: #f1f5f9;
      --text-muted: #94a3b8;
      --accent: #38bdf8;
      --accent-rgb: 56, 189, 248;
      --success: #10b981;
      --warning: #f59e0b;
      --danger: #ef4444;
      --bar-bg: #1e293b;
      --bar-fill: #3b82f6;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background-color: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      padding: 16px;
      line-height: 1.4;
    }
    .container { max-width: 1240px; margin: 0 auto; }
    
    /* Header */
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding-bottom: 16px;
      margin-bottom: 20px;
      border-bottom: 1px solid var(--card-border);
      flex-wrap: wrap;
      gap: 12px;
    }
    .logo-area { display: flex; align-items: center; gap: 12px; }
    .logo-badge {
      background: linear-gradient(135deg, #2563eb, #38bdf8);
      color: white;
      font-weight: 800;
      font-size: 1.15rem;
      padding: 6px 14px;
      border-radius: 8px;
      letter-spacing: 0.5px;
      box-shadow: 0 4px 12px rgba(37, 99, 235, 0.3);
    }
    .title-area h1 { font-size: 1.25rem; font-weight: 700; color: #f8fafc; }
    .title-area p { font-size: 0.8rem; color: var(--text-muted); }
    .header-pills { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .badge {
      font-size: 0.75rem;
      font-weight: 700;
      padding: 4px 10px;
      border-radius: 20px;
      letter-spacing: 0.5px;
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }
    .badge-success { background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.4); }
    .badge-cyan { background: rgba(56, 189, 248, 0.2); color: #7dd3fc; border: 1px solid rgba(56, 189, 248, 0.4); }
    .badge-danger { background: rgba(239, 68, 68, 0.2); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.4); }
    .pulse-dot {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: #10b981;
      box-shadow: 0 0 8px #10b981;
      animation: pulse 2s infinite;
    }
    @keyframes pulse {
      0% { opacity: 1; transform: scale(1); }
      50% { opacity: 0.4; transform: scale(0.85); }
      100% { opacity: 1; transform: scale(1); }
    }

    /* Top Grid */
    .grid-top {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 14px;
      margin-bottom: 20px;
    }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 16px;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.2);
    }
    .card-title {
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.8px;
      color: var(--text-muted);
      margin-bottom: 6px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .card-val { font-size: 1.75rem; font-weight: 700; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }
    .card-sub { font-size: 0.8rem; color: var(--text-muted); margin-top: 4px; }
    .soc-bar-bg {
      width: 100%;
      height: 6px;
      background: var(--bar-bg);
      border-radius: 3px;
      overflow: hidden;
      margin-top: 8px;
    }
    .soc-bar-fill {
      height: 100%;
      border-radius: 3px;
      background: linear-gradient(90deg, #10b981, #38bdf8);
      transition: width 0.4s ease;
    }

    /* Section Title */
    .section-title {
      font-size: 1.1rem;
      font-weight: 700;
      margin: 24px 0 14px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 8px;
    }
    .section-subtitle {
      font-size: 0.8rem;
      color: var(--text-muted);
      font-weight: 400;
    }

    /* Modules Grid */
    .modules-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(480px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }
    @media (max-width: 600px) {
      .modules-grid {
        grid-template-columns: 1fr;
      }
    }

    /* Individual Battery Pack Card */
    .pack-card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      overflow: hidden;
      box-shadow: 0 4px 10px rgba(0,0,0,0.25);
      transition: transform 0.15s ease, border-color 0.15s ease;
    }
    .pack-card:hover {
      border-color: #38bdf8;
    }
    .pack-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 12px 16px;
      background: rgba(255, 255, 255, 0.03);
      border-bottom: 1px solid var(--card-border);
      flex-wrap: wrap;
      gap: 8px;
    }
    .pack-title-grp {
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .pack-pill {
      background: linear-gradient(135deg, #3b82f6, #6366f1);
      color: white;
      font-weight: 700;
      font-size: 0.85rem;
      padding: 3px 10px;
      border-radius: 6px;
      letter-spacing: 0.5px;
    }
    .node-pill {
      background: #1e293b;
      color: #94a3b8;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 0.75rem;
      padding: 3px 8px;
      border-radius: 6px;
      border: 1px solid #334155;
    }
    .pack-v-grp {
      display: flex;
      align-items: baseline;
      gap: 10px;
    }
    .pack-v-val {
      font-size: 1.35rem;
      font-weight: 700;
      color: #f8fafc;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    }
    .pack-delta-val {
      font-size: 0.85rem;
      font-weight: 600;
      color: #38bdf8;
      background: rgba(56, 189, 248, 0.1);
      padding: 2px 6px;
      border-radius: 4px;
      border: 1px solid rgba(56, 189, 248, 0.25);
    }
    .pack-meta-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 8px 16px;
      font-size: 0.8rem;
      color: var(--text-muted);
      background: rgba(0, 0, 0, 0.15);
      border-bottom: 1px solid rgba(255, 255, 255, 0.04);
      flex-wrap: wrap;
      gap: 8px;
    }
    .meta-item { display: flex; align-items: center; gap: 4px; }
    .meta-item b { color: #cbd5e1; }

    /* 8 Cells Grid inside Pack Card */
    .pack-cells-grid {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 8px;
      padding: 12px 14px;
    }
    @media (max-width: 480px) {
      .pack-cells-grid {
        grid-template-columns: repeat(2, 1fr);
      }
    }
    .cell-box {
      background: var(--card-inner);
      border: 1px solid #1e293b;
      border-radius: 8px;
      padding: 8px 10px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      position: relative;
    }
    .cell-top {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 4px;
    }
    .cell-id {
      font-size: 0.7rem;
      font-weight: 700;
      color: var(--text-muted);
      text-transform: uppercase;
    }
    .cell-v {
      font-size: 1.15rem;
      font-weight: 700;
      color: #f1f5f9;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    }
    .cell-bar-bg {
      width: 100%;
      height: 5px;
      background: var(--bar-bg);
      border-radius: 3px;
      overflow: hidden;
      margin-top: 6px;
    }
    .cell-bar-fill {
      height: 100%;
      border-radius: 3px;
      background: linear-gradient(90deg, #3b82f6, #38bdf8);
      transition: width 0.3s ease;
    }

    /* Badges for cell highlights */
    .tag-min {
      font-size: 0.6rem;
      padding: 1px 4px;
      border-radius: 3px;
      background: rgba(56, 189, 248, 0.15);
      color: #7dd3fc;
      border: 1px solid rgba(56, 189, 248, 0.4);
      font-weight: 700;
    }
    .tag-max {
      font-size: 0.6rem;
      padding: 1px 4px;
      border-radius: 3px;
      background: rgba(245, 158, 11, 0.15);
      color: #fde68a;
      border: 1px solid rgba(245, 158, 11, 0.4);
      font-weight: 700;
    }
    .tag-global-min {
      font-size: 0.6rem;
      padding: 1px 4px;
      border-radius: 3px;
      background: rgba(56, 189, 248, 0.3);
      color: #38bdf8;
      border: 1px solid #38bdf8;
      font-weight: 800;
      box-shadow: 0 0 6px rgba(56, 189, 248, 0.4);
    }
    .tag-global-max {
      font-size: 0.6rem;
      padding: 1px 4px;
      border-radius: 3px;
      background: rgba(244, 63, 94, 0.3);
      color: #fda4af;
      border: 1px solid #f43f5e;
      font-weight: 800;
      box-shadow: 0 0 6px rgba(244, 63, 94, 0.4);
    }

    .pack-footer {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 8px 16px;
      font-size: 0.75rem;
      color: var(--text-muted);
      background: rgba(0, 0, 0, 0.1);
      border-top: 1px solid rgba(255, 255, 255, 0.03);
    }

    /* Standby Slots Card */
    .standby-card {
      border: 1px dashed #334155;
      border-radius: 12px;
      padding: 16px;
      background: rgba(15, 21, 35, 0.4);
      display: flex;
      flex-direction: column;
      justify-content: center;
      align-items: center;
      text-align: center;
      color: var(--text-muted);
      gap: 6px;
    }
    .standby-title {
      font-size: 0.9rem;
      font-weight: 600;
      color: #cbd5e1;
    }
    .standby-desc {
      font-size: 0.75rem;
      max-width: 380px;
      line-height: 1.35;
    }

    /* Inverter & Gateway Grid */
    .gateway-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 14px;
      margin-top: 12px;
    }
    .gw-item { display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid rgba(255,255,255,0.05); }
    .gw-item:last-child { border-bottom: none; }
    .gw-key { color: var(--text-muted); font-size: 0.85rem; }
    .gw-val { font-weight: 600; font-size: 0.85rem; }

    footer {
      text-align: center;
      margin-top: 32px;
      font-size: 0.75rem;
      color: var(--text-muted);
    }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="logo-area">
        <div class="logo-badge">BMSanta</div>
        <div class="title-area">
          <h1>CATL 24V Energy Storage System</h1>
          <p>Huasi BME 8S1P • Modbus RTU / Pylontech Gateway • 8-Module Daisy-Chain Bus</p>
        </div>
      </div>
      <div class="header-pills">
        <div id="module-count-badge" class="badge badge-cyan">2 of 8 Modules Active</div>
        <div id="health-badge" class="badge badge-success"><span class="pulse-dot"></span> ONLINE (HEALTHY)</div>
      </div>
    </header>

    <!-- Top Bank Summary Cards -->
    <div class="grid-top">
      <div class="card">
        <div class="card-title">
          <span>Bank Voltage</span>
          <span style="font-size:0.7rem; color:#38bdf8;">8S PARALLEL</span>
        </div>
        <div class="card-val" id="bank-v">--.--- V</div>
        <div class="card-sub" id="capacity-sub">-- Ah (--.-- kWh)</div>
      </div>

      <div class="card">
        <div class="card-title">
          <span>Bank State of Charge</span>
          <span id="soh-badge" style="font-size:0.7rem; color:#10b981;">100% SOH</span>
        </div>
        <div class="card-val" id="soc-val">--%</div>
        <div class="soc-bar-bg">
          <div class="soc-bar-fill" id="soc-bar" style="width: 0%;"></div>
        </div>
        <div class="card-sub" style="margin-top:6px;">LFP Resting OCV Curve</div>
      </div>

      <div class="card">
        <div class="card-title">
          <span>Global Cell Imbalance (&Delta;V)</span>
          <span id="delta-status" style="font-size:0.7rem; color:#34d399;">BALANCED</span>
        </div>
        <div class="card-val" id="delta-val">--.- mV</div>
        <div class="card-sub" id="min-max-sub">Min: -- V | Max: -- V</div>
      </div>

      <div class="card">
        <div class="card-title">
          <span>Bank Thermistors</span>
          <span style="font-size:0.7rem; color:#94a3b8;">NTC PROBES</span>
        </div>
        <div class="card-val" id="temp-val">--.- °C</div>
        <div class="card-sub" id="temp-f-sub">--.- °F</div>
      </div>
    </div>

    <!-- Individual Battery Modules Section -->
    <div class="section-title">
      <div>
        <span>Individual Battery Modules</span>
        <span class="section-subtitle" id="active-packs-sub"> (Showing online packs)</span>
      </div>
      <span style="font-size:0.8rem; color:#94a3b8;" id="daisy-chain-label">Daisy Chain Bus: can1 @ 250 kbps</span>
    </div>

    <div class="modules-grid" id="modules-container">
      <!-- Generated dynamically per module -->
    </div>

    <!-- Standby Slot Summary -->
    <div id="standby-container" style="margin-bottom: 24px;"></div>

    <!-- Inverter Closed-Loop & Gateway Section -->
    <div class="section-title">
      <span>PowMr Inverter Closed-Loop Gateway</span>
      <span class="section-subtitle">Pylontech RS-485 / Modbus RTU Dynamic Limits</span>
    </div>

    <div class="gateway-grid">
      <div class="card">
        <div class="card-title">Dynamic Closed-Loop Limits</div>
        <div class="gw-item">
          <span class="gw-key">Charge Current Limit (CCL)</span>
          <span class="gw-val" id="ccl-val" style="color:#38bdf8;">-- A</span>
        </div>
        <div class="gw-item">
          <span class="gw-key">Discharge Current Limit (DCL)</span>
          <span class="gw-val" id="dcl-val" style="color:#34d399;">-- A</span>
        </div>
        <div class="gw-item">
          <span class="gw-key">Charge Switch</span>
          <span class="gw-val" id="charge-state">ENABLED</span>
        </div>
        <div class="gw-item">
          <span class="gw-key">Discharge Switch</span>
          <span class="gw-val" id="discharge-state">ENABLED</span>
        </div>
      </div>

      <div class="card">
        <div class="card-title">Inverter Interface Status</div>
        <div class="gw-item">
          <span class="gw-key">PowMr RS-485 (/dev/ttyUSB0)</span>
          <span class="gw-val" style="color:#34d399;">Active (9600 8N1 PYL)</span>
        </div>
        <div class="gw-item">
          <span class="gw-key">PowMr Screen Settings</span>
          <span class="gw-val" style="color:#38bdf8;">Prog 05: LIB / Prog 43: PYL</span>
        </div>
        <div class="gw-item">
          <span class="gw-key">Battery CAN Bus</span>
          <span class="gw-val" style="color:#34d399;">can1 (250 kbps, 8-Pack Auto)</span>
        </div>
        <div class="gw-item">
          <span class="gw-key">Active Alarms</span>
          <span class="gw-val" id="alarms-val" style="color:#34d399;">None</span>
        </div>
      </div>
    </div>

    <footer>
      BMSanta Multi-Module ESS Controller • Polling at 1 Hz • Last Response: <span id="last-update">--:--:--</span>
    </footer>
  </div>

  <script>
    async function updateDashboard() {
      try {
        const resp = await fetch('/api/status');
        if (!resp.ok) return;
        const data = await resp.json();

        // 1. Bank Overview
        document.getElementById('bank-v').textContent = (data.bank_voltage || 0).toFixed(3) + ' V';
        const soc = data.soc_pct || 0;
        document.getElementById('soc-val').textContent = soc + '%';
        const socBar = document.getElementById('soc-bar');
        socBar.style.width = Math.min(100, Math.max(0, soc)) + '%';
        if (soc > 40) socBar.style.background = 'linear-gradient(90deg, #10b981, #38bdf8)';
        else if (soc > 15) socBar.style.background = 'linear-gradient(90deg, #f59e0b, #38bdf8)';
        else socBar.style.background = 'linear-gradient(90deg, #ef4444, #f59e0b)';

        const delta = data.delta_cell_mv || 0;
        document.getElementById('delta-val').textContent = delta.toFixed(1) + ' mV';
        const deltaStatus = document.getElementById('delta-status');
        if (delta <= 25) {
          deltaStatus.textContent = 'EXCELLENT';
          deltaStatus.style.color = '#34d399';
        } else if (delta <= 60) {
          deltaStatus.textContent = 'GOOD';
          deltaStatus.style.color = '#7dd3fc';
        } else if (delta <= 120) {
          deltaStatus.textContent = 'DERATED';
          deltaStatus.style.color = '#fbbf24';
        } else {
          deltaStatus.textContent = 'HIGH DELTA';
          deltaStatus.style.color = '#f87171';
        }

        document.getElementById('temp-val').textContent = (data.avg_temp_c || 0).toFixed(1) + ' °C';
        const tempF = ((data.avg_temp_c || 22.5) * 9 / 5 + 32).toFixed(1);
        document.getElementById('temp-f-sub').textContent = `${tempF} °F • Range: ${(data.min_temp_c||0).toFixed(1)}° to ${(data.max_temp_c||0).toFixed(1)}°C`;

        const onlineCount = data.online_modules || 1;
        document.getElementById('capacity-sub').textContent = `${(data.total_capacity_ah || 100).toFixed(1)} Ah (${(data.total_energy_kwh || 2.56).toFixed(2)} kWh Usable)`;
        document.getElementById('min-max-sub').textContent = `Min: ${(data.min_cell_v||0).toFixed(3)}V (${data.min_cell_location||''}) | Max: ${(data.max_cell_v||0).toFixed(3)}V`;

        // 2. Health & Count Badges
        const healthBadge = document.getElementById('health-badge');
        if (data.safety && data.safety.is_healthy) {
          healthBadge.innerHTML = '<span class="pulse-dot"></span> ONLINE (HEALTHY)';
          healthBadge.className = 'badge badge-success';
        } else {
          healthBadge.innerHTML = '<span class="pulse-dot" style="background:#ef4444; box-shadow:0 0 8px #ef4444;"></span> ALARM ACTIVE';
          healthBadge.className = 'badge badge-danger';
        }
        document.getElementById('module-count-badge').textContent = `${onlineCount} of 8 Modules Active`;
        document.getElementById('active-packs-sub').textContent = ` (${onlineCount} active pack${onlineCount > 1 ? 's' : ''} aggregated)`;

        // 3. Dynamic Safety Limits
        if (data.safety) {
          document.getElementById('ccl-val').textContent = (data.safety.ccl_amps || 0).toFixed(0) + ' A';
          document.getElementById('dcl-val').textContent = (data.safety.dcl_amps || 0).toFixed(0) + ' A';
          document.getElementById('charge-state').textContent = data.safety.charge_enabled ? 'ALLOWED' : 'BLOCKED';
          document.getElementById('charge-state').style.color = data.safety.charge_enabled ? '#34d399' : '#f87171';
          document.getElementById('discharge-state').textContent = data.safety.discharge_enabled ? 'ALLOWED' : 'BLOCKED';
          document.getElementById('discharge-state').style.color = data.safety.discharge_enabled ? '#34d399' : '#f87171';
          const alarms = data.safety.alarms || [];
          document.getElementById('alarms-val').textContent = alarms.length > 0 ? alarms.join(', ') : 'None (Normal)';
          document.getElementById('alarms-val').style.color = alarms.length > 0 ? '#f87171' : '#34d399';
        }

        // 4. Render Individual Battery Modules
        const modulesContainer = document.getElementById('modules-container');
        let packsHtml = '';
        const modules = data.modules || {};
        const sortedNodeKeys = Object.keys(modules).sort((a, b) => parseInt(a) - parseInt(b));

        const globalMinV = data.min_cell_v || 0;
        const globalMaxV = data.max_cell_v || 0;

        let renderedPackNums = [];

        sortedNodeKeys.forEach((nidStr, idx) => {
          const m = modules[nidStr];
          const nid = parseInt(nidStr);
          const packNum = m.pack_num || (idx + 1);
          renderedPackNums.push(packNum);
          const nodeHex = m.node_hex || ('0x' + nid.toString(16).toUpperCase());
          const packV = (m.pack_v || 0).toFixed(3);
          const packDelta = (m.delta_mv || 0).toFixed(1);
          const fw = m.firmware || '1.32.0';
          const tempsC = m.temps_c || [23.0, 23.0];
          const t1C = (tempsC[0] || 0).toFixed(1);
          const t2C = (tempsC[1] || 0).toFixed(1);
          const avgC = ((tempsC[0] + tempsC[1]) / 2.0).toFixed(1);
          const avgF = (((tempsC[0] + tempsC[1]) / 2.0) * 9 / 5 + 32).toFixed(1);

          const cells = m.cells || [];
          const packMinV = m.min_cell || 0;
          const packMaxV = m.max_cell || 0;

          // Build 8 cells HTML
          let cellsHtml = '';
          cells.forEach((v, cIdx) => {
            const cellNum = cIdx + 1;
            let tagHtml = '';

            // Check if Global Bank Min/Max or Pack Min/Max
            const isGlobalMin = Math.abs(v - globalMinV) < 0.0005;
            const isGlobalMax = Math.abs(v - globalMaxV) < 0.0005;
            const isPackMin = Math.abs(v - packMinV) < 0.0005;
            const isPackMax = Math.abs(v - packMaxV) < 0.0005;

            if (isGlobalMin) {
              tagHtml = '<span class="tag-global-min">★ BANK MIN</span>';
            } else if (isGlobalMax) {
              tagHtml = '<span class="tag-global-max">★ BANK MAX</span>';
            } else if (isPackMin) {
              tagHtml = '<span class="tag-min">MIN</span>';
            } else if (isPackMax) {
              tagHtml = '<span class="tag-max">MAX</span>';
            }

            // Cell bar percent (range 3.00V to 3.45V)
            const pct = Math.max(0, Math.min(100, ((v - 3.00) / (3.45 - 3.00)) * 100));

            cellsHtml += `
              <div class="cell-box">
                <div class="cell-top">
                  <span class="cell-id">Cell ${cellNum}</span>
                  ${tagHtml}
                </div>
                <div class="cell-v">${v.toFixed(3)} V</div>
                <div class="cell-bar-bg">
                  <div class="cell-bar-fill" style="width: ${pct}%;"></div>
                </div>
              </div>
            `;
          });

          const hwBadge = (m.hw_node_hex && m.hw_node_hex !== nodeHex) ? `<span class="node-pill" style="color:#7dd3fc; border-color:#0284c7;">CAN ${m.hw_node_hex}</span>` : '';

          packsHtml += `
            <div class="pack-card">
              <div class="pack-header">
                <div class="pack-title-grp">
                  <span class="pack-pill">BATTERY #${packNum}</span>
                  <span class="node-pill">${nodeHex}</span>
                  ${hwBadge}
                  <span class="badge badge-success" style="padding:2px 8px; font-size:0.7rem;">● ONLINE</span>
                </div>
                <div class="pack-v-grp">
                  <span class="pack-v-val">${packV} V</span>
                  <span class="pack-delta-val">&Delta; ${packDelta} mV</span>
                </div>
              </div>

              <div class="pack-meta-row">
                <div class="meta-item">
                  <span>🌡️ Temps:</span>
                  <b>${t1C}°C / ${t2C}°C</b>
                  <span style="color:#64748b;">(${avgF}°F)</span>
                </div>
                <div class="meta-item">
                  <span>📊 Spread:</span>
                  <b>${packMinV.toFixed(3)}V – ${packMaxV.toFixed(3)}V</b>
                </div>
                <div class="meta-item">
                  <span>📦 FW:</span>
                  <b>v${fw}</b>
                </div>
              </div>

              <div class="pack-cells-grid">
                ${cellsHtml}
              </div>

              <div class="pack-footer">
                <span>Capacity: 100.0 Ah (2.56 kWh)</span>
                <span>Active 8S1P LiFePO4 • Status: Normal</span>
              </div>
            </div>
          `;
        });

        modulesContainer.innerHTML = packsHtml;

        // 5. Standby expansion slots for up to 8 packs
        const standbyContainer = document.getElementById('standby-container');
        if (onlineCount < 8) {
          const remaining = 8 - onlineCount;
          standbyContainer.innerHTML = `
            <div class="standby-card">
              <div class="standby-title">⚡ Daisy-Chain Expansion Slots Available (${remaining} of 8 remaining)</div>
              <div class="standby-desc">
                BMSanta is configured for up to 8 daisy-chained CATL modules on single-bus CAN1.
                As you daisy-chain Modules #3 through #8 onto the wiring harness, they will be auto-detected,
                monitored, and dynamically aggregated into the bank live.
              </div>
            </div>
          `;
        } else {
          standbyContainer.innerHTML = '';
        }

        document.getElementById('last-update').textContent = new Date().toLocaleTimeString();
      } catch (e) {
        console.error("Dashboard update error:", e);
      }
    }

    updateDashboard();
    setInterval(updateDashboard, 1000);
  </script>
</body>
</html>
"""


class DashboardHandler(BaseHTTPRequestHandler):
    get_state_fn: Optional[Callable] = None

    def log_message(self, format, *args):
        # Suppress routine GET logging to prevent spam
        pass

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            content = HTML_DASHBOARD.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        elif self.path == "/api/status":
            state_data = {}
            if DashboardHandler.get_state_fn:
                state_data = DashboardHandler.get_state_fn()
            else:
                for p in ["/run/bmsanta_status.json", "/tmp/bmsanta_status.json"]:
                    try:
                        with open(p, "r") as f:
                            state_data = json.load(f)
                        break
                    except Exception:
                        pass

            content = json.dumps(state_data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        elif self.path == "/api/health":
            content = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        else:
            self.send_response(404)
            self.end_headers()


class EmbeddedWebServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 5000, get_state_fn: Optional[Callable] = None):
        self.host = host
        self.port = port
        DashboardHandler.get_state_fn = get_state_fn
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self):
        try:
            self._server = HTTPServer((self.host, self.port), DashboardHandler)
            self._thread = threading.Thread(target=self._server.serve_forever, name="WebDashboardServer", daemon=True)
            self._thread.start()
            logger.info("Embedded Web Dashboard running at http://%s:%d", self.host, self.port)
        except Exception as e:
            logger.error("Failed to start Web Dashboard on port %d: %s", self.port, e)

    def stop(self):
        if self._server:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            self._server = None
