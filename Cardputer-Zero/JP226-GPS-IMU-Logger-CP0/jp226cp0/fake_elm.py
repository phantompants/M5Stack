"""A pretend ELM327 on a pretend Ford, for tests and the scanner's --demo.

It answers like a real adapter with ATH1 ATS1 (headers and spaces on),
including ISO-TP multi-frame replies, NO DATA for absent modules and
negative replies for unknown DIDs. The numbers are made up.
"""

from typing import Dict, Optional, Tuple

from .elm327 import Transport

VIN = b"MNAUMFF50PW000000"  # made up

# request header -> {DID: reply bytes, or an NRC int}
DEMO_MODULES: Dict[int, Dict[int, object]] = {
    0x7E0: {0xF190: VIN, 0xF188: b"PB3A-14C204-AB", 0xDD01: bytes([0x01, 0x86, 0xA0])},
    0x726: {0xF190: VIN, 0x2813: bytes([0x0E, 0x10]), 0x2814: bytes([0x0E, 0x1A]),
            0x2815: bytes([0x0D, 0xFC]), 0x2816: bytes([0x0E, 0x06]), 0x2820: 0x33},
    0x760: {0xF190: 0x31},  # answers, but has no VIN DID
}
DEFAULT_NRC = 0x31


def _frames(header: int, payload: bytes):
    h = "{:03X}".format(header)
    if len(payload) <= 7:
        yield "{} {:02X} {}".format(h, len(payload), payload.hex(" ").upper())
        return
    n = len(payload)
    yield "{} {:02X} {:02X} {}".format(h, 0x10 | (n >> 8), n & 0xFF,
                                       payload[:6].hex(" ").upper())
    seq, i = 1, 6
    while i < n:
        yield "{} {:02X} {}".format(h, 0x20 | (seq & 0x0F),
                                    payload[i:i + 7].hex(" ").upper())
        seq += 1
        i += 7


class FakeElm(Transport):
    name = "fake ELM327"

    def __init__(self, modules: Optional[Dict[int, Dict[int, object]]] = None,
                 supported_pids: Tuple[int, ...] = (0x04, 0x05, 0x0C, 0x0D, 0x0F, 0x11, 0x2F, 0x33, 0x42, 0x46)):
        self.modules = DEMO_MODULES if modules is None else modules
        self.pids = set(supported_pids)
        self.header = 0x7DF
        self.out = b""
        self.requests = []

    def write(self, data: bytes) -> None:
        cmd = data.decode("ascii").strip().upper().replace(" ", "")
        self.requests.append(cmd)
        self.out += ("\r".join(self._answer(cmd)) + "\r\r>").encode("ascii")

    def read(self, timeout_s: float) -> bytes:
        out, self.out = self.out, b""
        return out

    # ----------------------------------------------------------------
    def _answer(self, cmd: str):
        if cmd.startswith("AT"):
            if cmd == "ATZ":
                return ["", "ELM327 v1.5"]
            if cmd == "ATI":
                return ["ELM327 v1.5"]
            if cmd == "AT@1":
                return ["FAKE ADAPTER"]
            if cmd == "ATRV":
                return ["12.6V"]
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
        if service == 0x01 and self.header in (0x7DF, 0x7E0):
            return self._mode01(req)
        if service == 0x09 and self.header in (0x7DF, 0x7E0):
            return self._mode09(req)
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
            return list(_frames(self.header + 8, payload))
        return ["NO DATA"]

    def _mode01(self, req: bytes):
        pid = req[1]
        if pid % 0x20 == 0:  # supported-PID bitmap
            bits = 0
            for p in self.pids:
                if pid < p <= pid + 0x20:
                    bits |= 1 << (32 - (p - pid))
            if any(p > pid + 0x20 for p in self.pids):
                bits |= 1
            if bits == 0 and pid != 0:
                return ["NO DATA"]
            return list(_frames(0x7E8, bytes([0x41, pid]) + bits.to_bytes(4, "big")))
        if pid in self.pids:
            return list(_frames(0x7E8, bytes([0x41, pid, 0x40, 0x10])))
        return ["NO DATA"]

    def _mode09(self, req: bytes):
        pid = req[1]
        if pid == 0x00:
            return list(_frames(0x7E8, bytes([0x49, 0x00, 0x54, 0x40, 0x00, 0x00])))
        if pid == 0x02:
            return list(_frames(0x7E8, bytes([0x49, 0x02, 0x01]) + VIN))
        if pid == 0x0A:
            return list(_frames(0x7E8, bytes([0x49, 0x0A, 0x01]) + b"ECM\x00-EngineControl"))
        return ["NO DATA"]
