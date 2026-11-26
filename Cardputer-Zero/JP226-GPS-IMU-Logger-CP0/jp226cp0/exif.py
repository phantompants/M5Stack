"""GPS EXIF for waypoint and camera photos. Standard library only.

The camera writes its own EXIF block; this replaces it with one that carries
the GPS fix (position, altitude, UTC time, speed, course, DOP, satellites) and
the local capture time, so iPhone Photos, Google Photos and Google Earth put
the photo on the map. Camera exposure details from the original block are not
kept.
"""

import datetime
import os
import struct
from typing import List, Optional, Tuple

_BYTE, _ASCII, _SHORT, _LONG, _RATIONAL, _UNDEFINED = 1, 2, 3, 4, 5, 7
_EXIF_HEADER = b"Exif\0\0"
_EXIF_IFD = 0x8769
_GPS_IFD = 0x8825

Entry = Tuple[int, int, int, bytes]  # tag, type, count, value bytes


def _ascii(tag: int, text: str) -> Entry:
    data = text.encode("ascii", errors="replace") + b"\0"
    return tag, _ASCII, len(data), data


def _bytes(tag: int, kind: int, data: bytes) -> Entry:
    return tag, kind, len(data), data


def _short(tag: int, value: int) -> Entry:
    return tag, _SHORT, 1, struct.pack("<H", value)


def _long(tag: int, value: int) -> Entry:
    return tag, _LONG, 1, struct.pack("<I", value)


def _rationals(tag: int, values: List[Tuple[int, int]]) -> Entry:
    data = b"".join(struct.pack("<II", n, d) for n, d in values)
    return tag, _RATIONAL, len(values), data


def _rational(value: float, denominator: int) -> Tuple[int, int]:
    return max(0, int(round(abs(value) * denominator))), denominator


def _dms(value: float) -> List[Tuple[int, int]]:
    """Degrees, minutes and seconds (to 1/10000 s, about 3 mm)."""
    value = abs(value)
    degrees = int(value)
    minutes_float = (value - degrees) * 60
    minutes = int(minutes_float)
    seconds = (minutes_float - minutes) * 60
    ticks = int(round(seconds * 10000))
    if ticks >= 600000:            # rounding reached 60 s
        ticks -= 600000
        minutes += 1
    if minutes >= 60:
        minutes -= 60
        degrees += 1
    return [(degrees, 1), (minutes, 1), (ticks, 10000)]


def _ifd_size(entries: List[Entry]) -> int:
    data = sum(len(e[3]) + (len(e[3]) & 1) for e in entries if len(e[3]) > 4)
    return 2 + 12 * len(entries) + 4 + data


def _pack_ifd(entries: List[Entry], offset: int) -> bytes:
    entries = sorted(entries)
    data_offset = offset + 2 + 12 * len(entries) + 4
    table = [struct.pack("<H", len(entries))]
    data = []
    for tag, kind, count, value in entries:
        if len(value) <= 4:
            field = value.ljust(4, b"\0")
        else:
            field = struct.pack("<I", data_offset)
            padded = value + (b"\0" if len(value) & 1 else b"")
            data.append(padded)
            data_offset += len(padded)
        table.append(struct.pack("<HHI", tag, kind, count) + field)
    table.append(struct.pack("<I", 0))           # no next IFD
    return b"".join(table) + b"".join(data)


def _offset_text(offset_s: int) -> str:
    sign = "-" if offset_s < 0 else "+"
    minutes = abs(offset_s) // 60
    return "{}{:02d}:{:02d}".format(sign, minutes // 60, minutes % 60)


def build_exif(utc_ms: Optional[int], offset_s: int = 0,
               latitude: Optional[float] = None,
               longitude: Optional[float] = None,
               altitude_m: Optional[float] = None,
               speed_kmh: Optional[float] = None,
               course_deg: Optional[float] = None,
               hdop: Optional[float] = None,
               satellites: Optional[int] = None,
               software: str = "") -> bytes:
    """An APP1 EXIF payload ('Exif\\0\\0' + TIFF). GPS tags only when a
    position is given; times only when utc_ms is known."""
    ifd0: List[Entry] = [
        _ascii(0x010F, "M5Stack"), _ascii(0x0110, "CardputerZero"),
        _short(0x0112, 1),
    ]
    if software:
        ifd0.append(_ascii(0x0131, software))
    exif: List[Entry] = [_bytes(0x9000, _UNDEFINED, b"0232")]
    gps: List[Entry] = []
    if utc_ms is not None:
        utc = datetime.datetime(1970, 1, 1) + datetime.timedelta(
            milliseconds=utc_ms)
        local = (utc + datetime.timedelta(seconds=offset_s)).strftime(
            "%Y:%m:%d %H:%M:%S")
        ifd0.append(_ascii(0x0132, local))
        exif += [_ascii(0x9003, local), _ascii(0x9004, local),
                 _ascii(0x9010, _offset_text(offset_s)),
                 _ascii(0x9011, _offset_text(offset_s))]
    if latitude is not None and longitude is not None:
        gps += [
            _bytes(0x0000, _BYTE, bytes((2, 3, 0, 0))),
            _ascii(0x0001, "N" if latitude >= 0 else "S"),
            _rationals(0x0002, _dms(latitude)),
            _ascii(0x0003, "E" if longitude >= 0 else "W"),
            _rationals(0x0004, _dms(longitude)),
            _ascii(0x0009, "A"),
            _ascii(0x0012, "WGS-84"),
        ]
        if altitude_m is not None:
            gps += [_bytes(0x0005, _BYTE, bytes((0 if altitude_m >= 0 else 1,))),
                    _rationals(0x0006, [_rational(altitude_m, 100)])]
        if satellites is not None:
            gps.append(_ascii(0x0008, str(int(satellites))))
        if hdop is not None:
            gps.append(_rationals(0x000B, [_rational(hdop, 100)]))
        if speed_kmh is not None:
            gps += [_ascii(0x000C, "K"),
                    _rationals(0x000D, [_rational(speed_kmh, 100)])]
        if course_deg is not None:
            gps += [_ascii(0x000E, "T"),
                    _rationals(0x000F, [_rational(course_deg % 360.0, 100)])]
        if utc_ms is not None:
            seconds = (utc_ms // 1000) % 86400
            gps += [
                _rationals(0x0007, [(seconds // 3600, 1),
                                    ((seconds // 60) % 60, 1),
                                    (seconds % 60, 1)]),
                _ascii(0x001D, utc.strftime("%Y:%m:%d")),
            ]
    # Pointers are LONGs, so the IFD sizes do not depend on their values.
    ifd0.append(_long(_EXIF_IFD, 0))
    if gps:
        ifd0.append(_long(_GPS_IFD, 0))
    exif_offset = 8 + _ifd_size(ifd0)
    gps_offset = exif_offset + _ifd_size(exif)
    ifd0 = [e for e in ifd0 if e[0] not in (_EXIF_IFD, _GPS_IFD)]
    ifd0.append(_long(_EXIF_IFD, exif_offset))
    if gps:
        ifd0.append(_long(_GPS_IFD, gps_offset))
    tiff = (b"II*\0" + struct.pack("<I", 8) + _pack_ifd(ifd0, 8) +
            _pack_ifd(exif, exif_offset) +
            (_pack_ifd(gps, gps_offset) if gps else b""))
    return _EXIF_HEADER + tiff


def replace_exif(jpeg: bytes, payload: bytes) -> bytes:
    """The JPEG with every existing EXIF APP1 removed and `payload` added
    straight after SOI. Other segments (JFIF, XMP, tables) are kept."""
    if jpeg[:2] != b"\xff\xd8":
        raise ValueError("not a JPEG")
    if len(payload) + 2 > 0xFFFF:
        raise ValueError("EXIF block too large")
    kept = []
    pos = 2
    while pos + 4 <= len(jpeg) and jpeg[pos] == 0xFF:
        marker = jpeg[pos + 1]
        if marker == 0xDA:                       # start of scan: image data
            break
        length = struct.unpack(">H", jpeg[pos + 2:pos + 4])[0]
        segment = jpeg[pos:pos + 2 + length]
        if not (marker == 0xE1 and segment[4:10] == _EXIF_HEADER):
            kept.append(segment)
        pos += 2 + length
    app1 = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    return b"\xff\xd8" + app1 + b"".join(kept) + jpeg[pos:]


def write_exif(path: str, payload: bytes) -> None:
    """Rewrite the photo at `path` with this EXIF block, atomically."""
    with open(path, "rb") as handle:
        jpeg = handle.read()
    updated = replace_exif(jpeg, payload)
    temp = path + ".tmp"
    with open(temp, "wb") as handle:
        handle.write(updated)
    os.replace(temp, path)


def read_gps(jpeg: bytes) -> Optional[Tuple[float, float]]:
    """(latitude, longitude) from an EXIF block this module wrote (or any
    little-endian one), else None. For the GALLERY page and tests."""
    pos = 2
    while pos + 4 <= len(jpeg) and jpeg[pos] == 0xFF and jpeg[pos + 1] != 0xDA:
        length = struct.unpack(">H", jpeg[pos + 2:pos + 4])[0]
        if jpeg[pos + 1] == 0xE1 and jpeg[pos + 4:pos + 10] == _EXIF_HEADER:
            return _gps_from_tiff(jpeg[pos + 10:pos + 2 + length])
        pos += 2 + length
    return None


def _ifd_entries(tiff: bytes, offset: int):
    count = struct.unpack_from("<H", tiff, offset)[0]
    for i in range(count):
        yield struct.unpack_from("<HHI4s", tiff, offset + 2 + 12 * i)


def _gps_from_tiff(tiff: bytes) -> Optional[Tuple[float, float]]:
    try:
        if tiff[:4] != b"II*\0":
            return None
        ifd0 = struct.unpack_from("<I", tiff, 4)[0]
        gps_offset = None
        for tag, _kind, _count, value in _ifd_entries(tiff, ifd0):
            if tag == _GPS_IFD:
                gps_offset = struct.unpack("<I", value)[0]
        if gps_offset is None:
            return None
        found = {}
        for tag, _kind, _count, value in _ifd_entries(tiff, gps_offset):
            if tag in (1, 3):
                found[tag] = value[:1].decode("ascii")
            elif tag in (2, 4):
                at = struct.unpack("<I", value)[0]
                d, m, s = (n / den for n, den in (
                    struct.unpack_from("<II", tiff, at + 8 * k) for k in range(3)))
                found[tag] = d + m / 60 + s / 3600
        lat = found[2] * (-1 if found.get(1) == "S" else 1)
        lon = found[4] * (-1 if found.get(3) == "W" else 1)
        return lat, lon
    except (KeyError, struct.error, ZeroDivisionError, UnicodeDecodeError):
        return None
