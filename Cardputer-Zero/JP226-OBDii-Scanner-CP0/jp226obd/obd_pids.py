"""Standard OBD-II Mode 01/03/07/09 and UDS Mode 22 decoders.

Covers:
  - All 12 core metrics + MIL/DTCs + Ford TPMS DIDs used by JP226-GPS-IMU-Logger
  - EV / Hybrid propulsion, pedal, torque, SoC and HV signals needed by the
    planned EV Sound Emulator (Cardputer Zero & ADV)
"""

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class PidSpec:
    pid: int
    name: str
    short: str
    unit: str
    category: str  # engine | pedal | ev | temp | power | trip
    min_bytes: int
    decoder: Callable[[bytes], object]


def _u8(d: bytes) -> int:
    return d[0]


def _u16(d: bytes) -> int:
    return (d[0] << 8) | d[1]


def _u32(d: bytes) -> int:
    return (d[0] << 24) | (d[1] << 16) | (d[2] << 8) | d[3]


def _pct255(d: bytes) -> float:
    return round(d[0] * 100.0 / 255.0, 1)


def _temp40(d: bytes) -> float:
    return float(d[0] - 40)


def _signed_pct125(d: bytes) -> float:
    return float(d[0] - 125)


FUEL_TYPES = {
    0: "Not available",
    1: "Gasoline",
    2: "Methanol",
    3: "Ethanol",
    4: "Diesel",
    5: "LPG",
    6: "CNG",
    7: "Propane",
    8: "Electric",
    9: "Bifuel Gasoline",
    10: "Bifuel Methanol",
    11: "Bifuel Ethanol",
    12: "Bifuel LPG",
    13: "Bifuel CNG",
    14: "Bifuel Propane",
    15: "Bifuel Electric",
    16: "Bifuel Hybrid",
    17: "Hybrid Gasoline",
    18: "Hybrid Ethanol",
    19: "Hybrid Diesel",
    20: "Hybrid Electric",
    21: "Hybrid Mixed",
    22: "Hybrid Regen",
    23: "Bifuel Diesel",
}


STANDARD_PIDS: Dict[int, PidSpec] = {
    0x01: PidSpec(
        0x01, "Monitor Status / DTC Count", "MIL", "cnt", "engine", 4,
        lambda d: {"mil_on": bool(d[0] & 0x80), "dtc_count": d[0] & 0x7F},
    ),
    0x04: PidSpec(0x04, "Calculated Engine Load", "LOAD", "%", "engine", 1, _pct255),
    0x05: PidSpec(0x05, "Engine Coolant Temp", "COOLANT", "C", "temp", 1, _temp40),
    0x0A: PidSpec(0x0A, "Fuel Pressure", "FUEL_P", "kPa", "engine", 1, lambda d: d[0] * 3.0),
    0x0B: PidSpec(0x0B, "Intake Manifold Pressure", "MAP", "kPa", "engine", 1, lambda d: float(d[0])),
    0x0C: PidSpec(0x0C, "Engine RPM", "RPM", "rpm", "engine", 2, lambda d: round(_u16(d) / 4.0, 1)),
    0x0D: PidSpec(0x0D, "Vehicle Speed", "SPEED", "km/h", "trip", 1, lambda d: float(d[0])),
    0x0F: PidSpec(0x0F, "Intake Air Temp", "INTAKE", "C", "temp", 1, _temp40),
    0x10: PidSpec(0x10, "MAF Air Flow Rate", "MAF", "g/s", "engine", 2, lambda d: round(_u16(d) / 100.0, 2)),
    0x11: PidSpec(0x11, "Throttle Position", "THROTTLE", "%", "pedal", 1, _pct255),
    0x1F: PidSpec(0x1F, "Run Time Since Start", "RUNTIME", "s", "trip", 2, _u16),
    0x21: PidSpec(0x21, "Distance With MIL On", "DIST_MIL", "km", "trip", 2, _u16),
    0x2F: PidSpec(0x2F, "Fuel Tank Level", "FUEL", "%", "power", 1, _pct255),
    0x31: PidSpec(0x31, "Distance Since Clear", "DIST_CLR", "km", "trip", 2, _u16),
    0x33: PidSpec(0x33, "Barometric Pressure", "BARO", "kPa", "engine", 1, lambda d: float(d[0])),
    0x42: PidSpec(0x42, "Control Module Voltage", "VOLT", "V", "power", 2, lambda d: round(_u16(d) / 1000.0, 3)),
    0x43: PidSpec(0x43, "Absolute Load Value", "ABS_LOAD", "%", "engine", 2, lambda d: round(_u16(d) * 100.0 / 255.0, 1)),
    0x45: PidSpec(0x45, "Relative Throttle Pos", "REL_THR", "%", "pedal", 1, _pct255),
    0x46: PidSpec(0x46, "Ambient Air Temp", "AMBIENT", "C", "temp", 1, _temp40),
    0x47: PidSpec(0x47, "Absolute Throttle Pos B", "THR_B", "%", "pedal", 1, _pct255),
    0x49: PidSpec(0x49, "Accelerator Pedal Pos D", "PEDAL_D", "%", "pedal", 1, _pct255),
    0x4A: PidSpec(0x4A, "Accelerator Pedal Pos E", "PEDAL_E", "%", "pedal", 1, _pct255),
    0x4B: PidSpec(0x4B, "Accelerator Pedal Pos F", "PEDAL_F", "%", "pedal", 1, _pct255),
    0x4C: PidSpec(0x4C, "Commanded Throttle", "CMD_THR", "%", "pedal", 1, _pct255),
    0x51: PidSpec(0x51, "Fuel Type", "FUEL_TYPE", "", "power", 1, lambda d: FUEL_TYPES.get(d[0], "Code {}".format(d[0]))),
    0x5A: PidSpec(0x5A, "Relative Pedal Pos", "REL_PEDAL", "%", "pedal", 1, _pct255),
    0x5B: PidSpec(0x5B, "Hybrid/EV Battery SoC", "HV_SOC", "%", "ev", 1, _pct255),
    0x5C: PidSpec(0x5C, "Engine Oil Temp", "OIL", "C", "temp", 1, _temp40),
    0x5E: PidSpec(0x5E, "Engine Fuel Rate", "FUEL_RATE", "L/h", "engine", 2, lambda d: round(_u16(d) * 0.05, 2)),
    0x61: PidSpec(0x61, "Driver Demand Torque", "DEM_TRQ", "%", "ev", 1, _signed_pct125),
    0x62: PidSpec(0x62, "Actual Torque", "ACT_TRQ", "%", "ev", 1, _signed_pct125),
    0x63: PidSpec(0x63, "Reference Torque", "REF_TRQ", "Nm", "ev", 2, _u16),
    0xA6: PidSpec(0xA6, "Odometer", "ODO", "km", "trip", 4, lambda d: round(_u32(d) / 10.0, 1)),
}


# Metrics used by JP226-GPS-IMU-Logger (matching Common/include/ObdProtocol.h).
LOGGER_METRIC_PIDS: Tuple[Tuple[str, int, str], ...] = (
    ("RPM", 0x0C, "obd_rpm"),
    ("SPEED", 0x0D, "obd_speed_kmh"),
    ("COOLANT", 0x05, "obd_coolant_c"),
    ("THROTTLE", 0x11, "obd_throttle_pct"),
    ("LOAD", 0x04, "obd_load_pct"),
    ("INTAKE", 0x0F, "obd_intake_c"),
    ("MAP", 0x0B, "obd_map_kpa"),
    ("BARO", 0x33, "obd_baro_kpa"),
    ("FUEL", 0x2F, "obd_fuel_pct"),
    ("AMBIENT", 0x46, "obd_ambient_c"),
    ("OIL", 0x5C, "obd_oil_c"),
    ("VOLTAGE", 0x42, "obd_voltage_v"),
)

# Ford BCM / TPMS Mode 22 DIDs (from Common/include/ObdProtocol.h).
FORD_TPMS_DIDS: Dict[int, str] = {
    0x280F: "tpms_id_lf",
    0x2810: "tpms_id_rf",
    0x2812: "tpms_id_lr",
    0x2811: "tpms_id_rr",
    0x2813: "tpms_psi_lf",
    0x2814: "tpms_psi_rf",
    0x2816: "tpms_psi_lr",
    0x2815: "tpms_psi_rr",
    0x280B: "tpms_last_sensor",
}

# Common EV/Hybrid & identification UDS DIDs for targeted quick probes.
COMMON_TARGET_MODULES: Tuple[Tuple[int, str], ...] = (
    (0x7E0, "ECM / Engine"),
    (0x7E1, "TCM / Transmission"),
    (0x7E2, "VCU / Hybrid Control"),
    (0x7E4, "BMS / HV Battery"),
    (0x7E5, "Inverter / Drive Motor"),
    (0x7E7, "OBC / Charger"),
    (0x726, "BCM / Body Control"),
    (0x760, "ABS / Wheel Speed"),
)


def decode_pid(pid: int, data: bytes) -> Optional[object]:
    """Decode one Mode 01 PID payload (bytes AFTER the PID byte)."""
    spec = STANDARD_PIDS.get(pid)
    if spec is None or len(data) < spec.min_bytes:
        return None
    return spec.decoder(data)


def decode_multi_pid(payload: bytes) -> Dict[int, object]:
    """Decode a Mode 01 response containing one or multiple PIDs in sequence."""
    out: Dict[int, object] = {}
    i = 0
    while i < len(payload):
        pid = payload[i]
        i += 1
        if pid % 0x20 == 0:
            if i + 4 > len(payload):
                break
            i += 4
            continue
        spec = STANDARD_PIDS.get(pid)
        if spec is None:
            break
        if i + spec.min_bytes > len(payload):
            break
        out[pid] = spec.decoder(payload[i:i + spec.min_bytes])
        i += spec.min_bytes
    return out


def format_dtc(code_int: int) -> str:
    prefix = "PCBU"[(code_int >> 14) & 0x03]
    digit = (code_int >> 12) & 0x03
    return "{}{}{:03X}".format(prefix, digit, code_int & 0x0FFF)


def decode_dtcs(payload: bytes) -> List[str]:
    """Decode Mode 03/07 DTC payload (with or without ISO 15765-4 count byte)."""
    if not payload:
        return []
    # ISO 15765-4 CAN replies include a leading count byte: len == 1 + 2 * count.
    if len(payload) % 2 == 1 and len(payload) >= 1 + payload[0] * 2:
        raw = payload[1:1 + payload[0] * 2]
    elif len(payload) % 2 == 0:
        raw = payload
    else:
        raw = payload[1:]
    codes: List[str] = []
    for idx in range(0, len(raw) - 1, 2):
        val = (raw[idx] << 8) | raw[idx + 1]
        if val != 0:
            codes.append(format_dtc(val))
    return codes


def decode_mode09_text(payload: bytes) -> str:
    """Decode Mode 09 ASCII payload (bytes after PID), stripping count prefix."""
    if not payload:
        return ""
    body = payload[1:] if payload[0] < 0x20 else payload
    return "".join(chr(b) if 32 <= b < 127 else "" for b in body).strip()


def decode_ford_tpms(did: int, data: bytes) -> Optional[object]:
    """Decode known Ford TPMS DIDs (bytes AFTER the 2-byte DID)."""
    if did in (0x280F, 0x2810, 0x2811, 0x2812) and len(data) >= 4:
        sensor_id = _u32(data)
        if sensor_id in (0, 0xFFFFFFFF):
            return None
        return "{:08X}".format(sensor_id)
    if did in (0x2813, 0x2814, 0x2815, 0x2816) and len(data) >= 2:
        raw = _u16(data)
        if raw == 0xFFFF:
            return None
        return round(raw / 20.0, 2)
    if did == 0x280B and len(data) >= 9:
        sensor_id = _u32(data)
        if sensor_id in (0, 0xFFFFFFFF) or data[6] == 0xFF:
            return None
        return {
            "sensor_id": "{:08X}".format(sensor_id),
            "temp_c": float(data[6] - 40),
        }
    return None


def infer_did_summary(data: bytes) -> Dict[str, object]:
    """Provide human-readable decoding hints for an arbitrary Mode 22 DID reply."""
    summary: Dict[str, object] = {
        "len": len(data),
        "hex": data.hex(),
    }
    if not data:
        return summary
    printable = sum(1 for b in data if 32 <= b < 127)
    if len(data) >= 3 and printable == len(data):
        summary["ascii"] = data.decode("ascii", "replace").strip()
    if len(data) == 1:
        summary["u8"] = data[0]
    elif len(data) == 2:
        summary["u16_be"] = _u16(data)
    elif len(data) == 4:
        summary["u32_be"] = _u32(data)
    return summary
