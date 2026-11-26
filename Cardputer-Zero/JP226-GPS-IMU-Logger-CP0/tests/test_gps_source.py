"""Serial auto-detection with a fake serial port (no hardware, no pyserial)."""

import queue
import threading
import unittest

from jp226cp0 import hardware
from jp226cp0.gps_source import (SerialGpsReader, candidate_bauds,
                                 candidate_devices, count_valid_sentences,
                                 detect_gps, probe, valid_nmea_line)
from jp226cp0.nmea import make_sentence

from .helpers import nmea_burst

STREAM = ("\r\n".join(nmea_burst(0)) + "\r\n").encode("ascii")


class FakePort:
    """Repeats a real NMEA burst, or garbage / silence."""

    def __init__(self, kind):
        self.kind = kind
        self.closed = False
        self._pos = 0

    def read(self, n):
        if self.kind == "nmea":
            chunk = (STREAM * 3)[self._pos:self._pos + n]
            self._pos = (self._pos + n) % len(STREAM)
            return chunk
        if self.kind == "garbage":
            return b"\xff\x00\x13garbage$GPGGA,1*00\r\n"
        return b""

    def close(self):
        self.closed = True


def opener(good):
    """good = {(device, baud)}; anything else is garbage; 'missing' fails."""
    opened = []

    def open_fn(device, baud):
        opened.append((device, baud))
        if device.endswith("missing"):
            raise OSError("no such device")
        return FakePort("nmea" if (device, baud) in good else "garbage")

    open_fn.opened = opened
    return open_fn


class HelperTests(unittest.TestCase):
    def test_valid_line(self):
        self.assertTrue(valid_nmea_line(make_sentence("PCAS03,1,1")))
        self.assertFalse(valid_nmea_line("$GPGGA,1*00"))
        self.assertEqual(count_valid_sentences(STREAM), 4)

    def test_candidate_devices_order_and_filtering(self):
        present = {"/dev/ttyS0", "/dev/ttyUSB0", "/dev/serial0"}
        # realpath is faked: on a CardputerZero /dev/serial0 really is ttyS0.
        devices = candidate_devices("", exists=lambda p: p in present,
                                    realpath=lambda p: p)
        self.assertEqual(devices, ["/dev/serial0", "/dev/ttyS0", "/dev/ttyUSB0"])
        devices = candidate_devices("/dev/ttyACM9", exists=lambda p: p in present)
        self.assertEqual(devices[0], "/dev/ttyACM9")            # configured first
        self.assertEqual(candidate_devices("/dev/ttyS0", exists=lambda p: True)
                         .count("/dev/ttyS0"), 1)

    def test_candidate_list_matches_the_documented_assumption(self):
        self.assertEqual(hardware.GPS_SERIAL_CANDIDATES,
                         ("/dev/serial0", "/dev/ttyAMA1", "/dev/ttyAMA0",
                          "/dev/ttyS0", "/dev/ttyS1", "/dev/ttyUSB0"))

    def test_candidate_devices_deduplicate_serial_aliases(self):
        aliases = {"/dev/serial0": "/dev/ttyS0"}
        paths = ("/dev/serial0", "/dev/ttyAMA1", "/dev/ttyS0")
        devices = candidate_devices(
            candidates=paths,
            exists=lambda path: True,
            realpath=lambda path: aliases.get(path, path))
        self.assertEqual(devices, ["/dev/serial0", "/dev/ttyAMA1"])

    def test_baud_list_starts_with_cap_baud_and_covers_config_h(self):
        bauds = candidate_bauds()
        self.assertEqual(bauds[0], 115200)
        self.assertEqual(sorted(bauds), sorted({9600, 115200, 38400, 4800, 19200, 57600}))
        self.assertEqual(len(bauds), len(set(bauds)))
        self.assertEqual(candidate_bauds(4800), [4800])


class DetectTests(unittest.TestCase):
    def detect(self, good, present, **kw):
        return detect_gps(opener(good), listen_s=0.05, exists=lambda p: p in present, **kw)

    def test_finds_device_and_baud(self):
        found = self.detect({("/dev/ttyS0", 38400)},
                            {"/dev/ttyAMA0", "/dev/ttyS0"})
        self.assertEqual(found, ("/dev/ttyS0", 38400))

    def test_prefers_earlier_device_and_cap_baud(self):
        found = self.detect({("/dev/serial0", 115200), ("/dev/ttyS0", 115200)},
                            {"/dev/serial0", "/dev/ttyS0"})
        self.assertEqual(found, ("/dev/serial0", 115200))

    def test_no_gps_returns_none(self):
        self.assertIsNone(self.detect(set(), {"/dev/ttyS0"}))

    def test_configured_device_and_baud_limit_the_search(self):
        open_fn = opener({("/dev/ttyAMA0", 9600)})
        found = detect_gps(open_fn, "/dev/ttyAMA0", 9600, listen_s=0.05,
                           exists=lambda p: True)
        self.assertEqual(found, ("/dev/ttyAMA0", 9600))
        self.assertEqual(open_fn.opened[0], ("/dev/ttyAMA0", 9600))

    def test_unopenable_device_is_skipped(self):
        found = detect_gps(opener({("/dev/ttyUSB0", 115200)}), "/dev/ttymissing",
                           0, listen_s=0.02,
                           exists=lambda p: p == "/dev/ttyUSB0")
        self.assertEqual(found, ("/dev/ttyUSB0", 115200))

    def test_probe_closes_the_port(self):
        ports = []

        def open_fn(device, baud):
            ports.append(FakePort("silent"))
            return ports[-1]

        self.assertFalse(probe(open_fn, "/dev/x", 9600, 0.03, 2))
        self.assertTrue(ports[0].closed)

    def test_stop_callback_aborts(self):
        self.assertIsNone(detect_gps(opener(set()), listen_s=0.05,
                                     exists=lambda p: True, stop=lambda: True))


class ReaderTests(unittest.TestCase):
    def test_reader_detects_and_queues_lines(self):
        reader = SerialGpsReader(lambda: 42, open_fn=opener({("/dev/ttyAMA0", 115200)}),
                                 exists=lambda p: p == "/dev/ttyAMA0")
        reader.start()
        try:
            stamp, line = reader.lines.get(timeout=5)
            self.assertEqual(stamp, 42)
            self.assertTrue(line.startswith("$"))
            self.assertEqual((reader.device, reader.baud), ("/dev/ttyAMA0", 115200))
            self.assertTrue(reader.status.startswith("OK"))
        finally:
            reader.stop()
            reader.join(timeout=5)
        self.assertFalse(reader.is_alive())

    def test_reader_with_nothing_attached_reports_it(self):
        reader = SerialGpsReader(lambda: 0, open_fn=opener(set()),
                                 exists=lambda p: False)
        reader.start()
        try:
            for _ in range(100):
                if reader.status == "NO GPS FOUND":
                    break
                threading.Event().wait(0.05)
            self.assertEqual(reader.status, "NO GPS FOUND")
        finally:
            reader.stop()
            reader.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
