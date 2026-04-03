#!/usr/bin/env python3
"""
FSD Toggle Server — runs on comma 3
Serves a mobile web UI to switch between FSD mode and openpilot mode.

Usage:
  python3 /data/fsd_toggle_server.py

Then open http://<comma-ip>:8088 on your phone (same WiFi).
"""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

FSD_SCRIPT = "/data/tesla_fsd_comma3_hw3.py"
PORT = 8088
# Keep in sync with CAN_STATS_PATH in tesla_fsd_comma3_hw3.py
CAN_STATS_PATH = "/tmp/fsd_can_stats.json"
CAN_STATS_STALE_SEC = 10.0

# Same layout as openpilot launch_chffrplus.sh: PYTHONPATH = openpilot root (symlinked as /data/pythonpath).
_VENV_PYTHON = "/usr/local/venv/bin/python3"


def _comma_subprocess_env():
    env = os.environ.copy()
    for root in ("/data/pythonpath", "/data/openpilot"):
        if os.path.isdir(root):
            openpilot_root = os.path.realpath(root)
            prev = env.get("PYTHONPATH", "").strip()
            env["PYTHONPATH"] = openpilot_root if not prev else f"{openpilot_root}:{prev}"
            break
    return env

# ── State ─────────────────────────────────────────────────────────────────────

state = {
    "mode": "comma",          # "comma" | "fsd"
    "fsd_pid": None,
    "switched_at": None,
    "log": [],
}


def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    state["log"].insert(0, line)
    state["log"] = state["log"][:50]


def _read_can_stats_payload():
    """
    Returns (stats_dict_or_none, stale_bool) for /status when in FSD mode.
    Stale if file missing, corrupt, script reports running=false, or updated_at too old.
    """
    try:
        with open(CAN_STATS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None, True

    if not isinstance(data, dict):
        return None, True

    stale = False
    if not data.get("running", True):
        stale = True
    updated = data.get("updated_at")
    try:
        if updated is not None and (time.time() - float(updated)) > CAN_STATS_STALE_SEC:
            stale = True
    except (TypeError, ValueError):
        stale = True

    return data, stale


# ── Process control ────────────────────────────────────────────────────────────

def openpilot_running():
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "openpilot"],
            capture_output=True, text=True
        )
        return result.stdout.strip() == "active"
    except Exception:
        return False


def _read_cmdline(pid: str) -> list[str]:
    try:
        with open(os.path.join("/proc", pid, "cmdline"), "rb") as f:
            return [x.decode("utf-8", "replace") for x in f.read().split(b"\0") if x]
    except OSError:
        return []


def _panda_stack_pids() -> list[str]:
    """Native ./pandad and Python selfdrive.pandad.pandad (respawns native if only native is killed)."""
    out: list[str] = []
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        argv = _read_cmdline(name)
        if not argv:
            continue
        base = os.path.basename(argv[0]).lower()
        joined = " ".join(argv).lower()
        if base == "pandad":
            out.append(name)
            continue
        if base.startswith("python") and "selfdrive.pandad.pandad" in joined:
            out.append(name)
            continue
        if base == "boardd" or "system/boardd" in joined.replace("\\", "/").lower():
            out.append(name)
    return sorted(out, key=int, reverse=True)


def _sigkill_pids(pids: list[str]) -> list[str]:
    killed: list[str] = []
    for pid in pids:
        try:
            os.kill(int(pid), signal.SIGKILL)
            killed.append(pid)
        except (ProcessLookupError, ValueError):
            pass
        except PermissionError:
            pass
    return killed


def _kill_stray_panda_daemons():
    """
    systemctl stop openpilot often does nothing when openpilot.service is absent;
    native pandad still holds USB. Kill by PID (works as user comma) then sudo fallback.
    """
    for _round in range(3):
        pids = _panda_stack_pids()
        if not pids:
            break
        got = _sigkill_pids(pids)
        if got:
            log(f"Killed panda stack PID(s): {', '.join(got)}")
        time.sleep(0.4)

    for name in ("pandad", "boardd"):
        r = subprocess.run(
            ["sudo", "-n", "killall", "-KILL", name],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0 and r.stderr.strip():
            log(f"sudo killall {name}: {r.stderr.strip()}")
    r = subprocess.run(
        ["sudo", "-n", "pkill", "-KILL", "-f", "selfdrive.pandad.pandad"],
        capture_output=True,
        text=True,
    )
    if r.returncode not in (0, 1) and r.stderr.strip():
        log(f"sudo pkill pandad wrapper: {r.stderr.strip()}")
    time.sleep(1)


def stop_openpilot():
    log("Stopping openpilot...")
    subprocess.run(["sudo", "systemctl", "stop", "openpilot"], capture_output=True)
    # Wait until inactive so pandad/boardd release the panda USB interface.
    for _ in range(30):
        if not openpilot_running():
            break
        time.sleep(0.5)
    time.sleep(2)
    _kill_stray_panda_daemons()
    log("openpilot stopped.")


def start_openpilot():
    log("Starting openpilot...")
    subprocess.run(["sudo", "systemctl", "start", "openpilot"], capture_output=True)
    time.sleep(2)
    log("openpilot started.")


def start_fsd():
    log("Starting FSD script...")
    _kill_stray_panda_daemons()
    py = _VENV_PYTHON if os.path.isfile(_VENV_PYTHON) else sys.executable
    proc = subprocess.Popen(
        [py, FSD_SCRIPT],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=_comma_subprocess_env(),
    )
    state["fsd_pid"] = proc.pid
    log(f"FSD script running (PID {proc.pid})")

    # stream FSD output to log in background
    def tail():
        for line in proc.stdout:
            log(f"[FSD] {line.strip()}")
        state["fsd_pid"] = None
        log("FSD script exited.")

    threading.Thread(target=tail, daemon=True).start()
    return proc


def stop_fsd():
    pid = state.get("fsd_pid")
    if pid:
        log(f"Stopping FSD script (PID {pid})...")
        try:
            subprocess.run(["kill", str(pid)], capture_output=True)
        except Exception as e:
            log(f"Kill error: {e}")
        state["fsd_pid"] = None
        time.sleep(1)
        log("FSD script stopped.")
    else:
        log("FSD script was not running.")


_switch_lock = threading.Lock()
_fsd_proc = None


def switch_to_fsd():
    global _fsd_proc
    with _switch_lock:
        if state["mode"] == "fsd":
            log("Already in FSD mode.")
            return
        stop_openpilot()
        _fsd_proc = start_fsd()
        state["mode"] = "fsd"
        state["switched_at"] = time.time()
        log("✅ Switched to FSD mode.")


def switch_to_comma():
    global _fsd_proc
    with _switch_lock:
        if state["mode"] == "comma":
            log("Already in Comma mode.")
            return
        stop_fsd()
        start_openpilot()
        state["mode"] = "comma"
        state["switched_at"] = time.time()
        log("✅ Switched to Comma / openpilot mode.")


# ── HTML ───────────────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0"/>
<title>comma 3 · Mode Switch</title>
<style>
  :root {
    --bg: #0d0d0d;
    --card: #1a1a1a;
    --border: #2a2a2a;
    --fsd: #e31937;
    --comma: #ffffff;
    --text: #f0f0f0;
    --muted: #666;
    --green: #22c55e;
    --radius: 20px;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    min-height: 100dvh;
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 32px 20px 20px;
    gap: 24px;
  }
  h1 {
    font-size: 1.1rem;
    font-weight: 600;
    letter-spacing: .08em;
    text-transform: uppercase;
    color: var(--muted);
  }

  /* Status pill */
  #status-pill {
    display: flex;
    align-items: center;
    gap: 10px;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 100px;
    padding: 10px 22px;
    font-size: 1rem;
    font-weight: 600;
    letter-spacing: .04em;
    transition: all .4s;
  }
  .dot {
    width: 10px; height: 10px;
    border-radius: 50%;
    background: var(--green);
    animation: pulse 2s infinite;
  }
  @keyframes pulse {
    0%,100% { opacity: 1; }
    50% { opacity: .3; }
  }

  /* Big toggle button */
  #toggle-btn {
    width: 100%;
    max-width: 340px;
    padding: 28px 0;
    border-radius: var(--radius);
    border: none;
    font-size: 1.4rem;
    font-weight: 700;
    letter-spacing: .04em;
    cursor: pointer;
    transition: all .25s;
    -webkit-tap-highlight-color: transparent;
  }
  #toggle-btn:active { transform: scale(.96); }
  #toggle-btn.fsd-btn {
    background: var(--fsd);
    color: #fff;
    box-shadow: 0 0 40px rgba(227,25,55,.35);
  }
  #toggle-btn.comma-btn {
    background: var(--comma);
    color: #000;
    box-shadow: 0 0 40px rgba(255,255,255,.15);
  }
  #toggle-btn:disabled {
    opacity: .5;
    cursor: not-allowed;
    transform: none;
  }

  /* Info cards */
  .cards {
    display: flex;
    gap: 12px;
    width: 100%;
    max-width: 340px;
  }
  .card {
    flex: 1;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 14px;
    text-align: center;
  }
  .card .label { font-size: .7rem; color: var(--muted); text-transform: uppercase; letter-spacing: .06em; margin-bottom: 6px; }
  .card .value { font-size: 1.1rem; font-weight: 700; }

  /* Log */
  #log-box {
    width: 100%;
    max-width: 340px;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 14px;
    max-height: 200px;
    overflow-y: auto;
    font-family: 'SF Mono', monospace;
    font-size: .72rem;
    color: var(--muted);
    line-height: 1.6;
  }
  .log-line { border-bottom: 1px solid var(--border); padding: 3px 0; }
  .log-line:last-child { border: none; }

  /* Spinner */
  .spinner {
    display: none;
    width: 22px; height: 22px;
    border: 3px solid rgba(255,255,255,.2);
    border-top-color: #fff;
    border-radius: 50%;
    animation: spin .7s linear infinite;
    margin: 0 auto;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* CAN debug */
  #can-debug {
    width: 100%;
    max-width: 340px;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 14px;
  }
  #can-debug .section-title {
    font-size: .7rem;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: .06em;
    margin-bottom: 10px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }
  #can-debug .stale-badge {
    font-size: .65rem;
    color: #f59e0b;
    font-weight: 600;
    text-transform: none;
    letter-spacing: 0;
  }
  .can-cards {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 10px;
  }
  .can-cards .mini {
    flex: 1 1 30%;
    min-width: 88px;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 8px 6px;
    text-align: center;
  }
  .can-cards .mini .label { font-size: .6rem; color: var(--muted); margin-bottom: 4px; }
  .can-cards .mini .value { font-size: .95rem; font-weight: 700; }
  .can-line {
    font-size: .68rem;
    color: var(--muted);
    line-height: 1.5;
    margin-bottom: 6px;
  }
  .can-line strong { color: var(--text); font-weight: 600; }
  .can-hex {
    font-family: 'SF Mono', ui-monospace, monospace;
    font-size: .62rem;
    word-break: break-all;
    color: #a3a3a3;
    margin-top: 2px;
    max-height: 72px;
    overflow-y: auto;
  }
  .can-placeholder { font-size: .72rem; color: var(--muted); line-height: 1.5; }
</style>
</head>
<body>
<h1>comma 3 · mode switch</h1>

<div id="status-pill">
  <div class="dot"></div>
  <span id="status-text">Loading…</span>
</div>

<button id="toggle-btn" class="comma-btn" onclick="doSwitch()" disabled>
  <span id="btn-label">–</span>
  <div class="spinner" id="spinner"></div>
</button>

<div class="cards">
  <div class="card">
    <div class="label">Active since</div>
    <div class="value" id="uptime">–</div>
  </div>
  <div class="card">
    <div class="label">openpilot</div>
    <div class="value" id="op-status">–</div>
  </div>
</div>

<div id="can-debug">
  <div class="section-title">
    <span>CAN debug</span>
    <span class="stale-badge" id="can-stale" style="display:none">Stale</span>
  </div>
  <div id="can-body" class="can-placeholder">Run FSD mode to see CAN stats.</div>
</div>

<div id="log-box"><div class="log-line" style="color:#444">Waiting for log…</div></div>

<script>
let currentMode = null;
let switchedAt = null;
let busy = false;

async function poll() {
  try {
    const r = await fetch('/status');
    const d = await r.json();
    update(d);
  } catch(e) {}
}

function update(d) {
  currentMode = d.mode;
  switchedAt = d.switched_at;

  const pill = document.getElementById('status-pill');
  const text = document.getElementById('status-text');
  const btn = document.getElementById('toggle-btn');
  const lbl = document.getElementById('btn-label');

  if (d.mode === 'fsd') {
    text.textContent = 'FSD MODE ACTIVE';
    pill.style.borderColor = '#e31937';
    btn.className = 'comma-btn';
    lbl.textContent = '⬅ Switch to Comma';
  } else {
    text.textContent = 'COMMA / OPENPILOT';
    pill.style.borderColor = '#555';
    btn.className = 'fsd-btn';
    lbl.textContent = 'Switch to FSD ➡';
  }

  if (!busy) btn.disabled = false;

  // uptime
  if (d.switched_at) {
    const secs = Math.floor(Date.now()/1000 - d.switched_at);
    const m = Math.floor(secs/60), s = secs%60;
    document.getElementById('uptime').textContent = m > 0 ? m+'m '+s+'s' : s+'s';
  } else {
    document.getElementById('uptime').textContent = '–';
  }

  document.getElementById('op-status').textContent =
    d.openpilot_running ? '🟢 Running' : '🔴 Stopped';

  // log
  const box = document.getElementById('log-box');
  box.innerHTML = (d.log || []).map(l =>
    '<div class="log-line">'+escHtml(l)+'</div>'
  ).join('') || '<div class="log-line" style="color:#444">No log yet.</div>';

  updateCanDebug(d);
}

const profileNames = {0: 'Chill', 1: 'Normal', 2: 'Hurry'};

function updateCanDebug(d) {
  const body = document.getElementById('can-body');
  const staleEl = document.getElementById('can-stale');
  staleEl.style.display = 'none';

  if (d.mode !== 'fsd') {
    body.className = 'can-placeholder';
    body.textContent = 'Run FSD mode to see CAN stats.';
    return;
  }

  const s = d.can_stats;
  if (!s) {
    body.className = 'can-placeholder';
    body.textContent = 'Waiting for FSD script stats (/tmp/fsd_can_stats.json not ready yet)…';
    staleEl.style.display = d.can_stats_stale ? 'inline' : 'none';
    staleEl.textContent = 'Stale';
    return;
  }

  if (d.can_stats_stale) {
    staleEl.style.display = 'inline';
    staleEl.textContent = s.running === false ? 'Stopped' : 'Stale';
  }

  const mux = s.mods_by_mux || {};
  const p = profileNames[s.speed_profile] != null ? profileNames[s.speed_profile] : String(s.speed_profile);

  body.className = '';
  body.innerHTML =
    '<div class="can-cards">' +
      mini('Captured', s.frames_total) +
      mini('Modified', s.frames_modified) +
      mini('ID 1016', s.frames_id_1016) +
      mini('ID 1021', s.frames_id_1021) +
      mini('Other IDs', s.frames_other) +
    '</div>' +
    '<div class="can-line"><strong>State</strong> · FSD ' + (s.fsd_active ? 'active' : 'waiting') +
    ' · Nag ' + (s.nag_suppressed ? 'suppressed' : 'on') +
    ' · Profile ' + escHtml(String(p)) + ' · Offset ' + escHtml(String(s.speed_offset)) + '</div>' +
    '<div class="can-line"><strong>Mods by mux</strong> · 0:' + escHtml(String(mux[0] ?? '–')) +
    ' · 1:' + escHtml(String(mux[1] ?? '–')) + ' · 2:' + escHtml(String(mux[2] ?? '–')) + '</div>' +
    '<div class="can-line"><strong>Bus / flags</strong> · can_bus ' + escHtml(String(s.can_bus)) +
    ' · transmit ' + escHtml(String(s.transmit)) + ' · dummy ' + escHtml(String(s.dummy_mode)) +
    ' · pid ' + escHtml(String(s.pid)) + '</div>' +
    '<div class="can-line"><strong>Last 1016</strong><div class="can-hex">' + escHtml(s.last_1016_hex || '–') + '</div></div>' +
    '<div class="can-line"><strong>Last 1021 in</strong><div class="can-hex">' + escHtml(s.last_1021_in_hex || '–') + '</div></div>' +
    '<div class="can-line"><strong>Last 1021 out</strong><div class="can-hex">' + escHtml(s.last_1021_out_hex || '–') + '</div></div>';
}

function mini(label, val) {
  const v = val != null ? val : '–';
  return '<div class="mini"><div class="label">' + escHtml(label) + '</div><div class="value">' + escHtml(String(v)) + '</div></div>';
}

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

async function doSwitch() {
  if (busy) return;
  const target = currentMode === 'fsd' ? 'comma' : 'fsd';
  const ok = confirm(`Switch to ${target.toUpperCase()} mode?`);
  if (!ok) return;

  busy = true;
  const btn = document.getElementById('toggle-btn');
  const lbl = document.getElementById('btn-label');
  const spin = document.getElementById('spinner');
  btn.disabled = true;
  lbl.style.display = 'none';
  spin.style.display = 'block';

  try {
    await fetch('/switch', { method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({target})
    });
  } catch(e) {}

  // poll until mode changes
  let tries = 0;
  const wait = setInterval(async () => {
    tries++;
    await poll();
    if (currentMode === target || tries > 20) {
      clearInterval(wait);
      busy = false;
      spin.style.display = 'none';
      lbl.style.display = 'block';
      btn.disabled = false;
    }
  }, 1000);
}

// poll every 2s
setInterval(poll, 2000);
poll();
</script>
</body>
</html>
"""


# ── HTTP Handler ───────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        pass  # suppress default access log

    def send_json(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif self.path == "/status":
            payload = {
                "mode": state["mode"],
                "fsd_pid": state["fsd_pid"],
                "switched_at": state["switched_at"],
                "openpilot_running": openpilot_running(),
                "log": state["log"][:20],
            }
            if state["mode"] == "fsd":
                stats, stale = _read_can_stats_payload()
                payload["can_stats"] = stats
                payload["can_stats_stale"] = stale
            else:
                payload["can_stats"] = None
                payload["can_stats_stale"] = False
            self.send_json(payload)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/switch":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            target = body.get("target")
            if target == "fsd":
                threading.Thread(target=switch_to_fsd, daemon=True).start()
            elif target == "comma":
                threading.Thread(target=switch_to_comma, daemon=True).start()
            self.send_json({"ok": True})
        else:
            self.send_response(404)
            self.end_headers()


# ── Main ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not os.path.exists(FSD_SCRIPT):
        print(f"⚠️  FSD script not found at {FSD_SCRIPT}")
        print(f"   Run: curl -o {FSD_SCRIPT} https://raw.githubusercontent.com/mich41v4294/tesla-fsd-comma4/main/tesla_fsd_comma3_hw3.py")

    log(f"FSD Toggle Server starting on port {PORT}...")
    log(f"Open http://<comma-ip>:{PORT} on your phone")

    server = HTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("Server stopped.")
