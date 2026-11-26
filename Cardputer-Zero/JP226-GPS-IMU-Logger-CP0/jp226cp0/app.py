"""The logger core: ties GPS state, fix gate, clock, time zone, state machine,
CSV writing and waypoints together. No hardware, UI or threads in here, so the
whole pipeline runs on a desktop (see replay_lines and the tests).
"""

import math
import os
import sys
import time
from typing import Dict, Iterable, Optional

from . import __version__, config
from .clock import Clock
from .csvlog import DailyCsv, CSV_HEADER, build_telemetry_row
from .build_info import read_build_info
from .camera import CameraCapture
from .dino import DinoGame
import base64

from . import (bridge, env, espnow, exif, export, gps_power, hotspot,
               sysmon, webshare)
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
        saved_profile = self.store.get("vehicle_profile")
        self.obd_profile = vehicle_settings.load_profile(saved_profile)
        self.obd_message = ("SAVED PROFILE LOADED" if isinstance(saved_profile, dict)
                            else "SELECT PROFILE")
        self.obd_status = "NOT CONNECTED"
        self.waypoint_message = ""
        self.last_key = ""
        self.source_info = {"device": "", "baud": 0, "status": "NO SOURCE"}
        self.gps_source = gps_power.normalise(
            self.store.get("gps_source"), settings.gps_source)
        self.gps_power_status = ""
        self.bridge = bridge.BridgeState()
        self.bridge_outbox = bridge.BridgeOutbox(__version__)
        self.bridge_link_status = "NOT STARTED"
        self.controllers = bridge.Controllers()
        self.radio_devices = bridge.DeviceTable()
        self._bridge_tx = []
        self._status_sequence = 0
        self._position_sequence = 0
        self.trip_started_ms: Optional[int] = None
        self.last_waypoint_id = ""
        self.photo_message = ""
        self.last_photo = ""
        self.gallery_index = 0
        self.hotspot = hotspot.Hotspot()
        self.env_enabled = bool(self.store.get("env_grove", False))
        self.env_power_status = ""
        self.env_reader = None
        self.probes = {}   # (source, probe id) -> (now_ms, celsius or None)
        self.system = sysmon.SystemMonitor(data_dir)
        self._system_at: Optional[int] = None
        self.share_enabled = bool(self.store.get("share_enabled", False))
        try:
            zip_days = int(self.store.get("zip_days", 7))
        except (TypeError, ValueError):
            zip_days = 7
        self.zip_days = zip_days if zip_days in webshare.ZIP_DAY_CHOICES else 7
        self.exporter = export.NightlyExporter(data_dir, self.local_date_today)
        self.web = webshare.WebShare(data_dir, settings.web_port,
                                     lambda: self.zip_days,
                                     export_lock=self.exporter.lock)
        self._share_urls = []
        self._share_urls_at = None
        self.last_tick_ms = 0
        self.last_utc_ms: Optional[int] = None

    # ------------------------------------------------------------------
    def feed_line(self, line: str, now_ms: int) -> None:
        self.gps.feed_line(line, now_ms)

    def feed_bridge_line(self, line: str, now_ms: int) -> None:
        """A line from the AtomS3 bridge: its own $PJP226 lines update the
        bridge state; its GPS Base NMEA is used only when ATOM is the source."""
        if bridge.is_bridge_line(line):
            fields = self.bridge.handle(line, now_ms)
            if fields and fields[0] == "RX":
                self.radio_devices.note(fields, now_ms)
                self._note_temperatures(fields, now_ms)
                self._bridge_tx.extend(self.controllers.handle_rx(
                    fields, now_ms,
                    lambda action, value, text: self.handle_dial_command(
                        action, value, text, now_ms)))
        elif self.gps_source == gps_power.ATOM:
            self.gps.feed_line(line, now_ms)

    def _note_temperatures(self, fields, now_ms: int) -> None:
        try:
            report = espnow.decode_temperature(
                base64.b64decode(fields[3], validate=True))
        except (ValueError, IndexError):
            return
        if report is None:
            return
        source, readings = report
        for probe_id, celsius in readings:
            self.probes[(source, probe_id)] = (now_ms, celsius)

    def probe_rows(self, now_ms: int):
        """(label, celsius, age ms) per probe heard in the last 10 minutes."""
        rows = []
        counts = {}
        for (source, probe_id) in sorted(self.probes):
            stamp, celsius = self.probes[(source, probe_id)]
            if now_ms - stamp > 600_000:
                continue
            name = espnow.PROBE_SOURCES.get(source, "PROBE")
            counts[name] = counts.get(name, 0) + 1
            rows.append(("{} {}".format(name, counts[name]), celsius,
                         max(0, now_ms - stamp)))
        return rows

    # -- ENV page: M5 ENV unit on the Grove port ----------------------------
    def apply_env_power(self) -> None:
        enabled = self.env_enabled and self.gps_source != gps_power.GROVE
        self.env_power_status = gps_power.apply_env(enabled, self.gps_source)
        if enabled and self.env_reader is None:
            self.env_reader = env.GroveEnv()
            self.env_reader.start()
        elif not enabled and self.env_reader is not None:
            self.env_reader.stop()
            self.env_reader = None

    def toggle_env(self) -> None:
        self.env_enabled = not self.env_enabled
        self.store.put(env_grove=self.env_enabled)
        self.apply_env_power()

    def env_readings(self):
        if self.gps_source == gps_power.GROVE and self.env_enabled:
            return env.null_readings("GROVE GPS IN USE")
        if self.env_reader is None:
            return env.null_readings("OFF")
        return self.env_reader.readings()

    def bridge_lines_due(self, now_ms: int):
        """Bodies to send to the bridge now: HELLO and TIME once a second, and
        GPS,<0|1> while its NMEA forwarding does not match the GPS source."""
        utc_s = self.last_utc_ms // 1000 if self.last_utc_ms is not None else None
        offset_s = (self.location_time.offset_at(utc_s)
                    if utc_s is not None else None)
        battery = self.system.values
        bodies = self.bridge_outbox.due(
            now_ms, self.bridge, self.gps_source == gps_power.ATOM,
            utc_s, offset_s,
            battery_percent=battery["battery_pct"]
            if battery["battery_pct"] is not None else -1,
            battery_mv=battery["battery_mv"] or 0)
        bodies.extend(self._bridge_tx)
        self._bridge_tx = []
        bodies.extend(self.controllers.due(
            now_ms, lambda: self.dial_status_packet(now_ms),
            lambda: self.position_report_packet()))
        return bodies

    def bridge_status_text(self, now_ms: int) -> str:
        if not self.bridge.connected(now_ms):
            return self.bridge_link_status
        dials = len(self.controllers.fresh(now_ms))
        text = self.bridge.status_text(now_ms)
        return text + " {} DIAL".format(dials) if dials else text

    # -- M5Dial through the AtomS3 bridge (the ESP32 logger's handleDialCommand)
    def handle_dial_command(self, action: int, value: float, text: str,
                            now_ms: int):
        """Run one DialCommand. Returns (accepted, waypoint_id)."""
        text = text.strip().upper()
        field = {espnow.SET_ROAD: "road_type",
                 espnow.SET_SURFACE: "road_surface",
                 espnow.SET_SUSPENSION_FRONT: "suspension_front",
                 espnow.SET_SUSPENSION_REAR: "suspension_rear",
                 espnow.SET_LOAD: "vehicle_load"}.get(action)
        if action == espnow.MARK_WAYPOINT:
            self.create_waypoint(now_ms, category=text or "GENERIC")
            return bool(self.last_waypoint_id), self.last_waypoint_id
        if field is not None:
            if text not in vehicle_settings.CHOICES[field]:
                return False, ""
            self.vehicle[field] = text
        elif action in (espnow.SET_TYRE_FRONT, espnow.SET_TYRE_REAR):
            if not (math.isfinite(value) and 0.0 <= value <= 100.0):
                return False, ""
            sides = (("tyre_set_fl_psi", "tyre_set_fr_psi")
                     if action == espnow.SET_TYRE_FRONT
                     else ("tyre_set_rl_psi", "tyre_set_rr_psi"))
            for name in sides:
                self.vehicle[name] = int(round(value))
        else:
            return False, ""
        self.store.put(vehicle_setup=self.vehicle)
        return True, ""

    def dial_status_packet(self, now_ms: int) -> bytes:
        snap = self.snapshot
        self._status_sequence += 1
        last = self.waypoints.last
        poi = self.waypoints.poi_for(snap)[0]
        storage_ok = not (self.telemetry.last_error or self.waypoints.last_error)
        return espnow.encode_status(
            self._status_sequence, now_ms,
            (now_ms - self.trip_started_ms) // 1000
            if self.trip_started_ms is not None else 0,
            snap.speed_kmh if snap.speed_fresh else 0.0,
            float(self.vehicle["tyre_set_fl_psi"]),
            float(self.vehicle["tyre_set_rl_psi"]),
            snap.fix_valid, snap.satellites if snap.satellites_valid else 0,
            espnow.LOG_MODES.get(self.machine.mode, 0),
            self.vehicle["road_type"], self.vehicle["suspension_front"],
            self.vehicle["suspension_rear"], self.vehicle["vehicle_load"],
            poi, last["id"] if last else "",
            battery_percent=self.system.values["battery_pct"]
            if self.system.values["battery_pct"] is not None else -1,
            link_flags=espnow.LINK_SD_READY if storage_ok else 0,
            road_surface=self.vehicle["road_surface"])

    def position_report_packet(self) -> bytes:
        snap = self.snapshot
        self._position_sequence += 1
        utc_ms = self.last_utc_ms
        offset_min = (self.location_time.offset_at(utc_ms // 1000) // 60
                      if utc_ms is not None else 0)
        return espnow.encode_position(
            self._position_sequence, utc_ms, offset_min, snap.fix_valid,
            snap.position_fresh, snap.satellites if snap.satellites_valid else 0,
            espnow.LOG_MODES.get(self.machine.mode, 0), snap.latitude,
            snap.longitude, snap.altitude_m,
            snap.speed_kmh if snap.speed_fresh else 0.0, snap.course_deg,
            snap.hdop,
            imu=self.imu.latest_sample() if self.imu.available else None)

    def tick(self, now_ms: int) -> GpsSnapshot:
        """One pass of the firmware's loop(): snapshot, clock, zone, state
        machine, and a CSV row when one is due."""
        snap = take_gps_snapshot(self.gps, self.guard, now_ms)
        self.snapshot = snap
        self.last_tick_ms = now_ms
        if snap.fix_valid and self.trip_started_ms is None:
            self.trip_started_ms = now_ms
        self.clock.sync_from_gps(snap.utc_valid, snap.utc_epoch_ms, now_ms)
        self.location_time.update(snap.latitude, snap.longitude,
                                  snap.position_fresh, snap.speed_kmh, now_ms)
        utc_ms = self.clock.now_utc_ms(now_ms)
        self.last_utc_ms = utc_ms
        now_utc = utc_ms // 1000 if utc_ms is not None else 0
        self.machine.update(snap, now_ms, now_utc)
        if self._system_at is None or now_ms - self._system_at >= 2000:
            self._system_at = now_ms
            self.system.sample()
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
    def create_waypoint(self, now_ms: Optional[int] = None,
                        category: str = "GENERIC") -> str:
        """Waypoint key, or MarkWaypoint from the M5Dial with its category.
        Returns the on-screen message; last_waypoint_id is the new ID or ''."""
        self.last_waypoint_id = ""
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
            if photo_reference:
                self._tag_photo(photo_reference, snap, utc_ms, offset_s)
            record = self.waypoints.create(
                snap, utc_ms, offset_s,
                category=category if snap.fix_valid else "PHOTO",
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
                self.last_waypoint_id = record["id"]
                self.waypoint_message = record["id"] + " SAVED"
                if self.camera_message == "PHOTO ERROR":
                    self.waypoint_message += " PHOTO ERROR"
        return self.waypoint_message

    # -- CAMERA and GALLERY pages ------------------------------------------
    def _tag_photo(self, photo_reference: str, snap: GpsSnapshot,
                   utc_ms: Optional[int], offset_s: int) -> bool:
        """Write the GPS fix and capture time into the photo's EXIF."""
        have_position = snap.fix_valid and snap.position_fresh
        payload = exif.build_exif(
            utc_ms, offset_s,
            latitude=snap.latitude if have_position else None,
            longitude=snap.longitude if have_position else None,
            altitude_m=snap.altitude_m if snap.altitude_fresh else None,
            speed_kmh=snap.speed_kmh if snap.speed_fresh else None,
            course_deg=snap.course_deg if snap.course_fresh else None,
            hdop=snap.hdop if snap.hdop_valid else None,
            satellites=snap.satellites if snap.satellites_valid else None,
            software="JP226 GPS-IMU Logger " + __version__)
        try:
            exif.write_exif(self.camera.path_of(photo_reference), payload)
            return True
        except (OSError, ValueError) as exc:
            print("photo EXIF: {}".format(exc), file=sys.stderr)
            return False

    def photo_name(self, utc_ms: Optional[int], offset_s: int) -> str:
        """IMG_YYYYMMDD_HHMMSS (local time), unique in the photo folder."""
        if utc_ms is not None:
            base = civil_from_epoch(utc_ms // 1000, offset_s).strftime(
                "IMG_%Y%m%d_%H%M%S")
        else:
            base = "IMG_NOCLOCK_{}".format(int(time.time()))
        name, n = base, 1
        while os.path.exists(os.path.join(self.camera.photo_dir, name + ".jpg")):
            n += 1
            name = "{}_{}".format(base, n)
        return name

    def take_photo(self, now_ms: Optional[int] = None) -> str:
        """CAMERA page: a photo only (no waypoint), with GPS in its EXIF."""
        if now_ms is None:
            now_ms = self.last_tick_ms
        snap = self.snapshot
        utc_ms = self.clock.now_utc_ms(now_ms)
        offset_s = self.location_time.offset_at(utc_ms // 1000) if utc_ms else 0
        if not self.camera.available:
            self.photo_message = "NO CAMERA"
            return self.photo_message
        try:
            reference = self.camera.capture_named(self.photo_name(utc_ms, offset_s))
        except (OSError, RuntimeError) as exc:
            print("photo: {}".format(exc), file=sys.stderr)
            self.photo_message = "PHOTO ERROR"
            return self.photo_message
        tagged = self._tag_photo(reference, snap, utc_ms, offset_s)
        self.last_photo = reference.split("/")[-1]
        self.gallery_index = 0
        self.photo_message = "SAVED" + ("" if snap.fix_valid and tagged
                                        else " (NO GPS)")
        return self.photo_message

    def camera_page(self, active: bool) -> None:
        """Start the live preview while the CAMERA page is shown."""
        if active and not self.camera.live.running:
            self.camera.live.start()
        elif not active and self.camera.live.running:
            self.camera.live.stop()

    def gallery_photos(self):
        return self.camera.list_photos()

    def gallery_step(self, delta: int) -> None:
        count = len(self.gallery_photos())
        self.gallery_index = (self.gallery_index + delta) % count if count else 0

    # -- SHARE page hotspot ------------------------------------------------
    def toggle_hotspot(self) -> None:
        """Hotspot on also turns the web page on: that is what it is for."""
        if not self.hotspot.active and not self.web.running:
            self.toggle_share()
        self.hotspot.toggle()
        self._share_urls_at = None

    def select_vehicle_setting(self, delta: int) -> None:
        self.vehicle_selection = (
            self.vehicle_selection + delta) % len(vehicle_settings.FIELD_LABELS)

    def adjust_vehicle_setting(self, delta: int) -> None:
        field = vehicle_settings.FIELD_LABELS[self.vehicle_selection][0]
        if vehicle_settings.adjust(self.vehicle, field, delta):
            self.store.put(vehicle_setup=self.vehicle)

    def toggle_gps_source(self) -> str:
        """Step through LoRa Cap, Grove and AtomS3 GPS; saved at once.
        The caller restarts the serial reader and applies the power rails."""
        self.gps_source = gps_power.next_source(self.gps_source)
        self.store.put(gps_source=self.gps_source)
        self.gps_power_status = ""
        return self.gps_source

    # -- SHARE page: Wi-Fi web server + nightly KML/GPX export -------------
    def local_date_today(self) -> Optional[str]:
        utc_ms = self.last_utc_ms
        if utc_ms is None:
            return None
        utc_s = utc_ms // 1000
        return local_date_text(utc_s, self.location_time.offset_at(utc_s))

    def start_services(self) -> None:
        """Live mode only: nightly exporter, and the web server if enabled."""
        self.exporter.start()
        if self.share_enabled:
            self.web.start()

    def toggle_share(self) -> None:
        self.share_enabled = not self.share_enabled
        self.store.put(share_enabled=self.share_enabled)
        if self.share_enabled:
            self.web.start()
        else:
            self.web.stop()
        self._share_urls_at = None

    def change_zip_days(self, delta: int) -> None:
        choices = webshare.ZIP_DAY_CHOICES
        self.zip_days = choices[(choices.index(self.zip_days) + delta) % len(choices)]
        self.store.put(zip_days=self.zip_days)

    def export_now(self) -> None:
        if self.exporter.is_alive():
            self.exporter.request_now()
        else:
            self.exporter.run_once(include_today=True)

    def share_urls(self, now_ms: int):
        if self._share_urls_at is None or now_ms - self._share_urls_at > 10_000:
            # Listed even while the server is off, so the address is known.
            self._share_urls = self.web.labelled_urls()
            self._share_urls_at = now_ms
        return self._share_urls

    def press_dino(self, now_ms: int) -> None:
        self.dino.press(now_ms)

    def toggle_dino_character(self) -> None:
        self.dino.toggle_character()

    def select_obd_profile_field(self, delta: int) -> None:
        self.obd_selection = (
            self.obd_selection + delta) % len(vehicle_settings.PROFILE_FIELD_LABELS)

    def adjust_obd_profile(self, delta: int) -> None:
        field = vehicle_settings.PROFILE_FIELD_LABELS[self.obd_selection][0]
        if vehicle_settings.adjust_profile(self.obd_profile, field, delta):
            self.obd_message = "UNSAVED CHANGES"

    def change_obd_profile_field(self, field: str, delta: int = 1) -> None:
        """Number-key shortcut: select this profile field and step its value."""
        fields = [name for name, _label in vehicle_settings.PROFILE_FIELD_LABELS]
        self.obd_selection = fields.index(field)
        self.adjust_obd_profile(delta)

    def save_obd_profile(self) -> None:
        self.store.put(vehicle_profile=dict(self.obd_profile))
        self.obd_message = "PROFILE SAVED"

    def connect_obd(self) -> None:
        self.obd_status = "NOT IMPLEMENTED"
        self.obd_message = "NO OBD LINK YET"

    def close(self) -> None:
        self.camera.live.stop()
        if self.env_reader is not None:
            self.env_reader.stop()
        self.web.stop()
        if self.exporter.is_alive():
            self.exporter.stop()
            self.exporter.join(timeout=5)
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
            "gps_source": gps_power.LABELS[self.gps_source],
            "gps_power_status": self.gps_power_status,
            "bridge_status": self.bridge_status_text(now_ms),
            "radio_devices": self.radio_devices.rows(
                now_ms, set(self.controllers.fresh(now_ms))),
            "bridge_connected": self.bridge.connected(now_ms),
            "bridge_channel": self.bridge.channel,
            "system": self.system.values,
            "env": self.env_readings(),
            "probes": self.probe_rows(now_ms),
            "camera_live": self.camera.live.status,
            "camera_frame": self.camera.live.frame(),
            "photo_message": self.photo_message,
            "last_photo": self.last_photo,
            "gallery_photos": self.gallery_photos(),
            "gallery_index": self.gallery_index,
            "photo_dir": self.camera.photo_dir,
            "hotspot_available": self.hotspot.available,
            "hotspot_on": self.hotspot.active,
            "hotspot_busy": self.hotspot.busy,
            "hotspot_ssid": self.hotspot.ssid,
            "hotspot_password": self.hotspot.password,
            "hotspot_address": self.hotspot.address,
            "hotspot_error": self.hotspot.error,
            "share_on": self.web.running,
            "share_error": self.web.error,
            "share_urls": self.share_urls(now_ms),
            "share_port": self.web.port,
            "zip_days": webshare.zip_label(self.zip_days),
            "export_status": self.exporter.status,
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
            "obd_profile": [
                (label, self.obd_profile[field])
                for field, label in vehicle_settings.PROFILE_FIELD_LABELS],
            "obd_status": self.obd_status,
            "obd_message": self.obd_message,
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
