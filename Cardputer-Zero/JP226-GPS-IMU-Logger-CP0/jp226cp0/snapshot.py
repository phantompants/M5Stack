"""Fix validity gate: GpsState -> GpsSnapshot (port of takeGpsSnapshot()).

A fix is valid only with fresh position, speed and UTC date/time, at least
MINIMUM_SATELLITES satellites, HDOP <= MAXIMUM_HDOP, and it must pass the
plausibility guard. A fix that fails a gate is never used to make a new
movement/stopped decision.
"""

from dataclasses import dataclass
from typing import Optional

from . import config
from .nmea import GpsState
from .plausibility import Plausibility


@dataclass
class GpsSnapshot:
    position_fresh: bool = False
    speed_fresh: bool = False
    altitude_fresh: bool = False
    course_fresh: bool = False
    satellites_valid: bool = False
    hdop_valid: bool = False
    vdop_valid: bool = False
    utc_valid: bool = False
    fix_valid: bool = False
    latitude: float = 0.0
    longitude: float = 0.0
    altitude_m: float = 0.0
    speed_kmh: float = 0.0
    course_deg: float = 0.0
    satellites: int = 0
    hdop: float = 0.0
    vdop: float = 0.0
    fix_age_ms: Optional[int] = None   # None when no position was ever seen
    utc_epoch_ms: int = 0              # 0 when unknown


def _age(stamp: Optional[int], now_ms: int) -> Optional[int]:
    return None if stamp is None else max(0, now_ms - stamp)


def _fresh(stamp: Optional[int], now_ms: int, limit_ms: int) -> bool:
    age = _age(stamp, now_ms)
    return age is not None and age <= limit_ms


def take_gps_snapshot(state: GpsState, guard: Plausibility,
                      now_ms: int) -> GpsSnapshot:
    s = GpsSnapshot()
    s.position_fresh = _fresh(state.location_ms, now_ms, config.MAX_FIX_AGE_MS)
    s.speed_fresh = _fresh(state.speed_ms, now_ms, config.MAX_FIX_AGE_MS)
    s.altitude_fresh = _fresh(state.altitude_ms, now_ms, config.MAX_FIX_AGE_MS)
    s.course_fresh = _fresh(state.course_ms, now_ms, config.MAX_FIX_AGE_MS)
    s.satellites_valid = state.satellites_ms is not None
    s.hdop_valid = state.hdop_ms is not None
    s.vdop_valid = state.vdop_ms is not None
    s.fix_age_ms = _age(state.location_ms, now_ms)

    if s.position_fresh:
        s.latitude, s.longitude = state.latitude, state.longitude
    if s.speed_fresh:
        s.speed_kmh = state.speed_kmh
    if s.altitude_fresh:
        s.altitude_m = state.altitude_m
    if s.course_fresh:
        s.course_deg = state.course_deg
    if s.satellites_valid:
        s.satellites = state.satellites
    if s.hdop_valid:
        s.hdop = state.hdop
    if s.vdop_valid:
        s.vdop = state.vdop

    date_time_fresh = (state.utc_epoch_s is not None and
                       _fresh(state.datetime_ms, now_ms,
                              config.MAX_DATE_TIME_AGE_MS))
    s.utc_valid = date_time_fresh
    if date_time_fresh and state.utc_year_ok:
        # The NMEA time is the moment of the fix, but the sentence reaches us
        # some tens to hundreds of milliseconds later; adding that age stops
        # each clock resync stepping the clock back by it.
        age = min(_age(state.datetime_ms, now_ms), 2000)
        s.utc_epoch_ms = (state.utc_epoch_s * 1000 +
                          state.utc_centisecond * 10 + age)

    s.fix_valid = (s.position_fresh and s.speed_fresh and date_time_fresh and
                   s.satellites_valid and
                   s.satellites >= config.MINIMUM_SATELLITES and
                   s.hdop_valid and s.hdop <= config.MAXIMUM_HDOP)
    if s.fix_valid:
        # A fix the receiver calls good can still be a jump the vehicle could
        # not have made; those poor-sky fixes are marked invalid.
        position_ok, altitude_ok = guard.accept(
            s.latitude, s.longitude, s.altitude_m, s.altitude_fresh,
            s.speed_kmh, now_ms)
        if not position_ok:
            s.fix_valid = False
        elif not altitude_ok:
            s.altitude_fresh = False
    return s
