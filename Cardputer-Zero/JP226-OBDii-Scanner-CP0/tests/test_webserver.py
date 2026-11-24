import json
import os
import tempfile
import unittest
import urllib.request
import zipfile
import io

from jp226obd import scanner
from jp226obd.elm327 import Elm327
from jp226obd.fake_elm import FakeElm
from jp226obd.webserver import LogWebServer


class WebServerTests(unittest.TestCase):
    def test_index_download_zip_and_traversal_block(self):
        tmp = tempfile.mkdtemp()
        cfg = dict(scanner.DEFAULTS)
        cfg["scan_mode"] = "quick"
        cfg["benchmark_samples"] = "2"
        log_path = os.path.join(tmp, "obd_scan_20261125T000000Z.jsonl")
        log = scanner.ScanLog(log_path)
        summary = scanner.Scanner(Elm327(FakeElm("ev")), log, cfg).run()
        log.close()
        prof_path, prof = scanner.save_profile(summary, "demo", tmp, "20261125T000000Z")
        prof_name = os.path.basename(prof_path)

        srv = LogWebServer(tmp, port=0, profile_getter=lambda: prof)
        srv.start()
        try:
            self.assertTrue(srv.running)
            base = "http://127.0.0.1:{}".format(srv.port)

            with urllib.request.urlopen(base + "/") as resp:
                body = resp.read().decode("utf-8")
                self.assertEqual(resp.status, 200)
                self.assertIn(prof_name, body)
                self.assertIn("Download All", body)

            with urllib.request.urlopen(base + "/api/files") as resp:
                files = json.loads(resp.read().decode("utf-8"))
                names = {f["name"] for f in files}
                self.assertIn(prof_name, names)
                self.assertIn("obd_scan_20261125T000000Z.jsonl", names)

            with urllib.request.urlopen(base + "/download/" + prof_name) as resp:
                dl_prof = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(dl_prof["vehicle"]["powertrain_hint"], "BEV")

            with urllib.request.urlopen(base + "/download-all.zip") as resp:
                zbytes = resp.read()
                with zipfile.ZipFile(io.BytesIO(zbytes)) as zf:
                    self.assertIn(prof_name, zf.namelist())
                    self.assertIn("obd_scan_20261125T000000Z.jsonl", zf.namelist())

            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(base + "/download/../../etc/passwd")
            self.assertEqual(ctx.exception.code, 404)
            self.assertGreaterEqual(srv.downloads, 2)
        finally:
            srv.stop()


if __name__ == "__main__":
    unittest.main()
