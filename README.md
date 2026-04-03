# Tesla FSD CAN Mod — Comma 3 / HW3 Edition

Python port of [superpositiontime/tesla-fsd-comma4](https://github.com/superpositiontime/tesla-fsd-comma4),
adapted for the **comma 3 with integrated black panda** and **Tesla FSD Hardware 3** (V12/V13 firmware).

HW3 CAN protocol reference: [Tesla-OPEN-CAN-MOD/tesla-fsd-can-mod](https://gitlab.com/Tesla-OPEN-CAN-MOD/tesla-fsd-can-mod)

---

## Hardware Requirements

| Component | Specification |
|-----------|--------------|
| comma device | **comma 3** (integrated black panda) |
| Tesla autopilot computer | **HW3** — landscape center screen, MCU3 |
| Tesla firmware | Any FSD-capable V12 / V13 build |
| FSD package | Active subscription or purchased outright |

> **Not for HW4 / FSDV14.** If your vehicle has HW4 hardware (check *Controls → Software → Additional Vehicle Information*), use the [original comma 4 project](https://github.com/superpositiontime/tesla-fsd-comma4) instead.

---

## How to Identify HW3

In your Tesla: **Controls → Software → Additional Vehicle Information**

- Portrait center screen + HW3 → use the *Legacy* mode of the RP2040 project instead
- **Landscape center screen + HW3 → this project** ✅
- Landscape center screen + HW4 → use the comma 4 / HW4 project

---

## Key Differences from the HW4 Version

| Feature | HW4 original | HW3 this fork |
|---------|-------------|---------------|
| Follow-distance levels | 5 (values 1–5) | **3 (values 1–3)** |
| FSD enable bit | 46 + 60 | **46 only** |
| FSDV14 bit (60) | set | **omitted** |
| Emergency vehicle bit (59) | optional | **omitted** |
| Nag suppress bit (47) | set | **omitted** |
| Speed profile encoding | `data[7]` bits 4–6 (V14) | **`data[6]` bits 1–2 (V12/V13)** |
| Mux index-2 payload | profile → `data[7]` | **offset → `data[0]`/`data[1]`** |

---

## Installation

Runtime scripts live on **`/data`**. **Systemd unit files** (`.service` / `.timer`) must be
installed as **regular files** under `/etc/systemd/system/` — **not** symlinks into `/data`.
If the unit definition only exists on `/data`, systemd may never schedule the service at boot
(enabled but **inactive**, and `journalctl -u fsd-toggle` shows no lines).

### 1 — Copy files to the comma 3

```bash
ssh comma@comma.local mkdir -p /data/scripts
scp tesla_fsd_comma3_hw3.py fsd_toggle_server.py \
  fsd-toggle.service fsd-toggle.timer \
  comma@comma.local:/data/
scp scripts/install-fsd-systemd.sh comma@comma.local:/data/scripts/
```

Or directly on the device:

```bash
ssh comma@comma.local
mkdir -p /data/scripts
# replace <your-fork> / branch as needed
for f in tesla_fsd_comma3_hw3.py fsd_toggle_server.py fsd-toggle.service fsd-toggle.timer; do
  curl -o "/data/$f" "https://raw.githubusercontent.com/<your-fork>/main/$f"
done
curl -o /data/scripts/install-fsd-systemd.sh \
  https://raw.githubusercontent.com/<your-fork>/main/scripts/install-fsd-systemd.sh
chmod +x /data/scripts/install-fsd-systemd.sh
```

### 2 — Test offline first (no car needed)

```bash
ssh comma@comma.local
cd /data
# Edit tesla_fsd_comma3_hw3.py and set DUMMY_MODE = True
python3 tesla_fsd_comma3_hw3.py
```

You should see synthetic frames being processed and a status printout every 5 seconds.

### 3 — Run the toggle server (optional but recommended)

```bash
python3 /data/fsd_toggle_server.py &
```

The **systemd** unit uses `/usr/local/venv/bin/python3` and `PYTHONPATH=/data/pythonpath` so the
child FSD script can import `panda` / `opendbc`. A plain `python3` from ssh may need the same
(see [Troubleshooting](#troubleshooting)).

Open `http://<comma-ip>:8088` on your phone (same WiFi). Use the big button to
switch between **FSD mode** (stops openpilot, starts the CAN mod) and
**Comma / openpilot mode**.

### 4 — Live mode

Set `DUMMY_MODE = False` in `tesla_fsd_comma3_hw3.py`, then use the toggle server
or run directly:

```bash
python3 /data/tesla_fsd_comma3_hw3.py
```

### 5 — Auto-start on boot (optional)

The unit uses **`/usr/local/venv/bin/python3`**, **`PYTHONPATH=/data/pythonpath`**, and waits (up to
~120s in `ExecStartPre`) for `/data/fsd_toggle_server.py` and the venv so a slow `/data` mount
does not fail the first start. **`fsd-toggle.timer`** starts the service once per boot after
45s via **`timers.target`** (reliable if `multi-user.target` / `graphical.target` alone do not
pull the service).

**Recommended:** install **real copies** of both units in `/etc`, then enable service + timer:

```bash
sudo sh /data/scripts/install-fsd-systemd.sh
```

The script remounts `/` read-write, copies [`fsd-toggle.service`](fsd-toggle.service) and
[`fsd-toggle.timer`](fsd-toggle.timer) from `/data` into `/etc/systemd/system/`, runs
`daemon-reload`, and `enable --now` for both.

**Manual equivalent:**

```bash
sudo mount -o remount,rw /
sudo rm -f /etc/systemd/system/fsd-toggle.service /etc/systemd/system/fsd-toggle.timer
sudo cp /data/fsd-toggle.service /etc/systemd/system/fsd-toggle.service
sudo cp /data/fsd-toggle.timer   /etc/systemd/system/fsd-toggle.timer
sudo systemctl daemon-reload
sudo systemctl enable --now fsd-toggle.service
sudo systemctl enable --now fsd-toggle.timer
sudo mount -o remount,ro /
```

Reboot and verify:

```bash
sudo reboot
# after reboot:
ls -la /etc/systemd/system/fsd-toggle.service   # should be a file, not -> /data/...
systemctl status fsd-toggle --no-pager
systemctl list-timers --all | grep fsd
journalctl -u fsd-toggle -u fsd-toggle.timer -b --no-pager
```

The toggle server should be reachable at `http://<comma-ip>:8088`.

**Disable auto-start** (disable both; otherwise the timer may still start the service):

```bash
sudo mount -o remount,rw /
sudo systemctl disable fsd-toggle.service
sudo systemctl disable fsd-toggle.timer
sudo mount -o remount,ro /
```

> **Note:** AGNOS mounts `/` read-only; use `mount -o remount,rw /` before changing `/etc`.
> After an AGNOS update, re-copy unit files from `/data` if `/etc` was reset. **Do not** use
> `ln -sf /data/fsd-toggle.service /etc/systemd/system/` for the unit definition — that pattern
> breaks boot scheduling on many comma builds.

---

## Speed Profiles (HW3)

Speed profile is set by the **follow-distance stalk/setting** in the Tesla UI:

| Follow Distance | Profile | Description |
|----------------|---------|-------------|
| 2 (closest) | 2 | Hurry ⚡ |
| 3 (middle) | 1 | Normal 🟢 |
| 4 (furthest) | 0 | Chill ❄️ |

A fine-grained speed offset is also derived from the scroll-wheel position
(`data[3]` in the autopilot command frame) and injected on mux index-2 frames.

---

## Configuration Flags

Edit the top of `tesla_fsd_comma3_hw3.py`:

| Flag | Default | Description |
|------|---------|-------------|
| `DUMMY_MODE` | `False` | `True` = offline test with fake frames |
| `TRANSMIT` | `True` | `True` = modify & retransmit; uses `CarParams.SafetyModel.allOutput` (current comma stack), not legacy `Panda.SAFETY_ALLOUTPUT` |
| `SHOW_ON_SCREEN` | `True` | Show status in openpilot HUD via cereal |
| `CAN_BUS` | `2` | Vehicle CAN bus number (try 0 or 1 if nothing is seen) |
| `LOG_FRAMES` | `True` | Print frame activity to terminal |

### Environment variables (`tesla_fsd_comma3_hw3.py`)

| Variable | Purpose |
|----------|---------|
| `FSD_SKIP_HOLDER_CHECK` | Set to `1` to skip the pre-flight scan for `pandad` / `boardd` / manager (debug only). |
| `FSD_PANDA_SPI_ONLY` | Force SPI-only panda connection (bypass USB). |
| `FSD_PANDA_SPI_FIRST` | Prefer SPI before USB even off AGNOS. |
| `FSD_PANDA_USB_FIRST` | Prefer USB before SPI on comma (default on AGNOS is SPI-first to avoid USB `BUSY` / `CAN: BAD RECV`). |

---

## Troubleshooting

- **`openpilot.service` not loaded** — Your stack may run from tmux instead of systemd. Stop openpilot there, or kill the panda daemons before FSD mode: `sudo killall -KILL pandad` (and `boardd` if present). The toggle server also tries to kill native `pandad`, the Python `selfdrive.pandad.pandad` wrapper, and `boardd` after stopping openpilot.
- **`LIBUSB_ERROR_BUSY` / `CAN: BAD RECV, RETRYING`** — Another process still owns the panda (usually `./pandad`). Kill it as above. On AGNOS the script prefers **SPI** first; use `FSD_PANDA_USB_FIRST=1` if you must force USB on an external panda setup.
- **`No module named 'panda'`** — The systemd service sets `PYTHONPATH=/data/pythonpath` and uses the comma venv. For manual runs: `PYTHONPATH=/data/pythonpath /usr/local/venv/bin/python3 /data/tesla_fsd_comma3_hw3.py` (or run from a shell where openpilot’s paths are already set).
- **Holder check false positive** — Rare; use `FSD_SKIP_HOLDER_CHECK=1` only if you are sure nothing holds the panda.

---

## Disclaimer

This project modifies CAN bus messages on your vehicle. Use entirely at your own
risk. An active Tesla FSD subscription or purchase is required — this tool only
activates pre-downloaded FSD capability, it does not provide the neural network
software. Modifying vehicle CAN messages may cause unexpected behaviour and may
void your warranty. Tesla may alter or block these signals through OTA updates.

---

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE).

Original comma 4 / HW4 work by [superpositiontime](https://github.com/superpositiontime).
HW3 CAN protocol reference by [Tesla-OPEN-CAN-MOD](https://gitlab.com/Tesla-OPEN-CAN-MOD).
