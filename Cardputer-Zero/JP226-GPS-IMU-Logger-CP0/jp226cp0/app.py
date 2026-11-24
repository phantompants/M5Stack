"""The logger core: ties GPS state, fix gate, clock, time zone, state machine,
CSV writing and waypoints together. No hardware, UI or threads in here, so the
whole pipeline runs on a desktop (see replay_lines and the tests).
"""

import os
import sys
import threading
import time
import datetime
from typing import Dict, Iterable, Optional

from . import __version__, config, obdscan
from .clock import Clock
from .csvlog import DailyCsv, CSV_HEADER, build_telemetry_row
from .build_info import read_build_info
from .camera import CameraCapture
from .dino import DinoGame
from .elm327 import Elm327
from .imu import ImuReader, create_default_imu
from .location_time import LocationTime
from .logmode import LogStateMachine
from .nmea import GpsState, parse_date, parse_line, parse_time_of_day
from .obd import NullObd, ObdSource
from .plausibility import Plausibility
from .settings import Settings
from .snapshot import GpsSnapshot, take_gps_snapshot
from .statestore import StateStore
from .timeutil import (civil_from_epoch, days_from_civil, local_date_text)
from .waypoints import WaypointStore
from . import vehicle as vehicle_settings

STATE_FILE_NAME = "logger_state.json"


class LoggerApp:
    def __init__(self, settings: Settings, data_dir: str,
                 clock: Optional[Clock] = None,
                 imu: Optional[ImuReader] = None,
                 obd: Optional[ObdSource] = None,
                 camera: Optional[CameraCapture] = None):
        self.settings = settings
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.store = StateStore(os.path.join(data_dir, STATE_FILE_NAME))
        self.clock = clock if clock is not None else Clock(settings.clock_source)
        self.imu = imu if imu is not None else create_default_imu()
        self.obd = obd if obd is not None else NullObd()  # extension point only
        self.gps = GpsState()
        self.guard = Plausibility()
        self.location_time = LocationTime(
            self.store, settings.timezone, settings.timezone_auto)
        self.machine = LogStateMachine(self.store)
        self.telemetry = DailyCsv(data_dir, config.FILE_PREFIX, CSV_HEADER)
        self.waypoints = WaypointStore(data_dir, self.store)
        self.snapshot = GpsSnapshot()
        self.vehicle: Dict[str, object] = vehicle_settings.load(
            self.store.get("vehicle_setup"))
        if self.store.get("vehicle_setup") is None:
            self.store.put(vehicle_setup=self.vehicle)
        self.vehicle_selection = 0
        self.camera = camera if camera is not None else CameraCapture(data_dir)
        self.camera_message = ""
        self.build_info = read_build_info()
        try:
            dino_best = int(self.store.get("dino_pb", 0))
        except (TypeError, ValueError):
            dino_best = 0
        self.dino = DinoGame(dino_best)
        self.obd_selection = 0
        self.obd_link = self.store.get("obd_link", obdscan.DEFAULTS["link"])
        if self.obd_link not in obdscan.LINK_CHOICES:
            self.obd_link = obdscan.DEFAULTS["link"]
        self.obd_protocol = str(self.store.get(
            "obd_protocol", obdscan.DEFAULTS["protocol"])).upper()
        if self.obd_protocol not in obdscan.PROTOCOL_LABELS:
            self.obd_protocol = obdscan.DEFAULTS["protocol"]
        self.obd_full_scan = bool(self.store.get("obd_full_scan", False))
        self.obd_scan_status = "READY"
        self.obd_scan_progress = ""
        self.obd_scan_summary = ""
        self._obd_cancel = threading.Event()
        self._obd_thread: Optional[threading.Thread] = None
        self.waypoint_message = ""
        self.last_key = ""
        self.source_info = {"device": "", "baud": 0, "status": "NO SOURCE"}
        self.last_tick_ms = 0
        self.last_utc_ms: Optional[int] = None

    # ------------------------------------------------------------------
    def feed_line(self, line: str, now_ms: int) -> None:
        self.gps.feed_line(line, now_ms)

    def tick(self, now_ms: int) -> GpsSnapshot:
        """One pass of the firmware's loop(): snapshot, clock, zone, state
        machine, and a CSV row when one is due."""
        snap = take_gps_snapshot(self.gps, self.guard, now_ms)
        self.snapshot = snap
        self.last_tick_ms = now_ms
        self.clock.sync_from_gps(snap.utc_valid, snap.utc_epoch_ms, now_ms)
        self.location_time.update(snap.latitude, snap.longitude,
                                  snap.position_fresh, snap.speed_kmh, now_ms)
        utc_ms = self.clock.now_utc_ms(now_ms)
        self.last_utc_ms = utc_ms
        now_utc = utc_ms // 1000 if utc_ms is not None else 0
        self.machine.update(snap, now_ms, now_utc)
        self.imu.update(now_ms)
        self.obd.update(now_ms)  # extension point: result unused for now
        self.dino.update(now_ms, snap.speed_kmh if snap.speed_fresh else 0.0, 160)
        if self.dino.take_new_best():
            self.store.put(dino_pb=self.dino.best)
        if utc_ms is not None and self.machine.log_is_due(now_ms, now_utc):
            if self._write_row(snap, now_ms, utc_ms):
                self.machine.on_log_succeeded(now_ms, now_utc)
        return snap

    def _write_row(self, snap: GpsSnapshot, now_ms: int, utc_ms: int) -> bool:
        offset_s = self.location_time.offset_at(utc_ms // 1000)
        imu_sample = self.imu.snapshot_and_reset() if self.imu.available else None
        poi, poi_source, waypoint_id = self.waypoints.poi_for(snap)
        row = build_telemetry_row(
            snap, self.machine.mode, utc_ms, offset_s, now_ms,
            imu_available=bool(self.imu.available and imu_sample is not None
                               and imu_sample.valid),
            imu_type=self.imu.type_name, imu=imu_sample,
            poi=poi, poi_source=poi_source, waypoint_id=waypoint_id,
            road_type=self.vehicle["road_type"],
            road_surface=self.vehicle["road_surface"])
        date = local_date_text(utc_ms // 1000, offset_s)
        return self.telemetry.append(date, row, now_ms)

    # ------------------------------------------------------------------
    def create_waypoint(self, now_ms: Optional[int] = None) -> str:
        """Waypoint key. Returns the on-screen message."""
        if now_ms is None:
            now_ms = self.last_tick_ms
        snap = self.snapshot
        utc_ms = self.clock.now_utc_ms(now_ms)
        self.camera_message = ""
        if utc_ms is None and not snap.fix_valid:
            system_utc_ms = int(time.time() * 1000)
            utc_ms = system_utc_ms if system_utc_ms >= 1704067200000 else None
        if utc_ms is None:
            self.waypoint_message = "NO CLOCK"
        elif not snap.fix_valid and not self.camera.available:
            self.camera_message = "NO CAMERA"
            self.waypoint_message = "NO FIX / NO CAMERA"
        else:
            offset_s = self.location_time.offset_at(utc_ms // 1000)
            photo_reference = ""
            self.camera_message = ""
            if self.camera.available:
                try:
                    photo_reference = self.camera.capture_waypoint(
                        self.waypoints.next_waypoint_id)
                except (OSError, RuntimeError) as exc:
                    self.camera_message = "PHOTO ERROR"
                    print("waypoint photo: {}".format(exc), file=sys.stderr)
            else:
                self.camera_message = "NO CAMERA"
            if not snap.fix_valid and not photo_reference:
                self.waypoint_message = "PHOTO ERROR"
                return self.waypoint_message
            record = self.waypoints.create(
                snap, utc_ms, offset_s,
                category="GENERIC" if snap.fix_valid else "PHOTO",
                road_type=self.vehicle["road_type"],
                road_surface=self.vehicle["road_surface"],
                vehicle_setup=self.vehicle,
                photo_reference=photo_reference,
                allow_no_fix=not snap.fix_valid)
            if record is None:
                if photo_reference:
                    try:
                        self.camera.remove_photo(photo_reference)
                    except OSError as exc:
                        self.camera_message = "PHOTO CLEANUP ERROR"
                        print("waypoint photo cleanup: {}".format(exc),
                              file=sys.stderr)
                self.waypoint_message = "WRITE FAILED"
            else:
                self.waypoint_message = record["id"] + " SAVED"
                if self.camera_message == "PHOTO ERROR":
                    self.waypoint_message += " PHOTO ERROR"
        return self.waypoint_message

    def select_vehicle_setting(self, delta: int) -> None:
        self.vehicle_selection = (
            self.vehicle_selection + delta) % len(vehicle_settings.FIELD_LABELS)

    def adjust_vehicle_setting(self, delta: int) -> None:
        field = vehicle_settings.FIELD_LABELS[self.vehicle_selection][0]
        if vehicle_settings.adjust(self.vehicle, field, delta):
            self.store.put(vehicle_setup=self.vehicle)

    def press_dino(self, now_ms: int) -> None:
        self.dino.press(now_ms)

    def toggle_dino_character(self) -> None:
        self.dino.toggle_character()

    def select_obd_option(self, delta: int) -> None:
        self.obd_selection = (self.obd_selection + delta) % 3

    def adjust_obd_option(self, delta: int) -> None:
        if self.obd_selection == 0:
            index = obdscan.LINK_CHOICES.index(self.obd_link)
            self.obd_link = obdscan.LINK_CHOICES[
                (index + delta) % len(obdscan.LINK_CHOICES)]
            self.store.put(obd_link=self.obd_link)
        elif self.obd_selection == 1:
            protocols = tuple(obdscan.PROTOCOL_LABELS)
            index = protocols.index(self.obd_protocol)
            self.obd_protocol = protocols[
                (index + delta) % len(protocols)]
            self.store.put(obd_protocol=self.obd_protocol)
        else:
            self.obd_full_scan = not self.obd_full_scan
            self.store.put(obd_full_scan=self.obd_full_scan)

    def start_obd_scan(self) -> None:
        if self._obd_thread is not None and self._obd_thread.is_alive():
            self.obd_scan_status = "SCAN ALREADY RUNNING"
            return
        cfg = dict(obdscan.DEFAULTS)
        cfg.update({
            "link": self.obd_link,
            "protocol": self.obd_protocol,
            "steps": ("adapter,standard,modules,dids" if self.obd_full_scan
                      else "adapter,standard"),
            "out_dir": os.path.join(self.data_dir, "obd_scans"),
        })
        config_path = os.path.join(self.data_dir, "obd_scan.cfg")
        if os.path.isfile(config_path):
            try:
                configured = obdscan.load_config(config_path)
            except (OSError, ValueError) as exc:
                self.obd_scan_status = "CONFIG ERROR"
                self.obd_scan_progress = str(exc)
                return
            configured.update({
                "link": self.obd_link,
                "protocol": self.obd_protocol,
                "steps": cfg["steps"],
                "out_dir": cfg["out_dir"],
            })
            cfg = configured
        self._obd_cancel.clear()
        self.obd_scan_status = "STARTING"
        self.obd_scan_progress = ""
        self.obd_scan_summary = ""
        self._obd_thread = threading.Thread(
            target=self._run_obd_scan, args=(cfg,),
            name="jp226-obd-scan", daemon=True)
        self._obd_thread.start()

    def _run_obd_scan(self, cfg: Dict[str, str]) -> None:
        transport = None
        log = None
        try:
            transport = obdscan.open_link(cfg)
            os.makedirs(cfg["out_dir"], exist_ok=True)
            stamp = datetime.datetime.now(datetime.timezone.utc).strftime(
                "%Y%m%dT%H%M%SZ")
            log_path = os.path.join(
                cfg["out_dir"], "obd_scan_{}.jsonl".format(stamp))
            log = obdscan.ScanLog(log_path)
            log.write({"step": "start", "link": transport.name,
                       "config": {k: v for k, v in cfg.items()}})
            self.obd_scan_status = "SCANNING"
            scanner = obdscan.Scanner(
                Elm327(transport), log, cfg,
                progress=self._set_obd_progress,
                cancel_event=self._obd_cancel)
            summary = scanner.run()
            pids = summary.get("pids")
            modules = summary.get("modules")
            if not isinstance(pids, list) or not isinstance(modules, dict):
                raise RuntimeError("OBD scanner returned an invalid summary")
            self.obd_scan_summary = "Protocol {}  {} PIDs  {} modules".format(
                summary.get("protocol") or "--",
                len(pids), len(modules))
            self.obd_scan_progress = os.path.basename(log_path)
            self.obd_scan_status = (
                "CANCELLED" if summary.get("cancelled") else
                "NO ECU" if summary.get("protocol") is None else "COMPLETE")
        except (obdscan.elm327.LinkError, OSError, ValueError, RuntimeError) as exc:
            self.obd_scan_status = "SCAN ERROR"
            self.obd_scan_progress = str(exc)
            print("OBD scan: {}".format(exc), file=sys.stderr)
        except Exception as exc:
            self.obd_scan_status = "SCAN ERROR"
            self.obd_scan_progress = "{}: {}".format(type(exc).__name__, exc)
            print("OBD scan failed: {}".format(exc), file=sys.stderr)
        finally:
            if log is not None:
                log.close()
            if transport is not None:
                transport.close()

    def _set_obd_progress(self, progress: str) -> None:
        self.obd_scan_status = "SCANNING"
        self.obd_scan_progress = progress

    def close(self) -> None:
        if self._obd_thread is not None and self._obd_thread.is_alive():
            self._obd_cancel.set()
            self._obd_thread.join(timeout=6)
            if self._obd_thread.is_alive():
                print("OBD scan did not stop before app shutdown",
                      file=sys.stderr)
        self.telemetry.close()
        self.waypoints.close()

    # ------------------------------------------------------------------
    def ui_model(self, now_ms: int) -> Dict:
        """Everything the UI pages show, as plain values."""
        snap = self.snapshot
        utc_ms = self.last_utc_ms
        offset_s = self.location_time.offset_at(utc_ms // 1000) if utc_ms else 0
        local_text = utc_text = ""
        if utc_ms:
            t = civil_from_epoch(utc_ms // 1000)
            utc_text = "{:%Y-%m-%d %H:%M:%S}Z".format(t)
            local_text = "{:%H:%M:%S}".format(
                civil_from_epoch(utc_ms // 1000, offset_s))
        next_due = ""
        if self.machine.next_stopped_due_utc and utc_ms:
            wait = self.machine.next_stopped_due_utc - utc_ms // 1000
            next_due = "{} min".format(max(0, wait) // 60) if wait >= 60 else \
                "{} s".format(max(0, wait))
        last = self.waypoints.last
        imu_sample = self.imu.latest_sample()
        return {
            "version": __version__,
            "now_ms": now_ms,
            "speed_kmh": snap.speed_kmh if snap.speed_fresh else None,
            "fix_valid": snap.fix_valid,
            "satellites": snap.satellites if snap.satellites_valid else None,
            "hdop": snap.hdop if snap.hdop_valid else None,
            "vdop": snap.vdop if snap.vdop_valid else None,
            "lat": snap.latitude if snap.position_fresh else None,
            "lon": snap.longitude if snap.position_fresh else None,
            "alt": snap.altitude_m if snap.altitude_fresh else None,
            "heading": snap.course_deg if snap.course_fresh else None,
            "utc_text": utc_text,
            "local_text": local_text,
            "zone_name": self.location_time.zone_name,
            "zone_source": self.location_time.zone_source,
            "log_state": self.machine.mode,
            "rows_written": self.telemetry.rows_written,
            "file_name": os.path.basename(self.telemetry.current_path),
            "next_due": next_due,
            "storage_error": (self.telemetry.last_error or self.waypoints.last_error
                              or self.store.last_error),
            "clock_source": self.clock.source,
            "clock_gps_synced": self.clock.gps_synced,
            "waypoint_last": last,
            "waypoint_message": self.waypoint_message,
            "camera_message": self.camera_message,
            "camera_available": self.camera.available,
            "vehicle_settings": [
                (label, vehicle_settings.display_value(field, self.vehicle[field]))
                for field, label in vehicle_settings.FIELD_LABELS],
            "vehicle_selection": self.vehicle_selection,
            "gps_device": self.source_info.get("device", ""),
            "gps_baud": self.source_info.get("baud", 0),
            "gps_status": self.source_info.get("status", ""),
            "sentences_ok": self.gps.sentences_ok,
            "sentences_bad": self.gps.sentences_bad,
            "sentence_counts": dict(self.gps.counts),
            "last_key": self.last_key,
            "imu": self.imu.type_name,
            "imu_available": self.imu.available,
            "imu_error": self.imu.last_error,
            "imu_sample": imu_sample,
            "dino_state": self.dino.state,
            "dino_moving": self.dino.moving,
            "dino_character": self.dino.character,
            "dino_y": self.dino.y,
            "dino_obstacles": [
                {"x": o.x, "width": o.width, "height": o.height,
                 "cactus": o.cactus} for o in self.dino.obstacles],
            "dino_score": self.dino.score,
            "dino_best": self.dino.best,
            "dino_status": self.dino.status_text(),
            "obd_selection": self.obd_selection,
            "obd_settings": [
                ("ADAPTER", self.obd_link.upper()),
                ("PROTOCOL", obdscan.PROTOCOL_LABELS[self.obd_protocol]),
                ("SCAN", "FULL READ-ONLY" if self.obd_full_scan else "QUICK"),
            ],
            "obd_scan_status": self.obd_scan_status,
            "obd_scan_progress": self.obd_scan_progress,
            "obd_scan_summary": self.obd_scan_summary,
            "obd_config_path": os.path.join(self.data_dir, "obd_scan.cfg"),
            "build_git_hash": self.build_info["git_hash"],
            "build_date": self.build_info["build_date"],
            "data_dir": self.data_dir,
        }


# ----------------------------------------------------------------------
def rmc_epoch_ms(line: str) -> Optional[int]:
    """UTC epoch ms of an RMC sentence (None for anything else/unparseable)."""
    sentence = parse_line(line)
    if sentence is None or sentence.kind != "RMC":
        return None
    f = sentence.fields
    tod = parse_time_of_day(f[0]) if len(f) > 0 else None
    date = parse_date(f[8]) if len(f) > 8 else None
    if tod is None or date is None:
        return None
    seconds = (days_from_civil(*date) * 86400 + tod[0] * 3600 +
               tod[1] * 60 + tod[2])
    return seconds * 1000 + tod[3] * 10


def replay_lines(app: LoggerApp, lines: Iterable[str], speed: float = 0.0,
                 sleep=None, max_seconds: Optional[float] = None) -> int:
    """Feed NMEA lines through the app on a VIRTUAL clock taken from the RMC
    timestamps, ticking once per GPS second after all of that second's
    sentences have arrived. speed=0 runs as fast as possible; speed=N paces
    at N x real time. max_seconds stops the replay that many GPS seconds after
    the first RMC. Returns the final virtual time in ms."""
    import time as _time
    sleep = sleep if sleep is not None else _time.sleep
    virtual_ms = 0
    base = None
    for raw in lines:
        epoch = rmc_epoch_ms(raw)
        if epoch is not None:
            if base is None:
                base = epoch
            new = max(virtual_ms, epoch - base)
            if max_seconds is not None and new > max_seconds * 1000:
                break
            if new > virtual_ms:
                app.tick(virtual_ms)
                if speed > 0:
                    sleep((new - virtual_ms) / 1000.0 / speed)
                virtual_ms = new
        app.feed_line(raw, virtual_ms)
    app.tick(virtual_ms)
    return virtual_ms
