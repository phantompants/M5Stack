"""Waypoints: waypoints_YYYY-MM-DD.csv with the firmware's 28 columns.

Port of WaypointStore::create/appendWaypoint/appendEvent. A key press creates
a CREATE row (ID WP00001, WP00002, ... never reused); what the Zero has no
source for (tyre pressures, suspension, load, photo reference, road type) stays
empty. Events go to events_YYYY-MM-DD.csv like the firmware's WAYPOINT_CREATED.
"""

import math
from typing import Dict, Optional

from . import config
from .csvlog import DailyCsv, csv_field
from .snapshot import GpsSnapshot
from .statestore import StateStore
from .timeutil import format_utc, local_date_text
from .tzfallback import distance_km

WAYPOINT_HEADER = (
    "waypoint_id,timestamp,latitude,longitude,altitude_m,hdop,speed_kmh,"
    "heading_deg,poi_name,poi_source,user_note,photo_reference,road_type,"
    "tyre_set_front_psi,tyre_set_rear_psi,suspension_front,"
    "suspension_rear,vehicle_load,telemetry_source,category,revision,"
    "record_action,recorded_at,road_surface,tyre_set_fl_psi,"
    "tyre_set_fr_psi,tyre_set_rl_psi,tyre_set_rr_psi")
WAYPOINT_COLUMNS = WAYPOINT_HEADER.split(",")

EVENTS_HEADER = "timestamp,event_type,waypoint_id,detail,telemetry_source"


def sanitize_text(text: str, maximum: int = 64) -> str:
    """Printable ASCII only, trimmed, at most `maximum` characters."""
    cleaned = "".join(c for c in text if " " <= c <= "~")[:maximum]
    return cleaned.strip()


def _num(value: Optional[float], decimals: int) -> str:
    if value is None or not math.isfinite(value):
        return ""
    return "{:.{d}f}".format(value, d=decimals)


class WaypointStore:
    def __init__(self, directory: str, store: StateStore,
                 telemetry_source: str = "LOCAL_GPS"):
        self.store = store
        self.telemetry_source = telemetry_source
        self._waypoints = DailyCsv(directory, config.WAYPOINT_PREFIX,
                                   WAYPOINT_HEADER)
        self._events = DailyCsv(directory, config.EVENTS_PREFIX, EVENTS_HEADER)

    @property
    def last(self) -> Optional[Dict]:
        record = self.store.get("wp_last")
        return record if isinstance(record, dict) and record.get("id") else None

    def distance_from_last_m(self, latitude: float,
                             longitude: float) -> Optional[float]:
        last = self.last
        if last is None:
            return None
        return distance_km(last["lat"], last["lon"], latitude, longitude) * 1000.0

    def poi_for(self, snapshot: GpsSnapshot):
        """(poi, poi_source, waypoint_id) for a telemetry row: the last
        waypoint if within POI_RADIUS_M, else ('', 'NONE', '')."""
        last = self.last
        if last is not None and snapshot.position_fresh:
            d = self.distance_from_last_m(snapshot.latitude, snapshot.longitude)
            if d is not None and d <= config.POI_RADIUS_M:
                return last["name"], last["source"], last["id"]
        return "", "NONE", ""

    def create(self, snapshot: GpsSnapshot, utc_ms: int, offset_s: int,
               category: str = "GENERIC", road_type: str = "",
               road_surface: str = "") -> Optional[Dict]:
        """Create a waypoint at the current fix. Needs a valid fix and clock."""
        if not snapshot.fix_valid or utc_ms <= 0:
            return None
        number = int(self.store.get("wp_next", 1))
        waypoint_id = "WP{:05d}".format(number)
        # Reserve the ID before the write: an interrupted write may leave a gap
        # but a restart can never reuse an ID that reached the file.
        self.store.put(wp_next=number + 1)

        timestamp = format_utc(utc_ms)
        record = {
            "id": waypoint_id, "timestamp": timestamp,
            "lat": snapshot.latitude, "lon": snapshot.longitude,
            "alt": snapshot.altitude_m if snapshot.altitude_fresh else None,
            "hdop": snapshot.hdop if snapshot.hdop_valid else None,
            "speed": snapshot.speed_kmh if snapshot.speed_fresh else None,
            "heading": snapshot.course_deg if snapshot.course_fresh else None,
            "name": waypoint_id, "source": "USER_WAYPOINT", "note": "",
            "photo": "", "category": sanitize_text(category, 24) or "GENERIC",
            "revision": 0, "road_type": road_type, "road_surface": road_surface,
        }
        fields = {name: "" for name in WAYPOINT_COLUMNS}
        fields.update({
            "waypoint_id": csv_field(record["id"]),
            "timestamp": csv_field(timestamp),
            "latitude": _num(record["lat"], 7),
            "longitude": _num(record["lon"], 7),
            "altitude_m": _num(record["alt"], 2),
            "hdop": _num(record["hdop"], 2),
            "speed_kmh": _num(record["speed"], 2),
            "heading_deg": _num(record["heading"], 2),
            "poi_name": csv_field(record["name"]),
            "poi_source": csv_field(record["source"]),
            "road_type": csv_field(road_type),
            "telemetry_source": csv_field(self.telemetry_source),
            "category": csv_field(record["category"]),
            "revision": str(record["revision"]),
            "record_action": "CREATE",
            "recorded_at": timestamp,
            "road_surface": csv_field(road_surface),
        })
        row = ",".join(fields[name] for name in WAYPOINT_COLUMNS)
        date = local_date_text(utc_ms // 1000, offset_s)
        if not self._waypoints.append(date, row, None):
            return None
        self.store.put(wp_last=record)
        self._events.append(date, ",".join([
            csv_field(timestamp), "WAYPOINT_CREATED", csv_field(waypoint_id),
            csv_field(record["category"]), csv_field(self.telemetry_source)]), None)
        return record

    @property
    def last_error(self) -> str:
        return self._waypoints.last_error

    def close(self) -> None:
        self._waypoints.close()
        self._events.close()
