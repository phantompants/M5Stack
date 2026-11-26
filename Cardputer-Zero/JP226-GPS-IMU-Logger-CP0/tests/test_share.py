"""KML/GPX export, ZIP bundles, nightly export and the SHARE web server."""

import io
import os
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

from jp226cp0 import export, keys, ui, webshare
from jp226cp0.app import LoggerApp, replay_lines
from jp226cp0.clock import Clock
from jp226cp0.csvlog import CSV_HEADER
from jp226cp0.settings import Settings
from jp226cp0.waypoints import WAYPOINT_HEADER

from .test_pipeline import DEMO

KML = "{http://www.opengis.net/kml/2.2}"
GPX = "{http://www.topografix.com/GPX/1/1}"
DAY = "2026-10-20"


def telemetry_row(time_text, lat, lon, fix="1"):
    fields = {name: "" for name in CSV_HEADER.split(",")}
    fields.update(timestamp=time_text, lat=str(lat), lon=str(lon), alt_m="12.5",
                  speed_kmh="40", fix_valid=fix)
    return ",".join(fields[n] for n in CSV_HEADER.split(","))


def write_day(data_dir, date=DAY, rows=None, waypoints=None):
    rows = rows if rows is not None else [
        telemetry_row(date + "T01:00:00.000Z", -34.9, 138.6),
        telemetry_row(date + "T01:00:01.000Z", -34.9001, 138.6001),
        telemetry_row(date + "T01:00:02.000Z", "", "", fix="0"),
        telemetry_row(date + "T01:30:00.000Z", -34.95, 138.65),  # > 5 min gap
    ]
    with open(os.path.join(data_dir, "telemetry_{}.csv".format(date)), "w") as h:
        h.write(CSV_HEADER + "\n" + "\n".join(rows) + "\n")
    if waypoints is not None:
        cols = WAYPOINT_HEADER.split(",")
        lines = [WAYPOINT_HEADER]
        for wp in waypoints:
            fields = {n: "" for n in cols}
            fields.update(wp)
            lines.append(",".join(fields[n] for n in cols))
        with open(os.path.join(data_dir, "waypoints_{}.csv".format(date)), "w") as h:
            h.write("\n".join(lines) + "\n")


class ExportTests(unittest.TestCase):
    def test_kml_and_gpx_hold_valid_fixes_segments_and_waypoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_day(tmp, waypoints=[{
                "waypoint_id": "WP00001", "timestamp": DAY + "T01:00:01.000Z",
                "latitude": "-34.9", "longitude": "138.6",
                "poi_name": "Gate & <cattle grid>", "road_type": "GRAVEL",
                "photo_reference": "photos/WP00001.jpg"}])
            paths = export.export_day(tmp, DAY)
            kml = ET.parse(paths["kml"]).getroot()
            gpx = ET.parse(paths["gpx"]).getroot()
        lines = kml.findall(".//{0}LineString/{0}coordinates".format(KML))
        self.assertEqual([len(c.text.split()) for c in lines], [2, 1])
        self.assertEqual(lines[0].text.split()[0], "138.600000,-34.900000,12.5")
        names = [n.text for n in kml.findall(".//{0}Placemark/{0}name".format(KML))]
        self.assertIn("Gate & <cattle grid>", names)
        segs = gpx.findall(".//{0}trkseg".format(GPX))
        self.assertEqual([len(s) for s in segs], [2, 1])
        self.assertEqual(segs[0][0].find(GPX + "time").text, DAY + "T01:00:00.000Z")
        self.assertEqual(len(gpx.findall(GPX + "wpt")), 1)

    def test_needs_export_follows_log_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_day(tmp)
            self.assertTrue(export.needs_export(tmp, DAY))
            export.export_day(tmp, DAY)
            self.assertFalse(export.needs_export(tmp, DAY))
            later = time.time() + 5
            os.utime(os.path.join(tmp, "telemetry_{}.csv".format(DAY)), (later, later))
            self.assertTrue(export.needs_export(tmp, DAY))
            self.assertFalse(export.needs_export(tmp, "2026-01-01"))
            with self.assertRaises(ValueError):
                export.export_day(tmp, "../x")

    def test_real_replay_log_exports(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = LoggerApp(Settings(), tmp, clock=Clock("gps"))
            with open(DEMO) as handle:
                replay_lines(app, handle.read().splitlines(), max_seconds=120)
            app.close()
            day = export.list_days(tmp)[0]
            paths = export.export_day(tmp, day)
            gpx = ET.parse(paths["gpx"]).getroot()
        self.assertGreater(len(gpx.findall(".//{0}trkpt".format(GPX))), 10)

    def test_zip_has_logs_exports_and_only_safe_photos(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "photos"))
            with open(os.path.join(tmp, "photos", "WP00001.jpg"), "wb") as h:
                h.write(b"jpeg")
            with open(os.path.join(tmp, "secret.txt"), "w") as h:
                h.write("no")
            write_day(tmp, waypoints=[
                {"waypoint_id": "WP00001", "photo_reference": "photos/WP00001.jpg"},
                {"waypoint_id": "WP00002", "photo_reference": "../secret.txt"}])
            write_day(tmp, "2026-10-19")
            self.assertEqual(export.last_days(tmp, 1), [DAY])
            self.assertEqual(export.last_days(tmp, 0), [DAY, "2026-10-19"])
            buffer = io.BytesIO()
            export.write_zip(tmp, [DAY], buffer)
        names = zipfile.ZipFile(buffer).namelist()
        self.assertEqual(sorted(names), sorted([
            "telemetry_{}.csv".format(DAY), "waypoints_{}.csv".format(DAY),
            "exports/track_{}.kml".format(DAY), "exports/track_{}.gpx".format(DAY),
            "photos/WP00001.jpg"]))

    def test_nightly_exports_finished_days_and_manual_includes_today(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_day(tmp, "2026-10-19")
            write_day(tmp, DAY)
            job = export.NightlyExporter(tmp, lambda: DAY)
            self.assertEqual(job.run_once(), ["2026-10-19"])
            self.assertEqual(job.status, "EXPORTED 1")
            self.assertEqual(job.run_once(), [])
            self.assertEqual(job.status, "UP TO DATE")
            self.assertEqual(job.run_once(include_today=True), [DAY])
            waiting = export.NightlyExporter(tmp, lambda: None)
            self.assertEqual(waiting.run_once(), [])
            self.assertEqual(waiting.status, "WAITING FOR TIME")


class WebShareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        write_day(self.tmp.name)
        with open(os.path.join(self.tmp.name, "logger_state.json"), "w") as h:
            h.write("{}")
        self.web = webshare.WebShare(self.tmp.name, port=0, zip_days_fn=lambda: 3)
        self.assertTrue(self.web.start())
        self.base = "http://127.0.0.1:{}".format(self.web.port)

    def tearDown(self):
        self.web.stop()
        self.tmp.cleanup()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as response:
            return response.status, dict(response.headers), response.read()

    def test_index_lists_days_and_zip_choice(self):
        status, headers, body = self.get("/")
        text = body.decode()
        self.assertEqual(status, 200)
        self.assertIn(DAY, text)
        self.assertIn('href="/f/exports/track_{}.kml"'.format(DAY), text)
        self.assertIn('<option value="3" selected>3 days</option>', text)

    def test_downloads_logs_fresh_exports_and_zip(self):
        _s, headers, body = self.get("/f/telemetry_{}.csv".format(DAY))
        self.assertTrue(body.startswith(b"timestamp,lat,lon"))
        self.assertIn("attachment", headers["Content-Disposition"])
        _s, headers, body = self.get("/f/exports/track_{}.kml".format(DAY))
        self.assertEqual(headers["Content-Type"], "application/vnd.google-earth.kml+xml")
        self.assertIn(b"<kml", body)
        _s, headers, body = self.get("/zip?days=1")
        self.assertIn("jp226_logs_{0}_to_{0}.zip".format(DAY),
                      headers["Content-Disposition"])
        self.assertIn("exports/track_{}.gpx".format(DAY),
                      zipfile.ZipFile(io.BytesIO(body)).namelist())

    def test_only_log_files_are_served(self):
        for path in ("/f/logger_state.json", "/f/../logger_state.json",
                     "/f/exports/../logger_state.json", "/f/photos/../x.jpg",
                     "/f/telemetry_2026-10-20.csv.bak", "/etc/passwd"):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.get(path)
            self.assertEqual(caught.exception.code, 404, path)
            caught.exception.close()


class SharePageTests(unittest.TestCase):
    def test_keys_toggle_web_zip_days_and_render(self):
        self.assertEqual(
            [keys.page_action_for("SHARE", "KEY_{}".format(n)) for n in range(4, 8)],
            [keys.SHARE_WEB, keys.ZIP_DAYS_DOWN, keys.ZIP_DAYS_UP, keys.EXPORT_NOW])
        with tempfile.TemporaryDirectory() as tmp:
            app = LoggerApp(Settings(web_port=0), tmp, clock=Clock("gps"))
            self.assertFalse(app.share_enabled)
            app.change_zip_days(1)
            self.assertEqual(app.zip_days, 14)
            images = [ui.render_page(ui.PAGES.index("SHARE"), app.ui_model(0))]
            app.toggle_share()
            model = app.ui_model(0)
            self.assertTrue(model["share_on"])
            images.append(ui.render_page(ui.PAGES.index("SHARE"), model)
                          if ui.pillow_available() else None)
            app.export_now()
            app.close()
            again = LoggerApp(Settings(web_port=0), tmp, clock=Clock("gps"))
            self.assertTrue(again.share_enabled)
            self.assertEqual(again.zip_days, 14)
            again.close()
        for image in images:
            if image is not None:
                self.assertEqual(image.size, (320, 170))

    if not ui.pillow_available():
        test_keys_toggle_web_zip_days_and_render = unittest.skip("Pillow")(
            test_keys_toggle_web_zip_days_and_render)


if __name__ == "__main__":
    unittest.main()
