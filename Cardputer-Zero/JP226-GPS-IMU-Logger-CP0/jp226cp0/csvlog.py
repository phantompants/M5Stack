"""Daily CSV files named by LOCAL date, with the firmware's exact schema.

CSV_HEADER is copied verbatim from kCsvHeader in the ESP32 firmware's
src/main.cpp (63 columns); tests/test_csv_header.py checks it against that
file whenever the file is reachable, so existing tooling (tools/merge_obd.py,
the KML scripts) keeps working. Columns this device cannot fill stay empty.
"""

import math
import os
import time
from typing import Dict, List, Optional

from . import config
from .imu import ImuSample
from .snapshot import GpsSnapshot
from .timeutil import format_local, format_utc

CSV_HEADER = (
    "timestamp,lat,lon,alt_m,speed_kmh,heading_deg,satellites,hdop,vdop,"
    "acc_x_g,acc_y_g,acc_z_g,gyro_x_dps,gyro_y_dps,gyro_z_dps,pitch_deg,"
    "roll_deg,g_total,roughness_index,local_timestamp,fix_valid,fix_age_ms,"
    "imu_available,imu_type,log_state,uptime_ms,telemetry_source,"
    "remote_sequence,accel_x_mps2,accel_y_mps2,accel_z_mps2,"
    "accel_rms_mps2,vertical_accel_rms_mps2,"
    "vertical_accel_peak_pos_mps2,vertical_accel_peak_neg_mps2,"
    "lateral_accel_peak_mps2,longitudinal_accel_peak_mps2,"
    "vibration_rms_mps2,imu_samples,gps_age_ms,packet_age_ms,"
    "packets_lost,duplicate_packets,crc_errors,remote_tx_failures,"
    "poi,poi_source,auto_place,waypoint_id,"
    "probe_cab_c,probe_canopy_c,probe_outside_c,"
    "wx_temp_c,wx_humidity_pct,wx_pressure_hpa,wx_wind_kmh,wx_wind_dir_deg,"
    "wx_precip_mm,wx_code,wx_age_min,road_type,road_surface,probe_labrax_c")
COLUMNS: List[str] = CSV_HEADER.split(",")

TELEMETRY_SOURCE = "LOCAL_GPS"


def csv_field(value) -> str:
    """Quote a text field if it holds a comma, quote or line break."""
    text = "" if value is None else str(value)
    if any(c in text for c in ',"\n\r'):
        return '"' + text.replace('"', '""') + '"'
    return text


def number(value: Optional[float], decimals: int, valid: bool = True) -> str:
    if value is None or not valid or not math.isfinite(value):
        return ""
    return "{:.{d}f}".format(value, d=decimals)


# ---------------------------------------------------------------------------
class DailyCsv:
    """Append rows to <directory>/<prefix><YYYY-MM-DD>.csv.

    The file is chosen per row from the local date. A file that starts with a
    different header (older firmware/app) is moved aside as <name>-old.csv
    (-old2.csv, ...) so today's rows start under the current header.
    """

    def __init__(self, directory: str, prefix: str, header: str):
        self.directory = directory
        self.prefix = prefix
        self.header = header
        self.current_path = ""
        self.last_error = ""
        self.rows_written = 0
        self._handle = None
        self._failed_ms: Optional[int] = None
        self._last_sync = 0.0

    def path_for(self, local_date: str) -> str:
        return os.path.join(self.directory,
                            "{}{}.csv".format(self.prefix, local_date))

    def close(self) -> None:
        if self._handle is not None:
            try:
                self._handle.flush()
                self._handle.close()
            except OSError:
                pass
        self._handle = None
        self.current_path = ""

    def _move_aside_if_header_differs(self, path: str) -> None:
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            first = handle.readline().strip()
        if first and first != self.header:
            stem = path[:-4]
            old = stem + "-old.csv"
            n = 2
            while os.path.exists(old) and n < 100:
                old = "{}-old{}.csv".format(stem, n)
                n += 1
            os.replace(path, old)

    def _open(self, path: str) -> None:
        self.close()
        os.makedirs(self.directory, exist_ok=True)
        self._move_aside_if_header_differs(path)
        handle = open(path, "a", encoding="utf-8", newline="")
        if handle.tell() == 0:
            handle.write(self.header + "\n")
            handle.flush()
        self._handle = handle
        self.current_path = path

    def append(self, local_date: str, line: str,
               now_ms: Optional[int]) -> bool:
        """Write one row; False if storage is failing. With a now_ms, retries
        after a failure are throttled to config.STORAGE_RETRY_INTERVAL_MS;
        with None (low-rate files such as waypoints) every call tries."""
        if (now_ms is not None and self._failed_ms is not None and
                now_ms - self._failed_ms < config.STORAGE_RETRY_INTERVAL_MS):
            return False
        path = self.path_for(local_date)
        try:
            if self._handle is None or self.current_path != path:
                self._open(path)
            self._handle.write(line + "\n")
            self._handle.flush()
            if time.monotonic() - self._last_sync > 5.0:
                os.fsync(self._handle.fileno())
                self._last_sync = time.monotonic()
        except OSError as exc:
            self.last_error = "{}: {}".format(path, exc)
            self._failed_ms = now_ms
            self.close()
            return False
        self._failed_ms = None
        self.last_error = ""
        self.rows_written += 1
        return True


# ---------------------------------------------------------------------------
def build_telemetry_row(snapshot: GpsSnapshot, log_state: str, utc_ms: int,
                        offset_s: int, uptime_ms: int,
                        imu_available: bool = False, imu_type: str = "none",
                        imu: Optional[ImuSample] = None,
                        poi: str = "", poi_source: str = "NONE",
                        waypoint_id: str = "", auto_place: str = "",
                        road_type: str = "", road_surface: str = "") -> str:
    """One telemetry row, built by column name so the order always follows
    CSV_HEADER."""
    g = config.STANDARD_GRAVITY_MPS2
    v: Dict[str, str] = {name: "" for name in COLUMNS}
    s = snapshot

    v["timestamp"] = format_utc(utc_ms)
    v["lat"] = number(s.latitude, 6, s.position_fresh)
    v["lon"] = number(s.longitude, 6, s.position_fresh)
    v["alt_m"] = number(s.altitude_m, 1, s.altitude_fresh)
    v["speed_kmh"] = number(s.speed_kmh, 2, s.speed_fresh)
    v["heading_deg"] = number(s.course_deg, 1, s.course_fresh)
    v["satellites"] = str(s.satellites) if s.satellites_valid else ""
    v["hdop"] = number(s.hdop, 2, s.hdop_valid)
    v["vdop"] = number(s.vdop, 2, s.vdop_valid)
    v["local_timestamp"] = format_local(utc_ms, offset_s)
    v["fix_valid"] = "1" if s.fix_valid else "0"
    v["fix_age_ms"] = "" if s.fix_age_ms is None else str(int(s.fix_age_ms))
    v["imu_available"] = "1" if imu_available else "0"
    v["imu_type"] = imu_type
    v["log_state"] = log_state
    v["uptime_ms"] = str(int(uptime_ms))
    v["telemetry_source"] = TELEMETRY_SOURCE
    v["gps_age_ms"] = v["fix_age_ms"]
    v["poi"] = csv_field(poi)
    v["poi_source"] = poi_source
    v["auto_place"] = csv_field(auto_place)
    v["waypoint_id"] = waypoint_id
    v["road_type"] = csv_field(road_type)
    v["road_surface"] = csv_field(road_surface)

    if imu is not None and imu.valid:
        v["acc_x_g"] = number(imu.ax_mps2 / g, 4)
        v["acc_y_g"] = number(imu.ay_mps2 / g, 4)
        v["acc_z_g"] = number(imu.az_mps2 / g, 4)
        v["gyro_x_dps"] = number(imu.gx_dps, 3)
        v["gyro_y_dps"] = number(imu.gy_dps, 3)
        v["gyro_z_dps"] = number(imu.gz_dps, 3)
        v["pitch_deg"] = number(imu.pitch_deg, 2)
        v["roll_deg"] = number(imu.roll_deg, 2)
        v["g_total"] = number(imu.g_total_mps2 / g, 4)
        v["roughness_index"] = number(imu.legacy_roughness_mps2 / g, 5)
        v["accel_x_mps2"] = number(imu.ax_mps2, 4)
        v["accel_y_mps2"] = number(imu.ay_mps2, 4)
        v["accel_z_mps2"] = number(imu.az_mps2, 4)
        v["accel_rms_mps2"] = number(imu.accel_rms_mps2, 4)
        v["vertical_accel_rms_mps2"] = number(imu.vertical_accel_rms_mps2, 4)
        v["vertical_accel_peak_pos_mps2"] = number(
            imu.vertical_accel_peak_pos_mps2, 4)
        v["vertical_accel_peak_neg_mps2"] = number(
            imu.vertical_accel_peak_neg_mps2, 4)
        v["lateral_accel_peak_mps2"] = number(imu.lateral_accel_peak_mps2, 4)
        v["longitudinal_accel_peak_mps2"] = number(
            imu.longitudinal_accel_peak_mps2, 4)
        v["vibration_rms_mps2"] = number(imu.vibration_rms_mps2, 4)
        v["imu_samples"] = str(int(imu.sample_count))

    return ",".join(v[name] for name in COLUMNS)
