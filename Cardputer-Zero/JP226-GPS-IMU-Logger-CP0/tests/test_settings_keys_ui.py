import os
import tempfile
import unittest

from jp226cp0 import hardware, keys, ui
from jp226cp0.app import LoggerApp, replay_lines
from jp226cp0.clock import Clock
from jp226cp0.imu import ImuReader, ImuSample
from jp226cp0.csvlog import COLUMNS
from jp226cp0.settings import Settings, load_settings, parse_cfg

from .test_pipeline import DEMO, read_rows, drive_lines


class SettingsTests(unittest.TestCase):
    def write_cfg(self, tmp, text):
        path = os.path.join(tmp, "logger.cfg")
        with open(path, "w") as handle:
            handle.write(text)
        return path

    def test_parse_cfg(self):
        values = parse_cfg("# c\n; c\n\nTimezone = AEST-10\nbad line\n=x\nk=v=w\n")
        self.assertEqual(values, {"timezone": "AEST-10", "k": "v=w"})

    def test_defaults(self):
        s = load_settings(config_path=None, env={})
        self.assertEqual((s.timezone, s.timezone_auto, s.gps_device, s.gps_baud),
                         ("", True, "", 0))
        self.assertEqual(s.clock_source, "auto")

    def test_file_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_cfg(tmp, "timezone=ACST-9:30\ntimezone_auto=false\n"
                                       "gps_device=/dev/ttyS0\ngps_baud=38400\n"
                                       "data_dir=/x/y\nclock_source=gps\n")
            s = load_settings(path, env={})
        self.assertEqual((s.timezone, s.timezone_auto, s.gps_device, s.gps_baud,
                          s.data_dir, s.clock_source),
                         ("ACST-9:30", False, "/dev/ttyS0", 38400, "/x/y", "gps"))
        self.assertEqual(s.config_path, path)

    def test_env_overrides_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_cfg(tmp, "timezone_auto=true\ngps_baud=9600\n")
            env = {"JP226_CP0_TIMEZONE_AUTO": "off", "JP226_CP0_GPS_BAUD": "115200",
                   "JP226_CP0_GPS_DEVICE": "/dev/ttyAMA0", "JP226_CP0_DATA_DIR": "/d",
                   "JP226_CP0_TIMEZONE": "AWST-8", "JP226_CP0_CLOCK_SOURCE": "system"}
            s = load_settings(path, env=env)
        self.assertEqual((s.timezone_auto, s.gps_baud, s.gps_device, s.data_dir,
                          s.timezone, s.clock_source),
                         (False, 115200, "/dev/ttyAMA0", "/d", "AWST-8", "system"))

    def test_env_var_selects_config_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_cfg(tmp, "timezone=AWST-8\n")
            s = load_settings(env={"JP226_CP0_CONFIG": path})
        self.assertEqual(s.timezone, "AWST-8")

    def test_wifi_credentials_are_never_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_cfg(tmp, "wifi_ssid=Home\nwifi_password=hunter2\n")
            s = load_settings(path, env={})
        self.assertNotIn("hunter2", repr(s))
        self.assertNotIn("Home", repr(s))
        self.assertTrue(any("wifi_ssid" in w for w in s.warnings))
        self.assertFalse(hasattr(s, "wifi_password"))

    def test_bad_values_warn_and_fall_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_cfg(tmp, "gps_baud=fast\nclock_source=moon\n")
            s = load_settings(path, env={})
        self.assertEqual((s.gps_baud, s.clock_source), (0, "auto"))
        self.assertEqual(len(s.warnings), 2)

    def test_missing_explicit_file_warns(self):
        s = load_settings("/nonexistent/logger.cfg", env={})
        self.assertTrue(s.warnings)

    def test_repo_contains_no_wifi_password_in_example_config(self):
        example = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "logger.cfg.example")
        with open(example) as handle:
            text = handle.read()
        self.assertNotRegex(text, r"(?m)^\s*wifi_password\s*=\s*\S")


class KeyTests(unittest.TestCase):
    def test_actions(self):
        self.assertEqual(keys.action_for("KEY_TAB"), keys.NEXT_PAGE)
        self.assertEqual(keys.action_for("KEY_RIGHTBRACE"), keys.NEXT_PAGE)
        self.assertEqual(keys.action_for("KEY_RIGHT"), keys.NEXT_PAGE)
        self.assertEqual(keys.action_for("KEY_LEFTBRACE"), keys.PREV_PAGE)
        self.assertEqual(keys.action_for("KEY_LEFT"), keys.PREV_PAGE)
        self.assertEqual(keys.action_for("KEY_C"), keys.NEXT_PAGE)
        self.assertEqual(keys.action_for("KEY_Z"), keys.PREV_PAGE)
        self.assertEqual(keys.action_for("KEY_W"), keys.WAYPOINT)
        self.assertEqual(keys.action_for("KEY_ENTER"), keys.WAYPOINT)
        self.assertEqual(keys.action_for("KEY_ESC"), keys.QUIT)
        self.assertEqual(keys.action_for("KEY_Q"), keys.QUIT)
        self.assertIsNone(keys.action_for("KEY_X"))

    def test_physical_shortcut_keys(self):
        self.assertEqual(keys.shortcut_page_for("KEY_4"), "SPEED")
        self.assertEqual(keys.shortcut_page_for("KEY_5"), "GPS")
        self.assertEqual(keys.shortcut_page_for("KEY_6"), "IMU")
        self.assertEqual(keys.shortcut_page_for("KEY_7"), "WAYPOINT")
        self.assertEqual(keys.shortcut_page_for("KEY_8"), "OBD SCAN")
        self.assertIsNone(keys.shortcut_page_for("KEY_3"))

    def test_all_mapped_names_are_valid_linux_key_names(self):
        for name in (hardware.KEYS_NEXT_PAGE + hardware.KEYS_PREV_PAGE +
                     hardware.KEYS_WAYPOINT + hardware.KEYS_QUIT +
                     hardware.KEYS_SETTING_PREV + hardware.KEYS_SETTING_NEXT +
                     hardware.KEYS_SETTING_DEC + hardware.KEYS_SETTING_INC +
                     hardware.KEYS_DINO_JUMP + hardware.KEYS_DINO_TOGGLE +
                     hardware.KEYS_SHORTCUTS):
            self.assertRegex(name, r"^KEY_[A-Z0-9]+$")


class FakeImu(ImuReader):
    available = True
    type_name = "FAKE"

    def update(self, now_ms):
        pass

    def snapshot_and_reset(self):
        return ImuSample(ax_mps2=9.80665, ay_mps2=0.0, az_mps2=-9.80665,
                         g_total_mps2=9.80665, sample_count=100,
                         legacy_roughness_mps2=0.098)


class ImuHookTests(unittest.TestCase):
    def test_imu_hook_fills_the_imu_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = LoggerApp(Settings(), tmp, clock=Clock("gps"), imu=FakeImu())
            replay_lines(app, drive_lines(parked_before=3, moving=0, parked_after=0))
            app.close()
            rows = read_rows(os.path.join(tmp, "telemetry_2026-10-20.csv"))
        col = {name: i for i, name in enumerate(COLUMNS)}
        row = rows[1]
        self.assertEqual(row[col["imu_available"]], "1")
        self.assertEqual(row[col["imu_type"]], "FAKE")
        self.assertEqual(row[col["acc_x_g"]], "1.0000")
        self.assertAlmostEqual(float(row[col["accel_z_mps2"]]), -9.80665, 3)
        self.assertEqual(row[col["imu_samples"]], "100")
        self.assertAlmostEqual(float(row[col["roughness_index"]]), 0.01, 4)


@unittest.skipUnless(ui.pillow_available(), "Pillow not installed")
class UiTests(unittest.TestCase):
    def test_every_page_renders_320x170(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = LoggerApp(Settings(), tmp, clock=Clock("gps"))
            with open(DEMO) as handle:
                lines = handle.read().splitlines()
            final = replay_lines(app, lines, max_seconds=100)
            app.create_waypoint(final)
            model = app.ui_model(final)
            app.close()
        for i in range(len(ui.PAGES)):
            image = ui.render_page(i, model)
            self.assertEqual(image.size, (320, 170))

    def test_empty_model_renders(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = LoggerApp(Settings(), tmp, clock=Clock("gps"))
            model = app.ui_model(0)
            app.close()
        for i in range(len(ui.PAGES)):
            self.assertEqual(ui.render_page(i, model).size, (320, 170))


if __name__ == "__main__":
    unittest.main()
