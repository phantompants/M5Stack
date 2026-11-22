"""The telemetry CSV header must equal the ESP32 firmware's kCsvHeader so that
tools/merge_obd.py and the KML scripts keep working."""

import os
import re
import unittest

from jp226cp0.csvlog import COLUMNS, CSV_HEADER
from jp226cp0.waypoints import WAYPOINT_HEADER

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
# .../M5Stack/Cardputer-Zero/JP226-GPS-IMU-Logger-CP0 -> ../../../JP226-GPS-IMU-Logger
DEFAULT_ESP32_ROOT = os.path.normpath(
    os.path.join(PROJECT, "..", "..", "..", "JP226-GPS-IMU-Logger"))


def esp32_root():
    return os.environ.get("JP226_ESP32_ROOT", DEFAULT_ESP32_ROOT)


def string_after(source: str, marker: str) -> str:
    """Concatenate the adjacent C string literals that follow `marker`."""
    start = source.index(marker) + len(marker)
    chunk = source[start:source.index(";", start)]
    return "".join(re.findall(r'"([^"]*)"', chunk))


class HeaderTests(unittest.TestCase):
    def test_header_has_63_columns_and_unique_names(self):
        self.assertEqual(len(COLUMNS), 63)
        self.assertEqual(len(set(COLUMNS)), 63)
        self.assertEqual(COLUMNS[0], "timestamp")
        self.assertEqual(COLUMNS[-1], "probe_labrax_c")

    def test_waypoint_header_has_28_columns(self):
        self.assertEqual(len(WAYPOINT_HEADER.split(",")), 28)

    def test_equals_esp32_firmware_header(self):
        path = os.path.join(esp32_root(), "src", "main.cpp")
        if not os.path.isfile(path):
            self.skipTest("ESP32 source not found at " + path)
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            source = handle.read()
        firmware = string_after(source, "constexpr char kCsvHeader[] =")
        self.assertEqual(CSV_HEADER, firmware)

    def test_waypoint_header_equals_esp32_firmware(self):
        path = os.path.join(esp32_root(), "src", "WaypointStore.cpp")
        if not os.path.isfile(path):
            self.skipTest("ESP32 source not found at " + path)
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            source = handle.read()
        firmware = string_after(source, "static constexpr char kHeader[] =")
        self.assertEqual(WAYPOINT_HEADER, firmware)


if __name__ == "__main__":
    unittest.main()
