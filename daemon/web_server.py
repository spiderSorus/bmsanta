#!/usr/bin/env python3
"""
daemon/web_server.py - Zero-Dependency Embedded Web Dashboard for BMSanta
Provides an offline, dark-mode real-time telemetry dashboard on port 5000.
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
  <title>BMSanta - 24V CATL LFP Monitor</title>
  <style>
    :root {
      --bg: #0b0f19;
      --card-bg: #151c2c;
      --card-border: #232f48;
      --text: #f1f5f9;
      --text-muted: #94a3b8;
      --accent: #38bdf8;
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
    .container { max-width: 960px; margin: 0 auto; }
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding-bottom: 16px;
      margin-bottom: 20px;
      border-bottom: 1px solid var(--card-border);
    }
    .logo-area { display: flex; align-items: center; gap: 12px; }
    .logo-badge {
      background: linear-gradient(135deg, #2563eb, #38bdf8);
      color: white;
      font-weight: 800;
      font-size: 1.1rem;
      padding: 6px 12px;
      border-radius: 8px;
      letter-spacing: 0.5px;
    }
    .title-area h1 { font-size: 1.3rem; font-weight: 700; }
    .title-area p { font-size: 0.85rem; color: var(--text-muted); }
    .badge {
      font-size: 0.75rem;
      font-weight: 700;
      padding: 4px 10px;
      border-radius: 20px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    .badge-success { background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.4); }
    .badge-warning { background: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.4); }
    .badge-danger { background: rgba(239, 68, 68, 0.2); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.4); }

    .grid-top {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
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
    }
    .card-val { font-size: 1.7rem; font-weight: 700; }
    .card-sub { font-size: 0.8rem; color: var(--text-muted); margin-top: 4px; }

    .section-title {
      font-size: 1.05rem;
      font-weight: 600;
      margin: 20px 0 12px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .cells-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
      gap: 12px;
    }
    .cell-card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 10px;
      padding: 12px;
      position: relative;
    }
    .cell-header {
      display: flex;
      justify-content: space-between;
      font-size: 0.85rem;
      font-weight: 600;
      margin-bottom: 8px;
    }
    .cell-voltage { font-size: 1.25rem; font-weight: 700; color: #e2e8f0; }
    .cell-tag {
      font-size: 0.65rem;
      padding: 2px 6px;
      border-radius: 4px;
      font-weight: 700;
    }
    .tag-min { background: rgba(56, 189, 248, 0.25); color: #7dd3fc; border: 1px solid rgba(56, 189, 248, 0.5); }
    .tag-max { background: rgba(244, 63, 94, 0.25); color: #fda4af; border: 1px solid rgba(244, 63, 94, 0.5); }
    .bar-container {
      width: 100%;
      height: 8px;
      background: var(--bar-bg);
      border-radius: 4px;
      overflow: hidden;
      margin-top: 8px;
    }
    .bar-inner {
      height: 100%;
      background: linear-gradient(90deg, #3b82f6, #38bdf8);
      border-radius: 4px;
      transition: width 0.3s ease;
    }

    .gateway-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 14px;
      margin-top: 14px;
    }
    .gw-item { display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid rgba(255,255,255,0.05); }
    .gw-item:last-child { border-bottom: none; }
    .gw-key { color: var(--text-muted); font-size: 0.85rem; }
    .gw-val { font-weight: 600; font-size: 0.85rem; }

    footer {
      text-align: center;
      margin-top: 28px;
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
          <h1>CATL 24V ESS Gateway</h1>
          <p id="fw-info">Huasi BME 8S1P • Modbus RTU / Pylontech CAN</p>
        </div>
      </div>
      <div id="health-badge" class="badge badge-success">ONLINE</div>
    </header>

    <div class="grid-top">
      <div class="card">
        <div class="card-title">Bank Voltage</div>
        <div class="card-val" id="bank-v">--.-- V</div>
        <div class="card-sub" id="capacity-sub">100 Ah (2.56 kWh)</div>
      </div>
      <div class="card">
        <div class="card-title">Estimated SOC</div>
        <div class="card-val" id="soc-val">--%</div>
        <div class="card-sub">LFP Rest OCV Estimation</div>
      </div>
      <div class="card">
        <div class="card-title">Cell Imbalance (&Delta;V)</div>
        <div class="card-val" id="delta-val">-- mV</div>
        <div class="card-sub" id="min-max-sub">Min: -- V | Max: -- V</div>
      </div>
      <div class="card">
        <div class="card-title">Module Thermistors</div>
        <div class="card-val" id="temp-val">--.- °C</div>
        <div class="card-sub" id="temp-f-sub">--.- °F</div>
      </div>
    </div>

    <div class="section-title">
      <span>8S Individual Cell Voltages</span>
      <span style="font-size:0.8rem; color:var(--text-muted);" id="active-packs">1 Pack Active (0xAA)</span>
    </div>
    <div class="cells-grid" id="cells-container">
      <!-- Generated dynamically -->
    </div>

    <div class="section-title">
      <span>Gateway & Inverter Closed-Loop Limits</span>
    </div>
    <div class="gateway-grid">
      <div class="card">
        <div class="card-title">Dynamic Current Limits</div>
        <div class="gw-item">
          <span class="gw-key">Max Charge Current (CCL)</span>
          <span class="gw-val" id="ccl-val" style="color:#38bdf8;">-- A</span>
        </div>
        <div class="gw-item">
          <span class="gw-key">Max Discharge Current (DCL)</span>
          <span class="gw-val" id="dcl-val" style="color:#34d399;">-- A</span>
        </div>
        <div class="gw-item">
          <span class="gw-key">Charge State</span>
          <span class="gw-val" id="charge-state">ENABLED</span>
        </div>
        <div class="gw-item">
          <span class="gw-key">Discharge State</span>
          <span class="gw-val" id="discharge-state">ENABLED</span>
        </div>
      </div>
      <div class="card">
        <div class="card-title">Inverter Interfaces</div>
        <div class="gw-item">
          <span class="gw-key">Modbus RTU (/dev/ttyUSB0)</span>
          <span class="gw-val" style="color:#34d399;">Active (9600 8N1)</span>
        </div>
        <div class="gw-item">
          <span class="gw-key">Pylontech CAN (can1)</span>
          <span class="gw-val" style="color:#34d399;">Active (1 Hz Broadcast)</span>
        </div>
        <div class="gw-item">
          <span class="gw-key">Battery CAN (can0)</span>
          <span class="gw-val" style="color:#34d399;">Active (250 kbps)</span>
        </div>
        <div class="gw-item">
          <span class="gw-key">Active Alarms</span>
          <span class="gw-val" id="alarms-val" style="color:#34d399;">None</span>
        </div>
      </div>
    </div>

    <footer>
      BMSanta Controller • Last Poll: <span id="last-update">--:--:--</span>
    </footer>
  </div>

  <script>
    async function updateDashboard() {
      try {
        const resp = await fetch('/api/status');
        if (!resp.ok) return;
        const data = await resp.json();

        // 1. Top Cards
        document.getElementById('bank-v').textContent = (data.bank_voltage || 0).toFixed(3) + ' V';
        document.getElementById('soc-val').textContent = (data.soc_pct || 0) + '%';
        document.getElementById('delta-val').textContent = (data.delta_cell_mv || 0).toFixed(1) + ' mV';
        document.getElementById('temp-val').textContent = (data.avg_temp_c || 0).toFixed(1) + ' °C';
        const tempF = ((data.avg_temp_c || 22.5) * 9 / 5 + 32).toFixed(1);
        document.getElementById('temp-f-sub').textContent = `${tempF} °F (T1/T2 Range: ${(data.min_temp_c||0).toFixed(1)}° - ${(data.max_temp_c||0).toFixed(1)}°)`;

        document.getElementById('capacity-sub').textContent = `${data.total_capacity_ah || 100} Ah (${(data.total_energy_kwh || 2.56).toFixed(2)} kWh)`;
        document.getElementById('min-max-sub').textContent = `Min: ${(data.min_cell_v||0).toFixed(3)}V (${data.min_cell_location||''}) | Max: ${(data.max_cell_v||0).toFixed(3)}V`;

        // 2. Health Badge
        const badge = document.getElementById('health-badge');
        if (data.safety && data.safety.is_healthy) {
          badge.textContent = 'ONLINE (HEALTHY)';
          badge.className = 'badge badge-success';
        } else {
          badge.textContent = 'ALARM ACTIVE';
          badge.className = 'badge badge-danger';
        }

        // 3. Current Limits & Inverter
        if (data.safety) {
          document.getElementById('ccl-val').textContent = (data.safety.ccl_amps || 0).toFixed(0) + ' A';
          document.getElementById('dcl-val').textContent = (data.safety.dcl_amps || 0).toFixed(0) + ' A';
          document.getElementById('charge-state').textContent = data.safety.charge_enabled ? 'ENABLED' : 'BLOCKED';
          document.getElementById('charge-state').style.color = data.safety.charge_enabled ? '#34d399' : '#f87171';
          document.getElementById('discharge-state').textContent = data.safety.discharge_enabled ? 'ENABLED' : 'BLOCKED';
          document.getElementById('discharge-state').style.color = data.safety.discharge_enabled ? '#34d399' : '#f87171';
          const alarms = data.safety.alarms || [];
          document.getElementById('alarms-val').textContent = alarms.length > 0 ? alarms.join(', ') : 'None';
          document.getElementById('alarms-val').style.color = alarms.length > 0 ? '#f87171' : '#34d399';
        }

        // 4. Render Cells
        const cellsContainer = document.getElementById('cells-container');
        let cellsHtml = '';
        const modules = data.modules || {};
        for (const [nid, m] of Object.entries(modules)) {
          const cells = m.cells || [];
          const minV = m.min_cell || 0;
          const maxV = m.max_cell || 0;

          cells.forEach((v, idx) => {
            const cellNum = idx + 1;
            let tag = '';
            if (v === minV && cells.length > 1) tag = '<span class="cell-tag tag-min">MIN</span>';
            else if (v === maxV && cells.length > 1) tag = '<span class="cell-tag tag-max">MAX</span>';

            // Percent bar calculation: range 3.00V to 3.45V
            const pct = Math.max(0, Math.min(100, ((v - 3.00) / (3.45 - 3.00)) * 100));

            cellsHtml += `
              <div class="cell-card">
                <div class="cell-header">
                  <span>Cell ${cellNum}</span>
                  ${tag}
                </div>
                <div class="cell-voltage">${v.toFixed(3)} V</div>
                <div class="bar-container">
                  <div class="bar-inner" style="width: ${pct}%;"></div>
                </div>
              </div>
            `;
          });
        }
        cellsContainer.innerHTML = cellsHtml;

        document.getElementById('last-update').textContent = new Date().toLocaleTimeString();
      } catch (e) {
        console.error("Dashboard fetch error:", e);
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
        # Suppress routine GET logging to prevent log spam
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
                # Fallback to reading file
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
