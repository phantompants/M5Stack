"""Photos (GPS EXIF, CAMERA page, gallery), the hotspot and the ENV page."""

import os
import struct
import subprocess
import tempfile
import unittest

from jp226cp0 import env, espnow, exif, gps_power, hotspot
from jp226cp0.app import LoggerApp
from jp226cp0.camera import CameraCapture, LiveCamera
from jp226cp0.clock import Clock
from jp226cp0.settings import Settings
from tests.helpers import make_snapshot, nmea_burst, utc_ms

# Smallest useful JPEG structure: SOI, JFIF APP0, an old EXIF APP1, then a
# start-of-scan with some "image data" and EOI.
_JFIF = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\0\x01\x01\0\0\x01\0\x01\0\0"
_OLD_EXIF = b"\xff\xe1" + struct.pack(">H", 10) + b"Exif\0\0OLD!"[:8]
_SCAN = b"\xff\xda" + struct.pack(">H", 4) + b"\0\0" + b"\x12\x34\x56" + b"\xff\xd9"
TINY_JPEG = b"\xff\xd8" + _JFIF + _OLD_EXIF + _SCAN


class ExifTests(unittest.TestCase):
    def test_gps_round_trip_and_segments(self):
        payload = exif.build_exif(
            utc_ms(2026, 11, 26, 4, 5, 6), 37800, latitude=-34.928499,
            longitude=138.600746, altitude_m=48.2, speed_kmh=62.5,
            course_deg=271.0, hdop=0.9, satellites=11, software="test")
        jpeg = exif.replace_exif(TINY_JPEG, payload)
        self.assertTrue(jpeg.startswith(b"\xff\xd8\xff\xe1"))
        self.assertNotIn(b"OLD!", jpeg)                  # old block removed
        self.assertIn(b"JFIF", jpeg)                     # others kept
        self.assertTrue(jpeg.endswith(_SCAN))
        lat, lon = exif.read_gps(jpeg)
        self.assertAlmostEqual(lat, -34.928499, places=6)
        self.assertAlmostEqual(lon, 138.600746, places=6)
        self.assertIn(b"2026:11:26 14:35:06", payload)  # local time, +10:30
        self.assertIn(b"+10:30", payload)
        self.assertIn(b"WGS-84", payload)

    def test_no_position_no_gps_block(self):
        payload = exif.build_exif(None)
        self.assertIsNone(exif.read_gps(exif.replace_exif(TINY_JPEG, payload)))

    def test_dms_rounding_never_reaches_60(self):
        self.assertEqual(exif._dms(10.99999999), [(11, 1), (0, 1), (0, 10000)])

    def test_rejects_non_jpeg(self):
        with self.assertRaises(ValueError):
            exif.replace_exif(b"PNG", b"Exif\0\0")


class EnvMathTests(unittest.TestCase):
    def test_sensirion_crc_datasheet_example(self):
        self.assertEqual(env.sensirion_crc(b"\xbe\xef"), 0x92)

    def test_sht_conversion(self):
        def word(value):
            data = struct.pack(">H", value)
            return data + bytes((env.sensirion_crc(data),))
        t, h = env.sht_convert(word(0x6666) + word(0x8000))
        self.assertAlmostEqual(t, 25.0, places=1)
        self.assertAlmostEqual(h, 50.0, places=1)
        self.assertIsNone(env.sht_convert(b"\x66\x66\x00" + word(0x8000)))

    def test_bmp280_datasheet_example(self):
        trim = struct.pack("<HhhHhhhhhhhh", 27504, 26435, -1000, 36477, -10685,
                           3024, 2855, 140, -7, 15500, -14600, 6000)
        t, p = env.Bmp280Calibration(trim).convert(415148, 519888)
        self.assertAlmostEqual(t, 25.08, places=2)
        self.assertAlmostEqual(p, 1006.5327, places=2)

    def test_qmp6988_signed_coefficients(self):
        otp = bytes([0x80] + [0] * 24)       # b00 = most negative 20-bit value
        self.assertEqual(env.Qmp6988Calibration(otp).b00, -(1 << 19) / 16.0)


class FakeI2C:
    """An SHT30 that NACKs the SHT4x command, and nothing else on the bus."""

    def __init__(self, bus, address):
        if address != env.SHT_ADDRESS:
            raise OSError("no device")
        self.last = b""

    def write(self, data):
        if data == b"\xfd":
            raise OSError("NACK")
        self.last = data

    def read(self, count):
        data = struct.pack(">H", 0x6666)
        word = data + bytes((env.sensirion_crc(data),))
        return word + word

    def read_register(self, register, count):
        raise OSError("no device")

    def close(self):
        pass


class EnvReaderTests(unittest.TestCase):
    def test_finds_an_sht30(self):
        reader = env.GroveEnv(device_factory=FakeI2C)
        reader._poll_once()
        values = reader.readings()
        self.assertEqual(values["status"], "OK")
        self.assertEqual(values["sensors"], "SHT3X")
        self.assertAlmostEqual(values["temp_c"], 25.0, places=1)
        self.assertIsNone(values["pressure_hpa"])

    def test_nothing_plugged_in(self):
        def none(bus, address):
            raise OSError("no device")
        reader = env.GroveEnv(device_factory=none)
        reader._poll_once()
        self.assertEqual(reader.readings()["status"], "NO ENV UNIT")

    def test_grove_rails(self):
        self.assertEqual(gps_power.env_rails(True, "atom"),
                         (("grove_fun", 1), ("grove_5v_out", 1)))
        self.assertEqual(gps_power.env_rails(False, "cap"),
                         (("grove_5v_out", 0),))
        self.assertEqual(gps_power.env_rails(True, "grove"), ())
        self.assertEqual(gps_power.apply_env(True, "grove"), "GROVE GPS IN USE")


class ProbeTests(unittest.TestCase):
    def test_temperature_report_round_trip(self):
        packet = espnow.encode_temperature(3, 1, [(0x1A2B, 21.5), (0x0C0D, None)])
        self.assertEqual(espnow.decode_temperature(packet),
                         (1, [(0x1A2B, 21.5), (0x0C0D, None)]))

    def test_app_lists_probes_from_the_bridge(self):
        import base64
        from jp226cp0 import bridge
        with tempfile.TemporaryDirectory() as tmp:
            app = LoggerApp(Settings(gps_source="atom"), tmp, clock=Clock("gps"))
            packet = espnow.encode_temperature(1, 3, [(7, 4.25)])
            app.feed_bridge_line(bridge.frame("PJP226,RX,AABBCCDDEEFF,-50,{}".format(
                base64.b64encode(packet).decode())), 1000)
            self.assertEqual(app.probe_rows(3000), [("BLACKBOX 1", 4.25, 2000)])
            self.assertEqual(app.probe_rows(700_000), [])
            # Old probes are dropped from the table, and it is capped.
            many = espnow.encode_temperature(2, 1, [(n, 20.0) for n in range(4)])
            for t in range(10):
                packet = espnow.encode_temperature(t, 1, [(100 + 4 * t + n, 20.0)
                                                          for n in range(4)])
                app.feed_bridge_line(bridge.frame("PJP226,RX,AABBCCDDEEFF,-50,{}".format(
                    base64.b64encode(packet).decode())), 700_000 + t)
            self.assertNotIn((3, 7), app.probes)
            self.assertLessEqual(len(app.probes), 32)
            app.close()


class FakeNmcli:
    def __init__(self):
        self.calls = []
        self.profiles = []
        self.active = []

    def __call__(self, command, **kw):
        args = command[1:]
        self.calls.append(args)
        out = ""
        if args[:3] == ["connection", "add", "type"]:
            self.profiles.append(hotspot.CONNECTION)
        elif args[:2] == ["connection", "up"]:
            self.active.append(hotspot.CONNECTION)
        elif args[:2] == ["connection", "down"]:
            self.active.clear()
        elif "--active" in args:
            out = "\n".join(self.active)
        elif args[:5] == ["-t", "-f", "NAME", "connection", "show"]:
            out = "\n".join(self.profiles)
        elif "IP4.ADDRESS" in args:
            out = "10.42.0.1/24\n"
        elif args and args[0] == "-s":
            out = "JP226-CZ-C7B6\nsecretpass\n"
        return subprocess.CompletedProcess(command, 0, out, "")


class HotspotTests(unittest.TestCase):
    def test_first_start_creates_a_wpa2_profile(self):
        fake = FakeNmcli()
        spot = hotspot.Hotspot(run=fake, which=lambda n: "/usr/bin/nmcli",
                               read_mac=lambda: "f0:40:af:90:c7:b6")
        self.assertTrue(spot.start())
        add = next(c for c in fake.calls if c[:2] == ["connection", "add"])
        self.assertIn("JP226-CZ-C7B6", add)
        self.assertIn("wpa-psk", add)
        self.assertIn("shared", add)
        self.assertEqual((spot.active, spot.address, spot.password),
                         (True, "10.42.0.1", "secretpass"))
        spot.stop()
        self.assertFalse(spot.active)
        self.assertTrue(spot.start())        # profile reused, not re-added
        self.assertEqual(sum(c[:2] == ["connection", "add"] for c in fake.calls), 1)

    def test_permission_denied(self):
        def denied(command, **kw):
            return subprocess.CompletedProcess(command, 1, "", "Not authorized")
        spot = hotspot.Hotspot(run=denied, which=lambda n: "/usr/bin/nmcli")
        self.assertFalse(spot.start())
        self.assertEqual(spot.error, "NO PERMISSION")

    def test_wifi_qr_text_escapes(self):
        self.assertEqual(hotspot.wifi_qr_text("A;B", "p:w"),
                         "WIFI:T:WPA;S:A\\;B;P:p\\:w;;")
        password = hotspot.make_password()
        self.assertEqual(len(password), 10)
        self.assertFalse(set(password) & set("0O1lI"))


class CameraRotationTests(unittest.TestCase):
    def test_rpicam_still_turns_the_photo_180_degrees(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            camera = CameraCapture(tmp, command="rpicam-still",
                                   live=LiveCamera())
            with mock.patch("jp226cp0.camera.subprocess.run") as run:
                camera._rpicam_still(os.path.join(tmp, "x.jpg"))
            argv = run.call_args[0][0]
            self.assertEqual(argv[argv.index("--rotation") + 1], "180")
            self.assertEqual(argv[-2], "--output")


class FakeShotCamera(CameraCapture):
    def __init__(self, data_dir):
        super().__init__(data_dir, command="/bin/true", live=LiveCamera())

    def _rpicam_still(self, path):
        with open(path, "wb") as handle:
            handle.write(TINY_JPEG)


class CameraPageTests(unittest.TestCase):
    def test_photo_gets_gps_exif_and_a_local_time_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = LoggerApp(Settings(), tmp, clock=Clock("gps"),
                            camera=FakeShotCamera(tmp))
            now = 0
            for t in range(4):
                for line in nmea_burst(t, kmh=30.0):
                    app.feed_line(line, now)
                app.tick(now)
                now += 1000
            self.assertEqual(app.take_photo(now), "SAVED")
            photos = app.gallery_photos()
            self.assertEqual(len(photos), 1)
            self.assertRegex(photos[0], r"^IMG_\d{8}_\d{6}\.jpg$")
            with open(os.path.join(tmp, "photos", photos[0]), "rb") as handle:
                lat, lon = exif.read_gps(handle.read())
            self.assertAlmostEqual(lat, -32.4936, places=4)
            self.assertAlmostEqual(lon, 137.7716, places=4)
            # No waypoint is made by the camera page.
            self.assertIsNone(app.waypoints.last)
            app.take_photo(now)                       # same second: unique name
            self.assertEqual(len(app.gallery_photos()), 2)
            app.gallery_step(1)
            self.assertEqual(app.gallery_index, 1)
            app.gallery_step(1)
            self.assertEqual(app.gallery_index, 0)
            app.close()

    def test_no_fix_photo_is_kept_without_gps(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = LoggerApp(Settings(), tmp, clock=Clock("gps"),
                            camera=FakeShotCamera(tmp))
            self.assertEqual(app.take_photo(0), "SAVED (NO GPS)")
            app.close()

    def test_no_camera(self):
        with tempfile.TemporaryDirectory() as tmp:
            camera = CameraCapture(tmp)
            camera.command = None
            app = LoggerApp(Settings(), tmp, clock=Clock("gps"), camera=camera)
            self.assertEqual(app.take_photo(0), "NO CAMERA")
            app.close()



class WebGalleryTests(unittest.TestCase):
    def test_gallery_inline_photo_and_zip(self):
        import io
        import urllib.request
        import zipfile
        from jp226cp0.webshare import WebShare
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "photos"))
            with open(os.path.join(tmp, "photos", "IMG_20261126_143506.jpg"),
                      "wb") as handle:
                handle.write(TINY_JPEG)
            share = WebShare(tmp, port=0)
            self.assertTrue(share.start())
            base = "http://127.0.0.1:{}".format(share.port)
            try:
                index = urllib.request.urlopen(base + "/").read().decode()
                self.assertIn("Photos (1)", index)
                self.assertIn("/v/photos/IMG_20261126_143506.jpg", index)
                gallery = urllib.request.urlopen(base + "/photos").read().decode()
                self.assertIn("Save to Photos", gallery)
                reply = urllib.request.urlopen(
                    base + "/v/photos/IMG_20261126_143506.jpg")
                self.assertTrue(reply.headers["Content-Disposition"].startswith("inline"))
                self.assertEqual(reply.read(), TINY_JPEG)
                thumb = urllib.request.urlopen(
                    base + "/t/photos/IMG_20261126_143506.jpg").read()
                self.assertTrue(thumb.startswith(bytes((0xFF, 0xD8))))
                data = urllib.request.urlopen(base + "/photos.zip").read()
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    self.assertEqual(archive.namelist(),
                                     ["photos/IMG_20261126_143506.jpg"])
                with self.assertRaises(urllib.error.HTTPError):
                    urllib.request.urlopen(base + "/v/photos/../secret.jpg")
                # DNS rebinding: a foreign host name is refused.
                request = urllib.request.Request(
                    base + "/", headers={"Host": "evil.example:8226"})
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(request)
                self.assertEqual(caught.exception.code, 403)
                request = urllib.request.Request(
                    base + "/", headers={"Host": "cardputerzero.local:8226"})
                import socket
                own = urllib.request.Request(base + "/", headers={
                    "Host": socket.gethostname() + ".local:8226"})
                self.assertEqual(urllib.request.urlopen(own).status, 200)
            finally:
                share.stop()


if __name__ == "__main__":
    unittest.main()
