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

### 1 — Copy files to the comma 3

```bash
scp tesla_fsd_comma3_hw3.py fsd_toggle_server.py fsd-toggle.service comma@comma.local:/data/
```

Or directly on the device:

```bash
ssh comma@comma.local
curl -o /data/tesla_fsd_comma3_hw3.py \
  https://raw.githubusercontent.com/<your-fork>/main/tesla_fsd_comma3_hw3.py
curl -o /data/fsd_toggle_server.py \
  https://raw.githubusercontent.com/<your-fork>/main/fsd_toggle_server.py
curl -o /data/fsd-toggle.service \
  https://raw.githubusercontent.com/<your-fork>/main/fsd-toggle.service
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

Install the included systemd service so the toggle server launches every time the
comma 3 powers on:

```bash
ssh comma@comma.local
sudo mount -o remount,rw /
sudo ln -sf /data/fsd-toggle.service /etc/systemd/system/fsd-toggle.service
sudo systemctl daemon-reload
sudo systemctl enable fsd-toggle
sudo mount -o remount,ro /
```

Reboot and verify:

```bash
sudo reboot
# after reboot:
systemctl status fsd-toggle
```

The toggle server will now auto-start on every boot at `http://<comma-ip>:8088`.

To disable auto-start later:

```bash
sudo mount -o remount,rw /
sudo systemctl disable fsd-toggle
sudo mount -o remount,ro /
```

> **Note:** The `mount -o remount,rw /` step is needed because AGNOS mounts the
> root filesystem as read-only. The symlink in `/etc/systemd/system/` persists
> across normal reboots but may need to be re-created after an AGNOS update.

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
| `TRANSMIT` | `True` | `True` = modify & retransmit (needs ALLOUTPUT) |
| `SHOW_ON_SCREEN` | `True` | Show status in openpilot HUD via cereal |
| `CAN_BUS` | `2` | Vehicle CAN bus number (try 0 or 1 if nothing is seen) |
| `LOG_FRAMES` | `True` | Print frame activity to terminal |

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
