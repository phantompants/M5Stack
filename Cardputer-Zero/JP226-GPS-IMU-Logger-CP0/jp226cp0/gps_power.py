"""GPS receiver choice and its power rails on the CardputerZero.

Both GPS options share CM0 GPIO14/15 (UART, /dev/ttyS0 via /dev/serial0):

  CAP    Cap LoRa-1262 GPS on the EXT bus. Powered by EXT 5V
         (M5IOE1 pin 12, LED-class ``ext_5v_out``). 115200 baud.
  GROVE  A Grove GPS unit on the Zero's own HY2.0-4P port, with no Cap or a
         Cap that leaves GPIO15 alone (not the NFC/Sub-1GHz Cap, see ATOM). Powered by Grove 5V
         (M5IOE1 PYG4, ``grove_5v_out``); GPIO4 ``grove_fun`` LOW routes the
         port to UART (HIGH = I2C1). Grove GPS units usually start at 9600 baud.

  ATOM   The AtomS3 USB bridge on an Atomic GPS Base (bridge.py), plugged
         into a Zero USB port. It does not use the shared UART, so this is the
         choice for the NFC/Sub-1GHz Cap: verified on the device 2026-11-26
         that the CC1101 drives a ~130 kHz clock onto GPIO15 (UART RX), which
         stops every UART GPS, and that the Cap's own Grove socket goes to
         GPIO3 (I2C1 SCL), not to the UART. USB powers the AtomS3; the app
         only switches Grove 5V off.

Verified on the device (2026-11-25): with ``ext_5v_out`` off the Cap GPS sends
nothing; on, it sends NMEA at 115200. The LED files are group ``gpio``
writable, so the app does not need root. Only one receiver may drive the
shared RX line, so each choice switches the other receiver's power off.
"""

import os
from typing import Callable, Dict, Optional, Tuple

LED_ROOT = "/sys/class/leds"

CAP = "cap"
GROVE = "grove"
ATOM = "atom"
SOURCES = (CAP, GROVE, ATOM)
LABELS = {CAP: "LORA CAP", GROVE: "GROVE", ATOM: "ATOMS3 USB"}

# LED name -> value, applied in order (route the port before powering it).
_RAILS: Dict[str, Tuple[Tuple[str, int], ...]] = {
    CAP: (("grove_5v_out", 0), ("ext_5v_out", 1)),
    GROVE: (("ext_5v_out", 0), ("grove_fun", 0), ("grove_5v_out", 1)),
    ATOM: (("grove_5v_out", 0),),
}

# Baud rates to try first for each receiver; the rest follow in the usual order.
PREFERRED_BAUD = {CAP: 115200, GROVE: 9600, ATOM: 115200}


def normalise(value: Optional[str], fallback: str = CAP) -> str:
    text = str(value or "").strip().lower()
    if text in ("cap", "lora", "lora_cap", "lora-1262"):
        return CAP
    if text in ("grove", "grove_gps"):
        return GROVE
    # The NFC/Sub-1GHz Cap blocks the UART, so it needs the AtomS3.
    if text in ("atom", "atoms3", "bridge", "usb", "nfc", "subghz", "cc1101"):
        return ATOM
    return fallback


def next_source(source: str) -> str:
    return SOURCES[(SOURCES.index(normalise(source)) + 1) % len(SOURCES)]


def baud_order(source: str, candidates) -> Tuple[int, ...]:
    first = PREFERRED_BAUD[normalise(source)]
    return (first,) + tuple(b for b in candidates if b != first)


def env_rails(enabled: bool, source: str) -> Tuple[Tuple[str, int], ...]:
    """Grove port for an ENV unit: I2C mode and Grove 5V on. Never with a
    Grove GPS (it would talk 9600 baud onto the system I2C bus)."""
    if normalise(source) == GROVE:
        return ()
    if enabled:
        return (("grove_fun", 1), ("grove_5v_out", 1))
    return (("grove_5v_out", 0),)


def apply_env(enabled: bool, source: str, led_root: str = LED_ROOT,
              write: Callable[[str, int], None] = None,
              exists: Callable[[str], bool] = os.path.exists) -> str:
    write = write or _write
    if normalise(source) == GROVE and enabled:
        return "GROVE GPS IN USE"
    for name, value in env_rails(enabled, source):
        path = os.path.join(led_root, name, "brightness")
        if not exists(path):
            return "NO " + name.upper()
        try:
            write(path, value)
        except PermissionError:
            return "NO PERMISSION"
        except OSError as exc:
            return "POWER ERROR {}".format(exc.errno)
    return "ON" if enabled else "OFF"


def _write(path: str, value: int) -> None:
    with open(path, "w", encoding="ascii") as handle:
        handle.write(str(value))


def apply(source: str, led_root: str = LED_ROOT,
          write: Callable[[str, int], None] = _write,
          exists: Callable[[str], bool] = os.path.exists) -> str:
    """Switch the rails for this source. Returns a short status for the UI."""
    source = normalise(source)
    if not exists(led_root):
        return "NO POWER CONTROL"
    missing = []
    for name, value in _RAILS[source]:
        path = os.path.join(led_root, name, "brightness")
        if not exists(path):
            missing.append(name)
            continue
        try:
            write(path, value)
        except PermissionError:
            return "POWER: NO PERMISSION"
        except OSError as exc:
            return "POWER ERROR {}".format(exc.errno)
    if missing:
        return "POWER: NO " + missing[0].upper()
    return "USB POWER" if source == ATOM else "POWER ON"
