"""AtomS3 USB bridge link: line format, state, outbound lines and the app."""

import base64
import tempfile
import threading
import time
import unittest

from jp226cp0 import bridge, espnow, gps_power
from jp226cp0.app import LoggerApp
from jp226cp0.clock import Clock
from jp226cp0.settings import Settings
from tests.helpers import nmea_burst

# Captured from the AtomS3 on the CardputerZero's /dev/ttyACM0, 2026-11-26.
REAL_BRIDGE = "$PJP226,BRIDGE,v0.20,89f11c7+,1,0,9600,1,1*1F"
REAL_RMC = "$GNRMC,,V,,,,,,,,,,N*4D"


class LineFormatTests(unittest.TestCase):
    def test_frame_matches_the_bridge_checksum(self):
        self.assertEqual(bridge.frame("PJP226,BRIDGE,v0.20,89f11c7+,1,0,9600,1,1"),
                         REAL_BRIDGE + "\r\n")

    def test_parse_accepts_only_valid_bridge_lines(self):
        self.assertEqual(bridge.parse(REAL_BRIDGE)[0], "BRIDGE")
        self.assertIsNone(bridge.parse(REAL_BRIDGE[:-2] + "00"))
        self.assertIsNone(bridge.parse(REAL_RMC))
        self.assertTrue(bridge.is_bridge_line(REAL_BRIDGE))
        self.assertFalse(bridge.is_bridge_line(REAL_RMC))

    def test_outbound_bodies_match_the_firmware_parser(self):
        self.assertEqual(bridge.hello_body("2026.11.03"),
                         "PJP226,HELLO,2026.11.03,C")
        self.assertEqual(bridge.time_body(1764100000, 600, 3),
                         "PJP226,TIME,1764100000,600,3,-1,0")
        self.assertEqual(bridge.gps_body(False), "PJP226,GPS,0")


class StateTests(unittest.TestCase):
    def test_bridge_status_line(self):
        state = bridge.BridgeState()
        self.assertFalse(state.connected(0))
        self.assertEqual(state.status_text(0), "NO ATOMS3")
        self.assertIsNotNone(state.handle(REAL_BRIDGE, 1000))
        self.assertEqual((state.version, state.git, state.channel, state.devices,
                          state.gps_baud, state.gps_live, state.forwarding),
                         ("v0.20", "89f11c7+", 1, 0, 9600, True, True))
        self.assertEqual(state.status_text(2000), "v0.20 CH1 0 DEV")
        self.assertEqual(state.gps_text(2000), "OK ATOMS3 GPS@9600")
        self.assertFalse(state.connected(1000 + bridge.LINK_TIMEOUT_MS + 1))

    def test_rx_is_counted_and_bad_lines_are_rejected(self):
        state = bridge.BridgeState()
        self.assertIsNotNone(state.handle(
            bridge.frame("PJP226,RX,AABBCCDDEEFF,-60,SlAyMg=="), 0))
        self.assertEqual(state.rx_packets, 1)
        self.assertIsNone(state.handle("$PJP226,BRIDGE*00", 0))
        self.assertIsNone(state.handle(bridge.frame("PJP226,WHAT"), 0))
        self.assertEqual(state.bad_lines, 2)


class OutboxTests(unittest.TestCase):
    def test_hello_and_time_once_a_second(self):
        box = bridge.BridgeOutbox("2026.11.03")
        state = bridge.BridgeState()
        first = box.due(0, state, False, 1764100000, 36000)
        self.assertEqual(first, ["PJP226,HELLO,2026.11.03,C",
                                 "PJP226,TIME,1764100000,600,3,-1,0"])
        self.assertEqual(box.due(500, state, False, None, None), [])
        later = box.due(1000, state, False, None, None)
        self.assertEqual(later[1], "PJP226,TIME,0,0,0,-1,0")

    def test_gps_switch_only_while_the_bridge_disagrees(self):
        box = bridge.BridgeOutbox("v")
        state = bridge.BridgeState()
        self.assertNotIn("PJP226,GPS,0", box.due(0, state, False, None, None))
        state.handle(REAL_BRIDGE, 0)                      # forwarding = 1
        self.assertIn("PJP226,GPS,0", box.due(100, state, False, None, None))
        self.assertNotIn("PJP226,GPS,0", box.due(200, state, False, None, None))
        self.assertIn("PJP226,GPS,0", box.due(2100, state, False, None, None))
        self.assertNotIn("PJP226,GPS,1", box.due(2200, state, True, None, None))


class FindDeviceTests(unittest.TestCase):
    def test_by_id_link_first_then_ttyacm0(self):
        found = bridge.find_device(
            ("/dev/serial/by-id/usb-Espressif*", "/dev/ttyACM0"),
            exists=lambda p: True,
            glob_fn=lambda pat: ["/dev/serial/by-id/usb-Espressif_x-if00"])
        self.assertEqual(found, "/dev/serial/by-id/usb-Espressif_x-if00")
        self.assertEqual(bridge.find_device(
            ("/dev/serial/by-id/usb-Espressif*", "/dev/ttyACM0"),
            exists=lambda p: p == "/dev/ttyACM0", glob_fn=lambda pat: []),
            "/dev/ttyACM0")
        self.assertEqual(bridge.find_device(("/dev/ttyACM0",),
                                            exists=lambda p: False), "")


class FakeBridgePort:
    def __init__(self):
        self.written = []
        self._data = (REAL_BRIDGE + "\r\n" + REAL_RMC + "\r\n").encode()

    def read(self, n):
        chunk, self._data = self._data[:n], self._data[n:]
        return chunk

    def write(self, data):
        self.written.append(data)

    def close(self):
        pass


class LinkThreadTests(unittest.TestCase):
    def test_reads_lines_and_writes_framed_bodies(self):
        port = FakeBridgePort()
        link = bridge.BridgeLink(lambda: 7, open_fn=lambda d, b: port,
                                 find_fn=lambda: "/dev/ttyACM0")
        link.start()
        try:
            got = [link.lines.get(timeout=2), link.lines.get(timeout=2)]
            self.assertEqual(got, [(7, REAL_BRIDGE), (7, REAL_RMC)])
            link.send("PJP226,GPS,1")
            deadline = time.monotonic() + 2
            while not port.written and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(port.written, [b"$PJP226,GPS,1*09\r\n"])
        finally:
            link.stop()
            link.join(timeout=2)

    def test_waits_while_not_plugged_in(self):
        link = bridge.BridgeLink(lambda: 0, open_fn=lambda d, b: None,
                                 find_fn=lambda: "")
        link.start()
        time.sleep(0.1)
        self.assertEqual(link.status, "NOT PLUGGED IN")
        link.send("PJP226,GPS,1")                         # dropped, no port
        link.stop()
        link.join(timeout=3)
        self.assertFalse(link.is_alive())


class AppTests(unittest.TestCase):
    def make_app(self, tmp, source):
        return LoggerApp(Settings(gps_source=source), tmp, clock=Clock("gps"))

    def test_bridge_nmea_feeds_gps_only_for_the_atom_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_app(tmp, "cap")
            app.feed_bridge_line(REAL_BRIDGE, 0)
            app.feed_bridge_line(REAL_RMC, 0)
            self.assertEqual(app.gps.sentences_ok, 0)
            self.assertTrue(app.bridge.connected(0))
            self.assertEqual(app.ui_model(0)["bridge_status"], "v0.20 CH1 0 DEV")
            # The Cap supplies GPS, so the bridge is told to stop forwarding.
            self.assertIn("PJP226,GPS,0", app.bridge_lines_due(0))
            app.close()
            app = self.make_app(tmp, "atom")
            self.assertEqual(app.gps_source, gps_power.ATOM)
            app.feed_bridge_line(REAL_RMC, 0)
            self.assertEqual(app.gps.sentences_ok, 1)
            app.close()

    def test_no_bridge_shows_the_link_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_app(tmp, "atom")
            app.bridge_link_status = "ATOMS3 NOT PLUGGED IN"
            self.assertEqual(app.ui_model(0)["bridge_status"],
                             "ATOMS3 NOT PLUGGED IN")
            app.close()


DIAL_MAC = "48CA433A1B2C"


def rx_line(packet, mac=DIAL_MAC):
    return bridge.frame("PJP226,RX,{},-55,{}".format(
        mac, base64.b64encode(packet).decode()))


def tx_packets(bodies, kind):
    """Decoded packets of one type from TX bodies, with their MAC."""
    found = []
    for body in bodies:
        fields = body.split(",")
        if fields[:2] == ["PJP226", "TX"]:
            data = base64.b64decode(fields[3])
            if espnow.packet_type(data) == kind:
                found.append((fields[2], data))
    return found


class PacketTests(unittest.TestCase):
    def test_sizes_match_the_esp32_compiler(self):
        # sizeof() from xtensa-esp-elf-g++ on shared/TelemetryProtocol.h.
        self.assertEqual(espnow.SIZES, {espnow.DIAL_COMMAND: 56,
                                        espnow.DIAL_STATUS: 152,
                                        espnow.DIAL_ACK: 44,
                                        espnow.POSITION_REPORT: 100,
                                        espnow.TEMPERATURE_REPORT: 44})

    def test_crc_matches_the_firmware(self):
        # telemetry::crc32 is the IEEE CRC-32: "123456789" -> 0xCBF43926.
        self.assertEqual(espnow.crc32(b"123456789"), 0xCBF43926)

    def test_command_round_trip_and_corruption(self):
        packet = espnow.encode_command(7, espnow.MARK_WAYPOINT, 0.0, "CAMP")
        self.assertEqual(espnow.decode_command(packet),
                         (7, espnow.MARK_WAYPOINT, 0.0, "CAMP"))
        broken = packet[:20] + b"X" + packet[21:]
        self.assertIsNone(espnow.decode_command(broken))
        self.assertIsNone(espnow.decode_command(packet[:-1]))

    def test_position_carries_pitch_and_roll(self):
        from jp226cp0.imu import ImuSample
        packet = espnow.encode_position(
            1, None, 0, True, True, 9, 1, -32.5, 137.8, 10.0, 0.0, 0.0, 0.9,
            imu=ImuSample(pitch_deg=12.5, roll_deg=-4.0))
        report = espnow.decode_position(packet)
        self.assertTrue(report["flags"] & espnow.POSITION_IMU_VALID)
        self.assertAlmostEqual(report["pitch_deg"], 12.5, places=4)
        self.assertAlmostEqual(report["roll_deg"], -4.0, places=4)
        plain = espnow.decode_position(espnow.encode_position(
            1, None, 0, False, False, 0, 0, 0, 0, 0, 0, 0, 0))
        self.assertFalse(plain["flags"] & espnow.POSITION_IMU_VALID)

    def test_status_carries_the_cardputer_zero_board(self):
        packet = espnow.encode_status(
            1, 1000, 60, 42.5, 36, 45, True, 9, 1, "GRAVEL", "SOFT",
            "NORMAL", "HEAVY", "A VERY LONG PLACE NAME THAT DOES NOT FIT",
            "WP00003", road_surface="WET")
        status = espnow.decode_status(packet)
        self.assertEqual(status["logger_board"], espnow.BOARD_CARDPUTER_ZERO)
        self.assertEqual(status["road"], "GRAVEL")
        self.assertEqual(status["road_surface"], 3)
        self.assertEqual(len(status["place"]), 31)          # room for the NUL
        self.assertEqual(status["last_waypoint"], "WP00003")


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.calls = []

    def execute(self, action, value, text):
        self.calls.append((action, value, text))
        return True, "WP00001"

    def fields(self, packet):
        return bridge.parse(rx_line(packet))

    def test_heartbeat_pairs_without_an_ack(self):
        controllers = bridge.Controllers()
        out = controllers.handle_rx(
            self.fields(espnow.encode_command(1, espnow.HEARTBEAT)), 0,
            self.execute)
        self.assertEqual(out, [])
        self.assertEqual(controllers.fresh(0), [DIAL_MAC])
        self.assertEqual(controllers.fresh(bridge.CONTROLLER_FRESH_MS + 1), [])

    def test_command_runs_once_and_repeats_get_the_same_ack(self):
        controllers = bridge.Controllers()
        command = self.fields(
            espnow.encode_command(5, espnow.MARK_WAYPOINT, 0, "FUEL"))
        first = controllers.handle_rx(command, 0, self.execute)
        again = controllers.handle_rx(command, 100, self.execute)
        self.assertEqual(self.calls, [(espnow.MARK_WAYPOINT, 0.0, "FUEL")])
        self.assertEqual(first, again)
        mac, ack = tx_packets(first, espnow.DIAL_ACK)[0]
        self.assertEqual(mac, DIAL_MAC)
        self.assertEqual(espnow.decode_ack(ack), (5, True, "WP00001"))

    def test_status_every_500_ms_and_position_every_second(self):
        controllers = bridge.Controllers()
        controllers.handle_rx(
            self.fields(espnow.encode_command(1, espnow.HEARTBEAT)), 0,
            self.execute)
        status = lambda: b"S"
        position = lambda: b"P"
        self.assertEqual(len(controllers.due(0, status, position)), 2)
        self.assertEqual(len(controllers.due(500, status, position)), 1)
        self.assertEqual(len(controllers.due(1000, status, position)), 2)
        self.assertEqual(controllers.due(9000, status, position), [])


class DialAppTests(unittest.TestCase):
    def make_app(self, tmp):
        return LoggerApp(Settings(gps_source="atom"), tmp, clock=Clock("gps"))

    def send(self, app, packet, now_ms):
        app.feed_bridge_line(rx_line(packet), now_ms)
        return app.bridge_lines_due(now_ms)

    def test_dial_changes_the_vehicle_setup(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_app(tmp)
            out = self.send(app, espnow.encode_command(
                1, espnow.SET_ROAD, 0, "GRAVEL"), 0)
            self.assertEqual(espnow.decode_ack(
                tx_packets(out, espnow.DIAL_ACK)[0][1]), (1, True, ""))
            self.send(app, espnow.encode_command(
                2, espnow.SET_TYRE_REAR, 28.4), 10)
            self.send(app, espnow.encode_command(
                3, espnow.SET_SURFACE, 0, "WET"), 20)
            bad = self.send(app, espnow.encode_command(
                4, espnow.SET_LOAD, 0, "TONNE"), 30)
            self.assertFalse(espnow.decode_ack(
                tx_packets(bad, espnow.DIAL_ACK)[0][1])[1])
            self.assertEqual(
                (app.vehicle["road_type"], app.vehicle["tyre_set_rl_psi"],
                 app.vehicle["tyre_set_rr_psi"], app.vehicle["road_surface"],
                 app.vehicle["vehicle_load"]),
                ("GRAVEL", 28, 28, "WET", "LIGHT"))
            app.close()
            again = self.make_app(tmp)                      # saved
            self.assertEqual(again.vehicle["road_type"], "GRAVEL")
            again.close()

    def test_dial_waypoint_and_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_app(tmp)
            app.camera.command = None          # no rpicam-still, as on a desktop
            now = 0
            for t in range(4):
                for line in nmea_burst(t, kmh=40.0):
                    app.feed_bridge_line(line, now)
                app.tick(now)
                now += 1000
            out = self.send(app, espnow.encode_command(
                9, espnow.MARK_WAYPOINT, 0, "CAMP"), now)
            ack = espnow.decode_ack(tx_packets(out, espnow.DIAL_ACK)[0][1])
            self.assertEqual(ack[:2], (9, True))
            self.assertEqual(app.waypoints.last["id"], ack[2])
            self.assertEqual(app.waypoints.last["category"], "CAMP")
            status = espnow.decode_status(
                tx_packets(out, espnow.DIAL_STATUS)[0][1])
            self.assertTrue(status["fix_valid"])
            self.assertAlmostEqual(status["speed_kmh"], 40.0, places=1)
            self.assertEqual(status["last_waypoint"], ack[2])
            self.assertEqual(status["logger_board"],
                             espnow.BOARD_CARDPUTER_ZERO)
            position = espnow.decode_position(
                tx_packets(out, espnow.POSITION_REPORT)[0][1])
            self.assertTrue(position["flags"] & espnow.POSITION_FIX)
            self.assertAlmostEqual(position["latitude"], -32.4936, places=4)
            app.close()

    def test_no_dial_no_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_app(tmp)
            out = app.bridge_lines_due(0)
            self.assertFalse(tx_packets(out, espnow.DIAL_STATUS))
            app.close()



def discovery_packet(board):
    """A DiscoveryPacket (30 bytes) whose time_flags carry `board`."""
    import struct
    body = struct.pack("<IBBHIIIhBbH", espnow.MAGIC, espnow.VERSION,
                       espnow.DISCOVERY, 30, 1, 1000, 0, 0, board << 4, -1, 0)
    return body + struct.pack("<I", espnow.crc32(body))


class DeviceTableTests(unittest.TestCase):
    def fields(self, packet, mac, rssi=-60):
        return bridge.parse(bridge.frame("PJP226,RX,{},{},{}".format(
            mac, rssi, base64.b64encode(packet).decode())))

    def test_types_from_what_each_device_sends(self):
        table = bridge.DeviceTable()
        table.note(self.fields(discovery_packet(2), "24587C5AE6CC"), 0)
        table.note(self.fields(espnow.encode_command(1, espnow.HEARTBEAT),
                               DIAL_MAC, -48), 1000)
        table.note(self.fields(espnow.encode_temperature(1, 3, [(1, 5.0)]),
                               "4C7525A23468"), 2000)
        rows = table.rows(3000, paired={DIAL_MAC})
        self.assertEqual([r[0] for r in rows], ["BLACKBOX", "DIAL/ECHO", "CP ADV"])
        self.assertEqual(rows[1], ("DIAL/ECHO", DIAL_MAC, -48, 2000, True))
        # A temperature report from the Dial names it properly.
        table.note(self.fields(espnow.encode_temperature(2, 1, [(9, 21.0)]),
                               DIAL_MAC), 3500)
        table.note(self.fields(espnow.encode_command(2, espnow.HEARTBEAT),
                               DIAL_MAC), 4000)
        self.assertEqual(table.rows(4000)[0][0], "M5DIAL")

    def test_bad_packets_ignored_and_old_devices_forgotten(self):
        table = bridge.DeviceTable()
        table.note(["RX", "AABBCCDDEEFF", "-50", "bm90IGEgcGFja2V0"], 0)
        self.assertEqual(table.rows(0), [])
        table.note(self.fields(discovery_packet(4), "AABBCCDDEEFF"), 0)
        self.assertEqual(table.rows(1000)[0][0], "CP ZERO")
        self.assertEqual(table.rows(bridge.DEVICE_FORGET_MS + 1), [])


if __name__ == "__main__":
    unittest.main()
