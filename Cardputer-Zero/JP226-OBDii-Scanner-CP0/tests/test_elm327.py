import socket
import threading
import unittest

from jp226obd import elm327
from jp226obd.elm327 import Elm327, classify, parse_frames


class FrameParsingTests(unittest.TestCase):
    def test_single_and_multiframe_11bit(self):
        self.assertEqual(
            parse_frames(["7E8 06 41 00 BE 3F A8 13"]),
            {"7E8": bytes.fromhex("4100BE3FA813")},
        )
        lines = [
            "7E8 10 14 49 02 01 4D 4E 41",
            "7E8 21 55 4D 46 46 35 30 50",
            "7E8 22 57 30 30 30 30 30 30",
        ]
        data = parse_frames(lines)["7E8"]
        self.assertEqual(len(data), 0x14)
        self.assertEqual(data[3:], b"MNAUMFF50PW000000")

    def test_29bit_can_headers(self):
        frames = parse_frames(["18DAF110 04 41 0C 1A F8"])
        self.assertEqual(frames, {"18DAF110": bytes.fromhex("410C1AF8")})

    def test_classify_variants(self):
        self.assertEqual(classify(["NO DATA"], 0x22)["kind"], "no_data")
        self.assertEqual(classify(["CAN ERROR"], 0x22)["kind"], "error")
        self.assertEqual(classify(["TIMEOUT"], 0x22)["kind"], "error")
        nrc = classify(["72E 03 7F 22 31"], 0x22)
        self.assertEqual(nrc["kind"], "nrc")
        self.assertEqual(nrc["replies"]["72E"]["nrc_name"], "requestOutOfRange")
        both = classify(["7E8 03 7F 22 31", "72E 05 62 28 13 02 D0"], 0x22)
        self.assertEqual(both["kind"], "positive")


class TcpTransportTests(unittest.TestCase):
    def test_wifi_tcp_round_trip(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def _serve():
            conn, _ = srv.accept()
            with conn:
                conn.recv(128)
                conn.sendall(b"ELM327 v1.5\r\r>")
                conn.recv(128)
                conn.sendall(b"7E8 04 41 0D 3C\r\r>")

        t = threading.Thread(target=_serve, daemon=True)
        t.start()
        try:
            tr = elm327.tcp_transport("127.0.0.1", port, timeout_s=2.0)
            try:
                e = Elm327(tr)
                self.assertIn("ELM327 v1.5", e.command("ATI"))
                res = classify(e.command("010D"), 0x01)
                self.assertEqual(res["kind"], "positive")
                self.assertEqual(res["replies"]["7E8"]["data"], "0d3c")
            finally:
                tr.close()
        finally:
            srv.close()
            t.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
