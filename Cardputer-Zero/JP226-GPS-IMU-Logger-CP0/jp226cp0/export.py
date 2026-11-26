"""Convert daily logs to KML (Google Earth) and GPX, and bundle days as a ZIP.

Inputs live in the data directory (see csvlog.py / waypoints.py):
  telemetry_YYYY-MM-DD.csv   track points (rows with fix_valid=1 and a position)
  waypoints_YYYY-MM-DD.csv   waypoints (become KML placemarks / GPX <wpt>)
  events_YYYY-MM-DD.csv      included in ZIPs only
  photos/WPnnnnn.jpg         waypoint photos, included in ZIPs
Outputs go to <data_dir>/exports/track_YYYY-MM-DD.kml and .gpx.

The telemetry CSV is never modified, so the fixed 63-column schema is
unaffected. Everything here is standard library only.
"""

import csv
import datetime
import os
import re
import threading
import zipfile
from typing import Callable, Dict, Iterable, List, Optional, Tuple
from xml.sax.saxutils import escape

from . import config

EXPORT_DIR = "exports"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# A gap longer than this between fixes starts a new track segment.
SEGMENT_GAP_S = 300
_DAY_FILE = re.compile(r"^(telemetry|waypoints|events)_(\d{4}-\d{2}-\d{2})\.csv$")


def export_dir(data_dir: str) -> str:
    return os.path.join(data_dir, EXPORT_DIR)


def day_files(data_dir: str, date: str) -> Dict[str, str]:
    """Existing source files for one day, by kind."""
    result = {}
    for kind, prefix in (("telemetry", config.FILE_PREFIX),
                         ("waypoints", config.WAYPOINT_PREFIX),
                         ("events", config.EVENTS_PREFIX)):
        path = os.path.join(data_dir, "{}{}.csv".format(prefix, date))
        if os.path.isfile(path):
            result[kind] = path
    return result


def export_paths(data_dir: str, date: str) -> Dict[str, str]:
    base = os.path.join(export_dir(data_dir), "track_" + date)
    return {"kml": base + ".kml", "gpx": base + ".gpx"}


def list_days(data_dir: str) -> List[str]:
    """Dates that have any log file, newest first."""
    days = set()
    try:
        names = os.listdir(data_dir)
    except OSError:
        return []
    for name in names:
        match = _DAY_FILE.match(name)
        if match:
            days.add(match.group(2))
    return sorted(days, reverse=True)


def _float(text: str) -> Optional[float]:
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return value if value == value else None  # drop NaN


def _parse_time(text: str) -> Optional[datetime.datetime]:
    try:
        return datetime.datetime.strptime(text.strip(), "%Y-%m-%dT%H:%M:%S.%fZ")
    except (AttributeError, ValueError):
        try:
            return datetime.datetime.strptime(text.strip(), "%Y-%m-%dT%H:%M:%SZ")
        except (AttributeError, ValueError):
            return None


def _read_rows(path: str) -> Iterable[Dict[str, str]]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace", newline="") as handle:
            for row in csv.DictReader(handle):
                yield row
    except OSError:
        return


def read_track(path: str) -> List[List[Dict]]:
    """Valid fixes as segments of {time, lat, lon, alt, speed} points."""
    segments: List[List[Dict]] = []
    last_time: Optional[datetime.datetime] = None
    for row in _read_rows(path):
        if row.get("fix_valid") != "1":
            continue
        lat, lon = _float(row.get("lat")), _float(row.get("lon"))
        if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        when = _parse_time(row.get("timestamp", ""))
        point = {"time": row.get("timestamp", ""), "lat": lat, "lon": lon,
                 "alt": _float(row.get("alt_m")),
                 "speed": _float(row.get("speed_kmh"))}
        gap = (when is not None and last_time is not None and
               (when - last_time).total_seconds() > SEGMENT_GAP_S)
        if not segments or gap:
            segments.append([])
        segments[-1].append(point)
        if when is not None:
            last_time = when
    return [s for s in segments if s]


def read_waypoints(path: Optional[str]) -> List[Dict]:
    """Waypoints with a position (photo-only waypoints have none)."""
    if not path:
        return []
    result = []
    for row in _read_rows(path):
        lat, lon = _float(row.get("latitude")), _float(row.get("longitude"))
        if lat is None or lon is None:
            continue
        result.append({
            "id": row.get("waypoint_id", ""), "time": row.get("timestamp", ""),
            "lat": lat, "lon": lon, "alt": _float(row.get("altitude_m")),
            "name": row.get("poi_name") or row.get("waypoint_id", ""),
            "note": row.get("user_note", ""),
            "photo": row.get("photo_reference", ""),
            "road": row.get("road_type", ""),
            "surface": row.get("road_surface", ""),
        })
    return result


def _waypoint_description(wp: Dict) -> str:
    parts = [wp["time"]]
    if wp["road"] or wp["surface"]:
        parts.append("{} {}".format(wp["road"], wp["surface"]).strip())
    if wp["note"]:
        parts.append(wp["note"])
    if wp["photo"]:
        parts.append("Photo: " + os.path.basename(wp["photo"]))
    return " | ".join(p for p in parts if p)


def build_kml(date: str, segments: List[List[Dict]], waypoints: List[Dict]) -> str:
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<kml xmlns="http://www.opengis.net/kml/2.2">',
           "<Document>",
           "<name>JP226 track {}</name>".format(escape(date)),
           '<Style id="track"><LineStyle><color>ff0055ff</color>'
           "<width>4</width></LineStyle></Style>",
           '<Style id="waypoint"><IconStyle><scale>1.1</scale><Icon><href>'
           "http://maps.google.com/mapfiles/kml/paddle/red-circle.png"
           "</href></Icon></IconStyle></Style>"]
    for index, segment in enumerate(segments, 1):
        first, last = segment[0]["time"], segment[-1]["time"]
        out.append("<Placemark><name>Track {} ({} points)</name>".format(
            index, len(segment)))
        out.append("<description>{} to {}</description>".format(
            escape(first), escape(last)))
        out.append("<styleUrl>#track</styleUrl>")
        if first and last:
            out.append("<TimeSpan><begin>{}</begin><end>{}</end></TimeSpan>".format(
                escape(first), escape(last)))
        out.append("<LineString><tessellate>1</tessellate>"
                   "<altitudeMode>clampToGround</altitudeMode><coordinates>")
        out.append(" ".join("{:.6f},{:.6f},{:.1f}".format(
            p["lon"], p["lat"], p["alt"] or 0.0) for p in segment))
        out.append("</coordinates></LineString></Placemark>")
    for wp in waypoints:
        out.append("<Placemark><name>{}</name>".format(escape(wp["name"])))
        out.append("<description>{}</description>".format(
            escape(_waypoint_description(wp))))
        out.append("<styleUrl>#waypoint</styleUrl>")
        if wp["time"]:
            out.append("<TimeStamp><when>{}</when></TimeStamp>".format(
                escape(wp["time"])))
        out.append("<Point><coordinates>{:.6f},{:.6f},{:.1f}</coordinates>"
                   "</Point></Placemark>".format(wp["lon"], wp["lat"],
                                                  wp["alt"] or 0.0))
    out.append("</Document></kml>")
    return "\n".join(out) + "\n"


def build_gpx(date: str, segments: List[List[Dict]], waypoints: List[Dict]) -> str:
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<gpx version="1.1" creator="JP226 GPS-IMU Logger" '
           'xmlns="http://www.topografix.com/GPX/1/1">',
           "<metadata><name>JP226 track {}</name></metadata>".format(escape(date))]
    for wp in waypoints:
        out.append('<wpt lat="{:.6f}" lon="{:.6f}">'.format(wp["lat"], wp["lon"]))
        if wp["alt"] is not None:
            out.append("<ele>{:.1f}</ele>".format(wp["alt"]))
        if wp["time"]:
            out.append("<time>{}</time>".format(escape(wp["time"])))
        out.append("<name>{}</name><desc>{}</desc></wpt>".format(
            escape(wp["name"]), escape(_waypoint_description(wp))))
    if segments:
        out.append("<trk><name>JP226 {}</name>".format(escape(date)))
        for segment in segments:
            out.append("<trkseg>")
            for p in segment:
                item = '<trkpt lat="{:.6f}" lon="{:.6f}">'.format(p["lat"], p["lon"])
                if p["alt"] is not None:
                    item += "<ele>{:.1f}</ele>".format(p["alt"])
                if p["time"]:
                    item += "<time>{}</time>".format(escape(p["time"]))
                out.append(item + "</trkpt>")
            out.append("</trkseg>")
        out.append("</trk>")
    out.append("</gpx>")
    return "\n".join(out) + "\n"


def _write_atomic(path: str, text: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.replace(tmp, path)


def needs_export(data_dir: str, date: str) -> bool:
    sources = day_files(data_dir, date)
    if "telemetry" not in sources and "waypoints" not in sources:
        return False
    newest = max(os.path.getmtime(p) for k, p in sources.items() if k != "events")
    for path in export_paths(data_dir, date).values():
        if not os.path.isfile(path) or os.path.getmtime(path) < newest:
            return True
    return False


def export_day(data_dir: str, date: str) -> Dict[str, str]:
    """Write track_<date>.kml/.gpx. Returns the paths written ({} if no data)."""
    if not DATE_RE.match(date):
        raise ValueError("bad date: " + date)
    sources = day_files(data_dir, date)
    if "telemetry" not in sources and "waypoints" not in sources:
        return {}
    segments = read_track(sources["telemetry"]) if "telemetry" in sources else []
    waypoints = read_waypoints(sources.get("waypoints"))
    os.makedirs(export_dir(data_dir), exist_ok=True)
    paths = export_paths(data_dir, date)
    _write_atomic(paths["kml"], build_kml(date, segments, waypoints))
    _write_atomic(paths["gpx"], build_gpx(date, segments, waypoints))
    return paths


def export_pending(data_dir: str, before: Optional[str] = None,
                   include: Optional[str] = None) -> List[str]:
    """Export every day older than `before` (and `include`) whose KML/GPX is
    missing or older than its logs. Returns the dates exported."""
    done = []
    for date in sorted(list_days(data_dir)):
        if (before is None or date < before or date == include) and \
                needs_export(data_dir, date):
            if export_day(data_dir, date):
                done.append(date)
    return done


def last_days(data_dir: str, count: int) -> List[str]:
    """The newest `count` days that have logs (0 = all), newest first."""
    days = list_days(data_dir)
    return days if count <= 0 else days[:count]


def _photo_paths(data_dir: str, waypoints_path: Optional[str]) -> List[str]:
    if not waypoints_path:
        return []
    photos_dir = os.path.realpath(os.path.join(data_dir, "photos"))
    result = []
    for row in _read_rows(waypoints_path):
        ref = row.get("photo_reference", "")
        if not ref:
            continue
        path = os.path.realpath(os.path.join(data_dir, ref))
        if path.startswith(photos_dir + os.sep) and os.path.isfile(path):
            result.append(path)
    return result


def write_zip(data_dir: str, dates: List[str], fileobj) -> int:
    """ZIP the logs, fresh KML/GPX and waypoint photos for these dates.
    Returns the number of files added."""
    added = 0
    with zipfile.ZipFile(fileobj, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for date in sorted(dates):
            if needs_export(data_dir, date):
                export_day(data_dir, date)
            sources = day_files(data_dir, date)
            members: List[Tuple[str, str]] = [
                (path, os.path.basename(path)) for path in sources.values()]
            for path in export_paths(data_dir, date).values():
                if os.path.isfile(path):
                    members.append((path, EXPORT_DIR + "/" + os.path.basename(path)))
            for path in _photo_paths(data_dir, sources.get("waypoints")):
                members.append((path, "photos/" + os.path.basename(path)))
            for path, arcname in members:
                archive.write(path, arcname)
                added += 1
    return added


class NightlyExporter(threading.Thread):
    """Exports finished days once the local date rolls over, plus any day the
    device missed while it was off. `today_fn` returns the local date
    (YYYY-MM-DD) or None while the time is unknown."""

    def __init__(self, data_dir: str, today_fn: Callable[[], Optional[str]],
                 interval_s: float = 60.0):
        super().__init__(name="jp226-export", daemon=True)
        self.data_dir = data_dir
        self.today_fn = today_fn
        self.interval_s = interval_s
        self.status = "WAITING FOR TIME"
        self.last_run = ""
        self._stop_event = threading.Event()
        self._now_event = threading.Event()
        self.lock = threading.Lock()   # shared with the web server
        self._checked_for = ""

    def stop(self) -> None:
        self._stop_event.set()
        self._now_event.set()

    def request_now(self) -> None:
        """Export every day including today, as soon as possible."""
        self.status = "EXPORT QUEUED"
        self._now_event.set()

    def run_once(self, include_today: bool = False) -> List[str]:
        today = self.today_fn()
        if today is None and not include_today:
            self.status = "WAITING FOR TIME"
            return []
        with self.lock:
            self.status = "EXPORTING"
            try:
                done = export_pending(self.data_dir, before=today,
                                      include=today if include_today else None)
            except (OSError, ValueError) as exc:
                self.status = "EXPORT ERROR"
                self.last_run = str(exc)[:40]
                return []
            if today is not None:
                self._checked_for = today
            self.status = "EXPORTED {}".format(len(done)) if done else "UP TO DATE"
            self.last_run = today or ""
            return done

    def run(self) -> None:
        while not self._stop_event.is_set():
            manual = self._now_event.is_set()
            self._now_event.clear()
            today = self.today_fn()
            if manual or (today is not None and today != self._checked_for):
                self.run_once(include_today=manual)
            self._now_event.wait(self.interval_s)
