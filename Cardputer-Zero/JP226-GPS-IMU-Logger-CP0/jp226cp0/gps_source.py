"""Where the NMEA lines come from: a serial port (auto-detected) or a file.

HARDWARE ASSUMPTION (see hardware.py): which serial device and baud the GPS is
on is unknown until the CardputerZero arrives, so detection tries the
configured device first, then every device in hardware.GPS_SERIAL_CANDIDATES
that exists, at each baud in hardware.GPS_BAUD_CANDIDATES, and keeps the first
(device, baud) that yields checksum-valid NMEA sentences. The detection logic
takes injected open/exists functions so it is unit-tested with a fake serial
port on a desktop; the real pyserial path is unverified until hardware.
"""

import os
import queue
import threading
import time
from typing import Callable, List, Optional, Tuple

from . import config, hardware
from .nmea import checksum


def valid_nmea_line(line: str) -> bool:
    """True for '$....*HH' with a correct checksum (any talker/type)."""
    line = line.strip()
    if not line.startswith("$"):
        return False
    star = line.rfind("*")
    if star < 0 or len(line) < star + 3:
        return False
    try:
        given = int(line[star + 1:star + 3], 16)
    except ValueError:
        return False
    return checksum(line[1:star]) == given


def count_valid_sentences(data: bytes) -> int:
    text = data.decode("ascii", errors="ignore")
    return sum(1 for line in text.splitlines() if valid_nmea_line(line))


def candidate_devices(configured: str = "",
                      candidates=hardware.GPS_SERIAL_CANDIDATES,
                      exists: Callable[[str], bool] = os.path.exists,
                      realpath: Callable[[str], str] = os.path.realpath
                      ) -> List[str]:
    """Configured device first (even if it does not exist yet), then the
    existing candidates, without aliases of the same serial device."""
    result: List[str] = []
    seen = set()
    if configured:
        result.append(configured)
        seen.add(realpath(configured))
    for dev in candidates:
        canonical = realpath(dev)
        if canonical not in seen and exists(dev):
            result.append(dev)
            seen.add(canonical)
    return result


def candidate_bauds(configured: int = 0,
                    candidates=hardware.GPS_BAUD_CANDIDATES) -> List[int]:
    if configured:
        return [int(configured)]
    return list(candidates)


def probe(open_fn: Callable[[str, int], object], device: str, baud: int,
          listen_s: float, min_sentences: int,
          time_fn: Callable[[], float] = time.monotonic) -> bool:
    """Listen on device@baud; True if >= min_sentences valid sentences."""
    try:
        port = open_fn(device, baud)
    except Exception:
        return False
    try:
        data = b""
        deadline = time_fn() + listen_s
        while time_fn() < deadline:
            chunk = port.read(512)
            if chunk:
                data += chunk
                if count_valid_sentences(data) >= min_sentences:
                    return True
            else:
                time.sleep(0.01)
        return count_valid_sentences(data) >= min_sentences
    except Exception:
        return False
    finally:
        try:
            port.close()
        except Exception:
            pass


def detect_gps(open_fn: Callable[[str, int], object],
               configured_device: str = "", configured_baud: int = 0,
               baud_candidates=hardware.GPS_BAUD_CANDIDATES,
               listen_s: float = hardware.GPS_PROBE_LISTEN_SEC,
               min_sentences: int = hardware.GPS_PROBE_MIN_SENTENCES,
               exists: Callable[[str], bool] = os.path.exists,
               on_try: Optional[Callable[[str, int], None]] = None,
               stop: Optional[Callable[[], bool]] = None
               ) -> Optional[Tuple[str, int]]:
    for device in candidate_devices(configured_device, exists=exists):
        for baud in candidate_bauds(configured_baud, baud_candidates):
            if stop is not None and stop():
                return None
            if on_try is not None:
                on_try(device, baud)
            if probe(open_fn, device, baud, listen_s, min_sentences):
                return device, baud
    return None


def open_pyserial(device: str, baud: int):
    """Real serial port. pyserial is an optional dependency. [UNVERIFIED on
    the CardputerZero]"""
    import serial  # noqa: WPS433 (optional import)
    return serial.Serial(device, baud, timeout=0.1)


class SerialGpsReader(threading.Thread):
    """Background thread: detect the port, read lines, queue (now_ms, line).

    If no checksum-valid sentence arrives for config.GPS_SOURCE_STALE_MS the
    port is closed and detection starts again (like the firmware's source
    failover), unless both device and baud are configured, in which case it
    just reopens that port.
    """

    def __init__(self, now_ms_fn: Callable[[], int], configured_device: str = "",
                 configured_baud: int = 0,
                 open_fn: Callable[[str, int], object] = open_pyserial,
                 exists: Callable[[str], bool] = os.path.exists,
                 baud_candidates=hardware.GPS_BAUD_CANDIDATES):
        super().__init__(name="gps-reader", daemon=True)
        self.lines: "queue.Queue[Tuple[int, str]]" = queue.Queue(maxsize=2000)
        self.now_ms_fn = now_ms_fn
        self.configured_device = configured_device
        self.configured_baud = configured_baud
        self.open_fn = open_fn
        self.exists = exists
        self.baud_candidates = tuple(baud_candidates)
        self.device = ""
        self.baud = 0
        self.status = "STARTING"
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def _set_trying(self, device: str, baud: int) -> None:
        self.status = "SCAN {}@{}".format(os.path.basename(device), baud)

    def run(self) -> None:
        try:
            import serial  # noqa: F401
        except ImportError:
            if self.open_fn is open_pyserial:
                self.status = "NO PYSERIAL"
                return
        while not self._stop_event.is_set():
            if self.configured_device and self.configured_baud:
                found = (self.configured_device, self.configured_baud)
            else:
                found = detect_gps(self.open_fn, self.configured_device,
                                   self.configured_baud,
                                   baud_candidates=self.baud_candidates,
                                   exists=self.exists,
                                   on_try=self._set_trying,
                                   stop=self._stop_event.is_set)
            if found is None:
                self.status = "NO GPS FOUND"
                self._stop_event.wait(2.0)
                continue
            self.device, self.baud = found
            self._read(*found)

    def _read(self, device: str, baud: int) -> None:
        try:
            port = self.open_fn(device, baud)
        except Exception as exc:
            self.status = "OPEN FAIL {}".format(exc)
            self._stop_event.wait(2.0)
            return
        self.status = "OK {}@{}".format(os.path.basename(device), baud)
        buffer = b""
        last_valid = time.monotonic()
        try:
            while not self._stop_event.is_set():
                chunk = port.read(256)
                if chunk:
                    buffer += chunk
                    while b"\n" in buffer:
                        raw, buffer = buffer.split(b"\n", 1)
                        line = raw.decode("ascii", errors="ignore").strip()
                        if valid_nmea_line(line):
                            last_valid = time.monotonic()
                        if line:
                            try:
                                self.lines.put_nowait((self.now_ms_fn(), line))
                            except queue.Full:
                                pass
                    buffer = buffer[-512:]
                else:
                    time.sleep(0.01)
                if (time.monotonic() - last_valid) * 1000 > config.GPS_SOURCE_STALE_MS:
                    self.status = "STALE"
                    return
        except Exception as exc:
            self.status = "READ FAIL {}".format(exc)
        finally:
            try:
                port.close()
            except Exception:
                pass
