"""ELM327 link layer for USB, Wi-Fi, and BLE OBD-II adapters.

Links:
  usb   USB serial adapter (vLinker FS USB, OBDLink EX, FTDI/CH340/CP210x):
        /dev/serial/by-id/*, /dev/ttyUSB*, /dev/ttyACM*
  wifi  Wi-Fi ELM327 adapter: TCP socket (typically 192.168.0.10:35000)
  ble   Bluetooth LE adapter (vLinker MC/FD/FS BLE, Vgate iCar Pro BLE,
        OBDLink CX): requires bleak (python3-bleak or pip install bleak)

Replies are read with headers and spaces on (ATH1 ATS1) so each frame
identifies its responding ECU module and ISO-TP multi-frame messages are
reassembled cleanly.
"""

import glob
import os
import socket
import threading
import time
from typing import Dict, List, Optional, Tuple


class LinkError(RuntimeError):
    """Raised when an OBD adapter link cannot be opened or drops."""


class Transport:
    """Byte pipe to an ELM327-compatible adapter."""

    name = "link"

    def write(self, data: bytes) -> None:
        raise NotImplementedError

    def read(self, timeout_s: float) -> bytes:
        raise NotImplementedError

    def close(self) -> None:
        pass


# ------------------------------------------------------------------ USB serial

_USB_HINTS = (
    "vlinker", "obdlink", "stn", "elm", "obd", "ftdi", "ch34", "cp210"
)
_USB_PROBE_BAUDS = (115200, 38400, 500000, 9600)


def find_usb_devices() -> List[str]:
    """Return candidate USB serial ports, likeliest OBD adapters first."""
    by_id = sorted(glob.glob("/dev/serial/by-id/*"))
    preferred = [p for p in by_id if any(h in p.lower() for h in _USB_HINTS)]
    others = [p for p in by_id if p not in preferred]
    plain = sorted(glob.glob("/dev/ttyUSB*")) + sorted(glob.glob("/dev/ttyACM*"))
    seen = set()
    out: List[str] = []
    for path in preferred + others + plain:
        real = os.path.realpath(path)
        if real not in seen:
            seen.add(real)
            out.append(path)
    return out


class SerialTransport(Transport):
    """USB serial ELM327/STN adapter transport with optional baud auto-probe."""

    def __init__(self, device: str, baud: int = 0):
        try:
            import serial  # pyserial
        except ImportError as exc:
            raise LinkError("usb link needs pyserial (python3-serial)") from exc

        if baud > 0:
            self._port = serial.Serial(device, baud, timeout=0)
            self.baud = baud
            self.name = "usb {} @{}".format(device, baud)
            return

        last_exc: Optional[Exception] = None
        for candidate in _USB_PROBE_BAUDS:
            port = None
            try:
                port = serial.Serial(device, candidate, timeout=0)
                port.reset_input_buffer()
                port.write(b"\rATI\r")
                reply = self._read_from_port(port, 0.8)
                text = reply.decode("ascii", "replace").upper()
                if ">" in text or "ELM" in text or "STN" in text or "VLINKER" in text:
                    self._port = port
                    self.baud = candidate
                    self.name = "usb {} @{}".format(device, candidate)
                    return
                port.close()
            except Exception as exc:
                last_exc = exc
                if port is not None:
                    try:
                        port.close()
                    except Exception:
                        pass
        # Fallback to 38400 if probe saw no banner (some adapters only wake on ATZ).
        try:
            self._port = serial.Serial(device, 38400, timeout=0)
            self.baud = 38400
            self.name = "usb {} @38400".format(device)
        except Exception as exc:
            raise LinkError(str(last_exc or exc)) from exc

    @staticmethod
    def _read_from_port(port, timeout_s: float) -> bytes:
        end = time.monotonic() + timeout_s
        buf = b""
        while True:
            chunk = port.read(4096)
            if chunk:
                buf += chunk
                if b">" in chunk:
                    return buf
            elif time.monotonic() >= end:
                return buf
            else:
                time.sleep(0.005)

    def write(self, data: bytes) -> None:
        self._port.write(data)

    def read(self, timeout_s: float) -> bytes:
        return self._read_from_port(self._port, timeout_s)

    def close(self) -> None:
        try:
            self._port.close()
        except Exception:
            pass


# --------------------------------------------------------------- Wi-Fi socket

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
                  timeout_s: float = 6.0) -> Transport:
    try:
        sock = socket.create_connection((host, port), timeout=timeout_s)
    except OSError as exc:
        raise LinkError("wifi {}:{} failed ({})".format(host, port, exc)) from exc
    return _SocketTransport(sock, "wifi {}:{}".format(host, port))


# ---------------------------------------------------------------- Bluetooth LE

_BLE_HINTS = ("vlink", "obd", "elm", "vgate", "ios-vlink", "carista", "veepeak", "obdlink")
_BLE_NOTIFY_PREFIXES = (
    "0000fff1",
    "0000ffe1",
    "6e400003",  # Nordic UART TX (notify)
    "bef8d6c9",  # Microchip / OBDLink-style transparent UART
)
_BLE_WRITE_PREFIXES = (
    "0000fff2",
    "0000ffe1",
    "6e400002",  # Nordic UART RX (write)
    "bef8d6c9",
)


def discover_ble_adapters(scan_s: float = 4.0) -> List[Tuple[str, str]]:
    """Scan for nearby BLE devices, returning (name, address) OBD hits first."""
    try:
        import asyncio
        from bleak import BleakScanner
    except ImportError as exc:
        raise LinkError("ble link needs bleak (python3-bleak)") from exc

    async def _scan():
        devices = await BleakScanner.discover(timeout=scan_s)
        obd_hits: List[Tuple[str, str]] = []
        others: List[Tuple[str, str]] = []
        for dev in devices:
            label = (dev.name or "").strip() or dev.address
            item = (label, dev.address)
            if dev.name and any(h in dev.name.lower() for h in _BLE_HINTS):
                obd_hits.append(item)
            elif dev.name:
                others.append(item)
        return obd_hits + others

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_scan())
    finally:
        loop.close()


class BleTransport(Transport):
    """BLE GATT UART transport for vLinker / Vgate / ELM327 BLE adapters."""

    def __init__(self, name_or_address: str = "", scan_s: float = 6.0):
        try:
            import asyncio
            from bleak import BleakClient, BleakScanner
        except ImportError as exc:
            raise LinkError("ble link needs bleak (python3-bleak)") from exc

        self._asyncio = asyncio
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True).start()

        async def _connect():
            target = (name_or_address or "").strip()
            resolved_name = target
            if ":" not in target:
                found = await BleakScanner.discover(timeout=scan_s)
                want = target.lower()
                hints = (want,) if want else _BLE_HINTS
                matches = [
                    d for d in found
                    if d.name and any(h in d.name.lower() for h in hints)
                ]
                if not matches:
                    raise LinkError("no BLE OBD adapter seen")
                target = matches[0].address
                resolved_name = matches[0].name or target

            client = BleakClient(target)
            await client.connect()
            notify = write = None
            for service in client.services:
                for ch in service.characteristics:
                    uuid = ch.uuid.lower()
                    props = set(ch.properties)
                    if notify is None and "notify" in props and uuid.startswith(_BLE_NOTIFY_PREFIXES):
                        notify = ch
                    if write is None and ({"write", "write-without-response"} & props) and uuid.startswith(_BLE_WRITE_PREFIXES):
                        write = ch
            if notify is None or write is None:
                for service in client.services:
                    for ch in service.characteristics:
                        props = set(ch.properties)
                        if notify is None and "notify" in props:
                            notify = ch
                        if write is None and ({"write", "write-without-response"} & props):
                            write = ch
            if notify is None or write is None:
                await client.disconnect()
                raise LinkError("BLE adapter has no notify/write UART characteristic")
            await client.start_notify(notify, self._on_notify)
            write_with_response = "write-without-response" not in set(write.properties)
            return client, write, write_with_response, resolved_name, target

        try:
            fut = asyncio.run_coroutine_threadsafe(_connect(), self._loop)
            (
                self._client,
                self._write_char,
                self._write_response,
                resolved_name,
                address,
            ) = fut.result(timeout=scan_s + 20)
            self.name = "ble {} ({})".format(resolved_name, address)
        except Exception:
            self._loop.call_soon_threadsafe(self._loop.stop)
            raise

    def _on_notify(self, _char, data: bytearray) -> None:
        with self._lock:
            self._buf.extend(data)

    def write(self, data: bytes) -> None:
        # Chunk writes at 20 bytes to stay safe across minimum BLE ATT MTUs.
        async def _send():
            for idx in range(0, len(data), 20):
                await self._client.write_gatt_char(
                    self._write_char,
                    data[idx:idx + 20],
                    response=self._write_response,
                )

        fut = self._asyncio.run_coroutine_threadsafe(_send(), self._loop)
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
                self._client.disconnect(), self._loop
            ).result(timeout=5)
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)


# ------------------------------------------------------------- ELM327 session

TEXT_ERRORS = (
    "CAN ERROR", "BUS INIT", "BUS ERROR", "UNABLE TO CONNECT",
    "BUFFER FULL", "STOPPED", "FB ERROR", "DATA ERROR", "ERR",
    "LV RESET", "ACT ALERT",
)


class Elm327:
    """High-level ELM327/STN session with ISO-TP CAN frame parsing."""

    def __init__(self, transport: Transport):
        self.t = transport

    def command(self, cmd: str, timeout_s: float = 2.0) -> List[str]:
        """Send one AT/OBD command; collect reply lines up to '>'."""
        self.t.read(0)
        self.t.write((cmd + "\r").encode("ascii"))
        return self._collect(cmd, timeout_s)

    def listen(self, timeout_s: float = 5.0) -> List[str]:
        """Wait for delayed reply frames without repeating the last command."""
        return [line for line in self._collect("", timeout_s) if line != "TIMEOUT"]

    def _collect(self, cmd: str, timeout_s: float) -> List[str]:
        raw = b""
        end = time.monotonic() + timeout_s
        while b">" not in raw and time.monotonic() < end:
            raw += self.t.read(max(0.01, end - time.monotonic()))
        text = raw.decode("ascii", "replace").replace(">", "")
        lines: List[str] = []
        for line in text.replace("\n", "\r").split("\r"):
            line = line.strip()
            if not line or line == cmd or line.startswith("SEARCHING"):
                continue
            lines.append(line)
        if b">" not in raw:
            lines.append("TIMEOUT")
        return lines

    def init(self, protocol: str = "0") -> Dict[str, object]:
        """Reset adapter, enable headers/spaces, and verify ECU communication."""
        info: Dict[str, object] = {}
        info["ATZ"] = self.command("ATZ", 4.0)
        for cmd in ("ATE0", "ATL0", "ATS1", "ATH1", "ATAT1"):
            info[cmd] = self.command(cmd)
        tried = [protocol] if protocol == "0" else [protocol, "0"]
        for proto in tried:
            info["ATSP" + proto] = self.command("ATSP" + proto)
            reply = self.command("0100", 8.0)
            info["0100@" + proto] = reply
            parsed = parse_frames(reply)
            if any(parsed.values()):
                detected = self.command("ATDPN")
                info["ATDPN"] = detected
                reported = detected[-1].strip().upper().lstrip("A") if detected else ""
                info["protocol"] = (
                    reported if reported in "0123456789ABCDEF" else proto
                )
                break
        return info


def _hex_tokens(line: str) -> Optional[List[int]]:
    toks = line.split()
    try:
        return [int(t, 16) for t in toks]
    except ValueError:
        return None


def parse_frames(lines: List[str]) -> Dict[str, bytes]:
    """Reassemble CAN 11-bit (3-hex) and 29-bit (8-hex) ISO-TP frames by header."""
    parts: Dict[str, Tuple[int, bytearray]] = {}
    done: Dict[str, bytes] = {}
    for line in lines:
        toks = line.split()
        if len(toks) < 2:
            continue
        # 11-bit CAN header = 3 hex chars; 29-bit CAN header = 8 hex chars.
        if len(toks[0]) in (3, 8):
            try:
                int(toks[0], 16)
            except ValueError:
                continue
            header = toks[0].upper()
            data = _hex_tokens(" ".join(toks[1:]))
            if data is None or not data:
                continue
            kind = data[0] >> 4
            if kind == 0:
                length = data[0] & 0x0F
                done[header] = bytes(data[1:1 + length])
            elif kind == 1 and len(data) >= 2:
                length = ((data[0] & 0x0F) << 8) | data[1]
                parts[header] = (length, bytearray(data[2:]))
            elif kind == 2 and header in parts:
                length, buf = parts[header]
                buf.extend(data[1:])
                if len(buf) >= length:
                    done[header] = bytes(buf[:length])
                    del parts[header]
            continue

        # Fallback for non-CAN protocols (KWP/ISO9141/J1850) with ATH1 headers.
        if len(toks) >= 5 and len(toks[0]) == 2 and len(toks[1]) == 2 and len(toks[2]) == 2:
            data = _hex_tokens(" ".join(toks))
            if data is not None and len(data) >= 5:
                src = "{:02X}".format(data[2])
                done[src] = bytes(data[3:-1]) if len(data) > 4 else bytes(data[3:])

    for header, (_length, buf) in parts.items():
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
    """Classify one reply to `service`: positive, nrc, no_data, error, or other."""
    frames = parse_frames(lines)
    text = " ".join(lines).upper()
    replies: Dict[str, Dict[str, str]] = {}
    best: Optional[str] = None
    for header, data in sorted(frames.items()):
        if data and data[0] == service + 0x40:
            replies[header] = {"kind": "positive", "data": data[1:].hex()}
            best = best or "positive"
        elif len(data) >= 3 and data[0] == 0x7F and data[1] == service:
            replies[header] = {
                "kind": "nrc",
                "nrc": "{:02X}".format(data[2]),
                "nrc_name": NRC_NAMES.get(data[2], "unknown"),
            }
            best = best or "nrc"
        else:
            replies[header] = {"kind": "other", "data": data.hex()}

    if best is None:
        if "NO DATA" in text:
            best = "no_data"
        elif "TIMEOUT" in text or any(err in text for err in TEXT_ERRORS) or "?" in text:
            best = "error"
        elif replies:
            best = "other"
        else:
            best = "no_data"

    if any(r["kind"] == "positive" for r in replies.values()):
        best = "positive"
    return {"kind": best, "replies": replies}
