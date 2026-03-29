# 🚗 Tesla FSD CAN Mod — Comma 4 Edition

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://python.org)
[![Target](https://img.shields.io/badge/target-2026%20Model%20Y%20Juniper-red)](https://www.tesla.com)
[![Hardware](https://img.shields.io/badge/hardware-comma%204-black)](https://comma.ai)
[![HW Version](https://img.shields.io/badge/autopilot-HW4-orange)]()
[![License](https://img.shields.io/badge/license-MIT-green)]()

A Python port of [Starmixcraft's Tesla FSD CAN Mod](https://gitlab.com/Starmixcraft/tesla-fsd-can-mod) — adapted to run directly on the **comma 4** via its built-in panda CAN interface.

No extra hardware needed. If your comma 4 is already working in your Tesla, this is purely a software mod.

---

## ✨ Features

- **FSD enable injection** — modifies autopilot CAN frames to activate Full Self-Driving without subscription
- **Nag suppression** — clears the "FSD subscription required" nag message
- **Speed profile mapping** — reads your follow distance setting and maps it to a speed profile (Chill / Normal / Sport)
- **HUD status display** — shows live mod status on the comma screen via openpilot's cereal bus
- **Dummy / offline test mode** — generates synthetic CAN frames so you can test at home without the car
- **Monitor-only mode** — run alongside openpilot without interrupting its driving (`TRANSMIT = False`)

---

## 🎯 Requirements

| Requirement | Detail |
|---|---|
| Hardware | comma 4 (with panda built in) |
| Vehicle | Tesla Model Y Juniper 2026 (HW4, firmware ≥ 2026.2.3) |
| openpilot | Already installed and working |
| Connection | SSH access to the comma (via comma connect or local WiFi) |

---

## ⚡ Quick Install

SSH into your comma and run:

```bash
curl -o /data/tesla_fsd_comma4.py \
  https://raw.githubusercontent.com/superpositiontime/tesla-fsd-comma4/main/tesla_fsd_comma4.py
```

---

## 🚀 Usage

### Option A — Monitor only (openpilot keeps driving)

```bash
ssh comma@comma.local
python3 /data/tesla_fsd_comma4.py
```

With default settings (`TRANSMIT = False`), the script listens and shows status on the HUD without interfering with openpilot.

### Option B — Full mod (openpilot paused)

Edit the top of the script:
```python
TRANSMIT = True   # enable CAN frame injection
```

Then:
```bash
sudo systemctl stop openpilot
python3 /data/tesla_fsd_comma4.py
# When done:
sudo systemctl start openpilot
```

### Option C — Dummy mode (no car needed)

```python
DUMMY_MODE = True   # generates synthetic Tesla CAN frames
```

```bash
python3 /data/tesla_fsd_comma4.py
```

Useful for testing the script logic at home before getting in the car.

---

## ⚙️ Configuration

Edit the top of `tesla_fsd_comma4.py`:

```python
DUMMY_MODE      = False   # True = offline test with fake CAN frames
TRANSMIT        = True    # True = modify & retransmit frames (requires ALLOUTPUT safety)
SHOW_ON_SCREEN  = True    # True = publish status to openpilot HUD via cereal
CAN_BUS         = 2       # Autopilot bus (try 0 or 1 if you see zero frames)
LOG_FRAMES      = True    # Print frame activity to terminal
```

---

## 📺 HUD Display

When `SHOW_ON_SCREEN = True`, live status is written to openpilot's params and the comma screen shows:

```
FSD Mod v2 | ACTIVE | Profile: Normal | Nag: OFF | Frames: 47 modified | Uptime: 83s
```

State changes also trigger a brief `userFlag` pulse in the openpilot HUD.

---

## 🔧 How It Works

This is a Python translation of the `HW4Handler` from the original CanFeather Arduino project. It:

1. **Listens** on the autopilot CAN bus (500 kbps) for two frame IDs:
   - `0x3F8` (1016) — Follow distance / speed profile
   - `0x3FD` (1021) — Autopilot command (3-frame mux)

2. **Modifies** the autopilot command frames:
   - **Mux 0** → sets bit 46 + bit 60 → signals FSD as active
   - **Mux 1** → clears bit 19 (nag) + sets bit 47 (suppress)
   - **Mux 2** → injects speed profile into byte 7 bits [6:4]

3. **Retransmits** the modified frames back onto the bus via panda's `SAFETY_ALLOUTPUT` mode

---

## ⚠️ Safety & Disclaimer

- **This is experimental software.** Use at your own risk.
- Setting `SAFETY_ALLOUTPUT` disables openpilot's safety checks — **openpilot will not be driving while this is active in TRANSMIT mode**.
- Tesla may change CAN message IDs at any time via OTA software updates.
- The original project targets HW4 (firmware ≥ 2026.2.3). Earlier HW3 vehicles use different frame IDs — see the [original project](https://gitlab.com/Starmixcraft/tesla-fsd-can-mod) for HW3 support.
- This mod has no affiliation with comma.ai or Tesla.

---

## 📄 Credits

- Original Arduino project: [Starmixcraft / tesla-fsd-can-mod](https://gitlab.com/Starmixcraft/tesla-fsd-can-mod)
- Comma 4 / panda Python port: this repo

---

## 📝 License

MIT — do whatever you want, just don't blame anyone if something goes sideways.
