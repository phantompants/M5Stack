"""GPS receiver choice and its power rails on the CardputerZero.

Both GPS options share CM0 GPIO14/15 (UART, /dev/ttyS0 via /dev/serial0):

  CAP    Cap LoRa-1262 GPS on the EXT bus. Powered by EXT 5V
         (M5IOE1 pin 12, LED-class ``ext_5v_out``). 115200 baud.
  GROVE  A Grove GPS unit on the HY2.0-4P port, e.g. with the NFC/Sub-1GHz
         (CC1101) Cap fitted instead of the GPS Cap. Powered by Grove 5V
         (M5IOE1 PYG4, ``grove_5v_out``); GPIO4 ``grove_fun`` LOW routes the
         port to UART (HIGH = I2C1). Grove GPS units usually start at 9600 baud.

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
SOURCES = (CAP, GROVE)
LABELS = {CAP: "LORA CAP", GROVE: "GROVE"}

# LED name -> value, applied in order (route the port before powering it).
_RAILS: Dict[str, Tuple[Tuple[str, int], ...]] = {
    CAP: (("grove_5v_out", 0), ("ext_5v_out", 1)),
    GROVE: (("ext_5v_out", 0), ("grove_fun", 0), ("grove_5v_out", 1)),
}

# Baud rates to try first for each receiver; the rest follow in the usual order.
PREFERRED_BAUD = {CAP: 115200, GROVE: 9600}


def normalise(value: Optional[str], fallback: str = CAP) -> str:
    text = str(value or "").strip().lower()
    if text in ("cap", "lora", "lora_cap", "lora-1262"):
        return CAP
    if text in ("grove", "grove_gps", "nfc", "subghz", "cc1101"):
        return GROVE
    return fallback


def next_source(source: str) -> str:
    return SOURCES[(SOURCES.index(normalise(source)) + 1) % len(SOURCES)]


def baud_order(source: str, candidates) -> Tuple[int, ...]:
    first = PREFERRED_BAUD[normalise(source)]
    return (first,) + tuple(b for b in candidates if b != first)


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
    return "POWER ON"
