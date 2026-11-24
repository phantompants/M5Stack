"""ELM327 link for OBD-II adapters, over any of the links an adapter offers.

  usb   a USB adapter (vLinker USB and others): /dev/ttyUSB*, /dev/ttyACM*
  bt    a Bluetooth Classic (SPP) adapter, by address, over an RFCOMM socket
  wifi  a Wi-Fi adapter: TCP, usually 192.168.0.10:35000
  ble   a Bluetooth LE adapter (vLinker FS/MS, Vgate BLE): needs python3-bleak

USB is the most reliable on the CardputerZero: no pairing, no radio.

Only the standard library is needed for wifi and bt; usb needs pyserial and
ble needs bleak, both imported only when that link is used.

Replies are read with headers and spaces on (ATH1 ATS1), so every frame says
which module sent it, and ISO-TP multi-frame replies are put back together
here (parse_frames).
"""

import glob
import os
import socket
import threading
import time
from typing import Dict, List, Optional, Tuple


class LinkError(RuntimeError):
    pass


class Transport:
    """Byte pipe to the adapter."""

    name = "link"

    def write(self, data: bytes) -> None:
        raise NotImplementedError

    def read(self, timeout_s: float) -> bytes:
        """Whatever arrives within timeout_s (may be empty)."""
        raise NotImplementedError

    def close(self) -> None:
        pass


# ------------------------------------------------------------------ USB serial

def find_usb_devices() -> List[str]:
    """USB serial ports, the likeliest OBD adapter first."""
    by_id = sorted(glob.glob("/dev/serial/by-id/*"))
    hints = ("vlinker", "elm", "obd", "stn", "ftdi", "ch34", "cp210", "obdlink")
    preferred = [p for p in by_id if any(h in p.lower() for h in hints)]
    others = [p for p in by_id if p not in preferred]
    plain = sorted(glob.glob("/dev/ttyUSB*")) + sorted(glob.glob("/dev/ttyACM*"))
    seen, out = set(), []
    for p in preferred + others + plain:
        real = os.path.realpath(p)
        if real not in seen:
            seen.add(real)
            out.append(p)
    return out


class SerialTransport(Transport):
    def __init__(self, device: str, baud: int = 38400):
        try:
            import serial  # pyserial
        except ImportError as e:
            raise LinkError("usb link needs pyserial (python3-serial)") from e
        self.name = "usb {} @{}".format(device, baud)
        self._port = serial.Serial(device, baud, timeout=0)

    def write(self, data: bytes) -> None:
        self._port.write(data)

    def read(self, timeout_s: float) -> bytes:
        end = time.monotonic() + timeout_s
        buf = b""
        while True:
            chunk = self._port.read(4096)
            if chunk:
                buf += chunk
                if b">" in chunk:
                    return buf
            elif time.monotonic() >= end:
                return buf
            else:
                time.sleep(0.005)

    def close(self) -> None:
        self._port.close()


# ------------------------------------------------------------- socket links

class _SocketTransport(Transport):
    def __init__(self, sock: socket.socket, name: str):
        self._sock = sock
        self.name = name

    def write(self, data: bytes) -> None:
        self._sock.sendall(data)

    def read(self, timeout_s: float) -> bytes:
        end = time.monotonic() + timeout_s
        buf = b""
        while True:
            left = end - time.monotonic()
            if left <= 0:
                return buf
            self._sock.settimeout(left)
            try:
                chunk = self._sock.recv(4096)
            except socket.timeout:
                return buf
            if not chunk:
                raise LinkError("adapter closed the connection")
            buf += chunk
            if b">" in chunk:
                return buf

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


def tcp_transport(host: str = "192.168.0.10", port: int = 35000,
                  timeout_s: float = 8.0) -> Transport:
    sock = socket.create_connection((host, port), timeout=timeout_s)
    return _SocketTransport(sock, "wifi {}:{}".format(host, port))


def rfcomm_transport(address: str, channel: int = 1) -> Transport:
    """Bluetooth Classic SPP. Pair the adapter first (bluetoothctl)."""
    if not hasattr(socket, "AF_BLUETOOTH"):
        raise LinkError("bt link needs Linux Bluetooth sockets")
    sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM,
                         socket.BTPROTO_RFCOMM)
    sock.settimeout(10)
    sock.connect((address, channel))
    return _SocketTransport(sock, "bt {} ch{}".format(address, channel))


# ---------------------------------------------------------------- Bluetooth LE

class BleTransport(Transport):
    """BLE UART-style adapter. UNTESTED on the CardputerZero."""

    # Common ELM327 BLE services: FFF0 (FFF1 notify, FFF2 write) and FFE0 (FFE1).
    _NOTIFY = ("0000fff1", "0000ffe1")
    _WRITE = ("0000fff2", "0000ffe1")

    def __init__(self, name_or_address: str = "", scan_s: float = 8.0):
        try:
            import asyncio
            from bleak import BleakClient, BleakScanner
        except ImportError as e:
            raise LinkError("ble link needs bleak (python3-bleak)") from e
        self._asyncio = asyncio
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True).start()

        async def connect():
            target = name_or_address
            if ":" not in target:
                found = await BleakScanner.discover(timeout=scan_s)
                want = (target or "").lower()
                hints = (want,) if want else ("vlink", "obd", "elm", "vgate", "ios-vlink")
                match = [d for d in found if d.name and any(h in d.name.lower() for h in hints)]
                if not match:
                    raise LinkError("no BLE OBD adapter seen")
                target = match[0].address
            client = BleakClient(target)
            await client.connect()
            notify = write = None
            for service in client.services:
                for ch in service.characteristics:
                    uuid = ch.uuid.lower()
                    if notify is None and "notify" in ch.properties and uuid.startswith(self._NOTIFY):
                        notify = ch
                    if write is None and ("write" in ch.properties or "write-without-response" in ch.properties) \
                            and uuid.startswith(self._WRITE):
                        write = ch
            if notify is None or write is None:  # any notify + any write
                for service in client.services:
                    for ch in service.characteristics:
                        if notify is None and "notify" in ch.properties:
                            notify = ch
                        if write is None and "write" in ch.properties:
                            write = ch
            if notify is None or write is None:
                raise LinkError("adapter has no notify/write characteristic")
            await client.start_notify(notify, self._on_notify)
            return client, write, target

        fut = asyncio.run_coroutine_threadsafe(connect(), self._loop)
        self._client, self._write_char, address = fut.result(timeout=scan_s + 20)
        self.name = "ble {}".format(address)

    def _on_notify(self, _char, data: bytearray) -> None:
        with self._lock:
            self._buf.extend(data)

    def write(self, data: bytes) -> None:
        fut = self._asyncio.run_coroutine_threadsafe(
            self._client.write_gatt_char(self._write_char, data, response=False),
            self._loop)
        fut.result(timeout=5)

    def read(self, timeout_s: float) -> bytes:
        end = time.monotonic() + timeout_s
        while True:
            with self._lock:
                if b">" in self._buf or time.monotonic() >= end:
                    out = bytes(self._buf)
                    self._buf.clear()
                    return out
            time.sleep(0.01)

    def close(self) -> None:
        try:
            self._asyncio.run_coroutine_threadsafe(
                self._client.disconnect(), self._loop).result(timeout=5)
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)


# ------------------------------------------------------------- ELM327 session

TEXT_ERRORS = ("CAN ERROR", "BUS INIT", "BUS ERROR", "UNABLE TO CONNECT",
               "BUFFER FULL", "STOPPED", "FB ERROR", "DATA ERROR", "ERR",
               "LV RESET", "ACT ALERT")


class Elm327:
    def __init__(self, transport: Transport):
        self.t = transport

    def command(self, cmd: str, timeout_s: float = 2.0) -> List[str]:
        """Send one command; the reply lines up to the '>' prompt."""
        self.t.read(0)  # drop anything stale
        self.t.write((cmd + "\r").encode("ascii"))
        return self._collect(cmd, timeout_s)

    def listen(self, timeout_s: float = 5.0) -> List[str]:
        """More reply lines without sending anything (never send a bare CR:
        an ELM327 repeats the last command on one)."""
        return [l for l in self._collect("", timeout_s) if l != "TIMEOUT"]

    def _collect(self, cmd: str, timeout_s: float) -> List[str]:
        raw = b""
        end = time.monotonic() + timeout_s
        while b">" not in raw and time.monotonic() < end:
            raw += self.t.read(max(0.01, end - time.monotonic()))
        text = raw.decode("ascii", "replace").replace(">", "")
        lines = []
        for line in text.replace("\n", "\r").split("\r"):
            line = line.strip()
            if not line or line == cmd or line.startswith("SEARCHING"):
                continue
            lines.append(line)
        if b">" not in raw:
            lines.append("TIMEOUT")
        return lines

    def init(self, protocol: str = "6") -> Dict[str, object]:
        """Reset and set up for logging every frame. Returns what was learnt."""
        info: Dict[str, object] = {}
        info["ATZ"] = self.command("ATZ", 4.0)
        for cmd in ("ATE0", "ATL0", "ATS1", "ATH1", "ATAT1"):
            info[cmd] = self.command(cmd)
        tried = [protocol] if protocol == "0" else [protocol, "0"]
        for p in tried:
            info["ATSP" + p] = self.command("ATSP" + p)
            reply = self.command("0100", 8.0)
            info["0100@" + p] = reply
            if any(parse_frames(reply)):
                detected = self.command("ATDPN")
                info["ATDPN"] = detected
                reported = detected[-1].strip().upper() if detected else ""
                info["protocol"] = (
                    reported if reported in "0123456789ABCDEF" else p)
                break
        return info


def _hex_tokens(line: str) -> Optional[List[int]]:
    toks = line.split()
    try:
        return [int(t, 16) for t in toks]
    except ValueError:
        return None


def parse_frames(lines: List[str]) -> Dict[str, bytes]:
    """CAN 11-bit replies (headers and spaces on) by sending module header.

    Single and multi-frame ISO-TP messages are reassembled. A module that sends
    more than one message keeps the last. Non-CAN or unexpected lines are
    skipped (the raw lines are always logged anyway).
    """
    parts: Dict[str, Tuple[int, bytearray]] = {}
    done: Dict[str, bytes] = {}
    for line in lines:
        toks = line.split()
        if len(toks) < 2 or len(toks[0]) != 3:
            continue
        header = toks[0].upper()
        data = _hex_tokens(" ".join(toks[1:]))
        if data is None or not data:
            continue
        kind = data[0] >> 4
        if kind == 0:  # single frame
            n = data[0] & 0x0F
            done[header] = bytes(data[1:1 + n])
        elif kind == 1 and len(data) >= 2:  # first frame
            n = ((data[0] & 0x0F) << 8) | data[1]
            parts[header] = (n, bytearray(data[2:]))
        elif kind == 2 and header in parts:  # consecutive frame
            n, buf = parts[header]
            buf.extend(data[1:])
            if len(buf) >= n:
                done[header] = bytes(buf[:n])
                del parts[header]
    for header, (n, buf) in parts.items():  # incomplete: keep what came
        done.setdefault(header, bytes(buf))
    return done


NRC_NAMES = {
    0x10: "generalReject",
    0x11: "serviceNotSupported",
    0x12: "subFunctionNotSupported",
    0x13: "incorrectMessageLength",
    0x14: "responseTooLong",
    0x21: "busyRepeatRequest",
    0x22: "conditionsNotCorrect",
    0x24: "requestSequenceError",
    0x25: "noResponseFromSubnet",
    0x31: "requestOutOfRange",
    0x33: "securityAccessDenied",
    0x35: "invalidKey",
    0x78: "responsePending",
    0x7E: "subFunctionNotSupportedInActiveSession",
    0x7F: "serviceNotSupportedInActiveSession",
}


def classify(lines: List[str], service: int) -> Dict[str, object]:
    """One reply to a request for `service`: positive, nrc, no_data or error."""
    frames = parse_frames(lines)
    text = " ".join(lines).upper()
    replies = {}
    best = None
    for header, data in sorted(frames.items()):
        if data and data[0] == service + 0x40:
            replies[header] = {"kind": "positive", "data": data[1:].hex()}
            best = best or "positive"
        elif len(data) >= 3 and data[0] == 0x7F and data[1] == service:
            replies[header] = {"kind": "nrc", "nrc": "{:02X}".format(data[2]),
                               "nrc_name": NRC_NAMES.get(data[2], "unknown")}
            best = best or "nrc"
        else:
            replies[header] = {"kind": "other", "data": data.hex()}
    if best is None:
        if "NO DATA" in text:
            best = "no_data"
        elif "TIMEOUT" in text or any(e in text for e in TEXT_ERRORS) or "?" in text:
            best = "error"
        elif replies:
            best = "other"
        else:
            best = "no_data"
    # A positive reply beats an NRC from another module in the overall kind.
    if any(r["kind"] == "positive" for r in replies.values()):
        best = "positive"
    return {"kind": best, "replies": replies}
