"""JP226 ESP-NOW packets (protocol v2), as shared/TelemetryProtocol.h in the
ESP32 repo defines them. Only the packets the CardputerZero exchanges with the
M5Dial and the other controllers through the AtomS3 bridge are here.

Every packet is little-endian and packed: an 8-byte header (magic "JP22",
version, type, size), the body, then a CRC-32 (IEEE, the same as zlib's) of
everything before it. The sizes are checked in tests against the ESP32
compiler's sizeof() values.
"""

import math
import struct
import zlib
from typing import Optional, Tuple

MAGIC = 0x4A503232
VERSION = 2

# PacketType
DISCOVERY = 2
DIAL_COMMAND = 4
DIAL_STATUS = 5
DIAL_ACK = 6
POSITION_REPORT = 7
TEMPERATURE_REPORT = 9

# ProbeSource
PROBE_SOURCES = {1: "DIAL", 2: "ECHO", 3: "BLACKBOX", 4: "LOGGER"}

# DialAction
HEARTBEAT = 0
MARK_WAYPOINT = 1
SET_ROAD = 2
SET_TYRE_FRONT = 3
SET_TYRE_REAR = 4
SET_SUSPENSION_FRONT = 5
SET_SUSPENSION_REAR = 6
SET_LOAD = 7
SET_SURFACE = 8

# LoggerBoard
BOARD_CARDPUTER_ZERO = 4

# ControllerKind: DialCommandPacket.sender (firmware v0.22 and later).
CONTROLLER_NAMES = {1: "DIAL", 2: "ECHO", 3: "BLACKBOX", 5: "CYD"}

# PositionFlag
POSITION_FIX = 1
POSITION_VALID = 2
POSITION_UTC_VALID = 4
POSITION_IMU_VALID = 8

# LinkFlag
LINK_SD_READY = 4

# kRoadSurfaces; index 0 = not set.
ROAD_SURFACES = ("", "DRY", "DAMP", "WET", "VERY WET")

# LogMode numbers, keyed by the logmode.py state names.
LOG_MODES = {"WAITING_FIX": 0, "MOVING": 1, "STOPPED_15M": 2,
             "STOPPED_HOURLY": 3, "FIX_LOST": 4}

_HEADER = "<IBBH"
_CRC = "I"
_COMMAND = struct.Struct(_HEADER + "IBB2xf32s" + _CRC)
_STATUS = struct.Struct(_HEADER + "IIIfffBBBB16s12s12s12s32s24sbBBB" + _CRC)
_ACK = struct.Struct(_HEADER + "IB3x24s" + _CRC)
_POSITION = struct.Struct(_HEADER + "IQBBBBh2xddffff9f" + _CRC)
_TEMPERATURE = struct.Struct(_HEADER + "IBB2x4H4f" + _CRC)

SIZES = {DIAL_COMMAND: _COMMAND.size, DIAL_STATUS: _STATUS.size,
         DIAL_ACK: _ACK.size, POSITION_REPORT: _POSITION.size,
         TEMPERATURE_REPORT: _TEMPERATURE.size}


def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def _seal(layout: struct.Struct, packet_type: int, *fields) -> bytes:
    body = layout.pack(MAGIC, VERSION, packet_type, layout.size, *fields, 0)
    return body[:-4] + struct.pack("<I", crc32(body[:-4]))


def packet_type(data: bytes) -> Optional[int]:
    """The type of a well-formed packet (magic, version, size, CRC), else None."""
    if len(data) < 12:
        return None
    magic, version, kind, size = struct.unpack_from(_HEADER, data)
    if magic != MAGIC or version != VERSION or size != len(data):
        return None
    if struct.unpack_from("<I", data, len(data) - 4)[0] != crc32(data[:-4]):
        return None
    return kind


def _text(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("ascii", errors="ignore")


def _field(text: str, size: int) -> bytes:
    """A NUL-terminated char[size] the way Arduino's toCharArray fills it."""
    return text.encode("ascii", errors="replace")[:size - 1]


def decode_command(data: bytes) -> Optional[Tuple[int, int, float, str]]:
    """(sequence, action, value, text) for a valid DialCommand, else None."""
    if packet_type(data) != DIAL_COMMAND or len(data) != _COMMAND.size:
        return None
    fields = _COMMAND.unpack(data)
    return fields[4], fields[5], fields[7], _text(fields[8])


def command_sender(data: bytes) -> int:
    """ControllerKind of a DialCommand (0 from firmware before v0.22)."""
    return data[13] if len(data) == _COMMAND.size else 0


def encode_command(sequence: int, action: int, value: float = 0.0,
                   text: str = "", sender: int = 0) -> bytes:
    """What an M5Dial sends; used by the tests."""
    return _seal(_COMMAND, DIAL_COMMAND, sequence, action, sender, value,
                 _field(text, 32))


def encode_ack(command_sequence: int, accepted: bool,
               waypoint_id: str = "") -> bytes:
    return _seal(_ACK, DIAL_ACK, command_sequence, 1 if accepted else 0,
                 _field(waypoint_id, 24))


def decode_ack(data: bytes) -> Optional[Tuple[int, bool, str]]:
    if packet_type(data) != DIAL_ACK or len(data) != _ACK.size:
        return None
    fields = _ACK.unpack(data)
    return fields[4], bool(fields[5]), _text(fields[6])


def encode_status(sequence: int, uptime_ms: int, log_elapsed_s: int,
                  speed_kmh: float, front_psi: float, rear_psi: float,
                  fix_valid: bool, satellites: int, log_mode: int,
                  road: str, suspension_front: str, suspension_rear: str,
                  vehicle_load: str, place: str, last_waypoint: str,
                  battery_percent: int = -1, link_flags: int = 0,
                  road_surface: str = "", source: int = 0) -> bytes:
    surface = (ROAD_SURFACES.index(road_surface)
               if road_surface in ROAD_SURFACES else 0)
    return _seal(
        _STATUS, DIAL_STATUS, sequence & 0xFFFFFFFF, uptime_ms & 0xFFFFFFFF,
        max(0, int(log_elapsed_s)) & 0xFFFFFFFF, float(speed_kmh),
        float(front_psi), float(rear_psi), 1 if fix_valid else 0,
        max(0, min(255, int(satellites))), log_mode, source,
        _field(road, 16), _field(suspension_front, 12),
        _field(suspension_rear, 12), _field(vehicle_load, 12),
        _field(place, 32), _field(last_waypoint, 24),
        max(-128, min(127, int(battery_percent))), BOARD_CARDPUTER_ZERO,
        link_flags, surface)


def decode_status(data: bytes) -> Optional[dict]:
    if packet_type(data) != DIAL_STATUS or len(data) != _STATUS.size:
        return None
    f = _STATUS.unpack(data)
    return {"sequence": f[4], "speed_kmh": f[7], "front_psi": f[8],
            "rear_psi": f[9], "fix_valid": bool(f[10]), "satellites": f[11],
            "log_mode": f[12], "road": _text(f[14]),
            "suspension_front": _text(f[15]), "suspension_rear": _text(f[16]),
            "vehicle_load": _text(f[17]), "place": _text(f[18]),
            "last_waypoint": _text(f[19]), "battery_percent": f[20],
            "logger_board": f[21], "link_flags": f[22], "road_surface": f[23]}


def encode_position(sequence: int, utc_ms: Optional[int], offset_min: int,
                    fix_valid: bool, position_valid: bool, satellites: int,
                    log_mode: int, latitude: float, longitude: float,
                    altitude_m: float, speed_kmh: float, course_deg: float,
                    hdop: float, imu=None) -> bytes:
    """`imu`: an ImuSample (accel m/s2, gyro deg/s, pitch, roll, vibration),
    or None. The black box logs these; the CYD shows pitch and roll."""
    flags = 0
    if fix_valid:
        flags |= POSITION_FIX
    if position_valid:
        flags |= POSITION_VALID
    if utc_ms is not None:
        flags |= POSITION_UTC_VALID
    def finite(value):
        return float(value) if value is not None and math.isfinite(value) else 0.0
    motion = [0.0] * 9
    if imu is not None and getattr(imu, "valid", False):
        flags |= POSITION_IMU_VALID
        motion = [finite(getattr(imu, name, 0.0)) for name in (
            "ax_mps2", "ay_mps2", "az_mps2", "gx_dps", "gy_dps", "gz_dps",
            "pitch_deg", "roll_deg", "vibration_rms_mps2")]
    return _seal(
        _POSITION, POSITION_REPORT, sequence & 0xFFFFFFFF, utc_ms or 0, flags,
        max(0, min(255, int(satellites))), BOARD_CARDPUTER_ZERO, log_mode,
        max(-32768, min(32767, int(offset_min))), finite(latitude),
        finite(longitude), finite(altitude_m), finite(speed_kmh),
        finite(course_deg), finite(hdop), *motion)


def decode_temperature(data: bytes):
    """(source, [(probe_id, celsius or None), ...]) for a TemperatureReport."""
    if packet_type(data) != TEMPERATURE_REPORT or len(data) != _TEMPERATURE.size:
        return None
    f = _TEMPERATURE.unpack(data)
    source, count = f[5], min(f[6], 4)
    ids, values = f[7:11], f[11:15]
    return source, [(ids[i], None if math.isnan(values[i]) else values[i])
                    for i in range(count)]


def encode_temperature(sequence: int, source: int, readings) -> bytes:
    """What an accessory sends; used by the tests."""
    ids = [r[0] for r in readings] + [0] * (4 - len(readings))
    values = [float("nan") if r[1] is None else r[1] for r in readings] + \
        [float("nan")] * (4 - len(readings))
    return _seal(_TEMPERATURE, TEMPERATURE_REPORT, sequence, source,
                 len(readings), *ids, *values)


def decode_position(data: bytes) -> Optional[dict]:
    if packet_type(data) != POSITION_REPORT or len(data) != _POSITION.size:
        return None
    f = _POSITION.unpack(data)
    return {"sequence": f[4], "utc_ms": f[5], "flags": f[6],
            "satellites": f[7], "logger_board": f[8], "log_mode": f[9],
            "utc_offset_min": f[10], "latitude": f[11], "longitude": f[12],
            "speed_kmh": f[14], "pitch_deg": f[23], "roll_deg": f[24]}
