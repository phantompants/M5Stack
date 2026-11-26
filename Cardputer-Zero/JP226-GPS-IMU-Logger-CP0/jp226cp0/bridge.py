"""Link to the AtomS3 USB bridge (``atoms3_zero_bridge`` in the ESP32 repo).

The AtomS3 sits on an Atomic GPS Base and plugs into a Zero USB port, where it
shows up as a USB CDC serial port (hardware.BRIDGE_SERIAL_CANDIDATES). It gives
the Zero two things the CM0 cannot do itself:

- ESP-NOW with the JP226 accessories (Dial, Echo, black box).
- GPS from its GPS Base, for when no GPS Cap is fitted (the NFC/Sub-1GHz Cap
  jams the Zero's only UART RX, GPIO15).

The link is NMEA-style text lines ``$<body>*HH``. The AtomS3 sends the GPS
Base NMEA unchanged, plus ``$PJP226,...`` lines of its own; see
docs/zero-bridge.md in the ESP32 repo for the full protocol.

This module handles the bridge's status, the GPS stream, the Zero's
HELLO/TIME/GPS lines, and the M5Dial (and any other controller): its
DialCommands arrive as ``RX`` lines, and the Zero answers with DialAck,
DialStatus and PositionReport packets in ``TX`` lines (espnow.py), the way the
ESP32 logger does. HELLO carries ``C`` so the bridge stops answering commands
itself. Other accessory packets (black box, temperatures) are only counted.
"""

import base64

import os
import queue
import threading
import time
from typing import Callable, List, Optional, Tuple

from . import espnow, hardware
from .gps_source import open_pyserial, valid_nmea_line
from .nmea import checksum

PREFIX = "$PJP226,"

# The AtomS3 sends BRIDGE once a second; the link is "up" for this long after.
LINK_TIMEOUT_MS = 3_000
HELLO_INTERVAL_MS = 1_000
# Resend GPS,<0|1> at most this often while the bridge reports the wrong state.
GPS_SWITCH_RETRY_MS = 2_000


def frame(body: str) -> str:
    """'$body*HH' plus CRLF, the way both ends write lines."""
    return "${}*{:02X}\r\n".format(body, checksum(body))


def is_bridge_line(line: str) -> bool:
    return line.startswith(PREFIX)


def parse(line: str) -> Optional[List[str]]:
    """Fields after 'PJP226' for a checksum-valid bridge line, else None."""
    line = line.strip()
    if not is_bridge_line(line) or not valid_nmea_line(line):
        return None
    body = line[1:line.rfind("*")]
    fields = body.split(",")
    return fields[1:] if len(fields) > 1 else None


def hello_body(version: str, caps: str = "C") -> str:
    return "PJP226,HELLO,{},{}".format(version, caps)


def time_body(utc_s: int, offset_min: int, flags: int,
              battery_percent: int = -1, battery_mv: int = 0) -> str:
    return "PJP226,TIME,{},{},{},{},{}".format(
        int(utc_s), int(offset_min), int(flags), int(battery_percent),
        int(battery_mv))


def gps_body(forward: bool) -> str:
    return "PJP226,GPS,{}".format(1 if forward else 0)


# TimeFlag bits from shared/TelemetryProtocol.h.
TIME_UTC_VALID = 1
TIME_OFFSET_VALID = 2


def _int(text: str, default: int = 0) -> int:
    try:
        return int(text)
    except (TypeError, ValueError):
        return default


class BridgeState:
    """What the Zero knows about the AtomS3, from its PJP226 lines."""

    def __init__(self):
        self.version = ""
        self.git = ""
        self.channel = 0
        self.devices = 0
        self.gps_baud = 0
        self.gps_live = False
        self.forwarding = False
        self.last_status_ms: Optional[int] = None
        self.rx_packets = 0
        self.bad_lines = 0

    def handle(self, line: str, now_ms: int) -> Optional[List[str]]:
        """Take one $PJP226 line. Its fields, or None if it was invalid or
        unknown."""
        fields = parse(line)
        if fields is None:
            self.bad_lines += 1
            return None
        kind = fields[0]
        if kind == "BRIDGE" and len(fields) >= 8:
            self.version = fields[1]
            self.git = fields[2]
            self.channel = _int(fields[3])
            self.devices = _int(fields[4])
            self.gps_baud = _int(fields[5])
            self.gps_live = fields[6] == "1"
            self.forwarding = fields[7] == "1"
            self.last_status_ms = now_ms
            return fields
        if kind == "RX" and len(fields) >= 4:
            self.rx_packets += 1
            return fields
        self.bad_lines += 1
        return None

    def connected(self, now_ms: int) -> bool:
        return (self.last_status_ms is not None and
                0 <= now_ms - self.last_status_ms <= LINK_TIMEOUT_MS)

    def status_text(self, now_ms: int) -> str:
        if not self.connected(now_ms):
            return "NO ATOMS3"
        return "{} CH{} {} DEV".format(self.version or "?", self.channel,
                                       self.devices)

    def gps_text(self, now_ms: int) -> str:
        if not self.connected(now_ms):
            return "NO ATOMS3"
        if not self.forwarding:
            return "ATOMS3 GPS OFF"
        if not self.gps_live:
            return "ATOMS3 NO GPS DATA"
        return "OK ATOMS3 GPS@{}".format(self.gps_baud)


def tx_body(mac: str, packet: bytes) -> str:
    return "PJP226,TX,{},{}".format(
        mac, base64.b64encode(packet).decode("ascii"))


# The ESP32 logger's controller rules (RemoteTelemetryReceiver): a controller
# is paired while it sent a DialCommand (heartbeats included) in the last 5 s;
# it gets a DialStatus every 500 ms and a PositionReport every second.
CONTROLLER_FRESH_MS = 5_000
# A controller's commands closer together than this are dropped unanswered
# (the sender retries), so nothing nearby can flood the card with waypoint
# photos. The M5Dial sends at most one command per user action.
COMMAND_MIN_INTERVAL_MS = 500
STATUS_INTERVAL_MS = 500
POSITION_INTERVAL_MS = 1_000
MAX_CONTROLLERS = 4


class Controllers:
    """The M5Dial and other controllers heard through the bridge."""

    def __init__(self):
        # MAC hex -> [last_ms, last command sequence or None, last ack bytes]
        self._seen = {}
        self._last_status_ms: Optional[int] = None
        self._last_position_ms: Optional[int] = None
        self.commands = 0

    def fresh(self, now_ms: int) -> List[str]:
        return [mac for mac, entry in self._seen.items()
                if 0 <= now_ms - entry[0] <= CONTROLLER_FRESH_MS]

    def handle_rx(self, fields: List[str], now_ms: int,
                  execute: Callable[[int, float, str], Tuple[bool, str]]
                  ) -> List[str]:
        """An RX line's fields (RX, mac, rssi, base64). A DialCommand runs
        `execute(action, value, text) -> (accepted, waypoint_id)` once per
        sequence number; a repeat gets the same ack again. Returns TX bodies."""
        try:
            data = base64.b64decode(fields[3], validate=True)
        except (ValueError, IndexError):
            return []
        command = espnow.decode_command(data)
        if command is None:
            return []
        mac = fields[1].upper()
        entry = self._seen.get(mac)
        if entry is None:
            if len(self.fresh(now_ms)) >= MAX_CONTROLLERS:
                return []
            for stale in [m for m in self._seen if m not in self.fresh(now_ms)]:
                del self._seen[stale]
            entry = self._seen[mac] = [now_ms, None, b"", None]
        entry[0] = now_ms
        sequence, action, value, text = command
        if action == espnow.HEARTBEAT:
            return []
        if entry[1] == sequence and entry[2]:
            return [tx_body(mac, entry[2])]
        if entry[3] is not None and now_ms - entry[3] < COMMAND_MIN_INTERVAL_MS:
            return []
        entry[3] = now_ms
        self.commands += 1
        accepted, waypoint_id = execute(action, value, text)
        entry[1] = sequence
        entry[2] = espnow.encode_ack(sequence, accepted, waypoint_id)
        return [tx_body(mac, entry[2])]

    def due(self, now_ms: int, status_fn: Callable[[], bytes],
            position_fn: Callable[[], bytes]) -> List[str]:
        """DialStatus and PositionReport TX bodies for every fresh controller."""
        macs = self.fresh(now_ms)
        if not macs:
            return []
        bodies: List[str] = []
        if (self._last_status_ms is None or
                now_ms - self._last_status_ms >= STATUS_INTERVAL_MS):
            self._last_status_ms = now_ms
            packet = status_fn()
            bodies.extend(tx_body(mac, packet) for mac in macs)
        if (self._last_position_ms is None or
                now_ms - self._last_position_ms >= POSITION_INTERVAL_MS):
            self._last_position_ms = now_ms
            packet = position_fn()
            bodies.extend(tx_body(mac, packet) for mac in macs)
        return bodies


# ESP-NOW page: green while heard in the last 5 s, orange until 30 s, then
# grey; forgotten after 2 minutes (the same as the AtomS3's DEVICES page).
DEVICE_FRESH_MS = 5_000
DEVICE_AWAY_MS = 30_000
DEVICE_FORGET_MS = 120_000

_BOARD_NAMES = {1: "CARDPUTER", 2: "CP ADV", 3: "CORE2", 4: "CP ZERO"}
_SPECIFIC = ("DIAL", "ECHO", "BLACKBOX", "CYD")
MAX_DEVICES = 32


class DeviceTable:
    """Every JP226 device the AtomS3 hears, worked out from what it sends."""

    def __init__(self):
        # MAC hex -> {"kind", "rssi", "last_ms", "packets"}
        self._devices = {}

    def note(self, fields: List[str], now_ms: int) -> None:
        """An RX line's fields: RX, mac, rssi, base64 packet."""
        try:
            data = base64.b64decode(fields[3], validate=True)
            rssi = int(fields[2])
        except (ValueError, IndexError):
            return
        kind = espnow.packet_type(data)
        if kind is None:
            return
        mac = fields[1].upper()
        if mac not in self._devices and len(self._devices) >= MAX_DEVICES:
            oldest = min(self._devices,
                         key=lambda m: self._devices[m]["last_ms"])
            del self._devices[oldest]
        entry = self._devices.setdefault(
            mac, {"kind": "DEVICE", "rssi": rssi, "last_ms": now_ms,
                  "packets": 0})
        entry["rssi"], entry["last_ms"] = rssi, now_ms
        entry["packets"] += 1
        name = entry["kind"]
        if kind == espnow.DISCOVERY and len(data) > 22:
            name = _BOARD_NAMES.get(data[22] >> 4, "LOGGER")
        elif kind in (1, 3):                      # Telemetry, RawImuBatch
            name = "ATOM GPS"
        elif kind == 8:                           # BlackBoxStatus
            name = "BLACKBOX"
        elif kind == espnow.TEMPERATURE_REPORT:
            report = espnow.decode_temperature(data)
            if report is not None:
                name = {1: "DIAL", 2: "ECHO", 3: "BLACKBOX"}.get(
                    report[0], name)
        elif kind == espnow.DIAL_COMMAND:
            sender = espnow.CONTROLLER_NAMES.get(espnow.command_sender(data))
            if sender:
                name = sender
            elif name not in _SPECIFIC:
                name = "CONTROLLER"
        entry["kind"] = name

    def rows(self, now_ms: int, paired=()) -> List[Tuple[str, str, int, int, bool]]:
        """(type, MAC, rssi, age ms, paired), sorted by type then MAC so the
        rows stay put; old ones dropped."""
        for mac in [m for m, e in self._devices.items()
                    if now_ms - e["last_ms"] > DEVICE_FORGET_MS]:
            del self._devices[mac]
        ordered = sorted(self._devices.items(),
                         key=lambda item: (item[1]["kind"], item[0]))
        return [(e["kind"], mac, e["rssi"], max(0, now_ms - e["last_ms"]),
                 mac in paired) for mac, e in ordered]


class BridgeOutbox:
    """Decides which lines the Zero sends to the bridge, and when."""

    def __init__(self, version: str):
        self.version = version
        self._last_hello_ms: Optional[int] = None
        self._last_gps_ms: Optional[int] = None

    def due(self, now_ms: int, state: BridgeState, want_gps: bool,
            utc_s: Optional[int], offset_s: Optional[int],
            battery_percent: int = -1, battery_mv: int = 0) -> List[str]:
        bodies: List[str] = []
        if (self._last_hello_ms is None or
                now_ms - self._last_hello_ms >= HELLO_INTERVAL_MS):
            self._last_hello_ms = now_ms
            bodies.append(hello_body(self.version))
            flags = 0
            if utc_s is not None:
                flags |= TIME_UTC_VALID
            if offset_s is not None:
                flags |= TIME_OFFSET_VALID
            bodies.append(time_body(utc_s or 0, (offset_s or 0) // 60, flags,
                                    battery_percent, battery_mv))
        # The bridge saves the GPS setting, so only send it when it is wrong.
        if (state.connected(now_ms) and state.forwarding != want_gps and
                (self._last_gps_ms is None or
                 now_ms - self._last_gps_ms >= GPS_SWITCH_RETRY_MS)):
            self._last_gps_ms = now_ms
            bodies.append(gps_body(want_gps))
        return bodies


def find_device(candidates=None,
                exists: Callable[[str], bool] = os.path.exists,
                glob_fn=None) -> str:
    """The first bridge serial port that exists, or ''."""
    if candidates is None:
        candidates = hardware.BRIDGE_SERIAL_CANDIDATES
    if glob_fn is None:
        import glob
        glob_fn = glob.glob
    for pattern in candidates:
        if any(ch in pattern for ch in "*?["):
            matches = sorted(glob_fn(pattern))
            if matches:
                return matches[0]
        elif exists(pattern):
            return pattern
    return ""


class BridgeLink(threading.Thread):
    """Background thread: find the AtomS3 port, queue every line it sends as
    (now_ms, line), and write the lines given to send(). Re-finds the port
    after an unplug, so the AtomS3 can be plugged in at any time."""

    def __init__(self, now_ms_fn: Callable[[], int], configured_device: str = "",
                 open_fn: Callable[[str, int], object] = open_pyserial,
                 find_fn: Callable[[], str] = find_device):
        super().__init__(name="bridge-link", daemon=True)
        self.lines: "queue.Queue[Tuple[int, str]]" = queue.Queue(maxsize=2000)
        self.now_ms_fn = now_ms_fn
        self.configured_device = configured_device
        self.open_fn = open_fn
        self.find_fn = find_fn
        self.device = ""
        self.status = "STARTING"
        self._outgoing: "queue.Queue[str]" = queue.Queue(maxsize=100)
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def send(self, body: str) -> None:
        """Queue one line; dropped while no port is open or the queue is full."""
        if not self.device:
            return
        try:
            self._outgoing.put_nowait(frame(body))
        except queue.Full:
            pass

    def run(self) -> None:
        try:
            import serial  # noqa: F401
        except ImportError:
            if self.open_fn is open_pyserial:
                self.status = "NO PYSERIAL"
                return
        while not self._stop_event.is_set():
            device = self.configured_device or self.find_fn()
            if not device or (self.configured_device and
                              not os.path.exists(device)):
                self.status = "NOT PLUGGED IN"
                self._stop_event.wait(2.0)
                continue
            self._read(device)
            self.device = ""
            self._stop_event.wait(1.0)

    def _read(self, device: str) -> None:
        try:
            port = self.open_fn(device, hardware.BRIDGE_BAUD)
        except Exception as exc:
            self.status = "OPEN FAIL {}".format(exc)
            self._stop_event.wait(2.0)
            return
        self.device = device
        self.status = "OPEN " + os.path.basename(device)
        buffer = b""
        try:
            while not self._stop_event.is_set():
                while True:
                    try:
                        line = self._outgoing.get_nowait()
                    except queue.Empty:
                        break
                    port.write(line.encode("ascii"))
                chunk = port.read(512)
                if chunk:
                    buffer += chunk
                    while b"\n" in buffer:
                        raw, buffer = buffer.split(b"\n", 1)
                        text = raw.decode("ascii", errors="ignore").strip()
                        if text:
                            try:
                                self.lines.put_nowait((self.now_ms_fn(), text))
                            except queue.Full:
                                pass
                    buffer = buffer[-1024:]
                else:
                    time.sleep(0.01)
        except Exception as exc:  # unplugged: SerialException / OSError
            self.status = "LOST {}".format(exc)
        finally:
            try:
                port.close()
            except Exception:
                pass
