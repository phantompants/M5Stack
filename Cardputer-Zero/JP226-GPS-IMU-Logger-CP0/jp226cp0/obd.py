"""EXTENSION POINT: OBD-II (future module). Intentionally NOT implemented.

In the ESP32 system OBD data comes from a separate logger (cardputer_obd)
writing its own obd_YYYY-MM-DD.csv, and tools/merge_obd.py joins it to the
telemetry CSV by timestamp. A future version of this app can keep that
arrangement (write obd_*.csv next to telemetry_*.csv, same UTC timestamps) or
feed values into the main log. The telemetry CSV header is fixed and has no OBD
columns, so nothing here touches it.

An implementation would subclass ObdSource, talk to an ELM327-style adapter
(serial/Bluetooth, hardware not chosen yet) and be passed to LoggerApp(obd=...).
LoggerApp only calls update() and ignores the result for now.
"""

from typing import Dict


class ObdSource:
    """Interface; subclass and override."""

    available = False

    def update(self, now_ms: int) -> None:
        """Poll the adapter. Called from the main loop."""

    def latest(self) -> Dict[str, float]:
        """Latest decoded values by name; empty until implemented."""
        return {}


class NullObd(ObdSource):
    pass
