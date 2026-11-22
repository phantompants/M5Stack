import json
import os
import tempfile
import unittest

from jp226cp0 import obdscan
from jp226cp0.elm327 import Elm327, classify, parse_frames
from jp226cp0.fake_elm import VIN, FakeElm


class FrameTests(unittest.TestCase):
    def test_single_frame(self):
        frames = parse_frames(["7E8 06 41 00 BE 3F A8 13"])
        self.assertEqual(frames, {"7E8": bytes.fromhex("4100BE3FA813")})

    def test_multi_frame_vin(self):
        lines = ["7E8 10 14 49 02 01 4D 4E 41",
                 "7E8 21 55 4D 46 46 35 30 50",
                 "7E8 22 57 30 30 30 30 30 30"]
        data = parse_frames(lines)["7E8"]
        self.assertEqual(len(data), 0x14)
        self.assertEqual(data[3:], b"MNAUMFF50PW000000")

    def test_classify(self):
        self.assertEqual(classify(["NO DATA"], 0x22)["kind"], "no_data")
        r = classify(["72E 03 7F 22 31"], 0x22)
        self.assertEqual(r["kind"], "nrc")
        self.assertEqual(r["replies"]["72E"]["nrc_name"], "requestOutOfRange")
        self.assertEqual(classify(["72E 05 62 28 13 0E 10"], 0x22)["kind"], "positive")
        self.assertEqual(classify(["CAN ERROR"], 0x22)["kind"], "error")
        self.assertEqual(classify(["TIMEOUT"], 0x22)["kind"], "error")
        # A positive reply wins over another module's refusal.
        both = classify(["7E8 03 7F 22 31", "72E 05 62 28 13 0E 10"], 0x22)
        self.assertEqual(both["kind"], "positive")

    def test_ranges(self):
        self.assertEqual(obdscan.parse_ranges("F100-F1FF, 2813"),
                         [(0xF100, 0xF1FF), (0x2813, 0x2813)])


class ScanTests(unittest.TestCase):
    def scan(self, **overrides):
        cfg = dict(obdscan.DEFAULTS)
        cfg.update({"modules": "720-77F", "did_ranges": "F190,2810-2820,DD01"})
        cfg.update(overrides)
        fake = FakeElm()
        self.tmp = tempfile.mkdtemp()
        path = os.path.join(self.tmp, "scan.jsonl")
        log = obdscan.ScanLog(path)
        summary = obdscan.Scanner(Elm327(fake), log, cfg).run()
        log.close()
        with open(path, encoding="utf-8") as fh:
            records = [json.loads(line) for line in fh]
        return fake, summary, records

    def test_finds_modules_pids_and_dids(self):
        fake, summary, records = self.scan()
        self.assertEqual(summary["protocol"], "6")
        self.assertIn("0C", summary["pids"])
        self.assertEqual(sorted(summary["modules"]), ["726", "760"])
        body = summary["modules"]["726"]
        self.assertEqual(body["responders"], ["72E"])
        self.assertEqual(body["vin"], VIN.decode())
        self.assertEqual(body["dids"]["2813"], "0e10")
        self.assertEqual(body["refusals"].get("33"), 1)  # 2820 locked
        self.assertEqual(summary["modules"]["760"]["vin_nrc"], "31")
        self.assertEqual(records[-1]["step"], "summary")
        self.assertTrue(any(v.startswith("MNAU") for v in summary["mode09"].values()))

    def test_only_read_services_sent(self):
        fake, _, _ = self.scan()
        for cmd in fake.requests:
            if cmd.startswith(("AT", "ST")) or not cmd:
                continue
            self.assertIn(int(cmd[:2], 16), obdscan.READ_ONLY_SERVICES, cmd)

    def test_refuses_write_services(self):
        log = obdscan.ScanLog(os.path.join(tempfile.mkdtemp(), "x.jsonl"))
        s = obdscan.Scanner(Elm327(FakeElm()), log, dict(obdscan.DEFAULTS))
        for service in (0x04, 0x10, 0x11, 0x14, 0x27, 0x2E, 0x31):
            with self.assertRaises(ValueError):
                s.request(0x7E0, bytes([service, 0x01]), "test")
        log.close()

    def test_time_budget(self):
        _, summary, _ = self.scan(did_ranges="0000-FFFF", per_module_minutes="0")
        for info in summary["modules"].values():
            self.assertFalse(info["did_scan_complete"])

    def test_config_file(self):
        path = os.path.join(tempfile.mkdtemp(), "obd_scan.cfg")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("link = wifi  # adapter\nhost=10.0.0.5\ndid_ranges=everything\nbogus=1\n")
        cfg = obdscan.load_config(path)
        self.assertEqual(cfg["link"], "wifi")
        self.assertEqual(cfg["host"], "10.0.0.5")
        self.assertEqual(cfg["did_ranges"], "0000-FFFF")
        self.assertNotIn("bogus", cfg)


if __name__ == "__main__":
    unittest.main()
