import os
import subprocess
import tempfile
import unittest
from unittest import mock

from jp226cp0.camera import CameraCapture
from jp226cp0.dino import DinoGame
from jp226cp0.app import LoggerApp
from jp226cp0.clock import Clock
from jp226cp0.fake_elm import FakeElm
from jp226cp0.imu import Bmi270IioReader, NullImu, create_default_imu
from jp226cp0.obdscan import PROTOCOL_LABELS
from jp226cp0.settings import Settings
from jp226cp0.vehicle import adjust, defaults, load
from jp226cp0 import ui


class Bmi270Tests(unittest.TestCase):
    def make_device(self, root):
        device = os.path.join(root, "iio-device0")
        os.makedirs(device)
        values = {
            "name": "bmi270",
            "in_accel_scale": "0.002394",
            "in_anglvel_scale": "0.001065",
            "in_accel_x_raw": "1000",
            "in_accel_y_raw": "0",
            "in_accel_z_raw": "4096",
            "in_anglvel_x_raw": "1",
            "in_anglvel_y_raw": "2",
            "in_anglvel_z_raw": "3",
        }
        for name, value in values.items():
            with open(os.path.join(device, name), "w", encoding="ascii") as handle:
                handle.write(value)
        return device

    def test_reads_scaled_values_and_resets_window_statistics(self):
        with tempfile.TemporaryDirectory() as tmp:
            device = self.make_device(tmp)
            imu = Bmi270IioReader(device)
            imu.update(0)
            imu.update(5)
            imu.update(10)
            sample = imu.snapshot_and_reset()
            latest = imu.latest_sample()
        self.assertTrue(sample.valid)
        self.assertEqual(sample.sample_count, 2)
        self.assertAlmostEqual(sample.ax_mps2, 2.394, places=3)
        self.assertAlmostEqual(sample.az_mps2, 9.806, places=2)
        self.assertAlmostEqual(sample.gx_dps, 0.065, places=2)
        self.assertAlmostEqual(sample.vibration_rms_mps2,
                               sample.vertical_accel_rms_mps2)
        self.assertEqual(latest.sample_count, 2)
        self.assertIsNone(imu.snapshot_and_reset())

    def test_default_factory_uses_null_imu_when_no_bmi270(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsInstance(create_default_imu(tmp), NullImu)


class VehicleSetupTests(unittest.TestCase):
    def test_requested_vehicle_defaults(self):
        setup = defaults()
        self.assertEqual(
            (setup["road_type"], setup["road_surface"],
             setup["tyre_set_fl_psi"], setup["tyre_set_fr_psi"],
             setup["tyre_set_rl_psi"], setup["tyre_set_rr_psi"],
             setup["suspension_front"], setup["suspension_rear"],
             setup["vehicle_load"]),
            ("PAVED", "DRY", 36, 36, 45, 45, "NORMAL", "NORMAL", "LIGHT"))

    def test_load_discards_invalid_values_and_settings_cycle(self):
        setup = load({"road_type": "INVALID", "tyre_set_fl_psi": 31,
                      "tyre_set_fr_psi": True})
        self.assertEqual(setup["road_type"], "PAVED")
        self.assertEqual(setup["tyre_set_fl_psi"], 31)
        self.assertEqual(setup["tyre_set_fr_psi"], 36)
        self.assertTrue(adjust(setup, "road_type", 1))
        self.assertEqual(setup["road_type"], "GRAVEL")
        self.assertTrue(adjust(setup, "tyre_set_fr_psi", 1))
        self.assertEqual(setup["tyre_set_fr_psi"], 37)


class CameraTests(unittest.TestCase):
    def test_capture_returns_data_directory_relative_photo_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            camera = CameraCapture(tmp, command="rpicam-still")

            def capture(args, **kwargs):
                with open(args[-1], "wb") as handle:
                    handle.write(b"jpeg")
                return subprocess.CompletedProcess(
                    args, 0, stdout="", stderr="")

            with mock.patch("jp226cp0.camera.subprocess.run",
                            side_effect=capture):
                photo = camera.capture_waypoint("WP00001")
            self.assertEqual(photo, "photos/WP00001.jpg")
            self.assertGreater(os.path.getsize(os.path.join(tmp, photo)), 0)

    def test_capture_reports_missing_camera_command(self):
        with mock.patch("jp226cp0.camera.shutil.which", return_value=None):
            camera = CameraCapture("/tmp")
        with self.assertRaisesRegex(RuntimeError, "not installed"):
            camera.capture_waypoint("WP00001")


class DinoTests(unittest.TestCase):
    def test_parked_game_starts_and_driving_returns_to_auto(self):
        game = DinoGame()
        game.update(0, 0.0, 160)
        self.assertEqual(game.state, DinoGame.READY)
        game.press(0)
        self.assertEqual(game.state, DinoGame.PLAYING)
        game.update(1000, 0.0, 160)
        self.assertEqual(game.score, 1)
        game.update(1100, 10.0, 160)
        self.assertEqual(game.state, DinoGame.AUTO)
        self.assertTrue(game.moving)

    def test_flexi_rex_sprite_keeps_fixed_art_dimensions_and_joint_segments(self):
        self.assertEqual(len(ui._DINO_FRAMES), 2)
        for frame in ui._DINO_FRAMES:
            self.assertEqual(len(frame), 16)
            self.assertTrue(all(len(row) == 24 for row in frame))
        self.assertIn("J", "".join(ui._DINO_FRAMES[0]))
        self.assertTrue(all(
            len(frame) == 16 and all(len(row) == 24 for row in frame)
            for frame in ui._BRONTO_FRAMES))
        game = DinoGame()
        game.toggle_character()
        self.assertEqual(game.character, "BRONTO")
        game.toggle_character()
        self.assertEqual(game.character, "TREX")


class ObdUiTests(unittest.TestCase):
    def test_adapter_options_and_read_only_scan_run_in_background(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = LoggerApp(Settings(), tmp, clock=Clock("gps"))
            app.obd_selection = 0
            app.adjust_obd_option(1)
            self.assertEqual(app.obd_link, "wifi")
            app.obd_selection = 1
            app.adjust_obd_option(1)
            self.assertIn(app.obd_protocol, PROTOCOL_LABELS)

            app.obd_link = "usb"
            app.obd_protocol = "0"
            with mock.patch("jp226cp0.obdscan.open_link",
                            return_value=FakeElm()):
                app.start_obd_scan()
                app._obd_thread.join(timeout=10)
            self.assertEqual(app.obd_scan_status, "COMPLETE")
            self.assertIn("Protocol 6", app.obd_scan_summary)
            self.assertTrue(os.path.isdir(os.path.join(tmp, "obd_scans")))
            app.close()


if __name__ == "__main__":
    unittest.main()
