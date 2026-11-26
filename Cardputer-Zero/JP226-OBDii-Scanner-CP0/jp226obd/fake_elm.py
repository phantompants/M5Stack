"""Simulated ELM327 adapters for offline testing and desktop UI preview.

Includes three vehicle profiles:
  ranger  ICE/diesel truck (standard PIDs, DTCs, Ford BCM TPMS DIDs)
  ev      Battery Electric Vehicle (pedal, HV SoC, torque, BMS/VCU DIDs)
  hybrid  Plug-in Hybrid (both ICE RPM/coolant/fuel and EV HV/pedal PIDs)
"""

import time
from typing import Callable, Dict, List, Optional, Tuple

from .elm327 import Transport

DEMO_PROFILES = ("ranger", "ev", "hybrid")

_VIN_BY_PROFILE = {
    "ranger": b"MNAUMFF50PW000000",
    "ev": b"5YJ3E1EA1PF000000",
    "hybrid": b"JTDKN3DP0P0000000",
}

_ECU_NAME_BY_PROFILE = {
    "ranger": b"ECM\x00-RangerControl",
    "ev": b"VCU\x00-EVDriveCtrl",
    "hybrid": b"HCU\x00-HybridCtrl",
}

_PIDS_BY_PROFILE: Dict[str, Tuple[int, ...]] = {
    "ranger": (
        0x01, 0x04, 0x05, 0x0B, 0x0C, 0x0D, 0x0F, 0x11,
        0x2F, 0x33, 0x42, 0x46, 0x49, 0x51, 0x5C,
    ),
    "ev": (
        0x01, 0x0D, 0x42, 0x46, 0x49, 0x4A, 0x51, 0x5A,
        0x5B, 0x61, 0x62, 0x63, 0xA6,
    ),
    "hybrid": (
        0x01, 0x04, 0x05, 0x0B, 0x0C, 0x0D, 0x0F, 0x11,
        0x2F, 0x33, 0x42, 0x46, 0x49, 0x51, 0x5A, 0x5B,
        0x5C, 0x61, 0x62, 0x63,
    ),
}

_MODULES_BY_PROFILE: Dict[str, Dict[int, Dict[int, object]]] = {
    "ranger": {
        0x7E0: {
            0xF190: _VIN_BY_PROFILE["ranger"],
            0xF188: b"PB3A-14C204-AB",
            0xDD01: bytes([0x01, 0x86, 0xA0]),
        },
        0x726: {
            0xF190: _VIN_BY_PROFILE["ranger"],
            0x280F: bytes.fromhex("11223344"),
            0x2810: bytes.fromhex("11223345"),
            0x2811: bytes.fromhex("11223346"),
            0x2812: bytes.fromhex("11223347"),
            0x2813: bytes([0x02, 0xD0]),  # 720 / 20 = 36.0 PSI
            0x2814: bytes([0x02, 0xDA]),  # 730 / 20 = 36.5 PSI
            0x2815: bytes([0x03, 0x84]),  # 900 / 20 = 45.0 PSI
            0x2816: bytes([0x03, 0x8E]),  # 910 / 20 = 45.5 PSI
            0x280B: bytes.fromhex("1122334402D0430600"),
            0x2820: 0x33,
        },
        0x760: {0xF190: 0x31},
    },
    "ev": {
        0x7E2: {
            0xF190: _VIN_BY_PROFILE["ev"],
            0xF188: b"EV-VCU-2026A",
            0x0101: bytes([0x00, 0x2E, 0x1A, 0x44]),  # motor/drive state
        },
        0x7E4: {
            0xF190: _VIN_BY_PROFILE["ev"],
            0x0101: bytes([0x53, 0x0E, 0x9C, 0x10]),  # BMS SoC / HV pack V / I
            0x0105: bytes([0x18, 0x19, 0x1A, 0x19]),  # battery module temps
        },
        0x7E5: {
            0xF190: _VIN_BY_PROFILE["ev"],
            0x2101: bytes([0x13, 0x88, 0x00, 0xFA]),  # inverter motor rpm / torque
        },
        0x760: {0xF190: 0x31},
    },
    "hybrid": {
        0x7E0: {
            0xF190: _VIN_BY_PROFILE["hybrid"],
            0xF188: b"HYB-ECM-04",
        },
        0x7E4: {
            0xF190: _VIN_BY_PROFILE["hybrid"],
            0x0101: bytes([0x4E, 0x08, 0x34, 0x05]),
        },
    },
}

DEFAULT_NRC = 0x31


def _frames(header: int, payload: bytes) -> List[str]:
    h = "{:03X}".format(header)
    if len(payload) <= 7:
        return ["{} {:02X} {}".format(h, len(payload), payload.hex(" ").upper())]
    n = len(payload)
    lines = ["{} {:02X} {:02X} {}".format(
        h, 0x10 | (n >> 8), n & 0xFF, payload[:6].hex(" ").upper()
    )]
    seq, idx = 1, 6
    while idx < n:
        lines.append("{} {:02X} {}".format(
            h, 0x20 | (seq & 0x0F), payload[idx:idx + 7].hex(" ").upper()
        ))
        seq += 1
        idx += 7
    return lines


class FakeElm(Transport):
    """Simulated ELM327 adapter returning deterministic yet live-varying data."""

    def __init__(
        self,
        profile: str = "ranger",
        modules: Optional[Dict[int, Dict[int, object]]] = None,
        supported_pids: Optional[Tuple[int, ...]] = None,
        clock: Callable[[], float] = time.monotonic,
        multi_pid: bool = True,
    ):
        if profile not in DEMO_PROFILES:
            profile = "ranger"
        self.profile = profile
        self.name = "demo ELM327 ({})".format(profile)
        self.modules = _MODULES_BY_PROFILE[profile] if modules is None else modules
        self.pids = set(_PIDS_BY_PROFILE[profile] if supported_pids is None else supported_pids)
        self.vin = _VIN_BY_PROFILE[profile]
        self.ecu_name = _ECU_NAME_BY_PROFILE[profile]
        self.clock = clock
        self.multi_pid = multi_pid
        self.header = 0x7DF
        self.out = b""
        self.requests: List[str] = []

    def write(self, data: bytes) -> None:
        cmd = data.decode("ascii").strip().upper().replace(" ", "")
        self.requests.append(cmd)
        self.out += ("\r".join(self._answer(cmd)) + "\r\r>").encode("ascii")

    def read(self, timeout_s: float) -> bytes:
        out, self.out = self.out, b""
        return out

    def _dynamic_pid_bytes(self, pid: int) -> bytes:
        phase = int(self.clock() * 4) % 10
        pedal = 18 + phase * 6  # sweeps 18..72%
        speed = 42 + phase * 4  # sweeps 42..78 km/h
        rpm_raw = (1400 + phase * 220) * 4
        if pid == 0x01:
            return bytes([0x81 if self.profile == "ranger" else 0x00, 0x07, 0x65, 0x00])
        if pid == 0x04:
            return bytes([int((28 + phase * 4) * 2.55)])
        if pid == 0x05:
            return bytes([88 + 40])  # 88 C
        if pid == 0x0B:
            return bytes([108 + phase * 3])
        if pid == 0x0C:
            return bytes([(rpm_raw >> 8) & 0xFF, rpm_raw & 0xFF])
        if pid == 0x0D:
            return bytes([speed])
        if pid == 0x0F:
            return bytes([31 + 40])
        if pid == 0x11:
            return bytes([int(pedal * 2.55)])
        if pid == 0x2F:
            return bytes([int(64 * 2.55)])
        if pid == 0x33:
            return bytes([101])
        if pid == 0x42:
            mv = 14120 if self.profile == "ranger" else 13850
            return bytes([mv >> 8, mv & 0xFF])
        if pid == 0x46:
            return bytes([24 + 40])
        if pid in (0x49, 0x4A, 0x4B, 0x5A):
            return bytes([int(pedal * 2.55)])
        if pid == 0x51:
            return bytes([4 if self.profile == "ranger" else (8 if self.profile == "ev" else 20)])
        if pid == 0x5B:
            return bytes([int(78 * 2.55)])
        if pid == 0x5C:
            return bytes([94 + 40])
        if pid == 0x61:
            return bytes([125 + 20 + phase * 3])
        if pid == 0x62:
            return bytes([125 + 18 + phase * 3])
        if pid == 0x63:
            return bytes([0x01, 0x90])  # 400 Nm
        if pid == 0xA6:
            return bytes([0x00, 0x02, 0x62, 0x5A])  # 15625.0 km
        return bytes([0x40, 0x10])

    def _answer(self, cmd: str) -> List[str]:
        if cmd.startswith("AT"):
            if cmd == "ATZ":
                return ["", "ELM327 v1.5"]
            if cmd == "ATI":
                return ["ELM327 v1.5"]
            if cmd == "AT@1":
                return ["DEMO OBD ADAPTER ({})".format(self.profile.upper())]
            if cmd == "ATRV":
                return ["13.9V" if self.profile == "ev" else "14.1V"]
            if cmd == "ATDP":
                return ["ISO 15765-4 (CAN 11/500)"]
            if cmd == "ATDPN":
                return ["6"]
            if cmd.startswith("ATSH"):
                self.header = int(cmd[4:], 16)
            return ["OK"]
        if cmd.startswith("ST"):
            return ["?"]
        try:
            req = bytes.fromhex(cmd)
        except ValueError:
            return ["?"]

        service = req[0]
        reply_hdr = 0x7E8 if self.header == 0x7DF else self.header + 8
        if service == 0x01 and self.header in (0x7DF, 0x7E0, 0x7E2):
            return self._mode01(req, reply_hdr)
        if service == 0x03 and self.header in (0x7DF, 0x7E0, 0x7E2):
            if self.profile == "ranger":
                return _frames(reply_hdr, bytes([0x43, 0x01, 0x20, 0x02]))
            return _frames(reply_hdr, bytes([0x43, 0x00]))
        if service == 0x07 and self.header in (0x7DF, 0x7E0, 0x7E2):
            if self.profile == "ranger":
                return _frames(reply_hdr, bytes([0x47, 0x01, 0x02, 0x99]))
            return _frames(reply_hdr, bytes([0x47, 0x00]))
        if service == 0x09 and self.header in (0x7DF, 0x7E0, 0x7E2):
            return self._mode09(req, reply_hdr)
        if service == 0x22 and len(req) == 3:
            dids = self.modules.get(self.header)
            if dids is None:
                return ["NO DATA"]
            did = (req[1] << 8) | req[2]
            value = dids.get(did, DEFAULT_NRC)
            if isinstance(value, int):
                payload = bytes([0x7F, 0x22, value])
            else:
                payload = bytes([0x62, req[1], req[2]]) + value
            return _frames(self.header + 8, payload)
        return ["NO DATA"]

    def _mode01(self, req: bytes, reply_hdr: int) -> List[str]:
        pids = list(req[1:])
        if len(pids) == 1:
            pid = pids[0]
            if pid % 0x20 == 0:
                bits = 0
                for p in self.pids:
                    if pid < p <= pid + 0x20:
                        bits |= 1 << (32 - (p - pid))
                if any(p > pid + 0x20 for p in self.pids):
                    bits |= 1
                if bits == 0 and pid != 0:
                    return ["NO DATA"]
                return _frames(reply_hdr, bytes([0x41, pid]) + bits.to_bytes(4, "big"))
            if pid in self.pids:
                return _frames(reply_hdr, bytes([0x41, pid]) + self._dynamic_pid_bytes(pid))
            return ["NO DATA"]

        if not self.multi_pid:
            return ["NO DATA"]
        body = bytearray([0x41])
        any_hit = False
        for pid in pids:
            if pid in self.pids:
                any_hit = True
                body.append(pid)
                body.extend(self._dynamic_pid_bytes(pid))
        if not any_hit:
            return ["NO DATA"]
        return _frames(reply_hdr, bytes(body))

    def _mode09(self, req: bytes, reply_hdr: int) -> List[str]:
        pid = req[1]
        if pid == 0x00:
            return _frames(reply_hdr, bytes([0x49, 0x00, 0x54, 0x40, 0x00, 0x00]))
        if pid == 0x02:
            return _frames(reply_hdr, bytes([0x49, 0x02, 0x01]) + self.vin)
        if pid == 0x0A:
            return _frames(reply_hdr, bytes([0x49, 0x0A, 0x01]) + self.ecu_name)
        return ["NO DATA"]
