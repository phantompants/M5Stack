"""System monitor (FOSSIL RECORD page) and the SHARE page address list."""

import collections
import os
import unittest

from jp226cp0 import bridge, sysmon, webshare

Usage = collections.namedtuple("Usage", "total used free")


def fake_files(files):
    def reader(path):
        if path not in files:
            raise OSError(path)
        return files[path]
    return reader


BATTERY = "/sys/class/power_supply/bq27220-0"


class SystemMonitorTests(unittest.TestCase):
    def make(self, files, battery=True):
        files = dict(files)
        if battery:
            files.update({BATTERY + "/type": "Battery\n",
                          BATTERY + "/capacity": "55\n",
                          BATTERY + "/voltage_now": "3881000\n",
                          BATTERY + "/status": "Charging\n"})
        return sysmon.SystemMonitor(
            "/data", reader=fake_files(files),
            listdir=lambda p: ["bq27220-0"] if battery else [],
            disk_usage=lambda p: Usage(32_000_000_000, 8_000_000_000,
                                       24_000_000_000))

    def test_readings_from_proc_and_sys(self):
        files = {
            "/proc/stat": "cpu  100 0 100 800 0 0 0 0 0 0\n",
            sysmon.THERMAL_FILE: "57458\n",
            "/proc/meminfo": "MemTotal: 400000 kB\nMemAvailable: 100000 kB\n",
            "/proc/uptime": "3725.12 1000.0\n",
            "/proc/loadavg": "0.80 0.90 0.69 1/200 1234\n",
        }
        monitor = self.make(files)
        first = monitor.sample()
        self.assertIsNone(first["cpu_pct"])          # needs two samples
        files["/proc/stat"] = "cpu  150 0 150 900 0 0 0 0 0 0\n"
        monitor._reader = fake_files(dict(files, **{
            BATTERY + "/type": "Battery", BATTERY + "/capacity": "55",
            BATTERY + "/voltage_now": "3881000",
            BATTERY + "/status": "Charging"}))
        v = monitor.sample()
        self.assertAlmostEqual(v["cpu_pct"], 50.0)   # 100 busy of 200
        self.assertAlmostEqual(v["temp_c"], 57.458)
        self.assertAlmostEqual(v["mem_pct"], 75.0)
        self.assertEqual(v["mem_free_mb"], 97)
        self.assertAlmostEqual(v["disk_free_gb"], 24.0)
        self.assertEqual((v["battery_pct"], v["battery_mv"], v["battery_status"]),
                         (55, 3881, "CHARGING"))
        self.assertEqual(v["uptime_s"], 3725)
        self.assertEqual(sysmon.uptime_text(v["uptime_s"]), "1h 02m")
        self.assertEqual(sysmon.uptime_text(200_000), "2d 7h")

    def test_desktop_without_proc(self):
        v = self.make({}, battery=False).sample()
        self.assertIsNone(v["cpu_pct"])
        self.assertIsNone(v["battery_pct"])
        self.assertEqual(sysmon.uptime_text(None), "--")

    def test_battery_goes_to_the_bridge(self):
        box = bridge.BridgeOutbox("v")
        bodies = box.due(0, bridge.BridgeState(), False, 1764100000, 36000,
                         battery_percent=55, battery_mv=3881)
        self.assertIn("PJP226,TIME,1764100000,600,3,55,3881", bodies)


# `ip -4 -o addr show` on the CardputerZero, 2026-11-26.
IP_ADDR = """1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever preferred_lft forever
2: wlan0    inet 192.168.100.65/24 brd 192.168.100.255 scope global dynamic noprefixroute wlan0\\       valid_lft 80000sec preferred_lft 80000sec
4: eth0    inet 192.168.1.220/24 brd 192.168.1.255 scope global dynamic noprefixroute eth0\\       valid_lft 80000sec preferred_lft 80000sec
"""


class AddressTests(unittest.TestCase):
    def test_parse_and_order_wired_first(self):
        self.assertEqual(webshare.parse_ip_addr(IP_ADDR),
                         [("wlan0", "192.168.100.65"), ("eth0", "192.168.1.220")])

        class Result:
            stdout = IP_ADDR

        found = webshare.interface_addresses(run=lambda *a, **k: Result())
        self.assertEqual(found, [("WIRED", "192.168.1.220"),
                                 ("WI-FI", "192.168.100.65")])

    def test_labels(self):
        self.assertEqual(webshare.interface_label("eth0"), "WIRED")
        self.assertEqual(webshare.interface_label("wlan0"), "WI-FI")
        self.assertEqual(webshare.interface_label("tailscale0"), "TAILSCAL")

    def test_default_port_is_not_raspyjacks(self):
        self.assertEqual(webshare.DEFAULT_PORT, 8226)


if __name__ == "__main__":
    unittest.main()
