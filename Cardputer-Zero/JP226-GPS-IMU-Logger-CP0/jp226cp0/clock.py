"""GPS-disciplined UTC clock that never steps backwards.

The firmware sets the system clock from GPS (settimeofday) and keeps CSV
timestamps monotonic. A Linux app normally cannot set the system clock, so this
keeps its own offset between the process's monotonic milliseconds and UTC:

  * GPS time (when valid) re-synchronises it at most every
    config.CLOCK_RESYNC_INTERVAL_MS (the first sync is immediate);
  * until GPS time has been seen, source "auto"/"system" use the system clock
    if its year looks sane (>= 2024); source "gps" never does;
  * now_utc_ms() never returns less than a value already handed out, because
    a resync can step the clock back a little and CSV rows must stay in order.
"""

import time
from typing import Callable, Optional

from . import config

SOURCES = ("auto", "gps", "system")


class Clock:
    def __init__(self, source: str = "auto",
                 system_time_fn: Callable[[], float] = time.time):
        if source not in SOURCES:
            raise ValueError("clock source must be one of {}".format(SOURCES))
        self.source = source
        self._system_time_fn = system_time_fn
        self._offset_ms: Optional[int] = None   # utc_ms - mono_ms from GPS
        self._last_sync_ms: Optional[int] = None
        self._last_handed_ms = 0

    # ------------------------------------------------------------------
    @property
    def gps_synced(self) -> bool:
        return self._offset_ms is not None

    def _system_ms(self) -> Optional[int]:
        if self.source == "gps":
            return None
        ms = int(self._system_time_fn() * 1000)
        # clockIsReady(): year >= 2024. 1704067200 = 2024-01-01T00:00:00Z.
        return ms if ms >= 1_704_067_200_000 else None

    def ready(self, now_ms: int) -> bool:
        return self._raw_utc_ms(now_ms) is not None

    def _raw_utc_ms(self, now_ms: int) -> Optional[int]:
        if self._offset_ms is not None:
            return now_ms + self._offset_ms
        return self._system_ms()

    # ------------------------------------------------------------------
    def sync_from_gps(self, utc_valid: bool, utc_epoch_ms: int,
                      now_ms: int) -> bool:
        """Port of syncClockFromTelemetry(). Returns True if it resynced."""
        if self.source == "system":
            return False
        if not utc_valid or utc_epoch_ms <= 0:
            return False
        if (self._offset_ms is not None and self._last_sync_ms is not None and
                now_ms - self._last_sync_ms < config.CLOCK_RESYNC_INTERVAL_MS):
            return False
        self._offset_ms = int(utc_epoch_ms) - int(now_ms)
        self._last_sync_ms = now_ms
        return True

    def now_utc_ms(self, now_ms: int) -> Optional[int]:
        """Current UTC in ms, monotonic non-decreasing, or None if not ready."""
        raw = self._raw_utc_ms(now_ms)
        if raw is None:
            return None
        if raw < self._last_handed_ms:
            raw = self._last_handed_ms
        self._last_handed_ms = raw
        return raw
