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

  The HUD thread can use cereal while openpilot runs, but this script must
  open the panda for CAN — openpilot’s pandad (and legacy boardd) own it while
  those processes run, which causes USB BUSY and endless “CAN: BAD RECV”.
  Stop openpilot and ensure no stray pandad: sudo systemctl stop openpilot
  then sudo killall -KILL pandad boardd

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

import os
import sys
import time
import threading
import struct
import random
import subprocess

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
# Panda / opendbc (safety modes moved to CarParams on current comma software)
# ──────────────────────────────────────────────────────────────────────────────
def _panda_safety_modes(Panda):
    try:
        from opendbc.car.structs import CarParams
        return CarParams.SafetyModel.allOutput, CarParams.SafetyModel.silent
    except ImportError:
        pass
    al = getattr(Panda, "SAFETY_ALLOUTPUT", None)
    sil = getattr(Panda, "SAFETY_SILENT", None)
    if al is not None and sil is not None:
        return al, sil
    raise RuntimeError(
        "Cannot resolve panda safety modes: install opendbc (CarParams.SafetyModel) "
        "or use a panda build that defines Panda.SAFETY_ALLOUTPUT / SAFETY_SILENT."
    )


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "yes", "true")


def _panda_spi_only_connect(Panda, serial: str):
    """
    Force SPI by making usb_connect return no handle so Panda.connect() calls spi_connect.
    """
    orig = Panda.usb_connect

    @classmethod
    def _usb_skipped(cls, _serial, claim=True, no_error=False):
        return None, None, None, False

    try:
        Panda.usb_connect = _usb_skipped
        return Panda(serial=serial, claim=True, cli=False)
    finally:
        Panda.usb_connect = orig


def _prefer_spi_first() -> bool:
    """Internal comma pandas use SPI; broken USB partial-open skips SPI and breaks can_recv."""
    if _env_truthy("FSD_PANDA_USB_FIRST"):
        return False
    if _env_truthy("FSD_PANDA_SPI_ONLY") or _env_truthy("FSD_PANDA_SPI_FIRST"):
        return True
    try:
        return os.path.exists("/AGNOS")
    except OSError:
        return False


def _open_panda(Panda):
    serials = Panda.list()
    if not serials:
        raise RuntimeError("No panda found (USB/SPI list empty)")
    serial = serials[0]
    last_err: Exception | None = None

    attempts: list[tuple[str, object]] = []
    if _env_truthy("FSD_PANDA_SPI_ONLY"):
        attempts.append(("spi-only", None))
    elif _prefer_spi_first():
        attempts.extend(
            [
                ("spi-only", None),
                ("usb+claim", True),
                ("usb no-claim", False),
            ]
        )
    else:
        attempts.extend(
            [
                ("usb+claim", True),
                ("usb no-claim", False),
                ("spi-only", None),
            ]
        )

    for label, claim in attempts:
        for _ in range(6):
            try:
                if claim is None:
                    p = _panda_spi_only_connect(Panda, serial)
                else:
                    p = Panda(serial=serial, claim=claim, cli=False)
                link = "SPI" if p.is_connected_spi() else "USB"
                print(f"  Panda link: {link} ({label})")
                return p
            except Exception as e:
                last_err = e
                time.sleep(0.35)
    if last_err:
        raise last_err
    raise RuntimeError("Panda connect failed")


def _unpack_can_msg(msg):
    if len(msg) == 3:
        return msg[0], msg[1], msg[2]
    addr, _, dat, src = msg
    return addr, dat, src


def _read_proc_cmdline(pid: str) -> list[str]:
    try:
        with open(os.path.join("/proc", pid, "cmdline"), "rb") as f:
            parts = f.read().split(b"\0")
        return [p.decode("utf-8", "replace") for p in parts if p]
    except OSError:
        return []


def _cmdline_looks_like_openpilot_panda_holder(argv: list[str]) -> bool:
    """
    True only for real pandad / boardd / manager processes.
    pgrep -f '.../manager.py' falsely matches vim/nano/cat with that path in argv.
    """
    if not argv:
        return False
    joined = " ".join(argv)
    low = joined.lower()
    if "tesla_fsd_comma3" in low or "fsd_toggle_server" in low:
        return False

    base = os.path.basename(argv[0]).lower()
    non_holder = frozenset({
        "vim", "nvim", "vi", "view", "less", "more", "cat", "nano", "emacs",
        "grep", "rg", "ag", "head", "tail", "sed", "awk", "bash", "sh", "zsh",
        "ssh", "scp", "curl", "wget",
    })
    if base in non_holder:
        return False

    if base in ("pandad", "boardd"):
        return True

    is_py = base.startswith("python") or base == "python3" or "python" in base
    if not is_py:
        return False

    norm = joined.replace("\\", "/")
    if "selfdrive.pandad.pandad" in joined or "/selfdrive/pandad/pandad" in norm:
        return True
    if "pandad.py" in norm and "selfdrive" in low:
        return True
    if "boardd" in low and ("system/boardd" in norm or "/boardd/" in norm):
        return True
    if "manager.py" in norm and "/manager/" in norm and "openpilot" in low:
        return True
    if "manager.py" in norm and "/system/manager" in norm:
        return True
    return False


def _panda_holder_pids() -> list[str]:
    me, parent = str(os.getpid()), str(os.getppid())
    out: list[str] = []
    try:
        names = os.listdir("/proc")
    except OSError:
        return out
    for name in names:
        if not name.isdigit() or name in (me, parent):
            continue
        if _cmdline_looks_like_openpilot_panda_holder(_read_proc_cmdline(name)):
            out.append(name)
    return sorted(out, key=int)


def _assert_no_panda_holders():
    """pandad/boardd/manager own the panda; a second client gets USB BUSY and BAD RECV."""
    if os.environ.get("FSD_SKIP_HOLDER_CHECK", "").strip().lower() in ("1", "yes", "true"):
        print("  [WARN] FSD_SKIP_HOLDER_CHECK set — skipping panda holder scan.\n")
        return

    try:
        r = subprocess.run(
            ["systemctl", "is-active", "openpilot"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        op_active = r.stdout.strip() == "active"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        op_active = False

    holders = _panda_holder_pids()
    if not op_active and not holders:
        return

    lines = [
        "\n  [ERROR] Something is still using the comma panda (USB will stay BUSY, CAN recv breaks).",
        "",
    ]
    if op_active:
        lines.append("  • systemd reports openpilot active (if you use another launcher, ignore):")
        lines.append("      sudo systemctl stop openpilot")
        lines.append("")
    if holders:
        lines.append("  • suspected holder process(es):")
        native_pandad = False
        for pid in holders:
            argv = _read_proc_cmdline(pid)
            j = " ".join(argv)
            snippet = j[:120] + ("…" if len(j) > 120 else "")
            lines.append(f"      PID {pid}: {snippet}")
            if argv and os.path.basename(argv[0]) == "pandad":
                native_pandad = True
        lines.append("")
        lines.append("      sudo killall -KILL pandad")
        lines.append("      sudo killall -KILL boardd    # ok if: no process found")
        if native_pandad:
            lines.append("")
            lines.append("    Native ./pandad owns the panda USB handle; without killing it you get BUSY / BAD RECV.")
        lines.append("")
        lines.append("    If pandad comes back immediately, the Python wrapper or manager is still running:")
        lines.append("      sudo pkill -f 'selfdrive.pandad.pandad'")
        lines.append("    Or stop openpilot in tmux (Ctrl+C) / your usual launcher, then retry.")
        lines.append("")
    lines.append("  False alarm?  FSD_SKIP_HOLDER_CHECK=1 python3 ...")
    lines.append("  Or stop tmux/SSH sessions still running openpilot, then retry.\n")
    print("\n".join(lines))
    sys.exit(1)


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
    safety_silent = None

    if not DUMMY_MODE:
        _assert_no_panda_holders()
        try:
            from panda import Panda

            safety_all_output, safety_silent = _panda_safety_modes(Panda)
            panda = _open_panda(Panda)

            print(f"  Panda FW: {panda.get_version()}")
            if TRANSMIT:
                panda.set_safety_mode(safety_all_output)
                panda.set_can_speed_kbps(CAN_BUS, 500)
                print("  Safety: ALLOUTPUT — openpilot driving DISABLED while running")
            else:
                print("  Safety: default — openpilot can run alongside (monitor only)")
        except Exception as e:
            print(f"\n  [ERROR] Panda connect failed: {e}")
            print("  Stop openpilot and kill stray daemons: sudo systemctl stop openpilot")
            print("    sudo killall -KILL pandad boardd")
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
                for msg in messages:
                    addr, dat, src = _unpack_can_msg(msg)
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
        if panda is not None and TRANSMIT and safety_silent is not None:
            panda.set_safety_mode(safety_silent)
            print("  Panda → SILENT mode. Safe to restart openpilot:")
            print("  sudo systemctl start openpilot\n")
        print("  👋 Bye!\n")


if __name__ == "__main__":
    main()
