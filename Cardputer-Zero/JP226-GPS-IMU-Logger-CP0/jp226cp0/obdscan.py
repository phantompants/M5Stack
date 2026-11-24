"""OBD discovery scan: find everything the car will answer, and log it all.

  python3 -m jp226cp0.obdscan --config obd_scan.cfg
  python3 -m jp226cp0.obdscan --link usb            (vLinker USB, auto port)
  python3 -m jp226cp0.obdscan --link wifi --host 192.168.0.10
  python3 -m jp226cp0.obdscan --link bt --bt-address AA:BB:CC:DD:EE:FF
  python3 -m jp226cp0.obdscan --link ble --ble-name vLinker
  python3 -m jp226cp0.obdscan --demo                (pretend car, no adapter)

Steps, all read-only:
  1 adapter   ATI, AT@1, ATRV, ATDP, STI (what the adapter is, socket voltage)
  2 standard  Mode 01 supported PIDs and one reading of each; Mode 09 (VIN,
              calibration IDs, ECU name) through the functional address 7DF
  3 modules   every request address in the module range (default 700-7F7)
              asked for its VIN (22 F190); any reply, even a refusal, means
              a module is there
  4 dids      for each module found, every DID in the DID ranges (Mode 22),
              within a time budget per module

Every request and its raw reply, decoded (positive / refusal with its NRC /
no data / error) goes to obd_scan_<UTC time>.jsonl in the output folder, then
a summary record. Upload that file for analysis.

Safety: the scanner only ever sends AT/ST adapter commands and services 01,
09 and 22 (read data). It never writes, clears codes, resets, changes a
diagnostic session or asks for security access. Run it with the ignition on
(engine running or not); some modules only answer with the engine running.
"""

import argparse
import datetime
import json
import os
import sys
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from . import elm327
from .elm327 import Elm327, classify

READ_ONLY_SERVICES = (0x01, 0x09, 0x22)

DEFAULTS = {
    "link": "usb",
    "device": "",            # usb: blank = first USB serial port found
    "baud": "38400",         # usb: ignored by most USB-CDC adapters
    "host": "192.168.0.10",  # wifi
    "port": "35000",
    "bt_address": "",        # bt (Classic): pair first with bluetoothctl
    "bt_channel": "1",
    "ble_name": "",          # ble: name or address; blank = first OBD-ish name
    "protocol": "0",         # automatic for broad vehicle compatibility
    "steps": "adapter,standard,modules,dids",
    "modules": "700-7F7",
    "did_ranges": "F100-F1FF,DD00-DDFF,2800-28FF,4000-40FF",
    "module_timeout_ms": "100",   # ATST while looking for modules
    "per_module_minutes": "10",
    "total_minutes": "90",
    "out_dir": "",
}

LINK_CHOICES = ("usb", "wifi", "bt", "ble")
PROTOCOL_LABELS = {
    "0": "AUTO",
    "1": "J1850 PWM",
    "2": "J1850 VPW",
    "3": "ISO 9141-2",
    "4": "KWP 5-BAUD",
    "5": "KWP FAST",
    "6": "CAN 11/500",
    "7": "CAN 29/500",
    "8": "CAN 11/250",
    "9": "CAN 29/250",
    "A": "J1939 29/250",
}

# A "find everything" preset: every DID on every module. Many hours.
EVERYTHING_DID_RANGES = "0000-FFFF"


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
    """'F100-F1FF,2813' -> [(0xF100, 0xF1FF), (0x2813, 0x2813)]"""
    out = []
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
        record = dict(record)
        record["t"] = datetime.datetime.fromtimestamp(
            now, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        record["elapsed_s"] = round(now - self._start, 3)
        self._fh.write(json.dumps(record, sort_keys=True) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


def _ascii(hex_text: str) -> str:
    raw = bytes.fromhex(hex_text)
    return "".join(chr(b) if 32 <= b < 127 else "." for b in raw)


class Scanner:
    def __init__(self, elm: Elm327, log: ScanLog, cfg: Dict[str, str],
                 clock: Callable[[], float] = time.monotonic,
                 progress: Callable[[str], None] = lambda s: None,
                 cancel_event: Optional[threading.Event] = None):
        self.elm = elm
        self.log = log
        self.cfg = cfg
        self.clock = clock
        self.progress = progress
        self.cancel_event = cancel_event
        self.header = None
        self.deadline = clock() + float(cfg["total_minutes"]) * 60
        self.summary: Dict[str, object] = {"modules": {}, "pids": [], "mode09": {},
                                           "adapter": {}, "requests": 0}

    # -------------------------------------------------------------- basics
    def at(self, cmd: str, step: str, timeout_s: float = 2.0) -> List[str]:
        lines = self.elm.command(cmd, timeout_s)
        self.log.write({"step": step, "req": cmd, "raw": lines})
        return lines

    def set_header(self, header: int, step: str) -> None:
        if header != self.header:
            self.at("ATSH{:03X}".format(header), step)
            self.header = header

    def request(self, header: int, payload: bytes, step: str,
                timeout_s: float = 2.0) -> Dict[str, object]:
        if payload[0] not in READ_ONLY_SERVICES:
            raise ValueError("refusing non-read service {:02X}".format(payload[0]))
        self.set_header(header, step)
        req = payload.hex().upper()
        lines = self.elm.command(req, timeout_s)
        result = classify(lines, payload[0])
        if result["kind"] == "nrc" and all(
                r.get("nrc") == "78" for r in result["replies"].values() if r["kind"] == "nrc"):
            # responsePending: the module is still working on it; listen once more.
            more = self.elm.listen(5.0)
            lines = lines + more
            result = classify(lines, payload[0])
        self.summary["requests"] += 1
        self.log.write({"step": step, "header": "{:03X}".format(header), "req": req,
                        "raw": lines, "kind": result["kind"], "replies": result["replies"]})
        return result

    def out_of_time(self) -> bool:
        return (self.clock() >= self.deadline or
                (self.cancel_event is not None and self.cancel_event.is_set()))

    # --------------------------------------------------------------- steps
    def step_adapter(self) -> None:
        self.progress("adapter")
        for cmd in ("ATI", "AT@1", "ATRV", "ATDP", "ATDPN", "STI", "STDI"):
            self.summary["adapter"][cmd] = " ".join(self.at(cmd, "adapter"))

    def step_standard(self) -> None:
        self.progress("standard PIDs")
        supported: List[int] = []
        base = 0x00
        while base <= 0xE0 and not self.out_of_time():
            r = self.request(0x7DF, bytes([0x01, base]), "standard")
            bits = 0
            for reply in r["replies"].values():
                if reply["kind"] == "positive":
                    data = bytes.fromhex(reply["data"])
                    if len(data) >= 5:
                        bits |= int.from_bytes(data[1:5], "big")
            if not bits:
                break
            for i in range(32):
                if bits & (1 << (31 - i)):
                    supported.append(base + 1 + i)
            if not bits & 1:
                break
            base += 0x20
        for pid in supported:
            if pid % 0x20 == 0 or self.out_of_time():
                continue
            self.request(0x7DF, bytes([0x01, pid]), "standard")
        self.summary["pids"] = ["{:02X}".format(p) for p in supported if p % 0x20]
        self.progress("mode 09")
        for pid in (0x00, 0x02, 0x04, 0x06, 0x08, 0x0A):
            if self.out_of_time():
                break
            r = self.request(0x7DF, bytes([0x09, pid]), "mode09", 4.0)
            for header, reply in r["replies"].items():
                if reply["kind"] == "positive" and pid in (0x02, 0x04, 0x0A):
                    self.summary["mode09"]["{}:{:02X}".format(header, pid)] = _ascii(reply["data"][4:])

    def step_modules(self) -> List[int]:
        lo, hi = parse_ranges(self.cfg["modules"])[0]
        timeout_ms = max(4, int(self.cfg["module_timeout_ms"]))
        self.at("ATAT0", "modules")
        self.at("ATST{:02X}".format(min(0xFF, timeout_ms // 4)), "modules")
        found = []
        for header in range(lo, hi + 1):
            if self.out_of_time():
                break
            if header % 16 == 0:
                self.progress("modules {:03X} ({} found)".format(header, len(found)))
            r = self.request(header, bytes([0x22, 0xF1, 0x90]), "modules", 1.5)
            if r["kind"] in ("positive", "nrc", "other"):
                found.append(header)
                info = {"responders": sorted(r["replies"]), "vin_reply": r["kind"]}
                for reply in r["replies"].values():
                    if reply["kind"] == "positive":
                        info["vin"] = _ascii(reply["data"][4:])
                    elif reply["kind"] == "nrc":
                        info["vin_nrc"] = reply["nrc"]
                self.summary["modules"]["{:03X}".format(header)] = info
        self.at("ATST32", "modules")
        self.at("ATAT1", "modules")
        return found

    def step_dids(self, modules: List[int]) -> None:
        ranges = parse_ranges(self.cfg["did_ranges"])
        per_module_s = float(self.cfg["per_module_minutes"]) * 60
        for header in modules:
            key = "{:03X}".format(header)
            info = self.summary["modules"].setdefault(key, {})
            positives: Dict[str, str] = {}
            refusals: Dict[str, int] = {}
            module_deadline = self.clock() + per_module_s
            done = True
            for lo, hi in ranges:
                for did in range(lo, hi + 1):
                    if self.out_of_time() or self.clock() >= module_deadline:
                        done = False
                        break
                    if did % 64 == 0:
                        self.progress("module {} DID {:04X} ({} answered)".format(key, did, len(positives)))
                    r = self.request(header, bytes([0x22, did >> 8, did & 0xFF]), "dids")
                    for reply in r["replies"].values():
                        if reply["kind"] == "positive":
                            positives["{:04X}".format(did)] = reply["data"][4:]
                        elif reply["kind"] == "nrc":
                            refusals[reply["nrc"]] = refusals.get(reply["nrc"], 0) + 1
                if not done:
                    break
            info["dids"] = positives
            info["refusals"] = refusals
            info["did_scan_complete"] = done

    # ----------------------------------------------------------------- run
    def run(self) -> Dict[str, object]:
        steps = [s.strip() for s in self.cfg["steps"].split(",") if s.strip()]
        init = self.elm.init(self.cfg["protocol"])
        self.log.write({"step": "init", "replies": init})
        self.summary["protocol"] = init.get("protocol")
        if init.get("protocol") is None:
            self.log.write({"step": "summary", "error": "no ECU answered 0100", **self.summary})
            return self.summary
        if "adapter" in steps:
            self.step_adapter()
        if "standard" in steps:
            self.step_standard()
        modules: List[int] = []
        if "modules" in steps:
            modules = self.step_modules()
        if "dids" in steps:
            if not modules:  # dids without discovery: the engine and body modules
                modules = [0x7E0, 0x726]
            self.step_dids(modules)
        if self.cancel_event is not None and self.cancel_event.is_set():
            self.summary["cancelled"] = True
        self.summary["out_of_time"] = self.out_of_time()
        self.log.write({"step": "summary", **self.summary})
        return self.summary


# ----------------------------------------------------------------------- CLI

def open_link(cfg: Dict[str, str]) -> elm327.Transport:
    link = cfg["link"]
    if link == "usb":
        device = cfg["device"] or next(iter(elm327.find_usb_devices()), "")
        if not device:
            raise elm327.LinkError("no USB serial adapter found (plug in the vLinker USB)")
        return elm327.SerialTransport(device, int(cfg["baud"]))
    if link == "wifi":
        return elm327.tcp_transport(cfg["host"], int(cfg["port"]))
    if link == "bt":
        if not cfg["bt_address"]:
            raise elm327.LinkError("bt link needs bt_address")
        return elm327.rfcomm_transport(cfg["bt_address"], int(cfg["bt_channel"]))
    if link == "ble":
        return elm327.BleTransport(cfg["ble_name"])
    raise elm327.LinkError("unknown link: " + link)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="python3 -m jp226cp0.obdscan",
                                description="Read-only OBD discovery scan to a JSONL log.")
    p.add_argument("--config", help="settings file (see obd_scan.cfg.example)")
    p.add_argument("--demo", action="store_true", help="scan a pretend car, no adapter")
    for key in DEFAULTS:
        p.add_argument("--" + key.replace("_", "-"), dest=key)
    args = p.parse_args(argv)

    cfg = load_config(args.config)
    for key in DEFAULTS:
        value = getattr(args, key)
        if value is not None:
            cfg[key] = value
    if cfg["did_ranges"].lower() == "everything":
        cfg["did_ranges"] = EVERYTHING_DID_RANGES

    if args.demo:
        from .fake_elm import FakeElm
        transport = FakeElm()
        cfg["modules"] = cfg["modules"] if args.modules else "720-77F"
    else:
        try:
            transport = open_link(cfg)
        except (elm327.LinkError, OSError) as e:
            print("Could not open the adapter: {}".format(e), file=sys.stderr)
            return 2

    out_dir = cfg["out_dir"] or os.path.join(os.getcwd(), "obd_scans")
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log = ScanLog(os.path.join(out_dir, "obd_scan_{}.jsonl".format(stamp)))
    log.write({"step": "start", "link": transport.name,
               "config": {k: v for k, v in cfg.items()}})
    print("Scanning via {} -> {}".format(transport.name, log.path))
    scanner = Scanner(Elm327(transport), log, cfg,
                      progress=lambda s: print("  " + s, flush=True))
    try:
        summary = scanner.run()
    except KeyboardInterrupt:
        log.write({"step": "summary", "interrupted": True, **scanner.summary})
        summary = scanner.summary
        print("Stopped; what was found so far is in the log.")
    finally:
        log.close()
        transport.close()

    print("Protocol: {}".format(summary.get("protocol")))
    print("Standard PIDs: {}".format(" ".join(summary.get("pids", [])) or "none"))
    for header, info in sorted(summary.get("modules", {}).items()):
        dids = info.get("dids", {})
        print("Module {} replies from {}: {} DIDs answered{}".format(
            header, ",".join(info.get("responders", [])) or "?", len(dids),
            "" if info.get("did_scan_complete", True) else " (budget ran out)"))
    print("Log: {}".format(log.path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
