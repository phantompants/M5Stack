"""Pure-Python NMEA 0183 parsing (GGA, RMC, GSA, optional VTG).

Replaces the firmware's TinyGPSPlus. A sentence is only used if its checksum
is present and correct. As in TinyGPSPlus, position/speed/course/altitude are
only updated by sentences that report a fix, while satellites/HDOP are updated
by every GGA, and every value carries the time it was last updated so the
caller can enforce freshness (config.MAX_FIX_AGE_MS etc.).

Small, deliberate differences from TinyGPSPlus:
  * date+time come from RMC only, as one atomic pair (no GGA/RMC mixing
    around midnight);
  * an empty course/speed field leaves the previous value to age out instead
    of becoming 0.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .timeutil import days_from_civil

KMH_PER_KNOT = 1.852


def checksum(body: str) -> int:
    """XOR of all characters between '$' and '*'."""
    value = 0
    for ch in body:
        value ^= ord(ch)
    return value


def make_sentence(body: str) -> str:
    """Build '$body*CS' (no line ending) from a sentence body."""
    return "${}*{:02X}".format(body, checksum(body))


@dataclass
class Sentence:
    talker: str
    kind: str          # GGA, RMC, GSA, VTG, GSV, ...
    fields: List[str]  # after the address field
    raw: str


def parse_line(line: str) -> Optional[Sentence]:
    """Return the sentence, or None if malformed or the checksum is wrong."""
    line = line.strip()
    if not line.startswith("$"):
        return None
    star = line.rfind("*")
    if star < 0 or len(line) < star + 3:
        return None
    try:
        given = int(line[star + 1:star + 3], 16)
    except ValueError:
        return None
    body = line[1:star]
    if checksum(body) != given:
        return None
    parts = body.split(",")
    head = parts[0]
    if len(head) != 5:  # 2-letter talker + 3-letter type; ignores $PCAS... etc.
        return None
    return Sentence(head[:2], head[2:], parts[1:], line)


def _float(text: Optional[str]) -> Optional[float]:
    if text is None or text == "":
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return value


def _int(text: Optional[str]) -> Optional[int]:
    if text is None or text == "":
        return None
    try:
        return int(text)
    except ValueError:
        return None


def parse_coordinate(value: str, hemisphere: str,
                     limit: float) -> Optional[float]:
    """NMEA ddmm.mmmm / dddmm.mmmm plus N/S/E/W to signed decimal degrees."""
    if not value or hemisphere not in ("N", "S", "E", "W"):
        return None
    dot = value.find(".")
    if dot < 0:
        dot = len(value)
    if dot < 3:
        return None
    try:
        degrees = int(value[:dot - 2])
        minutes = float(value[dot - 2:])
    except ValueError:
        return None
    if minutes >= 60.0:
        return None
    result = degrees + minutes / 60.0
    if hemisphere in ("S", "W"):
        result = -result
    if abs(result) > limit:
        return None
    return result


def parse_time_of_day(text: str):
    """'hhmmss.ss' -> (hour, minute, second, centisecond) or None."""
    if len(text) < 6 or not text[:6].isdigit():
        return None
    hour, minute, second = int(text[0:2]), int(text[2:4]), int(text[4:6])
    if hour > 23 or minute > 59 or second > 59:
        return None
    centi = 0
    if len(text) > 6 and text[6] == ".":
        frac = text[7:9].ljust(2, "0")
        if not frac.isdigit():
            return None
        centi = int(frac)
    return hour, minute, second, centi


def parse_date(text: str):
    """'ddmmyy' -> (year, month, day) or None. Two-digit years are 20yy."""
    if len(text) != 6 or not text.isdigit():
        return None
    day, month, year = int(text[0:2]), int(text[2:4]), 2000 + int(text[4:6])
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return year, month, day


def format_latlon(value: float, is_lat: bool) -> Tuple[str, str]:
    """Signed degrees -> ('ddmm.mmmmm', 'N') for building test sentences."""
    if is_lat:
        hemi = "N" if value >= 0 else "S"
    else:
        hemi = "E" if value >= 0 else "W"
    value = abs(value)
    degrees = int(value)
    minutes = (value - degrees) * 60.0
    width = 2 if is_lat else 3
    return "{:0{w}d}{:08.5f}".format(degrees, minutes, w=width), hemi


@dataclass
class GpsState:
    """Latest values from the receiver with update timestamps (ms, caller's
    monotonic clock). *_ms is None until the first update."""

    # Position / altitude / speed / course (only from sentences with a fix).
    latitude: float = 0.0
    longitude: float = 0.0
    location_ms: Optional[int] = None
    altitude_m: float = 0.0
    altitude_ms: Optional[int] = None
    speed_kmh: float = 0.0
    speed_ms: Optional[int] = None
    course_deg: float = 0.0
    course_ms: Optional[int] = None
    # GGA satellites in use / HDOP (updated by every GGA).
    satellites: int = 0
    satellites_ms: Optional[int] = None
    hdop: float = 0.0
    hdop_ms: Optional[int] = None
    # GSA VDOP.
    vdop: float = 0.0
    vdop_ms: Optional[int] = None
    # RMC date+time pair.
    utc_epoch_s: Optional[int] = None  # None if the date/time is not parseable
    utc_centisecond: int = 0
    utc_year_ok: bool = False
    datetime_ms: Optional[int] = None
    # Diagnostics for the DIAG page / hardware bring-up.
    sentences_ok: int = 0
    sentences_bad: int = 0
    last_sentence_ms: Optional[int] = None
    counts: Dict[str, int] = field(default_factory=dict)

    def feed_line(self, line: str, now_ms: int) -> Optional[Sentence]:
        """Feed one text line. Returns the parsed sentence, or None if the
        line was not a valid NMEA sentence (bad ones are counted)."""
        stripped = line.strip()
        if not stripped:
            return None
        sentence = parse_line(stripped)
        if sentence is None:
            if stripped.startswith("$"):
                self.sentences_bad += 1
            return None
        self.sentences_ok += 1
        self.last_sentence_ms = now_ms
        self.counts[sentence.kind] = self.counts.get(sentence.kind, 0) + 1
        if sentence.kind == "GGA":
            self._gga(sentence.fields, now_ms)
        elif sentence.kind == "RMC":
            self._rmc(sentence.fields, now_ms)
        elif sentence.kind == "GSA":
            self._gsa(sentence.fields, now_ms)
        elif sentence.kind == "VTG":
            self._vtg(sentence.fields, now_ms)
        return sentence

    @staticmethod
    def _field(fields: List[str], index: int) -> str:
        return fields[index] if index < len(fields) else ""

    def _set_position(self, lat_text, ns, lon_text, ew, now_ms) -> bool:
        lat = parse_coordinate(lat_text, ns, 90.0)
        lon = parse_coordinate(lon_text, ew, 180.0)
        if lat is None or lon is None:
            return False
        self.latitude, self.longitude, self.location_ms = lat, lon, now_ms
        return True

    def _gga(self, f: List[str], now_ms: int) -> None:
        quality = _int(self._field(f, 5))
        if quality is not None and quality > 0:
            self._set_position(self._field(f, 1), self._field(f, 2),
                               self._field(f, 3), self._field(f, 4), now_ms)
            altitude = _float(self._field(f, 8))
            if altitude is not None:
                self.altitude_m, self.altitude_ms = altitude, now_ms
        sats = _int(self._field(f, 6))
        if sats is not None:
            self.satellites, self.satellites_ms = sats, now_ms
        hdop = _float(self._field(f, 7))
        if hdop is not None:
            self.hdop, self.hdop_ms = hdop, now_ms

    def _rmc(self, f: List[str], now_ms: int) -> None:
        tod = parse_time_of_day(self._field(f, 0))
        date = parse_date(self._field(f, 8))
        if tod is not None and date is not None:
            year, month, day = date
            self.utc_epoch_s = (days_from_civil(year, month, day) * 86400 +
                                tod[0] * 3600 + tod[1] * 60 + tod[2])
            self.utc_centisecond = tod[3]
            self.utc_year_ok = 2024 <= year <= 2099
            self.datetime_ms = now_ms
        if self._field(f, 1) != "A":
            return
        self._set_position(self._field(f, 2), self._field(f, 3),
                           self._field(f, 4), self._field(f, 5), now_ms)
        knots = _float(self._field(f, 6))
        if knots is not None:
            self.speed_kmh, self.speed_ms = knots * KMH_PER_KNOT, now_ms
        course = _float(self._field(f, 7))
        if course is not None:
            self.course_deg, self.course_ms = course % 360.0, now_ms

    def _vtg(self, f: List[str], now_ms: int) -> None:
        if self._field(f, 8) == "N":  # NMEA 2.3 mode indicator: data not valid
            return
        knots = _float(self._field(f, 4))
        kmh = _float(self._field(f, 6))
        if knots is not None:
            self.speed_kmh, self.speed_ms = knots * KMH_PER_KNOT, now_ms
        elif kmh is not None:
            self.speed_kmh, self.speed_ms = kmh, now_ms
        course = _float(self._field(f, 0))
        if course is not None and (knots is not None or kmh is not None):
            self.course_deg, self.course_ms = course % 360.0, now_ms

    def _gsa(self, f: List[str], now_ms: int) -> None:
        fix_type = _int(self._field(f, 1))
        vdop = _float(self._field(f, 16))
        if fix_type is not None and fix_type >= 2 and vdop is not None:
            self.vdop, self.vdop_ms = vdop, now_ms
