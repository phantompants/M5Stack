"""Application state controller for JP226-OBDii-Scanner-CP0."""

import datetime
import json
import os
import sys
import threading
import time
from typing import Dict, List, Optional, Tuple

from . import __version__, compatibility, elm327, hardware, scanner
from .build_info import read_build_info
from .elm327 import Elm327
from .fake_elm import DEMO_PROFILES
from .live import DEFAULT_LIVE_PIDS, LiveReader
from .statestore import StateStore
from .webserver import LogWebServer, list_log_files

STATE_FILE_NAME = "scanner_state.json"
WEB_PORTS = (8080, 8000, 8888)


def default_data_dir() -> str:
    env_dir = os.environ.get("JP226_OBD_DATA_DIR")
    if env_dir:
        return os.path.expanduser(env_dir)
    preferred = os.path.join(os.path.expanduser("~"), "telemetry", "obd_scanner")
    try:
        os.makedirs(preferred, exist_ok=True)
        if os.access(preferred, os.W_OK):
            return preferred
    except OSError:
        pass
    return os.path.join(os.path.expanduser("~"), "obd_scanner")


class ObdScannerApp:
    def __init__(self, data_dir: Optional[str] = None,
                 config_path: Optional[str] = None):
        self.data_dir = data_dir or default_data_dir()
        os.makedirs(self.data_dir, exist_ok=True)
        self.config_path = config_path or os.path.join(
            self.data_dir, "obd_scanner.cfg"
        )
        self.store = StateStore(os.path.join(self.data_dir, STATE_FILE_NAME))
        self.build_info = read_build_info()

        self.link = str(self.store.get("link", "usb"))
        if self.link not in scanner.LINK_CHOICES:
            self.link = "usb"
        self.protocol = str(self.store.get("protocol", "0")).upper()
        if self.protocol not in scanner.PROTOCOL_LABELS:
            self.protocol = "0"
        self.scan_mode = str(self.store.get("scan_mode", "quick"))
        if self.scan_mode not in scanner.SCAN_MODES:
            self.scan_mode = "quick"
        self.demo_profile = str(self.store.get("demo_profile", "ranger"))
        if self.demo_profile not in DEMO_PROFILES:
            self.demo_profile = "ranger"

        self.wifi_presets: List[Tuple[str, int]] = list(hardware.WIFI_PRESETS)
        self.wifi_index = int(self.store.get("wifi_index", 0)) % len(self.wifi_presets)
        self.usb_devices: List[str] = [""] + elm327.find_usb_devices()
        self.usb_index = 0
        self.ble_devices: List[Tuple[str, str]] = [("", "")]
        self.ble_index = 0
        self.ble_scanning = False

        self.connect_selection = 0
        self.module_scroll = 0
        self.last_key = ""

        self.status = "READY"
        self.progress = "Pick link and press ENTER"
        self.error = ""
        self.log_file = ""
        self.profile_file = ""
        self.link_name = ""

        self.summary: Dict[str, object] = {}
        self.profile: Dict[str, object] = {}
        self._load_last_profile()

        self._scan_cancel = threading.Event()
        self._scan_thread: Optional[threading.Thread] = None
        self._discover_thread: Optional[threading.Thread] = None

        self._live_cancel = threading.Event()
        self._live_thread: Optional[threading.Thread] = None
        self._live_reader: Optional[LiveReader] = None
        self.live_active = False
        self.live_snapshot: Dict[str, object] = {
            "values": {},
            "hz": 0.0,
            "latency_ms": 0.0,
            "samples": 0,
            "error": "",
        }
        try:
            saved_port = int(self.store.get("web_port", 8080))
        except (TypeError, ValueError):
            saved_port = 8080
        self.web_port = saved_port if saved_port in WEB_PORTS else 8080
        self.web = LogWebServer(
            self.data_dir, port=self.web_port, profile_getter=lambda: self.profile
        )

    def _load_last_profile(self) -> None:
        last_path = self.store.get("last_profile_path", "")
        if isinstance(last_path, str) and os.path.isfile(last_path):
            try:
                with open(last_path, "r", encoding="utf-8") as fh:
                    loaded = json.load(fh)
                if isinstance(loaded, dict) and loaded.get("schema_version") == 1:
                    self.profile = loaded
                    self.profile_file = os.path.basename(last_path)
            except (OSError, ValueError):
                pass

    # ------------------------------------------------------------ UI controls
    def select_connect_row(self, delta: int) -> None:
        self.connect_selection = (self.connect_selection + delta) % 4

    def cycle_connect_row(self, row: int, delta: int = 1) -> None:
        self.connect_selection = row % 4
        self.adjust_connect_row(delta)

    def scroll_modules(self, delta: int) -> None:
        modules = self.profile.get("modules", {})
        count = len(modules) if isinstance(modules, dict) else 0
        if count <= 4:
            self.module_scroll = 0
            return
        self.module_scroll = max(0, min(count - 4, self.module_scroll + delta))

    def _target_label(self) -> str:
        if self.link == "usb":
            dev = self.usb_devices[self.usb_index % len(self.usb_devices)]
            return os.path.basename(dev) if dev else "AUTO USB"
        if self.link == "wifi":
            host, port = self.wifi_presets[self.wifi_index]
            return "{}:{}".format(host, port)
        if self.link == "ble":
            name, addr = self.ble_devices[self.ble_index % len(self.ble_devices)]
            if not name and not addr:
                return "SCANNING..." if self.ble_scanning else "AUTO BLE"
            return name[:16]
        return self.demo_profile.upper()

    def adjust_connect_row(self, delta: int) -> None:
        if self.connect_selection == 0:
            idx = scanner.LINK_CHOICES.index(self.link)
            self.link = scanner.LINK_CHOICES[(idx + delta) % len(scanner.LINK_CHOICES)]
            self.store.put(link=self.link)
        elif self.connect_selection == 1:
            if self.link == "usb":
                self.usb_index = (self.usb_index + delta) % len(self.usb_devices)
            elif self.link == "wifi":
                self.wifi_index = (self.wifi_index + delta) % len(self.wifi_presets)
                self.store.put(wifi_index=self.wifi_index)
            elif self.link == "ble":
                self.ble_index = (self.ble_index + delta) % len(self.ble_devices)
            else:
                idx = DEMO_PROFILES.index(self.demo_profile)
                self.demo_profile = DEMO_PROFILES[(idx + delta) % len(DEMO_PROFILES)]
                self.store.put(demo_profile=self.demo_profile)
        elif self.connect_selection == 2:
            protos = tuple(scanner.PROTOCOL_LABELS)
            idx = protos.index(self.protocol)
            self.protocol = protos[(idx + delta) % len(protos)]
            self.store.put(protocol=self.protocol)
        else:
            idx = scanner.SCAN_MODES.index(self.scan_mode)
            self.scan_mode = scanner.SCAN_MODES[(idx + delta) % len(scanner.SCAN_MODES)]
            self.store.put(scan_mode=self.scan_mode)

    def refresh_devices(self) -> None:
        self.usb_devices = [""] + elm327.find_usb_devices()
        self.usb_index = 0
        if self.link == "ble":
            if self._discover_thread is not None and self._discover_thread.is_alive():
                return
            self.ble_scanning = True
            self.progress = "Scanning BLE adapters..."
            self._discover_thread = threading.Thread(
                target=self._run_ble_discover, name="jp226-ble-scan", daemon=True
            )
            self._discover_thread.start()
        else:
            self.progress = "Found {} USB port(s)".format(len(self.usb_devices) - 1)

    def _run_ble_discover(self) -> None:
        try:
            found = elm327.discover_ble_adapters(scan_s=4.0)
            self.ble_devices = [("", "")] + found
            self.ble_index = 1 if found else 0
            self.progress = "Found {} BLE device(s)".format(len(found))
        except Exception as exc:
            self.progress = str(exc)[:38]
        finally:
            self.ble_scanning = False

    # -------------------------------------------------------- scan / live
    def build_config(self) -> Dict[str, str]:
        cfg = dict(scanner.DEFAULTS)
        if os.path.isfile(self.config_path):
            cfg = scanner.load_config(self.config_path)
        host, port = self.wifi_presets[self.wifi_index]
        usb_dev = self.usb_devices[self.usb_index % len(self.usb_devices)]
        ble_name, ble_addr = self.ble_devices[self.ble_index % len(self.ble_devices)]
        cfg.update({
            "link": self.link,
            "device": usb_dev or cfg.get("device", ""),
            "host": host if self.wifi_index != 0 else cfg.get("host", host),
            "port": str(port),
            "ble_name": ble_addr or ble_name or cfg.get("ble_name", ""),
            "demo_profile": self.demo_profile,
            "protocol": self.protocol,
            "scan_mode": self.scan_mode,
            "out_dir": self.data_dir,
        })
        return cfg

    def is_scanning(self) -> bool:
        return self._scan_thread is not None and self._scan_thread.is_alive()

    def start_or_stop_scan(self) -> None:
        if self.is_scanning():
            self._scan_cancel.set()
            self.status = "STOPPING"
            return
        if self.live_active:
            self.stop_live()
        cfg = self.build_config()
        self._scan_cancel.clear()
        self.status = "CONNECTING"
        self.progress = "Opening {} link...".format(self.link.upper())
        self.error = ""
        self._scan_thread = threading.Thread(
            target=self._run_scan, args=(cfg,), name="jp226-obd-scan", daemon=True
        )
        self._scan_thread.start()

    def run_scan_sync(self, cfg: Optional[Dict[str, str]] = None) -> Dict[str, object]:
        """Synchronous scan helper for headless CLI, screenshots, and tests."""
        cfg = cfg or self.build_config()
        self._run_scan(cfg)
        return self.profile

    def _run_scan(self, cfg: Dict[str, str]) -> None:
        transport = None
        log = None
        sc = None
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        try:
            transport = scanner.open_link(cfg)
            self.link_name = transport.name
            os.makedirs(cfg["out_dir"], exist_ok=True)
            log_path = os.path.join(cfg["out_dir"], "obd_scan_{}.jsonl".format(stamp))
            log = scanner.ScanLog(log_path)
            log.write({"step": "start", "link": transport.name, "config": cfg})
            self.log_file = os.path.basename(log_path)
            self.status = "SCANNING"
            sc = scanner.Scanner(
                Elm327(transport),
                log,
                cfg,
                progress=self._set_progress,
                cancel_event=self._scan_cancel,
            )
            summary = sc.run()
            self.summary = summary
            prof_path, profile = scanner.save_profile(
                summary, transport.name, cfg["out_dir"], stamp
            )
            self.profile = profile
            self.profile_file = os.path.basename(prof_path)
            self.module_scroll = 0
            self.store.put(last_profile_path=prof_path)
            if summary.get("cancelled"):
                self.status = "CANCELLED"
            elif summary.get("protocol") is None:
                self.status = "NO ECU"
                self.progress = "No ECU answered 0100"
            else:
                self.status = "COMPLETE"
                ev = profile["ev_sound_fit"]
                lg = profile["logger_fit"]
                self.progress = "LOG {}/12  EV {} ({}Hz)".format(
                    lg["supported_count"], ev["grade"], ev["effective_hz"]
                )
        except (elm327.LinkError, OSError, ValueError, RuntimeError) as exc:
            self.status = "ERROR"
            self.error = str(exc)
            self.progress = str(exc)[:40]
            self._save_partial_scan(sc, log, transport, cfg, stamp, self.error)
            print("OBD scan error: {}".format(exc), file=sys.stderr)
        except Exception as exc:
            self.status = "ERROR"
            self.error = "{}: {}".format(type(exc).__name__, exc)
            self.progress = self.error[:40]
            self._save_partial_scan(sc, log, transport, cfg, stamp, self.error)
            print("OBD scan failed: {}".format(exc), file=sys.stderr)
        finally:
            if log is not None:
                log.close()
            if transport is not None:
                transport.close()

    def _save_partial_scan(self, sc, log, transport, cfg: Dict[str, str],
                           stamp: str, err: str) -> None:
        if sc is None:
            return
        self.summary = sc.summary
        if log is not None:
            try:
                log.write({"step": "summary", "error": err, **sc.summary})
            except Exception:
                pass
        try:
            link_name = transport.name if transport is not None else cfg.get("link", "?")
            prof_path, profile = scanner.save_profile(
                sc.summary, link_name, cfg["out_dir"], stamp
            )
            self.profile = profile
            self.profile_file = os.path.basename(prof_path)
            self.store.put(last_profile_path=prof_path)
        except Exception:
            pass

    def _set_progress(self, text: str) -> None:
        self.status = "SCANNING"
        self.progress = text

    def toggle_live(self) -> None:
        if self.live_active:
            self.stop_live()
            return
        if self.is_scanning():
            return
        self._live_cancel.clear()
        self.live_active = True
        self._live_thread = threading.Thread(
            target=self._run_live, args=(self.build_config(),),
            name="jp226-obd-live", daemon=True,
        )
        self._live_thread.start()

    def stop_live(self) -> None:
        self._live_cancel.set()
        if self._live_thread is not None and self._live_thread.is_alive():
            self._live_thread.join(timeout=3)
        self.live_active = False

    def _run_live(self, cfg: Dict[str, str]) -> None:
        transport = None
        live_log = None
        try:
            transport = scanner.open_link(cfg)
            elm = Elm327(transport)
            init = elm.init(cfg.get("protocol", "0"))
            if init.get("protocol") is None:
                self.live_snapshot = {
                    "values": {}, "hz": 0.0, "latency_ms": 0.0,
                    "samples": 0, "error": "No ECU answered 0100",
                }
                return
            os.makedirs(cfg["out_dir"], exist_ok=True)
            stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            live_log = scanner.ScanLog(
                os.path.join(cfg["out_dir"], "obd_live_{}.jsonl".format(stamp))
            )
            ev_fit = self.profile.get("ev_sound_fit", {})
            rec_pids = ev_fit.get("recommended_fast_pids") if isinstance(ev_fit, dict) else None
            pids = [int(p, 16) for p in rec_pids] if rec_pids else list(DEFAULT_LIVE_PIDS)
            multi = bool(ev_fit.get("multi_pid_supported")) if isinstance(ev_fit, dict) else False
            live_log.write({
                "step": "live_start",
                "link": transport.name,
                "protocol": init.get("protocol"),
                "pids": ["{:02X}".format(p) for p in pids],
                "multi_pid": multi,
            })
            reader = LiveReader(elm, pids, multi_pid=multi)
            self._live_reader = reader
            while not self._live_cancel.is_set():
                reader.poll_once()
                snap = reader.snapshot()
                self.live_snapshot = snap
                live_log.write({"step": "live_sample", **snap})
                time.sleep(0.02)
        except Exception as exc:
            self.live_snapshot = {
                "values": {}, "hz": 0.0, "latency_ms": 0.0,
                "samples": 0, "error": str(exc)[:38],
            }
            if live_log is not None:
                try:
                    live_log.write({"step": "live_error", "error": str(exc)})
                except Exception:
                    pass
        finally:
            self.live_active = False
            if live_log is not None:
                live_log.close()
            if transport is not None:
                transport.close()

    def start_web(self) -> None:
        if self.web.running and self.web.port == self.web_port:
            return
        self.web.stop()
        self.web = LogWebServer(
            self.data_dir, port=self.web_port, profile_getter=lambda: self.profile
        )
        self.web.start()

    def stop_web(self) -> None:
        self.web.stop()

    def toggle_web(self) -> None:
        if self.web.running:
            self.stop_web()
        else:
            self.start_web()

    def cycle_web_port(self) -> None:
        was_running = self.web.running
        idx = WEB_PORTS.index(self.web_port) if self.web_port in WEB_PORTS else 0
        self.web_port = WEB_PORTS[(idx + 1) % len(WEB_PORTS)]
        self.store.put(web_port=self.web_port)
        if was_running:
            self.stop_web()
            self.start_web()

    def close(self) -> None:
        self._scan_cancel.set()
        if self._scan_thread is not None and self._scan_thread.is_alive():
            self._scan_thread.join(timeout=5)
        self.stop_live()
        self.stop_web()

    # --------------------------------------------------------------- UI model
    def ui_model(self, now_ms: int) -> Dict[str, object]:
        prof = self.profile or {}
        adapter = prof.get("adapter", {})
        adapter_details = adapter.get("details", {}) if isinstance(adapter, dict) else {}
        vehicle = prof.get("vehicle", {})
        logger_fit = prof.get("logger_fit") or compatibility.analyze_logger_fit(self.summary)
        ev_fit = prof.get("ev_sound_fit") or compatibility.analyze_ev_sound_fit(self.summary)
        modules = prof.get("modules") or self.summary.get("modules") or {}
        dtcs = prof.get("dtcs") or self.summary.get("dtcs") or {}
        pids = prof.get("standard_pids") or self.summary.get("pid_values") or {}

        ip_url, local_url = self.web.urls()
        return {
            "version": __version__,
            "now_ms": now_ms,
            "status": self.status,
            "progress": self.progress,
            "error": self.error,
            "link": self.link.upper(),
            "link_name": self.link_name or adapter.get("link", self.link.upper()),
            "protocol": (adapter.get("protocol") if isinstance(adapter, dict) else None)
                        or self.summary.get("protocol") or "--",
            "connect_selection": self.connect_selection,
            "connect_rows": [
                ("LINK", self.link.upper()),
                ("TARGET", self._target_label()),
                ("PROTOCOL", scanner.PROTOCOL_LABELS[self.protocol]),
                ("MODE", scanner.SCAN_MODE_LABELS[self.scan_mode]),
            ],
            "ble_scanning": self.ble_scanning,
            "requests": self.summary.get("requests", 0),
            "pid_count": len(pids) if isinstance(pids, dict) else 0,
            "module_count": len(modules) if isinstance(modules, dict) else 0,
            "log_file": self.log_file,
            "profile_file": self.profile_file,
            "vin": vehicle.get("vin", "") if isinstance(vehicle, dict) else "",
            "ecu_name": vehicle.get("ecu_name", "") if isinstance(vehicle, dict) else "",
            "powertrain": vehicle.get("powertrain_hint", "--") if isinstance(vehicle, dict) else "--",
            "logger_fit": logger_fit,
            "ev_fit": ev_fit,
            "modules": modules if isinstance(modules, dict) else {},
            "module_scroll": self.module_scroll,
            "dtcs": dtcs if isinstance(dtcs, dict) else {},
            "live_active": self.live_active,
            "live": self.live_snapshot,
            "adapter_details": adapter_details if isinstance(adapter_details, dict) else {},
            "data_dir": self.data_dir,
            "last_key": self.last_key,
            "build_git_hash": self.build_info["git_hash"],
            "build_date": self.build_info["build_date"],
            "web_running": self.web.running,
            "web_port": self.web_port,
            "web_url": ip_url,
            "web_local_url": local_url,
            "web_files_count": len(list_log_files(self.data_dir)),
            "web_downloads": self.web.downloads,
            "web_error": self.web.error,
        }
