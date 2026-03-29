# 🚗 Tesla FSD CAN Mod — Comma 4 Edition

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://python.org)
[![Target](https://img.shields.io/badge/target-2026%20Model%20Y%20Juniper-red)](https://www.tesla.com)
[![Hardware](https://img.shields.io/badge/hardware-comma%204-black)](https://comma.ai)
[![HW Version](https://img.shields.io/badge/autopilot-HW4-orange)]()
[![License](https://img.shields.io/badge/license-MIT-green)]()

<p align="center">
  <img src="https://www.comma.ai/_app/immutable/assets/four_screen_on.WrrTderw.png" alt="comma four" width="420" />
</p>

A Python port of [Starmixcraft's Tesla FSD CAN Mod](https://gitlab.com/Starmixcraft/tesla-fsd-can-mod) — adapted to run directly on the **comma 4** via its built-in panda CAN interface.

**No extra hardware needed.** If your comma 4 is already installed in your Tesla, this is purely a software mod.

> **💡 What does this do?** This mod uses CAN bus bit injection to enable Tesla's Full Self-Driving on your car **without paying for a Tesla FSD subscription ($99/mo)**. It flips the FSD-enabled bit via the comma 4's panda interface. When you want openpilot instead, one tap switches back.

---

## 💰 Subscriptions & Cost Breakdown

| What | Cost | Required? | Notes |
|------|------|-----------|-------|
| **Tesla FSD subscription** | $99/mo | **❌ NO — this mod bypasses it** | The whole point of this mod. CAN bit injection tricks the car into activating FSD without a subscription. |
| **comma prime** | $24/mo | **❌ NO — optional** | Adds LTE connectivity, GPS tracking, cloud drive storage. Not needed for openpilot or this mod. [Details →](https://comma.ai/connect) |
| **comma 4 device** | ~$999 one-time | **✅ YES** | Includes the panda CAN interface used for both openpilot driving and FSD CAN injection. [Buy →](https://comma.ai/shop/comma-four) |
| **Tesla B harness** | ~$50 one-time | **✅ YES** | Connects the comma 4 to your Tesla's ADAS camera port. Usually bundled when you buy the comma 4. [Buy →](https://comma.ai/shop/car-harness) |
| **OBD-C cable** | Included | ✅ Included with comma 4 | Connects the comma 4 to the harness box. |
| **comma power** (OBD-II) | ~$30 one-time | ❌ Optional | Keeps comma 4 powered/online while car is off. Useful but not required. |

**Bottom line:** ~$999–$1,050 one-time hardware cost. **No monthly subscriptions required.**

---

## 🎯 Prerequisites

Before you start, make sure you have:

- [ ] **Tesla Model Y Juniper 2026** (or Model 3/Y with **HW4/HW4.5**)
- [ ] **Tesla firmware ≥ 2026.2.3** (check: car touchscreen → Software → Version)
- [ ] **Comma 4** with Tesla B harness — already physically installed and working with openpilot
- [ ] **openpilot running normally** — you should be able to engage openpilot and have it drive before attempting this mod
- [ ] **SSH access to your comma 4** — via comma connect app or local WiFi (`ssh comma@comma.local`)
- [ ] **Phone on the same WiFi** as the comma 4 (for the toggle web UI)

> ⚠️ **This mod assumes you already have a working comma 4 + openpilot setup.** If you haven't installed the comma 4 yet, follow the [official comma setup guide](https://comma.ai/setup) first.

---

## 📋 Step-by-Step Setup

### Step 1: Install the Comma 4 Hardware (if not already done)

Follow the [official comma.ai setup guide](https://comma.ai/setup). The short version:

1. **Remove the rearview mirror trim cover** (varies by car — strong tug on Teslas)
2. **Unplug the ADAS camera connector** behind the mirror
3. **Plug the Tesla B harness** in between the camera and the car's connector
4. **Mount the comma 4** centered on the windshield with the 3M adhesive mount
5. **Connect the OBD-C cable** from the harness box to the comma 4
6. **Reinstall the trim** — tuck the harness box inside, route the OBD-C cable out the top
7. **(Optional)** Plug in comma power to the OBD-II port for always-on connectivity

### Step 2: Verify openpilot works

Before touching the FSD mod:
- Start the car and confirm openpilot boots on the comma 4 screen
- Do a test drive with openpilot engaged — steering, gas, braking should all work
- If openpilot isn't working yet, fix that first. Check [howtocomma.com](https://docs.howtocomma.com/) for help

### Step 3: SSH into the comma 4

```bash
# Option A: via local WiFi (comma must be on same network)
ssh comma@comma.local

# Option B: via comma connect (if you have comma prime)
# Use the SSH option in the comma connect app
```

Default password is usually blank or `comma` depending on your setup.

### Step 4: Download the FSD mod scripts

```bash
# Download the main FSD CAN mod script
curl -o /data/tesla_fsd_comma4.py \
  https://raw.githubusercontent.com/superpositiontime/tesla-fsd-comma4/main/tesla_fsd_comma4.py

# Download the phone toggle server
curl -o /data/fsd_toggle_server.py \
  https://raw.githubusercontent.com/superpositiontime/tesla-fsd-comma4/main/fsd_toggle_server.py

# Install dependencies
pip install flask
```

### Step 5: Test in dummy mode (no car needed)

Before testing in the car, verify the script runs:

```bash
# Edit the script to enable dummy mode
sed -i 's/DUMMY_MODE.*=.*False/DUMMY_MODE = True/' /data/tesla_fsd_comma4.py

# Run it
python3 /data/tesla_fsd_comma4.py
```

You should see synthetic CAN frames being generated and modified. Press `Ctrl+C` to stop.

```bash
# Turn dummy mode back off for real use
sed -i 's/DUMMY_MODE.*=.*True/DUMMY_MODE = False/' /data/tesla_fsd_comma4.py
```

### Step 6: Start the toggle server

```bash
python3 /data/fsd_toggle_server.py &
```

Now open your phone browser and go to:

```
http://<comma-ip>:8088
```

Replace `<comma-ip>` with your comma's IP address (find it in the comma connect app or via `hostname -I` over SSH).

### Step 7: Toggle between modes

On the phone web UI, tap the big button to switch:

- **→ Tesla FSD:** Stops openpilot → starts the CAN mod → Tesla FSD activates
- **→ openpilot:** Stops the CAN mod → restarts openpilot → Comma AI drives

### Step 8: Auto-start on boot (optional)

To have the toggle server start automatically every time the comma boots:

```bash
echo 'python3 /data/fsd_toggle_server.py &' >> /data/rc.local
chmod +x /data/rc.local
```

---

## 🧠 How It Works — Architecture

> **Only one system drives at a time.** This mod switches between two completely separate driving brains.

### Mode A: openpilot (Comma drives the car)

```
┌─────────────────────────────────────────────────────┐
│  COMMA 4 DEVICE                                     │
│  ┌───────────┐  ┌──────────┐  ┌──────────────────┐  │
│  │ Cameras   │→ │ AI Model │→ │ Steering / Accel │  │
│  │ (onboard) │  │(openpilot│  │  via Panda CAN   │  │
│  └───────────┘  └──────────┘  └──────────────────┘  │
│                                                     │
│  Tesla FSD: OFF (bypassed)                          │
│  FSD CAN script: NOT RUNNING                        │
└─────────────────────────────────────────────────────┘
```

- The Comma 4 uses its own cameras + AI model to drive
- openpilot controls steering, gas, and braking through the panda CAN interface
- Tesla's FSD computer is completely bypassed
- The Comma's 1.9" OLED shows the openpilot driving view

### Mode B: Tesla FSD (Tesla drives the car)

```
┌─────────────────────────────────────────────────────┐
│  TESLA HW4 COMPUTER (built into the car)            │
│  ┌───────────┐  ┌──────────┐  ┌──────────────────┐  │
│  │ Tesla     │→ │ Tesla NN │→ │ Steering / Accel │  │
│  │ Cameras   │  │ (FSD)    │  │  (native)        │  │
│  └───────────┘  └──────────┘  └──────────────────┘  │
│                                                     │
│  COMMA 4: openpilot STOPPED                         │
│  Comma's role: CAN tool only                        │
│  ┌──────────────────────────────────────────────┐   │
│  │ tesla_fsd_comma4.py running via panda:       │   │
│  │  • Flip FSD-enabled bit (0x3FD mux 0)        │   │
│  │  • Suppress nag warnings (0x3FD mux 1)       │   │
│  │  • Inject speed profile (0x3FD mux 2)        │   │
│  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

- openpilot is **stopped** — the Comma does **not** drive
- Tesla's own HW4 computer + cameras + neural net handles everything
- The Comma 4's panda is used **only** as a CAN bus tool to:
  - Enable FSD without a subscription (bit injection on `0x3FD`)
  - Suppress "hands on wheel" nag warnings
  - Map follow distance to speed profiles (Chill / Normal / Sport)

### The Toggle (Phone Web UI)

```
        Phone browser → http://<comma-ip>:8088
                    ┌─────────────┐
     ┌──────────────│  SWITCH TO  │──────────────┐
     │              │    FSD      │              │
     ▼              └─────────────┘              ▼
 ┌────────┐     stops openpilot service     ┌────────┐
 │ COMMA  │ ──────────────────────────────→ │  FSD   │
 │  MODE  │     starts CAN mod script       │  MODE  │
 │        │ ←────────────────────────────── │        │
 └────────┘     stops CAN mod script        └────────┘
                starts openpilot service
```

The web toggle (`fsd_toggle_server.py`) serves a mobile-friendly page on port 8088. One tap switches modes. The transition takes ~5–10 seconds.

---

## ✨ Features

- **FSD enable injection** — modifies autopilot CAN frames to activate Full Self-Driving without subscription
- **Nag suppression** — clears the "FSD subscription required" nag message
- **Speed profile mapping** — reads your follow distance setting and maps it to a speed profile (Chill / Normal / Sport)
- **HUD status display** — shows live mod status on the comma screen via openpilot's cereal bus
- **Dummy / offline test mode** — generates synthetic CAN frames so you can test at home without the car
- **Monitor-only mode** — run alongside openpilot without interrupting its driving (`TRANSMIT = False`)
- **Phone toggle UI** — switch between FSD and openpilot from your phone browser

---

## 📱 Web Toggle UI

Switch between FSD and openpilot from your phone — no SSH needed after setup.

**Start the toggle server:**

```bash
python3 /data/fsd_toggle_server.py
```

Then open **`http://<comma-ip>:8088`** in your phone browser (same WiFi).

The UI shows:
- **Current mode** — which system is active (openpilot or Tesla FSD)
- **Live CAN bus log** — real-time frame modifications scrolling
- **One big button** — tap to switch modes (~5–10 second transition)
- **Status cards** — panda connection, transmit state, speed profile, uptime

---

## 🚀 Advanced Usage

### Monitor only (openpilot keeps driving)

```bash
ssh comma@comma.local
python3 /data/tesla_fsd_comma4.py
```

With default settings (`TRANSMIT = False`), the script listens and shows status on the HUD without interfering with openpilot.

### Full mod manually (without toggle UI)

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

### Dummy mode (no car needed)

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
TRANSMIT        = True    # True = modify & retransmit frames (requires ALLOUTPUT)
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

## 🔧 CAN Bus Details

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

## ❓ FAQ

**Q: Do I need to pay for Tesla FSD ($99/mo)?**
A: No. That's the entire point of this mod — it enables FSD via CAN bus bit injection without a Tesla subscription.

**Q: Do I need comma prime ($24/mo)?**
A: No. Comma prime is optional and only adds LTE connectivity, GPS tracking, and cloud storage. openpilot and this mod work fine without it.

**Q: Does this work on HW3 Teslas?**
A: No. This port targets HW4/HW4.5 only. For HW3 support, see the [original CanFeather project](https://gitlab.com/Starmixcraft/tesla-fsd-can-mod).

**Q: Can Tesla patch this via OTA?**
A: Potentially yes. Tesla could change CAN message IDs or add authentication. This mod targets firmware ≥ 2026.2.3. If Tesla updates break it, the CAN frame IDs in the script would need to be updated.

**Q: Does installing the comma 4 void my Tesla warranty?**
A: The [Magnuson-Moss Warranty Act](https://www.consumer.ftc.gov/articles/0138-auto-warranties-routine-maintenance) makes it illegal for companies to void your warranty simply because you used an aftermarket part. The comma 4 installation is fully reversible.

**Q: Can I run both openpilot and FSD at the same time?**
A: No. Only one system controls the car at a time. The toggle switches between them.

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
