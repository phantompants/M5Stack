"""System monitor for the FOSSIL RECORD page: CPU, temperature, memory,
storage, battery and uptime, from Linux /proc and /sys files. Standard library
only; every reading is None where its file does not exist (desktop, tests).

Checked on the CardputerZero 2026-11-26: the battery gauge is a BQ27220 at
/sys/class/power_supply/bq27220-0 and the SoC temperature is thermal_zone0.
"""

import os
import posixpath
import shutil
from typing import Callable, Dict, Optional, Tuple

POWER_SUPPLY_ROOT = "/sys/class/power_supply"
THERMAL_FILE = "/sys/class/thermal/thermal_zone0/temp"
THROTTLED_FILE = "/sys/devices/platform/soc/soc:firmware/get_throttled"


def _read(path: str, reader: Callable[[str], str]) -> Optional[str]:
    try:
        return reader(path).strip()
    except (OSError, ValueError):
        return None


def _read_file(path: str) -> str:
    with open(path, encoding="ascii", errors="replace") as handle:
        return handle.read()


def cpu_times(stat_text: str) -> Optional[Tuple[int, int]]:
    """(busy, total) jiffies from the first line of /proc/stat."""
    for line in stat_text.splitlines():
        if line.startswith("cpu "):
            values = [int(v) for v in line.split()[1:]]
            idle = values[3] + (values[4] if len(values) > 4 else 0)
            total = sum(values[:8])
            return total - idle, total
    return None


def memory_used(meminfo_text: str) -> Optional[Tuple[float, int]]:
    """(used %, available MB) from /proc/meminfo."""
    values = {}
    for line in meminfo_text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] in ("MemTotal:", "MemAvailable:"):
            values[parts[0]] = int(parts[1])
    total = values.get("MemTotal:")
    available = values.get("MemAvailable:")
    if not total or available is None:
        return None
    return 100.0 * (total - available) / total, available // 1024


class SystemMonitor:
    def __init__(self, data_dir: str = "/",
                 reader: Callable[[str], str] = _read_file,
                 listdir: Callable[[str], list] = os.listdir,
                 disk_usage=shutil.disk_usage):
        self.data_dir = data_dir
        self._reader = reader
        self._listdir = listdir
        self._disk_usage = disk_usage
        self._last_cpu: Optional[Tuple[int, int]] = None
        self.values: Dict = self._empty()

    @staticmethod
    def _empty() -> Dict:
        return {"cpu_pct": None, "temp_c": None, "mem_pct": None,
                "mem_free_mb": None, "disk_free_gb": None, "disk_pct": None,
                "battery_pct": None, "battery_mv": None, "battery_status": "",
                "uptime_s": None, "load1": None, "throttled": None}

    def sample(self) -> Dict:
        v = self._empty()
        stat = _read("/proc/stat", self._reader)
        times = cpu_times(stat) if stat else None
        if times and self._last_cpu:
            busy = times[0] - self._last_cpu[0]
            total = times[1] - self._last_cpu[1]
            if total > 0:
                v["cpu_pct"] = max(0.0, min(100.0, 100.0 * busy / total))
        elif self.values.get("cpu_pct") is not None:
            v["cpu_pct"] = self.values["cpu_pct"]
        self._last_cpu = times or self._last_cpu
        temp = _read(THERMAL_FILE, self._reader)
        if temp and temp.lstrip("-").isdigit():
            v["temp_c"] = int(temp) / 1000.0
        meminfo = _read("/proc/meminfo", self._reader)
        memory = memory_used(meminfo) if meminfo else None
        if memory:
            v["mem_pct"], v["mem_free_mb"] = memory
        try:
            usage = self._disk_usage(self.data_dir)
            v["disk_free_gb"] = usage.free / 1e9
            v["disk_pct"] = (100.0 * (usage.total - usage.free) / usage.total
                             if usage.total else None)
        except OSError:
            pass
        uptime = _read("/proc/uptime", self._reader)
        if uptime:
            try:
                v["uptime_s"] = int(float(uptime.split()[0]))
            except (ValueError, IndexError):
                pass
        load = _read("/proc/loadavg", self._reader)
        if load:
            try:
                v["load1"] = float(load.split()[0])
            except (ValueError, IndexError):
                pass
        throttled = _read(THROTTLED_FILE, self._reader)
        if throttled:
            try:
                v["throttled"] = int(throttled, 16)
            except ValueError:
                pass
        self._battery(v)
        self.values = v
        return v

    def _battery(self, v: Dict) -> None:
        try:
            names = sorted(self._listdir(POWER_SUPPLY_ROOT))
        except OSError:
            return
        for name in names:
            base = posixpath.join(POWER_SUPPLY_ROOT, name)
            if _read(posixpath.join(base, "type"), self._reader) != "Battery":
                continue
            capacity = _read(posixpath.join(base, "capacity"), self._reader)
            voltage = _read(posixpath.join(base, "voltage_now"), self._reader)
            if capacity and capacity.isdigit():
                v["battery_pct"] = int(capacity)
            if voltage and voltage.isdigit():
                v["battery_mv"] = int(voltage) // 1000      # reported in uV
            v["battery_status"] = (_read(posixpath.join(base, "status"),
                                         self._reader) or "").upper()
            return


def uptime_text(seconds: Optional[int]) -> str:
    if seconds is None:
        return "--"
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return "{}d {}h".format(days, hours)
    return "{}h {:02d}m".format(hours, minutes)
