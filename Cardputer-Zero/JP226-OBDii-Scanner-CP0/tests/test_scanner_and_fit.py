import json
import os
import tempfile
import unittest

from jp226obd import compatibility, obd_pids, scanner
from jp226obd.elm327 import Elm327
from jp226obd.fake_elm import FakeElm
from jp226obd.live import LiveReader


class PidDecodingTests(unittest.TestCase):
    def test_standard_pid_decoders(self):
        self.assertEqual(obd_pids.decode_pid(0x0C, bytes([0x1A, 0xF8])), 1726.0)
        self.assertEqual(obd_pids.decode_pid(0x0D, bytes([82])), 82.0)
        self.assertEqual(obd_pids.decode_pid(0x05, bytes([128])), 88.0)
        self.assertEqual(obd_pids.decode_pid(0x42, bytes([0x37, 0x28])), 14.12)
        self.assertEqual(obd_pids.decode_pid(0x62, bytes([145])), 20.0)

    def test_multi_pid_decode(self):
        # 0D=50 km/h, 49=51/255 (~20.0%), 0C=1500 rpm (0x1770)
        payload = bytes([0x0D, 50, 0x49, 51, 0x0C, 0x17, 0x70])
        out = obd_pids.decode_multi_pid(payload)
        self.assertEqual(out[0x0D], 50.0)
        self.assertEqual(out[0x49], 20.0)
        self.assertEqual(out[0x0C], 1500.0)

    def test_dtc_decoding(self):
        self.assertEqual(
            obd_pids.decode_dtcs(bytes([0x02, 0x20, 0x02, 0xC1, 0x00])),
            ["P2002", "U0100"],
        )

    def test_ford_tpms_decoding(self):
        self.assertEqual(obd_pids.decode_ford_tpms(0x2813, bytes([0x02, 0xD0])), 36.0)
        self.assertIsNone(obd_pids.decode_ford_tpms(0x2813, bytes([0xFF, 0xFF])))


class ScannerAndCompatibilityTests(unittest.TestCase):
    def _run_profile(self, profile_name: str, scan_mode: str = "ev_probe"):
        cfg = dict(scanner.DEFAULTS)
        cfg["scan_mode"] = scan_mode
        cfg["benchmark_samples"] = "3"
        fake = FakeElm(profile=profile_name)
        tmp = tempfile.mkdtemp()
        log_path = os.path.join(tmp, "scan.jsonl")
        log = scanner.ScanLog(log_path)
        summary = scanner.Scanner(Elm327(fake), log, cfg).run()
        log.close()
        prof_path, prof = scanner.save_profile(summary, fake.name, tmp)
        return fake, summary, prof, prof_path

    def test_ranger_logger_and_tpms_fit(self):
        fake, summary, prof, prof_path = self._run_profile("ranger", "quick")
        self.assertTrue(os.path.isfile(prof_path))
        self.assertEqual(prof["vehicle"]["powertrain_hint"], "ICE")
        lf = prof["logger_fit"]
        self.assertEqual(lf["supported_count"], 12)
        self.assertTrue(lf["mil_supported"])
        self.assertTrue(lf["dtc_supported"])
        self.assertTrue(lf["tpms_ready"])
        self.assertEqual(lf["tpms_pressure_count"], 4)
        self.assertIn("P2002", prof["dtcs"]["stored"])
        self.assertIn("P0299", prof["dtcs"]["pending"])
        self.assertTrue(prof["ev_sound_fit"]["multi_pid_supported"])
        # Confirm read-only safety across all issued commands
        for cmd in fake.requests:
            if not cmd or cmd.startswith(("AT", "ST")):
                continue
            self.assertIn(int(cmd[:2], 16), scanner.READ_ONLY_SERVICES, cmd)

    def test_ev_profile_fit(self):
        _, _, prof, _ = self._run_profile("ev", "ev_probe")
        ev = prof["ev_sound_fit"]
        self.assertEqual(prof["vehicle"]["powertrain_hint"], "BEV")
        self.assertEqual(ev["grade"], "READY")
        self.assertEqual(ev["available_channels"], 5)
        self.assertIn("0D", ev["recommended_fast_pids"])
        self.assertIn("49", ev["recommended_fast_pids"])
        self.assertTrue(ev["multi_pid_supported"])
        self.assertIn("7E4", prof["modules"])
        self.assertIn("7E5", prof["modules"])

    def test_hybrid_profile_fit(self):
        _, _, prof, _ = self._run_profile("hybrid", "ev_probe")
        self.assertEqual(prof["vehicle"]["powertrain_hint"], "HYBRID")
        self.assertEqual(prof["ev_sound_fit"]["grade"], "READY")
        self.assertGreaterEqual(prof["logger_fit"]["supported_count"], 10)

    def test_refuses_write_services(self):
        tmp = tempfile.mkdtemp()
        log = scanner.ScanLog(os.path.join(tmp, "s.jsonl"))
        sc = scanner.Scanner(Elm327(FakeElm()), log, dict(scanner.DEFAULTS))
        for svc in (0x04, 0x10, 0x11, 0x14, 0x27, 0x2E, 0x2F, 0x31):
            with self.assertRaises(ValueError):
                sc.request(0x7E0, bytes([svc, 0x01]), "test")
        log.close()

    def test_live_reader_multi_pid(self):
        elm = Elm327(FakeElm(profile="ev"))
        elm.init("6")
        reader = LiveReader(elm, [0x0D, 0x49, 0x62, 0x5B], multi_pid=True)
        reader.poll_once()
        snap = reader.snapshot()
        self.assertEqual(snap["samples"], 1)
        self.assertIn("SPEED", snap["values"])
        self.assertIn("PEDAL_D", snap["values"])
        self.assertIn("ACT_TRQ", snap["values"])
        self.assertIn("HV_SOC", snap["values"])


if __name__ == "__main__":
    unittest.main()
