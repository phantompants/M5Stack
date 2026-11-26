"""Analyze scanned OBD-II data for downstream Cardputer Zero & ADV apps.

Produces structured compatibility reports for:
  1. JP226-GPS-IMU-Logger (CP0 Python & Common/ADV ESP32 firmware)
  2. EV-Sound-Emulator (planned CP0 & ADV real-time sound synthesizer)
"""

from typing import Dict, List, Optional, Tuple

from .obd_pids import (
    FORD_TPMS_DIDS,
    LOGGER_METRIC_PIDS,
    STANDARD_PIDS,
    decode_ford_tpms,
)


def _pid_set(summary: Dict[str, object]) -> set:
    out = set()
    for p in summary.get("pids", []):
        try:
            out.add(int(str(p), 16))
        except ValueError:
            pass
    return out


def _pid_value(summary: Dict[str, object], pid: int) -> Optional[object]:
    values = summary.get("pid_values", {})
    if isinstance(values, dict):
        entry = values.get("{:02X}".format(pid))
        if isinstance(entry, dict):
            return entry.get("value")
    return None


def _find_ev_module_dids(summary: Dict[str, object]) -> Dict[str, Dict[str, object]]:
    modules = summary.get("modules", {})
    hits: Dict[str, Dict[str, object]] = {}
    if not isinstance(modules, dict):
        return hits
    for hdr in ("7E2", "7E4", "7E5", "7E7", "744"):
        info = modules.get(hdr)
        if not isinstance(info, dict):
            continue
        dids = info.get("dids", {})
        if isinstance(dids, dict) and dids:
            hits[hdr] = dict(dids)
    return hits


def analyze_logger_fit(summary: Dict[str, object]) -> Dict[str, object]:
    """Check readiness for JP226-GPS-IMU-Logger (ObdProtocol.h + ObdCsv.cpp)."""
    supported = _pid_set(summary)
    metrics: List[Dict[str, object]] = []
    supported_count = 0
    for label, pid, csv_col in LOGGER_METRIC_PIDS:
        is_ok = pid in supported
        if is_ok:
            supported_count += 1
        spec = STANDARD_PIDS[pid]
        metrics.append({
            "name": label,
            "pid": "{:02X}".format(pid),
            "csv_column": csv_col,
            "supported": is_ok,
            "value": _pid_value(summary, pid),
            "unit": spec.unit,
        })

    modules = summary.get("modules", {})
    tpms_values: Dict[str, object] = {}
    if isinstance(modules, dict) and isinstance(modules.get("726"), dict):
        bcm_dids = modules["726"].get("dids", {})
        if isinstance(bcm_dids, dict):
            for did, key in FORD_TPMS_DIDS.items():
                raw_hex = bcm_dids.get("{:04X}".format(did))
                if isinstance(raw_hex, str) and raw_hex:
                    try:
                        decoded = decode_ford_tpms(did, bytes.fromhex(raw_hex))
                    except ValueError:
                        decoded = None
                    if decoded is not None:
                        tpms_values[key] = decoded

    dtcs = summary.get("dtcs", {})
    mil_supported = 0x01 in supported
    dtc_supported = isinstance(dtcs, dict) and (
        bool(dtcs.get("mode03_supported")) or bool(dtcs.get("mode07_supported"))
    )
    tpms_pressures = sum(
        1 for k in ("tpms_psi_lf", "tpms_psi_rf", "tpms_psi_lr", "tpms_psi_rr")
        if k in tpms_values
    )

    return {
        "supported_count": supported_count,
        "total_count": len(LOGGER_METRIC_PIDS),
        "metrics": metrics,
        "mil_supported": mil_supported,
        "dtc_supported": dtc_supported,
        "tpms_ready": tpms_pressures > 0,
        "tpms_pressure_count": tpms_pressures,
        "tpms_values": tpms_values,
        "ready": supported_count >= 3 or tpms_pressures > 0,
    }


def _pick_first(
    supported: set,
    summary: Dict[str, object],
    candidates: Tuple[int, ...],
) -> Optional[Dict[str, object]]:
    for pid in candidates:
        if pid in supported:
            spec = STANDARD_PIDS[pid]
            return {
                "source": "PID {:02X}".format(pid),
                "pid": "{:02X}".format(pid),
                "name": spec.short,
                "unit": spec.unit,
                "value": _pid_value(summary, pid),
            }
    return None


def classify_powertrain(summary: Dict[str, object]) -> str:
    """Best-effort hint: BEV, HYBRID, ICE, or UNKNOWN."""
    supported = _pid_set(summary)
    fuel_val = str(_pid_value(summary, 0x51) or "").lower()
    if "electric" in fuel_val and "hybrid" not in fuel_val:
        return "BEV"
    if "hybrid" in fuel_val:
        return "HYBRID"

    ev_dids = _find_ev_module_dids(summary)
    has_ev_signals = (0x5B in supported) or any(h in ev_dids for h in ("7E2", "7E4", "7E5"))
    has_ice_signals = (0x0C in supported and 0x05 in supported) or (0x2F in supported)
    if has_ev_signals and has_ice_signals:
        return "HYBRID"
    if has_ev_signals and not has_ice_signals:
        return "BEV"
    if has_ice_signals or (0x0C in supported):
        return "ICE"
    return "UNKNOWN"


def analyze_ev_sound_fit(summary: Dict[str, object]) -> Dict[str, object]:
    """Evaluate real-time signal suitability for the EV Sound Emulator."""
    supported = _pid_set(summary)
    ev_dids = _find_ev_module_dids(summary)
    powertrain = classify_powertrain(summary)

    speed = _pick_first(supported, summary, (0x0D,))
    pedal = _pick_first(supported, summary, (0x49, 0x5A, 0x4A, 0x4B, 0x11, 0x45, 0x47, 0x4C))
    rpm_or_motor = _pick_first(supported, summary, (0x0C,))
    if rpm_or_motor is None and ("7E5" in ev_dids or "7E2" in ev_dids):
        mod = "7E5" if "7E5" in ev_dids else "7E2"
        first_did = sorted(ev_dids[mod])[0]
        rpm_or_motor = {
            "source": "UDS {}:{}".format(mod, first_did),
            "pid": None,
            "name": "EV_DRIVE",
            "unit": "raw",
            "value": ev_dids[mod][first_did],
        }
    torque_or_load = _pick_first(supported, summary, (0x62, 0x61, 0x04, 0x43))
    power_or_soc = _pick_first(supported, summary, (0x5B, 0x42))
    if power_or_soc is None and "7E4" in ev_dids:
        first_did = sorted(ev_dids["7E4"])[0]
        power_or_soc = {
            "source": "UDS 7E4:{}".format(first_did),
            "pid": None,
            "name": "BMS_DID",
            "unit": "raw",
            "value": ev_dids["7E4"][first_did],
        }

    channels = {
        "speed": speed,
        "pedal_or_throttle": pedal,
        "rpm_or_motor": rpm_or_motor,
        "torque_or_load": torque_or_load,
        "power_or_soc": power_or_soc,
    }
    available_channels = sum(1 for v in channels.values() if v is not None)

    recommended_pids: List[str] = []
    for ch in (speed, pedal, rpm_or_motor, torque_or_load, power_or_soc):
        if ch and ch.get("pid") and ch["pid"] not in recommended_pids:
            recommended_pids.append(str(ch["pid"]))

    bench = summary.get("benchmark", {})
    if not isinstance(bench, dict):
        bench = {}
    single_hz = float(bench.get("single_hz") or 0.0)
    multi_hz = float(bench.get("multi_hz") or 0.0)
    multi_ok = bool(bench.get("multi_pid_supported"))
    effective_hz = multi_hz if (multi_ok and multi_hz > single_hz) else (
        round(single_hz / max(1, len(recommended_pids)), 1) if single_hz else 0.0
    )

    has_motion = speed is not None and (pedal is not None or torque_or_load is not None or rpm_or_motor is not None)
    if has_motion and available_channels >= 4:
        grade = "READY"
    elif has_motion or available_channels >= 3:
        grade = "PARTIAL"
    elif available_channels >= 1:
        grade = "LIMITED"
    else:
        grade = "NONE"

    return {
        "grade": grade,
        "powertrain_hint": powertrain,
        "available_channels": available_channels,
        "total_channels": len(channels),
        "channels": channels,
        "recommended_fast_pids": recommended_pids,
        "multi_pid_supported": multi_ok,
        "single_hz": single_hz,
        "multi_hz": multi_hz,
        "effective_hz": effective_hz,
        "avg_latency_ms": bench.get("avg_ms"),
    }


def build_profile(summary: Dict[str, object], link_name: str,
                  generated_utc: str) -> Dict[str, object]:
    """Build a portable JSON profile for GPS-IMU Logger & EV Sound Emulator."""
    mode09 = summary.get("mode09", {})
    vin = ""
    ecu_name = ""
    if isinstance(mode09, dict):
        for k, v in sorted(mode09.items()):
            if k.endswith(":02") and not vin:
                vin = str(v)
            elif k.endswith(":0A") and not ecu_name:
                ecu_name = str(v)
    if not vin:
        modules = summary.get("modules", {})
        if isinstance(modules, dict):
            for info in modules.values():
                if isinstance(info, dict) and info.get("vin"):
                    vin = str(info["vin"])
                    break

    return {
        "schema_version": 1,
        "generated_utc": generated_utc,
        "adapter": {
            "link": link_name,
            "protocol": summary.get("protocol"),
            "details": summary.get("adapter", {}),
        },
        "vehicle": {
            "vin": vin,
            "ecu_name": ecu_name,
            "powertrain_hint": classify_powertrain(summary),
        },
        "benchmark": summary.get("benchmark", {}),
        "standard_pids": summary.get("pid_values", {}),
        "dtcs": summary.get("dtcs", {}),
        "mode09": mode09,
        "modules": summary.get("modules", {}),
        "logger_fit": analyze_logger_fit(summary),
        "ev_sound_fit": analyze_ev_sound_fit(summary),
    }
