"""Read-only OBD-II / UDS scanner, latency benchmarker, and profile exporter.

Supports USB serial, Wi-Fi TCP, BLE GATT, and simulated Demo adapters.
Safety: only adapter AT/ST commands and read-only OBD services 01, 03, 07, 09,
and 22 are ever sent. Any write/clear/reset/security service is refused.
"""

import argparse
import datetime
import json
import os
import re
import sys
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from . import compatibility, elm327
from .elm327 import Elm327, classify
from .obd_pids import (
    COMMON_TARGET_MODULES,
    FORD_TPMS_DIDS,
    STANDARD_PIDS,
    decode_dtcs,
    decode_mode09_text,
    decode_multi_pid,
    decode_pid,
    infer_did_summary,
)

READ_ONLY_SERVICES = (0x01, 0x03, 0x07, 0x09, 0x22)

LINK_CHOICES = ("usb", "wifi", "ble", "demo")
SCAN_MODES = ("quick", "ev_probe", "full")
SCAN_MODE_LABELS = {
    "quick": "QUICK + FIT",
    "ev_probe": "EV + MODULES",
    "full": "FULL DISCOVERY",
}
PROTOCOL_LABELS = {
    "0": "AUTO",
    "6": "CAN 11/500",
    "7": "CAN 29/500",
    "8": "CAN 11/250",
    "9": "CAN 29/250",
    "1": "J1850 PWM",
    "2": "J1850 VPW",
    "3": "ISO 9141-2",
    "4": "KWP 5-BAUD",
    "5": "KWP FAST",
    "A": "J1939 29/250",
}

EVERYTHING_DID_RANGES = "0000-FFFF"

DEFAULTS = {
    "link": "usb",
    "device": "",             # usb: blank = first USB serial adapter
    "baud": "0",              # usb: 0 = auto-probe (115200, 38400, 500000, 9600)
    "host": "192.168.0.10",   # wifi
    "port": "35000",          # wifi
    "ble_name": "",           # ble: name substring or MAC; blank = auto OBD match
    "demo_profile": "ranger", # demo: ranger | ev | hybrid
    "protocol": "0",          # 0 = AUTO
    "scan_mode": "quick",     # quick | ev_probe | full
    "steps": "",              # optional explicit step list override
    "modules": "700-7F7",
    "did_ranges": "F100-F1FF,DD00-DDFF,2800-28FF,4000-40FF,0100-01FF,2100-21FF",
    "module_timeout_ms": "100",
    "per_module_minutes": "8",
    "total_minutes": "60",
    "benchmark_samples": "8",
    "out_dir": "",
}


def steps_for_mode(mode: str) -> List[str]:
    if mode == "full":
        return ["adapter", "standard", "dtcs", "modules", "dids", "benchmark"]
    if mode == "ev_probe":
        return ["adapter", "standard", "dtcs", "ev_modules", "benchmark"]
    return ["adapter", "standard", "dtcs", "target_dids", "benchmark"]


def load_config(path: Optional[str]) -> Dict[str, str]:
    cfg = dict(DEFAULTS)
    if path:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.split("#", 1)[0].strip()
                if "=" not in line:
                    continue
                key, value = (s.strip() for s in line.split("=", 1))
                if key in cfg:
                    cfg[key] = value
    if cfg["did_ranges"].lower() == "everything":
        cfg["did_ranges"] = EVERYTHING_DID_RANGES
    return cfg


def parse_ranges(text: str) -> List[Tuple[int, int]]:
    out: List[Tuple[int, int]] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        lo, _, hi = part.partition("-")
        lo_v = int(lo, 16)
        hi_v = int(hi, 16) if hi else lo_v
        if hi_v < lo_v:
            lo_v, hi_v = hi_v, lo_v
        out.append((lo_v, hi_v))
    return out


class ScanLog:
    def __init__(self, path: str, clock: Callable[[], float] = time.time):
        self.path = path
        self._fh = open(path, "a", encoding="utf-8")
        self._clock = clock
        self._start = clock()

    def write(self, record: Dict[str, object]) -> None:
        now = self._clock()
        rec = dict(record)
        rec["t"] = (
            datetime.datetime.fromtimestamp(now, datetime.timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
            + "Z"
        )
        rec["elapsed_s"] = round(now - self._start, 3)
        self._fh.write(json.dumps(rec, sort_keys=True) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


def _ascii(hex_text: str) -> str:
    raw = bytes.fromhex(hex_text)
    return "".join(chr(b) if 32 <= b < 127 else "." for b in raw)


class Scanner:
    """Read-only OBD-II / UDS discovery scanner and benchmarker."""

    def __init__(
        self,
        elm: Elm327,
        log: ScanLog,
        cfg: Dict[str, str],
        clock: Callable[[], float] = time.monotonic,
        progress: Callable[[str], None] = lambda _s: None,
        cancel_event: Optional[threading.Event] = None,
    ):
        self.elm = elm
        self.log = log
        self.cfg = cfg
        self.clock = clock
        self.progress = progress
        self.cancel_event = cancel_event
        self.header: Optional[int] = None
        self.deadline = clock() + float(cfg.get("total_minutes", "60")) * 60.0
        self.summary: Dict[str, object] = {
            "protocol": None,
            "adapter": {},
            "pids": [],
            "pid_values": {},
            "dtcs": {
                "mode03_supported": False,
                "mode07_supported": False,
                "stored": [],
                "pending": [],
            },
            "mode09": {},
            "modules": {},
            "benchmark": {},
            "requests": 0,
        }

    def at(self, cmd: str, step: str, timeout_s: float = 2.0) -> List[str]:
        lines = self.elm.command(cmd, timeout_s)
        self.log.write({"step": step, "req": cmd, "raw": lines})
        return lines

    def set_header(self, header: int, step: str) -> None:
        if header != self.header:
            self.at("ATSH{:03X}".format(header), step)
            self.header = header

    def request(
        self,
        header: int,
        payload: bytes,
        step: str,
        timeout_s: float = 2.0,
    ) -> Dict[str, object]:
        if not payload or payload[0] not in READ_ONLY_SERVICES:
            svc = payload[0] if payload else 0
            raise ValueError("refusing non-read service {:02X}".format(svc))
        self.set_header(header, step)
        req = payload.hex().upper()
        lines = self.elm.command(req, timeout_s)
        result = classify(lines, payload[0])
        if result["kind"] == "nrc" and all(
            r.get("nrc") == "78"
            for r in result["replies"].values()
            if r["kind"] == "nrc"
        ):
            more = self.elm.listen(5.0)
            lines = lines + more
            result = classify(lines, payload[0])
        self.summary["requests"] = int(self.summary["requests"]) + 1
        self.log.write({
            "step": step,
            "header": "{:03X}".format(header),
            "req": req,
            "raw": lines,
            "kind": result["kind"],
            "replies": result["replies"],
        })
        return result

    def out_of_time(self) -> bool:
        return self.clock() >= self.deadline or (
            self.cancel_event is not None and self.cancel_event.is_set()
        )

    # ------------------------------------------------------------------ steps
    def step_adapter(self) -> None:
        self.progress("adapter info")
        adapter: Dict[str, str] = {}
        for cmd in ("ATI", "AT@1", "ATRV", "ATDP", "ATDPN", "STI", "STDI"):
            adapter[cmd] = " ".join(self.at(cmd, "adapter"))
        self.summary["adapter"] = adapter

    def step_standard(self) -> None:
        self.progress("standard PIDs")
        supported: List[int] = []
        base = 0x00
        while base <= 0xE0 and not self.out_of_time():
            res = self.request(0x7DF, bytes([0x01, base]), "standard")
            bits = 0
            for reply in res["replies"].values():
                if reply["kind"] == "positive":
                    data = bytes.fromhex(reply["data"])
                    if len(data) >= 5:
                        bits |= int.from_bytes(data[1:5], "big")
            if not bits:
                break
            for i in range(32):
                if bits & (1 << (31 - i)):
                    supported.append(base + 1 + i)
            if not (bits & 1):
                break
            base += 0x20

        pid_list = [p for p in supported if p % 0x20 != 0]
        self.summary["pids"] = ["{:02X}".format(p) for p in pid_list]

        pid_values: Dict[str, Dict[str, object]] = {}
        for pid in pid_list:
            if self.out_of_time():
                break
            res = self.request(0x7DF, bytes([0x01, pid]), "standard")
            for header, reply in res["replies"].items():
                if reply["kind"] != "positive":
                    continue
                data = bytes.fromhex(reply["data"])
                if not data or data[0] != pid:
                    continue
                val = decode_pid(pid, data[1:])
                spec = STANDARD_PIDS.get(pid)
                pid_values["{:02X}".format(pid)] = {
                    "name": spec.name if spec else "PID {:02X}".format(pid),
                    "short": spec.short if spec else "{:02X}".format(pid),
                    "unit": spec.unit if spec else "",
                    "value": val,
                    "raw": data[1:].hex(),
                    "ecu": header,
                }
                break
        self.summary["pid_values"] = pid_values

        self.progress("mode 09 info")
        mode09: Dict[str, str] = {}
        for pid in (0x00, 0x02, 0x04, 0x0A):
            if self.out_of_time():
                break
            res = self.request(0x7DF, bytes([0x09, pid]), "mode09", 4.0)
            for header, reply in res["replies"].items():
                if reply["kind"] == "positive" and pid in (0x02, 0x04, 0x0A):
                    data = bytes.fromhex(reply["data"])
                    mode09["{}:{:02X}".format(header, pid)] = decode_mode09_text(data[1:])
        self.summary["mode09"] = mode09

    def step_dtcs(self) -> None:
        self.progress("stored/pending DTCs")
        dtcs = self.summary["dtcs"]
        assert isinstance(dtcs, dict)
        for service, key_sup, key_list in (
            (0x03, "mode03_supported", "stored"),
            (0x07, "mode07_supported", "pending"),
        ):
            if self.out_of_time():
                break
            res = self.request(0x7DF, bytes([service]), "dtcs", 3.0)
            found: List[str] = []
            for reply in res["replies"].values():
                if reply["kind"] == "positive":
                    dtcs[key_sup] = True
                    data = bytes.fromhex(reply["data"])
                    for code in decode_dtcs(data):
                        if code not in found:
                            found.append(code)
            dtcs[key_list] = found

    def _probe_did(self, header: int, did: int, step: str) -> Optional[Dict[str, object]]:
        res = self.request(header, bytes([0x22, did >> 8, did & 0xFF]), step, 1.5)
        if res["kind"] not in ("positive", "nrc", "other"):
            return None
        return res

    def step_target_dids(self) -> None:
        """Fast targeted UDS probe for Ford TPMS & EV/Hybrid ECU signatures."""
        self.progress("target TPMS/EV DIDs")
        modules: Dict[str, Dict[str, object]] = self.summary["modules"]  # type: ignore[assignment]

        # 1. Check Ford BCM (726) TPMS + VIN, and ECM (7E0) VIN/odometer
        probes: List[Tuple[int, Tuple[int, ...]]] = [
            (0x7E0, (0xF190, 0xF188, 0xDD01)),
            (0x726, (0xF190,) + tuple(FORD_TPMS_DIDS.keys())),
            (0x7E2, (0xF190, 0xF188, 0x0101)),
            (0x7E4, (0xF190, 0x0101, 0x0105)),
            (0x7E5, (0xF190, 0x2101)),
            (0x760, (0xF190,)),
        ]
        self.at("ATAT0", "target_dids")
        self.at("ATST19", "target_dids")
        for header, did_list in probes:
            if self.out_of_time():
                break
            key = "{:03X}".format(header)
            responded = False
            responders = set()
            positives: Dict[str, str] = {}
            decoded_hints: Dict[str, object] = {}
            refusals: Dict[str, int] = {}
            vin = ""
            for did in did_list:
                if self.out_of_time():
                    break
                res = self._probe_did(header, did, "target_dids")
                if res is None:
                    if did == did_list[0]:
                        break  # module absent; skip rest of this module
                    continue
                responded = True
                responders.update(res["replies"])
                for reply in res["replies"].values():
                    if reply["kind"] == "positive":
                        raw_hex = reply["data"][4:]
                        positives["{:04X}".format(did)] = raw_hex
                        if did == 0xF190:
                            vin = _ascii(raw_hex)
                        else:
                            decoded_hints["{:04X}".format(did)] = infer_did_summary(
                                bytes.fromhex(raw_hex)
                            )
                    elif reply["kind"] == "nrc":
                        nrc = str(reply["nrc"])
                        refusals[nrc] = refusals.get(nrc, 0) + 1
            if responded:
                info: Dict[str, object] = {
                    "responders": sorted(responders),
                    "dids": positives,
                    "did_hints": decoded_hints,
                    "refusals": refusals,
                    "did_scan_complete": False,
                }
                if vin:
                    info["vin"] = vin
                modules[key] = info
        self.at("ATST32", "target_dids")
        self.at("ATAT1", "target_dids")

    def step_ev_modules(self) -> None:
        """Discover common powertrain/EV modules and sweep compact EV DID blocks."""
        self.progress("EV module scan")
        found: List[int] = []
        modules: Dict[str, Dict[str, object]] = self.summary["modules"]  # type: ignore[assignment]
        self.at("ATAT0", "ev_modules")
        self.at("ATST19", "ev_modules")
        for header, label in COMMON_TARGET_MODULES:
            if self.out_of_time():
                break
            res = self._probe_did(header, 0xF190, "ev_modules")
            if res is None:
                continue
            found.append(header)
            info: Dict[str, object] = {
                "label": label,
                "responders": sorted(res["replies"]),
                "vin_reply": res["kind"],
            }
            for reply in res["replies"].values():
                if reply["kind"] == "positive":
                    info["vin"] = _ascii(reply["data"][4:])
                elif reply["kind"] == "nrc":
                    info["vin_nrc"] = reply["nrc"]
            modules["{:03X}".format(header)] = info
        self.at("ATST32", "ev_modules")
        self.at("ATAT1", "ev_modules")

        if found:
            ev_cfg = dict(self.cfg)
            ev_cfg["did_ranges"] = "F180-F19F,0100-011F,2100-211F,280B-2816"
            saved_cfg = self.cfg
            self.cfg = ev_cfg
            try:
                self.step_dids(found)
            finally:
                self.cfg = saved_cfg

    def step_modules(self) -> List[int]:
        lo, hi = parse_ranges(self.cfg["modules"])[0]
        timeout_ms = max(4, int(self.cfg["module_timeout_ms"]))
        self.at("ATAT0", "modules")
        self.at("ATST{:02X}".format(min(0xFF, timeout_ms // 4)), "modules")
        found: List[int] = []
        modules: Dict[str, Dict[str, object]] = self.summary["modules"]  # type: ignore[assignment]
        for header in range(lo, hi + 1):
            if self.out_of_time():
                break
            if header % 16 == 0:
                self.progress("modules {:03X} ({} found)".format(header, len(found)))
            res = self.request(header, bytes([0x22, 0xF1, 0x90]), "modules", 1.5)
            if res["kind"] in ("positive", "nrc", "other"):
                found.append(header)
                info: Dict[str, object] = {
                    "responders": sorted(res["replies"]),
                    "vin_reply": res["kind"],
                }
                for reply in res["replies"].values():
                    if reply["kind"] == "positive":
                        info["vin"] = _ascii(reply["data"][4:])
                    elif reply["kind"] == "nrc":
                        info["vin_nrc"] = reply["nrc"]
                modules["{:03X}".format(header)] = info
        self.at("ATST32", "modules")
        self.at("ATAT1", "modules")
        return found

    def step_dids(self, modules: List[int]) -> None:
        ranges = parse_ranges(self.cfg["did_ranges"])
        per_module_s = float(self.cfg["per_module_minutes"]) * 60.0
        mod_map: Dict[str, Dict[str, object]] = self.summary["modules"]  # type: ignore[assignment]
        for header in modules:
            key = "{:03X}".format(header)
            info = mod_map.setdefault(key, {})
            positives: Dict[str, str] = {}
            hints: Dict[str, object] = {}
            refusals: Dict[str, int] = {}
            module_deadline = self.clock() + per_module_s
            done = True
            for lo, hi in ranges:
                for did in range(lo, hi + 1):
                    if self.out_of_time() or self.clock() >= module_deadline:
                        done = False
                        break
                    if did % 64 == 0:
                        self.progress("module {} DID {:04X} ({})".format(
                            key, did, len(positives)))
                    res = self.request(header, bytes([0x22, did >> 8, did & 0xFF]), "dids")
                    for reply in res["replies"].values():
                        if reply["kind"] == "positive":
                            raw_hex = reply["data"][4:]
                            did_key = "{:04X}".format(did)
                            positives[did_key] = raw_hex
                            hints[did_key] = infer_did_summary(bytes.fromhex(raw_hex))
                        elif reply["kind"] == "nrc":
                            nrc = str(reply["nrc"])
                            refusals[nrc] = refusals.get(nrc, 0) + 1
                if not done:
                    break
            info["dids"] = positives
            info["did_hints"] = hints
            info["refusals"] = refusals
            info["did_scan_complete"] = done

    def step_benchmark(self) -> None:
        """Measure single-PID latency (ms / Hz) and test multi-PID batching."""
        self.progress("latency benchmark")
        pid_strs = [str(p) for p in self.summary.get("pids", [])]
        sample_pid = int(pid_strs[0], 16) if pid_strs else 0x00
        samples = max(2, int(self.cfg.get("benchmark_samples", "8")))
        latencies: List[float] = []
        for _ in range(samples):
            if self.out_of_time():
                break
            t0 = self.clock()
            self.request(0x7DF, bytes([0x01, sample_pid]), "benchmark", 1.5)
            dt_ms = max(0.1, (self.clock() - t0) * 1000.0)
            latencies.append(dt_ms)

        avg_ms = sum(latencies) / len(latencies) if latencies else 0.0
        single_hz = round(1000.0 / avg_ms, 1) if avg_ms > 0 else 0.0

        # Test multi-PID batching (e.g. Speed + Pedal/Throttle + RPM in 1 frame)
        cand = [int(p, 16) for p in pid_strs if int(p, 16) in (0x0D, 0x49, 0x11, 0x0C, 0x62, 0x04)]
        batch = cand[:3] if len(cand) >= 2 else [int(p, 16) for p in pid_strs[:3]]
        multi_ok = False
        multi_hz = 0.0
        if len(batch) >= 2 and not self.out_of_time():
            t0 = self.clock()
            res = self.request(0x7DF, bytes([0x01] + batch), "benchmark", 1.5)
            dt_ms = max(0.1, (self.clock() - t0) * 1000.0)
            for reply in res["replies"].values():
                if reply["kind"] == "positive":
                    decoded = decode_multi_pid(bytes.fromhex(reply["data"]))
                    if len(decoded) >= 2:
                        multi_ok = True
                        multi_hz = round(1000.0 / dt_ms, 1)
                        break

        self.summary["benchmark"] = {
            "samples": len(latencies),
            "avg_ms": round(avg_ms, 1),
            "min_ms": round(min(latencies), 1) if latencies else 0.0,
            "max_ms": round(max(latencies), 1) if latencies else 0.0,
            "single_hz": single_hz,
            "multi_pid_supported": multi_ok,
            "multi_pid_tested": ["{:02X}".format(p) for p in batch],
            "multi_hz": multi_hz,
        }

    # -------------------------------------------------------------------- run
    def run(self) -> Dict[str, object]:
        explicit = [s.strip() for s in self.cfg.get("steps", "").split(",") if s.strip()]
        steps = explicit or steps_for_mode(self.cfg.get("scan_mode", "quick"))
        init = self.elm.init(self.cfg.get("protocol", "0"))
        self.log.write({"step": "init", "replies": init})
        self.summary["protocol"] = init.get("protocol")
        if init.get("protocol") is None:
            self.log.write({"step": "summary", "error": "no ECU answered 0100", **self.summary})
            return self.summary

        if "adapter" in steps:
            self.step_adapter()
        if "standard" in steps:
            self.step_standard()
        if "dtcs" in steps:
            self.step_dtcs()
        if "target_dids" in steps:
            self.step_target_dids()
        if "ev_modules" in steps:
            self.step_ev_modules()
        modules: List[int] = []
        if "modules" in steps:
            modules = self.step_modules()
        if "dids" in steps:
            if not modules:
                modules = [0x7E0, 0x726]
            self.step_dids(modules)
        if "benchmark" in steps:
            self.step_benchmark()

        if self.cancel_event is not None and self.cancel_event.is_set():
            self.summary["cancelled"] = True
        self.summary["out_of_time"] = self.out_of_time()
        self.log.write({"step": "summary", **self.summary})
        return self.summary


# ------------------------------------------------------------------- helpers

def save_profile(
    summary: Dict[str, object],
    link_name: str,
    out_dir: str,
    stamp: Optional[str] = None,
) -> Tuple[str, Dict[str, object]]:
    os.makedirs(out_dir, exist_ok=True)
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    if stamp is None:
        stamp = now_utc.strftime("%Y%m%dT%H%M%SZ")
    profile = compatibility.build_profile(
        summary, link_name, now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    vin = re.sub(r"[^A-Za-z0-9_-]", "", str(profile["vehicle"].get("vin") or ""))
    tag = "{}_{}".format(vin, stamp) if vin else stamp
    path = os.path.join(out_dir, "obd_profile_{}.json".format(tag))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(profile, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return path, profile


def open_link(cfg: Dict[str, str]) -> elm327.Transport:
    link = cfg["link"]
    if link == "usb":
        device = cfg["device"] or next(iter(elm327.find_usb_devices()), "")
        if not device:
            raise elm327.LinkError("no USB serial adapter found")
        return elm327.SerialTransport(device, int(cfg.get("baud") or 0))
    if link == "wifi":
        return elm327.tcp_transport(cfg["host"], int(cfg["port"]))
    if link == "ble":
        return elm327.BleTransport(cfg["ble_name"])
    if link == "demo":
        from .fake_elm import FakeElm
        return FakeElm(profile=cfg.get("demo_profile", "ranger"))
    raise elm327.LinkError("unknown link: " + link)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="python3 -m jp226obd.scanner",
        description="Read-only OBD-II scanner & compatibility profiler.",
    )
    p.add_argument("--config", help="settings file (see obd_scanner.cfg.example)")
    p.add_argument("--demo", action="store_true", help="simulate a vehicle (no adapter)")
    for key in DEFAULTS:
        p.add_argument("--" + key.replace("_", "-"), dest=key)
    args = p.parse_args(argv)

    cfg = load_config(args.config)
    for key in DEFAULTS:
        val = getattr(args, key)
        if val is not None:
            cfg[key] = val
    if args.demo:
        cfg["link"] = "demo"
    if cfg["did_ranges"].lower() == "everything":
        cfg["did_ranges"] = EVERYTHING_DID_RANGES

    try:
        transport = open_link(cfg)
    except (elm327.LinkError, OSError, ValueError) as exc:
        print("Could not open the adapter: {}".format(exc), file=sys.stderr)
        return 2

    out_dir = cfg["out_dir"] or os.path.join(os.getcwd(), "obd_scans")
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log = ScanLog(os.path.join(out_dir, "obd_scan_{}.jsonl".format(stamp)))
    log.write({"step": "start", "link": transport.name, "config": dict(cfg)})
    print("Scanning via {} -> {}".format(transport.name, log.path))

    scanner = Scanner(
        Elm327(transport),
        log,
        cfg,
        progress=lambda s: print("  " + s, flush=True),
    )
    try:
        summary = scanner.run()
    except KeyboardInterrupt:
        log.write({"step": "summary", "interrupted": True, **scanner.summary})
        summary = scanner.summary
        print("Stopped; partial results saved.")
    finally:
        log.close()
        transport.close()

    profile_path, profile = save_profile(summary, transport.name, out_dir, stamp)
    logger_fit = profile["logger_fit"]
    ev_fit = profile["ev_sound_fit"]
    print("Protocol: {} | Powertrain: {}".format(
        summary.get("protocol"), profile["vehicle"]["powertrain_hint"]))
    print("Standard PIDs ({}): {}".format(
        len(summary.get("pids", [])), " ".join(summary.get("pids", [])) or "none"))
    print("Logger fit: {}/{} core metrics | TPMS: {}".format(
        logger_fit["supported_count"], logger_fit["total_count"],
        "YES" if logger_fit["tpms_ready"] else "NO"))
    print("EV Sound fit: {} ({}/{} channels, {} Hz, multi-PID: {})".format(
        ev_fit["grade"], ev_fit["available_channels"], ev_fit["total_channels"],
        ev_fit["effective_hz"], "YES" if ev_fit["multi_pid_supported"] else "NO"))
    print("Log:     {}".format(log.path))
    print("Profile: {}".format(profile_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
