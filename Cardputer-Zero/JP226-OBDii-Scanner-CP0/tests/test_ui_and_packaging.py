import json
import os
import struct
import tempfile
import unittest

from jp226obd import cli, keys, ui
from jp226obd.app import ObdScannerApp

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATEGORIES = {
    "System Tools", "Development", "Hardware & IoT", "Security",
    "Radio & Comms", "AI", "Creative & Office", "Media", "Games",
    "Emulators", "Education", "Lifestyle", "Other",
}
PERMISSIONS = {
    "camera", "microphone", "imu", "network", "additional_hardware",
    "background_service", "external_display",
}


def png_size(path: str):
    with open(path, "rb") as fh:
        head = fh.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n", path
    return struct.unpack(">II", head[16:24])


def read_env(path: str):
    vals = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                vals[k] = v.strip('"')
    return vals


class AppAndUiTests(unittest.TestCase):
    def test_key_actions(self):
        self.assertEqual(keys.action_for("KEY_RIGHT"), keys.NEXT_PAGE)
        self.assertEqual(keys.action_for("KEY_LEFT"), keys.PREV_PAGE)
        self.assertEqual(keys.action_for("KEY_F"), keys.UP)
        self.assertEqual(keys.action_for("KEY_X"), keys.DOWN)
        self.assertEqual(keys.action_for("KEY_W"), keys.DEC)
        self.assertEqual(keys.action_for("KEY_E"), keys.INC)
        self.assertEqual(keys.action_for("KEY_R"), keys.REFRESH)
        self.assertEqual(keys.action_for("KEY_ENTER"), keys.ACTION)
        self.assertEqual(keys.action_for("KEY_Q"), keys.QUIT)
        self.assertEqual(keys.page_action_for("CONNECT", "KEY_4"), keys.CYCLE_LINK)
        self.assertEqual(keys.page_action_for("CONNECT", "KEY_8"), keys.TOGGLE_SCAN)
        self.assertEqual(keys.page_action_for("LIVE", "KEY_8"), keys.TOGGLE_LIVE)
        self.assertEqual(keys.page_action_for("EV SOUND FIT", "KEY_4"), keys.EV_SCAN)
        self.assertEqual(keys.page_action_for("MODULES", "KEY_5"), keys.SCROLL_DOWN)
        self.assertEqual(keys.page_action_for("SHARE", "KEY_4"), keys.TOGGLE_WEB)
        self.assertEqual(keys.page_action_for("SHARE", "KEY_5"), keys.CYCLE_WEB_PORT)
        self.assertEqual(keys.page_action_for("FOSSIL RECORD", "KEY_8"), keys.GO_DINO)
        self.assertEqual(keys.page_action_for("DINO DASH", "KEY_4"), keys.DINO_TOGGLE)
        self.assertEqual(keys.page_action_for("DINO DASH", "KEY_8"), keys.DINO_JUMP)
        self.assertEqual(len(ui.footer_slots(0)), 5)
        self.assertEqual([ui.footer_slot_x(i, 320) for i in range(5)], [53, 107, 160, 213, 267])

    def test_page_action_dispatch_and_dino(self):
        tmp = tempfile.mkdtemp()
        app = ObdScannerApp(data_dir=tmp)
        try:
            start_link = app.link
            cli._run_page_action(app, keys.CYCLE_LINK, ui.CONNECT_PAGE_INDEX)
            self.assertNotEqual(app.link, start_link)
            self.assertEqual(
                cli._run_page_action(app, keys.GO_MODULES, 3),
                ui.PAGES.index("MODULES"),
            )
            self.assertEqual(
                cli._run_page_action(app, keys.GO_DINO, ui.FOSSIL_PAGE_INDEX),
                ui.DINO_PAGE_INDEX,
            )
            self.assertEqual(app.dino.character, "TREX")
            cli._run_page_action(app, keys.DINO_TOGGLE, ui.DINO_PAGE_INDEX)
            self.assertEqual(app.dino.character, "BRONTO")
            app.tick_dino(100)
            cli._run_page_action(app, keys.DINO_JUMP, ui.DINO_PAGE_INDEX, 120)
            self.assertEqual(app.dino.state, app.dino.PLAYING)
        finally:
            app.close()

    def test_app_cycle_and_demo_scan(self):
        tmp = tempfile.mkdtemp()
        app = ObdScannerApp(data_dir=tmp)
        try:
            # Cycle link to demo
            while app.link != "demo":
                app.adjust_connect_row(1)
            app.select_connect_row(1)  # TARGET row -> cycle demo profile to ev
            while app.demo_profile != "ev":
                app.adjust_connect_row(1)
            prof = app.run_scan_sync()
            self.assertEqual(app.status, "COMPLETE")
            self.assertEqual(prof["ev_sound_fit"]["grade"], "READY")
            model = app.ui_model(500)
            self.assertEqual(model["powertrain"], "BEV")
            if ui.pillow_available():
                shots = ui.save_screenshots(model, os.path.join(tmp, "shots"))
                self.assertEqual(len(shots), len(ui.PAGES))
                for p in shots:
                    self.assertEqual(png_size(p), (320, 170))
        finally:
            app.close()


class PackagingTests(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(PROJECT, "app-builder.json"), encoding="utf-8") as fh:
            self.manifest = json.load(fh)
        self.store = self.manifest["store"]

    def test_manifest_fields(self):
        m = self.manifest
        self.assertEqual(m["package_name"], "jp226-obdii-scanner-cp0")
        self.assertEqual(m["app_name"], "JP226 OBD-II Scanner")
        self.assertRegex(m["package_name"], r"^[a-z0-9][a-z0-9.+-]*$")
        self.assertLessEqual(len(self.store["summary"]), 80)
        self.assertTrue(set(self.store["categories"]) <= CATEGORIES)
        self.assertEqual(self.store["author"]["github"], "jp226prints")
        self.assertEqual(self.store["author"]["display_name"], "JP226 Prints")
        self.assertEqual(set(self.store["permissions"]), PERMISSIONS)
        self.assertEqual(
            {k for k, v in self.store["permissions"].items() if v},
            {"network", "additional_hardware"},
        )

    def test_icon_and_screenshots(self):
        self.assertEqual(png_size(os.path.join(PROJECT, self.store["icon"])), (256, 256))
        for rel in self.store["screenshots"]:
            self.assertEqual(png_size(os.path.join(PROJECT, rel)), (320, 170), rel)

    def test_meta_env_and_scripts(self):
        env = read_env(os.path.join(PROJECT, "packaging", "meta.env"))
        self.assertEqual(env["PKG_NAME"], self.manifest["package_name"])
        self.assertEqual(env["PKG_VERSION"], self.manifest["version"])
        self.assertEqual(env["APP_NAME"], self.manifest["app_name"])
        self.assertEqual(env["PKG_MAINTAINER"], "JP226 Prints <github@jp226prints.au>")
        self.assertTrue(env["APP_EXEC"].endswith("/" + self.manifest["bin_name"]))
        for name in ("build.sh", "stage.sh", "postinst", "meta.env", "icon.png"):
            self.assertTrue(os.path.isfile(os.path.join(PROJECT, "packaging", name)), name)


if __name__ == "__main__":
    unittest.main()
