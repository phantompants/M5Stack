"""Offline Australian time zone from latitude/longitude.

Port of src/TimezoneFallback.cpp. Follows the state borders closely enough to
pick the right zone within a few kilometres; near a border an online lookup,
when available, is the authority. Returns None outside Australia.
"""

import math
from collections import namedtuple
from typing import Optional

Zone = namedtuple("Zone", "name rule")

SYDNEY = Zone("Australia/Sydney", "AEST-10AEDT,M10.1.0,M4.1.0/3")
BRISBANE = Zone("Australia/Brisbane", "AEST-10")
ADELAIDE = Zone("Australia/Adelaide", "ACST-9:30ACDT,M10.1.0,M4.1.0/3")
BROKEN_HILL = Zone("Australia/Broken_Hill", "ACST-9:30ACDT,M10.1.0,M4.1.0/3")
DARWIN = Zone("Australia/Darwin", "ACST-9:30")
PERTH = Zone("Australia/Perth", "AWST-8")
EUCLA = Zone("Australia/Eucla", "ACWST-8:45")
LORD_HOWE = Zone("Australia/Lord_Howe", "LHST-10:30LHDT-11,M10.1.0,M4.1.0/2")

# IANA names (as returned by an online lookup) to rules. Names not listed here
# fall back to a fixed-offset rule built from the reported UTC offset.
IANA_TO_RULE = {
    "Australia/Sydney": SYDNEY.rule, "Australia/Melbourne": SYDNEY.rule,
    "Australia/Hobart": SYDNEY.rule, "Australia/ACT": SYDNEY.rule,
    "Australia/Canberra": SYDNEY.rule, "Australia/NSW": SYDNEY.rule,
    "Australia/Victoria": SYDNEY.rule, "Australia/Tasmania": SYDNEY.rule,
    "Australia/Brisbane": BRISBANE.rule, "Australia/Lindeman": BRISBANE.rule,
    "Australia/Queensland": BRISBANE.rule,
    "Australia/Adelaide": ADELAIDE.rule, "Australia/Broken_Hill": ADELAIDE.rule,
    "Australia/South": ADELAIDE.rule,
    "Australia/Darwin": DARWIN.rule, "Australia/North": DARWIN.rule,
    "Australia/Perth": PERTH.rule, "Australia/West": PERTH.rule,
    "Australia/Eucla": EUCLA.rule,
    "Australia/Lord_Howe": LORD_HOWE.rule,
}


def _queensland_border_latitude(longitude: float) -> float:
    """The Queensland / New South Wales border: 29 S out to 150 E, then rising
    to the coast at Point Danger (28.17 S, 153.55 E)."""
    if longitude <= 150.0:
        return -29.0
    t = (longitude - 150.0) / 3.55
    return -29.0 + min(t, 1.0) * 0.83


def zone_for_position(latitude: float, longitude: float) -> Optional[Zone]:
    if not (math.isfinite(latitude) and math.isfinite(longitude)):
        return None
    if latitude > -9.0 or latitude < -44.5 or longitude < 112.5 or longitude > 154.0:
        return None

    if longitude < 125.5:
        return PERTH
    if longitude < 129.0:
        # The Eucla strip along the Eyre Highway keeps UTC+8:45; the rest of
        # Western Australia is on Perth time.
        eucla_strip = -33.3 < latitude < -30.5
        return EUCLA if eucla_strip else PERTH
    if longitude < 138.0:
        return DARWIN if latitude > -26.0 else ADELAIDE
    if longitude < 141.0:
        return BRISBANE if latitude > -26.0 else ADELAIDE
    if longitude < 142.0 and -33.3 < latitude < -29.2:
        # Broken Hill and the far west of New South Wales keep Adelaide time.
        return BROKEN_HILL
    if latitude > _queensland_border_latitude(longitude):
        return BRISBANE
    return SYDNEY  # NSW, ACT, Victoria, Tasmania


def distance_km(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> float:
    """Great-circle (haversine) distance in kilometres."""
    radius_km = 6371.0
    rad = math.pi / 180.0
    d_lat = (lat_b - lat_a) * rad
    d_lon = (lon_b - lon_a) * rad
    a = (math.sin(d_lat / 2) ** 2 +
         math.cos(lat_a * rad) * math.cos(lat_b * rad) * math.sin(d_lon / 2) ** 2)
    return 2.0 * radius_km * math.asin(math.sqrt(min(1.0, a)))
