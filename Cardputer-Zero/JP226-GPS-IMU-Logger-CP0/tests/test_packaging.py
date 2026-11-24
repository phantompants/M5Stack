"""Static checks of the AppBuilder packaging files (no device, no czdev)."""

import json
import os
import struct
import unittest

from jp226cp0 import __version__

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATEGORIES = {"System Tools", "Development", "Hardware & IoT", "Security",
              "Radio & Comms", "AI", "Creative & Office", "Media", "Games",
              "Emulators", "Education", "Lifestyle", "Other"}
PERMISSIONS = {"camera", "microphone", "imu", "network", "additional_hardware",
               "background_service", "external_display"}


def png_size(path):
    with open(path, "rb") as handle:
        head = handle.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n", path
    return struct.unpack(">II", head[16:24])


def read_env(path):
    values = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key] = value.strip('"')
    return values


class PackagingTests(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(PROJECT, "app-builder.json"), encoding="utf-8") as handle:
            self.manifest = json.load(handle)
        self.store = self.manifest["store"]

    def test_required_packaging_fields(self):
        m = self.manifest
        self.assertEqual(m["package_name"], "jp226-gps-imu-logger-cp0")
        self.assertEqual(m["app_name"], "JP226 GPS-IMU Logger")
        self.assertRegex(m["version"], r"^\d{4}\.(0[1-9]|1[0-2])\.\d{2}$")
        self.assertEqual(m["version"], __version__)
        self.assertTrue(m["bin_name"])
        self.assertRegex(m["package_name"], r"^[a-z0-9][a-z0-9.+-]*$")

    def test_store_section(self):
        s = self.store
        self.assertLessEqual(len(s["summary"]), 80)
        self.assertTrue(1 <= len(s["categories"]) <= 2)
        self.assertTrue(set(s["categories"]) <= CATEGORIES)
        self.assertEqual(s["license"], "MIT")
        self.assertEqual(s["author"]["github"], "jp226prints")
        self.assertEqual(s["author"]["display_name"], "JP226 Prints")
        self.assertEqual(set(s["permissions"]), PERMISSIONS)
        self.assertTrue(all(isinstance(v, bool) for v in s["permissions"].values()))
        self.assertEqual({k for k, v in s["permissions"].items() if v},
                         {"additional_hardware", "camera", "imu", "network"})
        self.assertEqual(
            s["source_repo"],
            "https://github.com/phantompants/M5Stack/tree/main/Cardputer-Zero/"
            "JP226-GPS-IMU-Logger-CP0")

    def test_icon_is_square_256(self):
        self.assertEqual(png_size(os.path.join(PROJECT, self.store["icon"])), (256, 256))

    def test_screenshots_are_320x170(self):
        self.assertTrue(1 <= len(self.store["screenshots"]) <= 6)
        for rel in self.store["screenshots"]:
            self.assertEqual(png_size(os.path.join(PROJECT, rel)), (320, 170), rel)

    def test_meta_env_matches_manifest(self):
        env = read_env(os.path.join(PROJECT, "packaging", "meta.env"))
        self.assertEqual(env["PKG_NAME"], self.manifest["package_name"])
        self.assertEqual(env["PKG_VERSION"], self.manifest["version"])
        self.assertEqual(env["APP_NAME"], self.manifest["app_name"])
        self.assertTrue(env["APP_EXEC"].endswith("/" + self.manifest["bin_name"]))
        self.assertEqual(env["PKG_MAINTAINER"], "JP226 Prints <github@jp226prints.au>")
        self.assertTrue(env["PKG_HOMEPAGE"].startswith("https://"))
        for dep in ("python3", "python3-pil", "python3-numpy", "python3-evdev",
                    "python3-serial"):
            self.assertIn(dep, env["PKG_DEPENDS"])

    def test_license_is_mit_like_the_esp32_repo(self):
        with open(os.path.join(PROJECT, "LICENSE"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertTrue(text.startswith("MIT License"))
        self.assertIn("JP226-GPS-IMU-Logger contributors", text)

    def test_packaging_scripts_exist(self):
        for name in ("build.sh", "stage.sh", "postinst", "meta.env", "icon.png"):
            self.assertTrue(os.path.isfile(os.path.join(PROJECT, "packaging", name)), name)

    def test_stage_script_installs_every_module(self):
        with open(os.path.join(PROJECT, "packaging", "stage.sh"), encoding="utf-8") as handle:
            stage = handle.read()
        self.assertIn("jp226cp0/*.py", stage)
        self.assertIn("jp226_logger.py", stage)
        self.assertIn("obd_scan.cfg.example", stage)


if __name__ == "__main__":
    unittest.main()
