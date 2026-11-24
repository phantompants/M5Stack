"""End to end: NMEA lines -> LoggerApp -> CSV files, on a virtual clock."""

import csv
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from jp226cp0 import ui
from jp226cp0.app import LoggerApp, replay_lines
from jp226cp0.camera import CameraCapture
from jp226cp0.clock import Clock
from jp226cp0.camera import CameraCapture
from jp226cp0.cli import main as cli_main
from jp226cp0.csvlog import COLUMNS, CSV_HEADER, DailyCsv
from jp226cp0.settings import Settings
from jp226cp0.waypoints import WAYPOINT_COLUMNS, WAYPOINT_HEADER

from .helpers import nmea_burst, utc_ms

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO = os.path.join(PROJECT, "sample_data", "demo_drive.nmea")

LAT0 = -32.4936


def drive_lines(start=(2026, 10, 20, 1, 30, 0), parked_before=10, moving=20,
                parked_after=15, kmh=60.0):
    """parked, then moving north at `kmh`, then parked again (1 Hz bursts)."""
    lines = []
    lat = LAT0
    t = 0
    for _ in range(parked_before):
        lines += nmea_burst(t, lat=lat, kmh=0.0, start=start)
        t += 1
    for _ in range(moving):
        lat += (kmh / 3.6) / 111195.0
        lines += nmea_burst(t, lat=lat, kmh=kmh, start=start)
        t += 1
    for _ in range(parked_after):
        lines += nmea_burst(t, lat=lat, kmh=0.0, start=start)
        t += 1
    return lines


def read_rows(path):
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.reader(handle))


def telemetry_files(directory):
    return sorted(f for f in os.listdir(directory)
                  if f.startswith("telemetry_") and f.endswith(".csv")
                  and "-old" not in f)


class ReplayTests(unittest.TestCase):
    def run_replay(self, lines, **kw):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        app = LoggerApp(Settings(**kw), tmp.name, clock=Clock("gps"))
        self.addCleanup(app.close)
        replay_lines(app, lines)
        app.close()
        return app, tmp.name

    def test_replay_writes_header_and_rows(self):
        app, out = self.run_replay(drive_lines())
        files = telemetry_files(out)
        self.assertEqual(files, ["telemetry_2026-10-20.csv"])   # Sydney: 12:30 local
        rows = read_rows(os.path.join(out, files[0]))
        self.assertEqual(",".join(rows[0]), CSV_HEADER)
        body = rows[1:]
        self.assertGreater(len(body), 20)
        for row in body:
            self.assertEqual(len(row), len(COLUMNS), row)
        col = {name: i for i, name in enumerate(COLUMNS)}
        states = [r[col["log_state"]] for r in body]
        self.assertEqual(states[0], "STOPPED_15M")        # parked at start
        self.assertIn("MOVING", states)
        self.assertEqual(states[-1], "STOPPED_15M")       # parked at the end
        self.assertEqual(states.count("STOPPED_15M") >= 2, True)
        for r in body:
            self.assertEqual(r[col["telemetry_source"]], "LOCAL_GPS")
            self.assertEqual(r[col["imu_available"]], "0")
            self.assertEqual(r[col["imu_type"]], "none")
            self.assertEqual(r[col["acc_x_g"]], "")
            self.assertEqual(r[col["fix_valid"]], "1")
            self.assertTrue(r[col["timestamp"]].endswith("Z"))
            self.assertTrue(r[col["local_timestamp"]].endswith("+11:00"))   # AEDT
            self.assertEqual(r[col["poi_source"]], "NONE")

    def test_moving_rows_are_1_hz_and_have_the_gps_values(self):
        _, out = self.run_replay(drive_lines())
        rows = read_rows(os.path.join(out, "telemetry_2026-10-20.csv"))[1:]
        col = {name: i for i, name in enumerate(COLUMNS)}
        moving = [r for r in rows if r[col["log_state"]] == "MOVING"]
        self.assertGreaterEqual(len(moving), 18)
        stamps = [r[col["timestamp"]] for r in moving]
        self.assertEqual(len(set(stamps)), len(stamps))        # one per second
        self.assertAlmostEqual(float(moving[5][col["speed_kmh"]]), 60.0, 1)
        self.assertEqual(moving[5][col["satellites"]], "9")
        self.assertEqual(moving[5][col["hdop"]], "0.90")
        self.assertEqual(moving[5][col["vdop"]], "1.20")
        self.assertAlmostEqual(float(moving[5][col["heading_deg"]]), 90.0, 1)

    def test_timestamps_never_go_backwards(self):
        _, out = self.run_replay(drive_lines())
        rows = read_rows(os.path.join(out, "telemetry_2026-10-20.csv"))[1:]
        stamps = [r[0] for r in rows]       # fixed-width ISO strings sort in time order
        self.assertEqual(stamps, sorted(stamps))

    def test_stopped_session_is_persisted(self):
        app, out = self.run_replay(drive_lines())
        with open(os.path.join(out, "logger_state.json")) as handle:
            saved = json.load(handle)
        self.assertEqual(saved["state"], 2)
        self.assertGreater(saved["next_utc"], saved["stop_utc"])

    def test_local_midnight_changes_file(self):
        # 12:59:50Z is 23:59:50 AEDT (Sydney default) on 2026-10-20.
        _, out = self.run_replay(drive_lines(start=(2026, 10, 20, 12, 59, 50),
                                             parked_before=2, moving=25,
                                             parked_after=2))
        self.assertEqual(telemetry_files(out),
                         ["telemetry_2026-10-20.csv", "telemetry_2026-10-21.csv"])
        first = read_rows(os.path.join(out, "telemetry_2026-10-20.csv"))[1:]
        second = read_rows(os.path.join(out, "telemetry_2026-10-21.csv"))[1:]
        self.assertTrue(first[-1][0] < "2026-10-20T13:00:00")
        self.assertTrue(second[0][0] >= "2026-10-20T13:00:00")
        self.assertTrue(second[0][19].startswith("2026-10-21T00:00"))

    def test_no_clock_no_rows(self):
        # No valid date: the GPS clock source never becomes ready.
        lines = [l for l in drive_lines() if "RMC" not in l]
        _, out = self.run_replay(lines)
        self.assertEqual(telemetry_files(out), [])

    def test_bad_checksum_lines_do_not_break_replay(self):
        lines = drive_lines()
        lines.insert(7, "$GNRMC,013007.00,A,3229.61600,S,13746.29600,E,99.0,0.0,201026,,,A*00")
        lines.insert(8, "garbage with no dollar")
        app, out = self.run_replay(lines)
        self.assertEqual(app.gps.sentences_bad, 1)
        rows = read_rows(os.path.join(out, "telemetry_2026-10-20.csv"))[1:]
        speeds = [float(r[COLUMNS.index("speed_kmh")]) for r in rows if r[COLUMNS.index("speed_kmh")]]
        self.assertLess(max(speeds), 61.0)

    def test_fix_lost_rows_blank_gps_fields(self):
        lines = drive_lines(parked_before=3, moving=10, parked_after=0)
        # GPS then disappears but the time still advances (receiver sends only RMC V).
        t = 13
        for _ in range(15):
            lines.append(nmea_burst(t, status="V", gga_quality=0, sats=0, hdop=99.0)[0])
            t += 1
        _, out = self.run_replay(lines)
        rows = read_rows(os.path.join(out, "telemetry_2026-10-20.csv"))[1:]
        col = {name: i for i, name in enumerate(COLUMNS)}
        lost = [r for r in rows if r[col["log_state"]] == "FIX_LOST"]
        self.assertTrue(lost)
        self.assertEqual(lost[0][col["fix_valid"]], "0")
        self.assertEqual(lost[0][col["lat"]], "")
        self.assertEqual(lost[0][col["speed_kmh"]], "")

    def test_timezone_follows_position_and_is_saved(self):
        # 3 minutes at Port Augusta (Adelaide zone): Sydney default -> Adelaide.
        lines = drive_lines(parked_before=190, moving=0, parked_after=0)
        app, out = self.run_replay(lines)
        self.assertEqual(app.location_time.zone_name, "Australia/Adelaide")
        rows = read_rows(os.path.join(out, "telemetry_2026-10-20.csv"))[1:]
        self.assertTrue(rows[0][19].endswith("+11:00"))        # before: Sydney AEDT
        with open(os.path.join(out, "logger_state.json")) as handle:
            self.assertEqual(json.load(handle)["tz_name"], "Australia/Adelaide")


class CliTests(unittest.TestCase):
    def test_cli_replay_of_bundled_demo(self):
        self.assertTrue(os.path.isfile(DEMO), DEMO)
        with tempfile.TemporaryDirectory() as tmp:
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = cli_main(["--headless", "--nmea-file", DEMO, "--out", tmp])
            self.assertEqual(code, 0, buf.getvalue())
            self.assertEqual(telemetry_files(tmp), ["telemetry_2026-10-20.csv"])
            rows = read_rows(os.path.join(tmp, "telemetry_2026-10-20.csv"))
            self.assertEqual(",".join(rows[0]), CSV_HEADER)
            col = {name: i for i, name in enumerate(COLUMNS)}
            states = {r[col["log_state"]] for r in rows[1:]}
            self.assertTrue({"MOVING", "STOPPED_15M"} <= states)
            # The 400 m glitch in the demo must have been rejected (fix_valid 0).
            self.assertIn("0", {r[col["fix_valid"]] for r in rows[1:]})

    def test_cli_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(cli_main(["--nmea-file", os.path.join(tmp, "none.nmea"),
                                       "--out", tmp]), 2)

    @unittest.skipUnless(ui.pillow_available(), "Pillow not installed")
    def test_cli_screenshots(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            shots = os.path.join(tmp, "shots")
            with redirect_stdout(io.StringIO()):
                code = cli_main(["--nmea-file", DEMO, "--screenshot", shots])
            self.assertEqual(code, 0)
            names = sorted(os.listdir(shots))
            self.assertEqual(len(names), len(ui.PAGES))
            for name in names:
                with Image.open(os.path.join(shots, name)) as image:
                    self.assertEqual(image.size, (320, 170))

    @unittest.skipIf(ui.pillow_available(), "Pillow is installed")
    def test_cli_screenshot_without_pillow_reports_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stdout(io.StringIO()):
                code = cli_main(["--nmea-file", DEMO, "--screenshot",
                                 os.path.join(tmp, "shots")])
            self.assertEqual(code, 4)


class DailyCsvTests(unittest.TestCase):
    def test_header_written_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = DailyCsv(tmp, "telemetry_", "a,b")
            self.assertTrue(f.append("2026-10-20", "1,2", 0))
            self.assertTrue(f.append("2026-10-20", "3,4", 1000))
            f.close()
            with open(os.path.join(tmp, "telemetry_2026-10-20.csv")) as handle:
                self.assertEqual(handle.read().splitlines(), ["a,b", "1,2", "3,4"])

    def test_old_header_is_renamed_like_the_firmware(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "telemetry_2026-10-20.csv")
            with open(path, "w") as handle:
                handle.write("old,header\n1,2\n")
            f = DailyCsv(tmp, "telemetry_", "a,b")
            self.assertTrue(f.append("2026-10-20", "9,9", 0))
            f.close()
            self.assertTrue(os.path.exists(os.path.join(tmp, "telemetry_2026-10-20-old.csv")))
            with open(path) as handle:
                self.assertEqual(handle.read().splitlines(), ["a,b", "9,9"])
            # A second mismatch must not overwrite the first -old file.
            with open(path, "w") as handle:
                handle.write("another,header\n")
            f = DailyCsv(tmp, "telemetry_", "a,b")
            f.append("2026-10-20", "8,8", 0)
            f.close()
            self.assertTrue(os.path.exists(os.path.join(tmp, "telemetry_2026-10-20-old2.csv")))

    def test_matching_header_file_is_appended_not_renamed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "telemetry_2026-10-20.csv")
            with open(path, "w") as handle:
                handle.write("a,b\n1,2\n")
            f = DailyCsv(tmp, "telemetry_", "a,b")
            f.append("2026-10-20", "3,4", 0)
            f.close()
            self.assertEqual(os.listdir(tmp), ["telemetry_2026-10-20.csv"])

    def test_storage_failure_is_reported_and_retried_later(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocker = os.path.join(tmp, "file")
            with open(blocker, "w") as handle:
                handle.write("x")
            f = DailyCsv(os.path.join(blocker, "sub"), "t_", "a")  # cannot mkdir
            self.assertFalse(f.append("2026-10-20", "1", 0))
            self.assertTrue(f.last_error)
            self.assertFalse(f.append("2026-10-20", "1", 5000))     # throttled
            f.directory = tmp
            self.assertFalse(f.append("2026-10-20", "1", 9999))     # still throttled
            self.assertTrue(f.append("2026-10-20", "1", 10000))     # retry after 10 s
            f.close()


class WaypointTests(unittest.TestCase):
    def make_app(self, tmp):
        app = LoggerApp(Settings(), tmp, clock=Clock("gps"))
        self.addCleanup(app.close)
        return app

    def feed_fix(self, app, lat=LAT0, seconds=3, kmh=0.0):
        for t in range(seconds):
            for line in nmea_burst(t, lat=lat, kmh=kmh):
                app.feed_line(line, t * 1000)
            app.tick(t * 1000)
        return (seconds - 1) * 1000

    def test_waypoint_needs_a_fix(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_app(tmp)
            self.assertEqual(app.create_waypoint(0), "NO FIX / NO CAMERA")
            self.assertFalse([f for f in os.listdir(tmp) if f.startswith("waypoints_")])

    def test_waypoint_rows_ids_and_poi(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_app(tmp)
            now = self.feed_fix(app)
            self.assertEqual(app.create_waypoint(now), "WP00001 SAVED")
            self.assertEqual(app.create_waypoint(now), "WP00002 SAVED")
            app.close()
            rows = read_rows(os.path.join(tmp, "waypoints_2026-10-20.csv"))
            self.assertEqual(",".join(rows[0]), WAYPOINT_HEADER)
            self.assertEqual(len(rows), 3)
            head = rows[0]
            r = dict(zip(head, rows[1]))
            self.assertEqual(r["waypoint_id"], "WP00001")
            self.assertEqual(r["record_action"], "CREATE")
            self.assertEqual(r["category"], "GENERIC")
            self.assertEqual(r["telemetry_source"], "LOCAL_GPS")
            self.assertEqual(r["revision"], "0")
            self.assertEqual(r["poi_source"], "USER_WAYPOINT")
            self.assertAlmostEqual(float(r["latitude"]), LAT0, 4)
            self.assertEqual(r["tyre_set_front_psi"], "36.0")
            self.assertEqual(len(rows[1]), 28)
            self.assertTrue(r["timestamp"].endswith("Z"))
            with open(os.path.join(tmp, "events_2026-10-20.csv")) as handle:
                self.assertEqual(len(handle.read().splitlines()), 3)

    def test_setup_and_automatic_photo_are_written_to_waypoint(self):
        class FakeCamera(CameraCapture):
            @property
            def available(self):
                return True

            def capture_waypoint(self, waypoint_id):
                return "photos/" + waypoint_id + ".jpg"

            def remove_photo(self, photo_reference):
                pass

        with tempfile.TemporaryDirectory() as tmp:
            camera = FakeCamera(tmp)
            app = LoggerApp(Settings(), tmp, clock=Clock("gps"), camera=camera)
            now = self.feed_fix(app)
            self.assertEqual(app.create_waypoint(now), "WP00001 SAVED")
            app.close()

            rows = read_rows(os.path.join(tmp, "waypoints_2026-10-20.csv"))
            record = dict(zip(rows[0], rows[1]))
            self.assertEqual(record["road_type"], "PAVED")
            self.assertEqual(record["road_surface"], "DRY")
            self.assertEqual(record["photo_reference"], "photos/WP00001.jpg")
            self.assertEqual(record["tyre_set_front_psi"], "36.0")
            self.assertEqual(record["tyre_set_rear_psi"], "45.0")
            self.assertEqual(record["tyre_set_fl_psi"], "36.0")
            self.assertEqual(record["tyre_set_rr_psi"], "45.0")
            self.assertEqual(record["suspension_front"], "NORMAL")
            self.assertEqual(record["suspension_rear"], "NORMAL")
            self.assertEqual(record["vehicle_load"], "LIGHT")
            restored = LoggerApp(Settings(), tmp, clock=Clock("gps"))
            self.assertEqual(restored.vehicle["tyre_set_fl_psi"], 36)
            self.assertEqual(restored.vehicle["vehicle_load"], "LIGHT")
            restored.close()

    def test_camera_failure_does_not_prevent_waypoint_save(self):
        class FailedCamera(CameraCapture):
            @property
            def available(self):
                return True

            def capture_waypoint(self, waypoint_id):
                raise RuntimeError("camera unavailable")

        with tempfile.TemporaryDirectory() as tmp:
            app = LoggerApp(Settings(), tmp, clock=Clock("gps"),
                            camera=FailedCamera(tmp))
            now = self.feed_fix(app)
            with redirect_stderr(io.StringIO()):
                message = app.create_waypoint(now)
            self.assertEqual(message, "WP00001 SAVED PHOTO ERROR")
            app.close()
            rows = read_rows(os.path.join(tmp, "waypoints_2026-10-20.csv"))
            self.assertEqual(rows[1][WAYPOINT_COLUMNS.index("photo_reference")], "")

    def test_waypoint_photo_can_be_saved_without_gps_fix(self):
        class FakeCamera(CameraCapture):
            @property
            def available(self):
                return True

            def capture_waypoint(self, waypoint_id):
                return "photos/" + waypoint_id + ".jpg"

        with tempfile.TemporaryDirectory() as tmp:
            camera = FakeCamera(tmp)
            app = LoggerApp(Settings(), tmp, clock=Clock("system"), camera=camera)
            self.assertEqual(app.create_waypoint(0), "WP00001 SAVED")
            self.assertFalse(app.snapshot.fix_valid)
            self.feed_fix(app)
            self.assertEqual(app.waypoints.poi_for(app.snapshot),
                             ("", "NONE", ""))
            app.close()
            path = next(os.path.join(tmp, name) for name in os.listdir(tmp)
                        if name.startswith("waypoints_") and name.endswith(".csv"))
            rows = read_rows(path)
            record = dict(zip(rows[0], rows[1]))
            self.assertEqual(record["category"], "PHOTO")
            self.assertEqual(record["latitude"], "")
            self.assertEqual(record["longitude"], "")
            self.assertEqual(record["photo_reference"], "photos/WP00001.jpg")

    def test_ids_continue_after_restart_and_poi_appears_in_telemetry(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_app(tmp)
            now = self.feed_fix(app)
            app.create_waypoint(now)
            app.close()
            app2 = self.make_app(tmp)
            now = self.feed_fix(app2, seconds=4)
            self.assertEqual(app2.create_waypoint(now), "WP00002 SAVED")
            app2.close()
            rows = read_rows(os.path.join(tmp, "telemetry_2026-10-20.csv"))
            col = {name: i for i, name in enumerate(COLUMNS)}
            last = rows[-1]
            self.assertEqual(last[col["poi_source"]], "USER_WAYPOINT")
            self.assertEqual(last[col["waypoint_id"]], "WP00001")


if __name__ == "__main__":
    unittest.main()


class ReplayLimitTests(unittest.TestCase):
    def test_max_seconds_stops_the_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = LoggerApp(Settings(), tmp, clock=Clock("gps"))
            final = replay_lines(app, drive_lines(), max_seconds=15)
            app.close()
            self.assertEqual(final, 15000)
            self.assertEqual(app.machine.mode, "MOVING")   # moving starts at 10 s
