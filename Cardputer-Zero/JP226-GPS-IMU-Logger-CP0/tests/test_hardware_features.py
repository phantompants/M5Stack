import os
import subprocess
import tempfile
import unittest
from unittest import mock

from jp226cp0.camera import CameraCapture
from jp226cp0.dino import DinoGame
from jp226cp0.app import LoggerApp
from jp226cp0.clock import Clock
from jp226cp0.imu import Bmi270IioReader, NullImu, create_default_imu
from jp226cp0.settings import Settings
from jp226cp0.vehicle import (adjust, adjust_profile, defaults, load,
                              load_profile, profile_year_choices)
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
        self.assertEqual(imu.type_name, "BMI270")   # DIAG and CSV imu_type
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


class VehicleProfileTests(unittest.TestCase):
    def test_years_run_from_current_year_back_to_1980(self):
        years = profile_year_choices(2026)
        self.assertEqual(years[:2], ("SELECT YEAR", "2026"))
        self.assertEqual(years[-1], "1980")

    def test_make_change_resets_model_and_models_follow_make(self):
        profile = load_profile(None)
        self.assertEqual(profile, {"make": "SELECT MAKE",
                                   "model": "SELECT MODEL",
                                   "year": "SELECT YEAR"})
        self.assertTrue(adjust_profile(profile, "make", 1))
        self.assertEqual(profile["make"], "AUDI")
        self.assertTrue(adjust_profile(profile, "model", 1))
        self.assertEqual(profile["model"], "A3")
        self.assertTrue(adjust_profile(profile, "make", 1))
        self.assertEqual((profile["make"], profile["model"]),
                         ("BMW", "SELECT MODEL"))
        self.assertTrue(adjust_profile(profile, "year", -1, current_year=2026))
        self.assertEqual(profile["year"], "1980")
        self.assertFalse(adjust_profile(profile, "colour", 1))

    def test_load_discards_unknown_or_mismatched_values(self):
        profile = load_profile({"make": "TOYOTA", "model": "CIVIC",
                                "year": "1975"}, current_year=2026)
        self.assertEqual(profile, {"make": "TOYOTA", "model": "SELECT MODEL",
                                   "year": "SELECT YEAR"})
        self.assertEqual(load_profile({"make": "BOGUS"})["make"], "SELECT MAKE")


class ObdPlaceholderTests(unittest.TestCase):
    def test_number_key_actions_edit_and_save_profile_across_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = LoggerApp(Settings(), tmp, clock=Clock("gps"))
            self.assertEqual(app.obd_status, "NOT CONNECTED")
            app.change_obd_profile_field("make")
            app.change_obd_profile_field("model")
            app.change_obd_profile_field("year")
            self.assertEqual(app.obd_selection, 2)
            self.assertEqual(app.obd_message, "UNSAVED CHANGES")
            saved = dict(app.obd_profile)
            app.save_obd_profile()
            self.assertEqual(app.obd_message, "PROFILE SAVED")
            app.close()

            again = LoggerApp(Settings(), tmp, clock=Clock("gps"))
            self.assertEqual(again.obd_profile, saved)
            self.assertEqual(again.obd_message, "SAVED PROFILE LOADED")
            again.close()

    def test_connect_reports_not_implemented_and_never_scans(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = LoggerApp(Settings(), tmp, clock=Clock("gps"))
            with mock.patch("threading.Thread") as thread:
                app.connect_obd()
            thread.assert_not_called()
            self.assertEqual(app.obd_status, "NOT IMPLEMENTED")
            self.assertEqual(app.obd_message, "NO OBD LINK YET")
            self.assertFalse(os.path.exists(os.path.join(tmp, "obd_scans")))
            model = app.ui_model(0)
            app.close()
        self.assertNotIn("obd_scan_status", model)
        self.assertEqual([label for label, _ in model["obd_profile"]],
                         ["MAKE", "MODEL", "YEAR"])

    @unittest.skipUnless(ui.pillow_available(), "Pillow not installed")
    def test_obd_page_renders_profile_and_planned_readings(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = LoggerApp(Settings(), tmp, clock=Clock("gps"))
            app.change_obd_profile_field("make")
            model = app.ui_model(0)
            app.close()
        image = ui.render_page(ui.OBD_PAGE_INDEX, model)
        self.assertEqual(image.size, (320, 170))
        self.assertEqual(ui.PAGES[ui.OBD_PAGE_INDEX], "OBD")
        self.assertIn("RPM", ui.PLANNED_OBD_READINGS)
        self.assertIn("TPMS PSI/TEMP", ui.PLANNED_VEHICLE_READINGS)


if __name__ == "__main__":
    unittest.main()
