"""GPS receiver choice (LoRa Cap vs Grove) and its power rails."""

import os
import tempfile
import unittest

from jp226cp0 import gps_power, hardware, keys, ui
from jp226cp0.app import LoggerApp
from jp226cp0.clock import Clock
from jp226cp0.settings import Settings, load_settings


def make_leds(root, names=("ext_5v_out", "grove_5v_out", "grove_fun")):
    for name in names:
        os.makedirs(os.path.join(root, name))
        with open(os.path.join(root, name, "brightness"), "w") as handle:
            handle.write("0")


def read_led(root, name):
    with open(os.path.join(root, name, "brightness")) as handle:
        return handle.read()


class GpsPowerTests(unittest.TestCase):
    def test_cap_powers_ext_5v_and_switches_grove_power_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_leds(tmp)
            with open(os.path.join(tmp, "grove_5v_out", "brightness"), "w") as h:
                h.write("1")
            self.assertEqual(gps_power.apply("cap", tmp), "POWER ON")
            self.assertEqual(read_led(tmp, "ext_5v_out"), "1")
            self.assertEqual(read_led(tmp, "grove_5v_out"), "0")

    def test_grove_routes_uart_powers_grove_and_switches_cap_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_leds(tmp)
            with open(os.path.join(tmp, "grove_fun", "brightness"), "w") as h:
                h.write("1")
            self.assertEqual(gps_power.apply("grove", tmp), "POWER ON")
            self.assertEqual(read_led(tmp, "grove_fun"), "0")       # LOW = UART
            self.assertEqual(read_led(tmp, "grove_5v_out"), "1")
            self.assertEqual(read_led(tmp, "ext_5v_out"), "0")

    def test_atom_switches_grove_power_off_and_leaves_the_cap_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_leds(tmp)
            for name in ("grove_5v_out", "ext_5v_out"):
                with open(os.path.join(tmp, name, "brightness"), "w") as h:
                    h.write("1")
            self.assertEqual(gps_power.apply("atom", tmp), "USB POWER")
            self.assertEqual(read_led(tmp, "grove_5v_out"), "0")
            self.assertEqual(read_led(tmp, "ext_5v_out"), "1")

    def test_missing_control_and_permission_errors_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(gps_power.apply("cap", os.path.join(tmp, "x")),
                             "NO POWER CONTROL")
            make_leds(tmp, ("grove_5v_out",))
            self.assertEqual(gps_power.apply("cap", tmp), "POWER: NO EXT_5V_OUT")

            def denied(path, value):
                raise PermissionError(13, "denied")
            self.assertEqual(gps_power.apply("cap", tmp, write=denied),
                             "POWER: NO PERMISSION")

    def test_names_toggle_and_baud_order(self):
        self.assertEqual(gps_power.normalise("NFC"), gps_power.ATOM)
        self.assertEqual(gps_power.normalise("AtomS3"), gps_power.ATOM)
        self.assertEqual(gps_power.normalise("LoRa"), gps_power.CAP)
        self.assertEqual(gps_power.normalise("bogus"), gps_power.CAP)
        self.assertEqual(gps_power.next_source("cap"), "grove")
        self.assertEqual(gps_power.next_source("grove"), "atom")
        self.assertEqual(gps_power.next_source("atom"), "cap")
        grove = gps_power.baud_order("grove", hardware.GPS_BAUD_CANDIDATES)
        self.assertEqual(grove[0], 9600)
        self.assertEqual(sorted(grove), sorted(hardware.GPS_BAUD_CANDIDATES))
        self.assertEqual(gps_power.baud_order(
            "cap", hardware.GPS_BAUD_CANDIDATES)[0], 115200)


class GpsSourceSettingTests(unittest.TestCase):
    def test_config_and_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "logger.cfg")
            with open(path, "w") as handle:
                handle.write("gps_source=grove\n")
            self.assertEqual(load_settings(path, env={}).gps_source, "grove")
            self.assertEqual(load_settings(
                path, env={"JP226_CP0_GPS_SOURCE": "cap"}).gps_source, "cap")
            with open(path, "w") as handle:
                handle.write("gps_source=moon\n")
            s = load_settings(path, env={})
        self.assertEqual(s.gps_source, "cap")
        self.assertTrue(any("gps_source" in w for w in s.warnings))

    def test_gps_page_key_4_switches_source_and_choice_is_saved(self):
        self.assertEqual(keys.page_action_for("GPS", "KEY_4"), keys.GPS_SOURCE)
        self.assertEqual(keys.page_action_for("GPS", "KEY_7"), keys.WAYPOINT)
        self.assertIn(("KEY_4", "SOURCE", keys.KIND_EDIT),
                      ui.footer_items(ui.PAGES.index("GPS")))
        with tempfile.TemporaryDirectory() as tmp:
            app = LoggerApp(Settings(gps_source="grove"), tmp, clock=Clock("gps"))
            self.assertEqual(app.ui_model(0)["gps_source"], "GROVE")
            self.assertEqual(app.toggle_gps_source(), "atom")
            self.assertEqual(app.ui_model(0)["gps_source"], "ATOMS3 USB")
            app.close()
            again = LoggerApp(Settings(gps_source="grove"), tmp, clock=Clock("gps"))
            self.assertEqual(again.gps_source, "atom")   # saved choice wins
            again.close()


if __name__ == "__main__":
    unittest.main()
