#!/usr/bin/env python3
"""
Tesla FSD CAN Mod — Comma 3 / Black Panda Edition  v2.0
Ported from tesla_fsd_comma4.py (HW4Handler) by Starmixcraft
Adapted for Comma 3 with integrated black panda + Tesla FSD HW3 (V12/V13)
Target: Model 3 / Model Y with landscape MCU and HW3 autopilot computer

HW3 vs HW4 differences implemented here:
  • Follow-distance maps 3 levels (1-3) instead of 5
  • FSD enable uses bit 46 only (no bit 60 / FSDV14, no bit 59)
  • Nag suppression clears bit 19 only (no bit 47)
  • Speed profile stored in data[6] bits 1-2 (V12/V13 encoding)
  • Speed profile + offset also derived from scroll-wheel (data[3]) on mux index 0
  • Mux index 2 writes speed offset into data[0] bits 6-7 + data[1] bits 0-5

Comma 3 panda note:
  The integrated black panda exposes the same Python `panda` API as the
  Comma 4 red panda. No panda initialisation changes are needed.

MODES:
  DUMMY_MODE = True   → Runs offline with synthetic frames (no car needed)
  DUMMY_MODE = False  → Live mode against the real Tesla CAN bus

SCREEN INTEGRATION:
  When SHOW_ON_SCREEN = True, the script publishes status alerts to
  openpilot's cereal bus so they appear in the HUD.

  This mode does NOT require stopping openpilot — it runs alongside it.
  However: for raw CAN TX you must use SAFETY_ALLOUTPUT (stops openpilot
  from driving). Screen-only monitoring (TRANSMIT = False) works with
  openpilot running normally.

HOW TO USE (live + screen):
  1. SSH: ssh comma@comma.local
  2. cd /data && python3 tesla_fsd_comma3_hw3.py

HOW TO USE (dummy test at home):
  1. Set DUMMY_MODE = True below
  2. ssh comma@comma.local
  3. python3 tesla_fsd_comma3_hw3.py
"""

# ── User Configuration ─────────────────────────────────────────────────────────
DUMMY_MODE      = False   # True = offline test with fake CAN frames
TRANSMIT        = True    # True = modify & retransmit frames (requires ALLOUTPUT)
SHOW_ON_SCREEN  = True    # True = publish status to openpilot HUD via cereal
CAN_BUS         = 2       # Autopilot bus via comma harness (try 0 or 1 if broken)
LOG_FRAMES      = True    # Print frame activity to terminal
# ──────────────────────────────────────────────────────────────────────────────

import sys
import time
import threading
import struct
import random

# ── CAN Frame IDs ─────────────────────────────────────────────────────────────
ID_FOLLOW_DISTANCE  = 1016   # 0x3F8
ID_AUTOPILOT_CMD    = 1021   # 0x3FD

# ── State ─────────────────────────────────────────────────────────────────────
speed_profile   = 1   # 0=Chill  1=Normal  2=Hurry
speed_offset    = 0   # HW3: scroll-wheel derived offset (0-100), sent on mux index 2
fsd_active      = False
nag_suppressed  = False
frames_total    = 0
frames_modified = 0
start_time      = time.time()


# ──────────────────────────────────────────────────────────────────────────────
# Bit helpers (mirrors CanFeather setBit exactly)
# ──────────────────────────────────────────────────────────────────────────────
def set_bit(data: bytearray, bit: int, value: bool) -> None:
    byte_i = bit // 8
    bit_i  = bit % 8
    if value:
        data[byte_i] |= (1 << bit_i)
    else:
        data[byte_i] &= ~(1 << bit_i)


# ──────────────────────────────────────────────────────────────────────────────
# Frame handlers
# ──────────────────────────────────────────────────────────────────────────────
def handle_follow_distance(data: bytes) -> None:
    """
    CAN ID 1016 — HW3 follow-distance → speed profile mapping.
    3 levels: fd=1 → Hurry (2), fd=2 → Normal (1), fd=3 → Chill (0).
    (HW4 used 5 levels and different indices.)
    """
    global speed_profile
    fd = (data[5] & 0b11100000) >> 5
    mapping = {1: 2, 2: 1, 3: 0}   # HW3: 3-level mapping
    if fd in mapping:
        speed_profile = mapping[fd]
        if LOG_FRAMES:
            names = {0: "Chill", 1: "Normal", 2: "Hurry"}
            print(f"  [DIST ] fd={fd} → speed profile={speed_profile} ({names.get(speed_profile,'?')})")


def handle_autopilot_cmd(panda, data: bytes) -> None:
    """
    CAN ID 1021 — HW3 autopilot command frame (multiplexed).

    Mux index 0  — FSD activation:
      • Reads scroll-wheel position from data[3] to derive speed_profile and
        speed_offset (HW3 V12/V13 dual-source profile).
      • Sets bit 46 (FSD enable).
      • Writes V12/V13 speed profile into data[6] bits 1-2.
      • Does NOT set bit 60 (FSDV14) or bit 59 (emergency vehicle) — HW3 only.

    Mux index 1  — Nag suppression:
      • Clears bit 19 (hands-on-wheel warning).
      • Does NOT set bit 47 — that is HW4 only.

    Mux index 2  — Speed offset:
      • Writes speed_offset (0-100) into data[0] bits 6-7 and data[1] bits 0-5.
      • HW4 wrote a speed profile into data[7] bits 4-6 here — HW3 differs.
    """
    global fsd_active, nag_suppressed, frames_modified, speed_profile, speed_offset

    index       = data[0] & 0x07
    fsd_in_ui   = bool((data[4] >> 6) & 0x01)
    modified    = bytearray(data)
    did_modify  = False

    if index == 0 and fsd_in_ui:
        # ── HW3: derive speed profile + offset from scroll-wheel position ──────
        # data[3] bits 1-6 hold a 6-bit scroll value; Tesla encodes ~30 as
        # the baseline, so off=0 → Normal, off=1 → Hurry, off=2 → Max.
        scroll_val   = (data[3] >> 1) & 0x3F
        off          = scroll_val - 30
        speed_offset = max(min(off * 5, 100), 0)

        if   off >= 2: speed_profile = 2   # Hurry
        elif off == 1: speed_profile = 1   # Normal
        elif off == 0: speed_profile = 0   # Chill
        # else: off < 0 means no valid scroll data yet → keep current profile

        # ── Set FSD enable bit 46 (HW3 only needs this, NOT bit 60/59) ────────
        set_bit(modified, 46, True)

        # ── Write V12/V13 speed profile into data[6] bits 1-2 ─────────────────
        # (HW4 wrote profile into data[7] bits 4-6 — different encoding)
        modified[6] = (modified[6] & ~0x06) | ((speed_profile & 0x03) << 1)

        fsd_active = True
        did_modify = True
        if LOG_FRAMES:
            names = {0: "Chill", 1: "Normal", 2: "Hurry"}
            print(f"  [FSD  ] mux=0 FSD_UI=True → bit 46 SET, "
                  f"profile={speed_profile} ({names.get(speed_profile,'?')}), "
                  f"offset={speed_offset} ✓")

    elif index == 1:
        # ── Clear hands-on-wheel nag (bit 19) ─────────────────────────────────
        # HW3 does NOT set bit 47 here (that is HW4-only behaviour)
        set_bit(modified, 19, False)
        nag_suppressed = True
        did_modify = True
        if LOG_FRAMES:
            print(f"  [NAG  ] mux=1 → bit 19 cleared (nag suppressed)")

    elif index == 2 and fsd_in_ui:
        # ── HW3 index-2: write speed offset into bytes 0-1 ────────────────────
        # Encoding: low 2 bits of offset → data[0] bits 6-7
        #           high 6 bits of offset → data[1] bits 0-5
        # (HW4 wrote speed profile into data[7] bits 4-6 instead)
        modified[0] = (modified[0] & ~0xC0) | ((speed_offset & 0x03) << 6)
        modified[1] = (modified[1] & ~0x3F) | (speed_offset >> 2)
        did_modify = True
        if LOG_FRAMES:
            print(f"  [SPEED] mux=2 → offset={speed_offset} injected into bytes 0-1")

    if did_modify:
        frames_modified += 1
        if TRANSMIT and panda is not None:
            panda.can_send(ID_AUTOPILOT_CMD, bytes(modified), CAN_BUS)


# ──────────────────────────────────────────────────────────────────────────────
# Dummy frame generator
# ──────────────────────────────────────────────────────────────────────────────
def generate_dummy_frames():
    """
    Yields a stream of synthetic (addr, data) tuples that simulate
    a Tesla HW3 CAN bus with FSD selected in the UI.
    Cycles through all three mux sub-frames of ID_AUTOPILOT_CMD.
    data[3] is set to (31 << 1) = 0x3E → scroll_val=31, off=1 → Normal profile.
    """
    mux_index = 0
    tick = 0
    while True:
        # Simulate follow distance frame every 10 ticks (fd=2 → Normal)
        if tick % 10 == 0:
            data = bytearray(8)
            data[5] = (2 << 5)  # fd=2 → Normal profile
            yield (ID_FOLLOW_DISTANCE, bytes(data))

        # Simulate autopilot command frame cycling mux 0→1→2
        data = bytearray(8)
        data[0] = mux_index & 0x07
        if mux_index == 0:
            data[4] = (1 << 6)       # FSD selected in UI
            data[3] = (31 << 1)      # scroll_val=31, off=1 → Normal, offset=5
        yield (ID_AUTOPILOT_CMD, bytes(data))

        mux_index = (mux_index + 1) % 3
        tick += 1
        time.sleep(0.05)   # 20 Hz like real Tesla bus


# ──────────────────────────────────────────────────────────────────────────────
# Comma screen integration via cereal
# ──────────────────────────────────────────────────────────────────────────────
_screen_thread = None
_screen_running = False

def _screen_worker():
    """
    Background thread: publishes FSD mod status to the openpilot HUD
    using cereal's controlsState-adjacent alertText mechanism.
    Runs independently so main CAN loop is never blocked.
    """
    global _screen_running
    try:
        sys.path.insert(0, '/data/openpilot')
        import cereal.messaging as messaging
        from openpilot.common.params import Params
        pm = messaging.PubMaster(['userFlag'])
        params = Params()
        print("  [SCRN ] Cereal connected — status will appear on HUD")
    except ImportError:
        print("  [SCRN ] cereal not found — screen integration unavailable")
        print("          (This is fine in DUMMY_MODE or outside comma)")
        _screen_running = False
        return

    speed_names = {0: "Chill", 1: "Normal", 2: "Hurry"}
    last_status = ""

    while _screen_running:
        uptime = int(time.time() - start_time)
        profile_name = speed_names.get(speed_profile, "?")

        # Write human-readable status to params (visible in dev UI sidebar)
        status_str = (
            f"FSD Mod HW3 | {'ACTIVE' if fsd_active else 'Waiting'} | "
            f"Profile: {profile_name} | Offset: {speed_offset} | "
            f"Nag: {'OFF' if nag_suppressed else 'ON'} | "
            f"Frames: {frames_modified} modified | "
            f"Uptime: {uptime}s"
        )
        if status_str != last_status:
            try:
                params.put("FsdModStatus", status_str)
                # Also push a userFlag event so the HUD blinks briefly on state change
                msg = messaging.new_message('userFlag')
                pm.send('userFlag', msg)
            except Exception:
                pass
            last_status = status_str

        time.sleep(1)


def start_screen_integration():
    global _screen_thread, _screen_running
    _screen_running = True
    _screen_thread = threading.Thread(target=_screen_worker, daemon=True)
    _screen_thread.start()


def stop_screen_integration():
    global _screen_running
    _screen_running = False


# ──────────────────────────────────────────────────────────────────────────────
# Status printer
# ──────────────────────────────────────────────────────────────────────────────
def print_status():
    speed_names = {0: "Chill", 1: "Normal", 2: "Hurry"}
    uptime = int(time.time() - start_time)
    print(
        f"\n  ── Status @ {uptime}s ──────────────────────────────\n"
        f"  FSD Active    : {'✅ YES' if fsd_active else '⏳ Waiting'}\n"
        f"  Nag Suppressed: {'✅ YES' if nag_suppressed else '❌ NO'}\n"
        f"  Speed Profile : {speed_profile} ({speed_names.get(speed_profile,'?')})\n"
        f"  Speed Offset  : {speed_offset}\n"
        f"  Frames seen   : {frames_total}\n"
        f"  Frames modified: {frames_modified}\n"
        f"  ────────────────────────────────────────────────\n"
    )


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────
def main():
    global frames_total

    print("\n" + "═" * 62)
    print("  Tesla FSD CAN Mod — Comma 3 Edition  (HW3 / V12-V13)  v2.0")
    print("═" * 62)
    print(f"  Mode          : {'🧪 DUMMY (offline test)' if DUMMY_MODE else '🚗 LIVE'}")
    print(f"  Transmit      : {'✅ YES (ALLOUTPUT)' if TRANSMIT else '👁  NO (monitor only)'}")
    print(f"  Screen HUD    : {'✅ YES (cereal)' if SHOW_ON_SCREEN else '❌ NO'}")
    print(f"  CAN Bus       : {CAN_BUS}")
    print("═" * 62 + "\n")

    panda = None

    if not DUMMY_MODE:
        try:
            from panda import Panda
            panda = Panda()
            print(f"  Panda FW: {panda.get_version()}")
            if TRANSMIT:
                panda.set_safety_mode(Panda.SAFETY_ALLOUTPUT)
                panda.set_can_speed_kbps(CAN_BUS, 500)
                print("  Safety: ALLOUTPUT — openpilot driving DISABLED while running")
            else:
                print("  Safety: default — openpilot can run alongside (monitor only)")
        except Exception as e:
            print(f"\n  [ERROR] Panda connect failed: {e}")
            print("  If openpilot is running: sudo systemctl stop openpilot")
            sys.exit(1)
    else:
        print("  ⚠️  DUMMY MODE — using synthetic frames, no panda needed\n")

    if SHOW_ON_SCREEN:
        start_screen_integration()

    print("  Listening for CAN frames... Press Ctrl+C to stop.\n")

    last_status_print = time.time()

    try:
        if DUMMY_MODE:
            # ── Dummy mode: iterate synthetic frames ──────────────────────────
            for addr, dat in generate_dummy_frames():
                frames_total += 1
                if addr == ID_FOLLOW_DISTANCE:
                    handle_follow_distance(dat)
                elif addr == ID_AUTOPILOT_CMD:
                    handle_autopilot_cmd(None, dat)
                if time.time() - last_status_print >= 5:
                    print_status()
                    last_status_print = time.time()

        else:
            # ── Live mode: read from panda ────────────────────────────────────
            while True:
                messages = panda.can_recv()
                for addr, _, dat, src in messages:
                    if src != CAN_BUS:
                        continue
                    frames_total += 1
                    if addr == ID_FOLLOW_DISTANCE:
                        handle_follow_distance(dat)
                    elif addr == ID_AUTOPILOT_CMD:
                        handle_autopilot_cmd(panda, dat)
                if time.time() - last_status_print >= 5:
                    print_status()
                    last_status_print = time.time()

    except KeyboardInterrupt:
        print("\n\n  Stopped by user.")
        print_status()

    finally:
        stop_screen_integration()
        if panda is not None and TRANSMIT:
            panda.set_safety_mode(Panda.SAFETY_SILENT)
            print("  Panda → SILENT mode. Safe to restart openpilot:")
            print("  sudo systemctl start openpilot\n")
        print("  👋 Bye!\n")


if __name__ == "__main__":
    main()
