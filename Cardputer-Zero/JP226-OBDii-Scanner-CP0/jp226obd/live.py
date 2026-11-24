"""Live OBD-II telemetry polling loop for verifying responsive signals."""

import threading
import time
from typing import Callable, Dict, List, Optional

from .elm327 import Elm327, classify
from .obd_pids import STANDARD_PIDS, decode_multi_pid, decode_pid

DEFAULT_LIVE_PIDS = (0x0D, 0x0C, 0x49, 0x11, 0x62, 0x04, 0x5B, 0x42, 0x05, 0x46)


class LiveReader:
    """Polls selected Mode 01 PIDs continuously and measures live update rate."""

    def __init__(
        self,
        elm: Elm327,
        pids: List[int],
        multi_pid: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.elm = elm
        self.pids = [p for p in pids if p in STANDARD_PIDS] or list(DEFAULT_LIVE_PIDS)
        self.multi_pid = multi_pid and len(self.pids) >= 2
        self.clock = clock
        self._lock = threading.Lock()
        self.values: Dict[str, object] = {}
        self.hz: float = 0.0
        self.latency_ms: float = 0.0
        self.error: str = ""
        self.samples: int = 0

    def poll_once(self) -> None:
        if self.multi_pid:
            # ELM327 / CAN allows up to 6 PIDs per Mode 01 request.
            for idx in range(0, len(self.pids), 6):
                chunk = self.pids[idx:idx + 6]
                t0 = self.clock()
                lines = self.elm.command(
                    "01" + "".join("{:02X}".format(p) for p in chunk), 1.5
                )
                dt = max(0.001, self.clock() - t0)
                res = classify(lines, 0x01)
                if res["kind"] == "error":
                    with self._lock:
                        self.error = " ".join(lines)[:40]
                    continue
                updated = {}
                for reply in res["replies"].values():
                    if reply["kind"] == "positive":
                        decoded = decode_multi_pid(bytes.fromhex(reply["data"]))
                        for pid, val in decoded.items():
                            updated[STANDARD_PIDS[pid].short] = val
                with self._lock:
                    self.values.update(updated)
                    self.latency_ms = round(dt * 1000.0, 1)
                    self.hz = round(1.0 / dt, 1)
                    self.samples += 1
                    self.error = ""
            return

        t0 = self.clock()
        for pid in self.pids:
            lines = self.elm.command("01{:02X}".format(pid), 1.0)
            res = classify(lines, 0x01)
            for reply in res["replies"].values():
                if reply["kind"] == "positive":
                    raw = bytes.fromhex(reply["data"])
                    if raw and raw[0] == pid:
                        val = decode_pid(pid, raw[1:])
                        if val is not None:
                            with self._lock:
                                self.values[STANDARD_PIDS[pid].short] = val
                    break
        dt = max(0.001, self.clock() - t0)
        with self._lock:
            self.latency_ms = round(dt * 1000.0, 1)
            self.hz = round(1.0 / dt, 1)
            self.samples += 1

    def snapshot(self) -> Dict[str, object]:
        with self._lock:
            return {
                "values": dict(self.values),
                "hz": self.hz,
                "latency_ms": self.latency_ms,
                "samples": self.samples,
                "error": self.error,
            }
