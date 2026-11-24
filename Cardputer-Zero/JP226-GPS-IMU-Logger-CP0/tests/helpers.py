"""Shared test helpers (standard library only)."""

import datetime

from jp226cp0.nmea import format_latlon, make_sentence
from jp226cp0.snapshot import GpsSnapshot


def utc_ms(year, month, day, hour=0, minute=0, second=0, ms=0):
    t = datetime.datetime(year, month, day, hour, minute, second,
                          tzinfo=datetime.timezone.utc)
    return int(t.timestamp()) * 1000 + ms


def nmea_burst(t, lat=-32.4936, lon=137.7716, kmh=0.0, sats=9, hdop=0.9,
               alt=14.0, status="A", start=(2026, 10, 20, 1, 30, 0),
               gga_quality=1):
    """RMC, VTG, GGA, GSA for the GPS second `start + t`."""
    now = datetime.datetime(*start) + datetime.timedelta(seconds=t)
    hhmmss = now.strftime("%H%M%S.00")
    ddmmyy = now.strftime("%d%m%y")
    lat_s, ns = format_latlon(lat, True)
    lon_s, ew = format_latlon(lon, False)
    knots = kmh / 1.852
    course = "90.00" if kmh > 1 else ""
    return [
        make_sentence("GNRMC,{},{},{},{},{},{},{:.2f},{},{},,,A".format(
            hhmmss, status, lat_s, ns, lon_s, ew, knots, course, ddmmyy)),
        make_sentence("GNVTG,{},T,,M,{:.2f},N,{:.2f},K,A".format(course, knots, kmh)),
        make_sentence("GNGGA,{},{},{},{},{},{},{:02d},{:.1f},{:.1f},M,12.0,M,,".format(
            hhmmss, lat_s, ns, lon_s, ew, gga_quality, sats, hdop, alt)),
        make_sentence("GNGSA,A,3,01,03,06,09,11,14,17,19,22,,,,1.5,{:.1f},1.2,1".format(hdop)),
    ]


def make_snapshot(speed=0.0, valid=True, **kw):
    s = GpsSnapshot()
    s.fix_valid = valid
    s.position_fresh = s.speed_fresh = valid
    s.speed_kmh = speed
    s.latitude, s.longitude = -32.5, 137.8
    s.satellites_valid = s.hdop_valid = True
    s.satellites, s.hdop = 9, 0.9
    for key, value in kw.items():
        setattr(s, key, value)
    return s
