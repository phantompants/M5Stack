import unittest

from jp226cp0 import nmea
from jp226cp0.nmea import GpsState, checksum, make_sentence, parse_line

# Textbook sentences (checksums as published in the NMEA examples).
GGA = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"
RMC = "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A"
GSA = "$GPGSA,A,3,04,05,,09,12,,,24,,,,,2.5,1.3,2.1*39"
VTG = "$GPVTG,054.7,T,034.4,M,005.5,N,010.2,K*48"


class ChecksumTests(unittest.TestCase):
    def test_known_checksums(self):
        for sentence in (GGA, RMC, GSA, VTG):
            body, given = sentence[1:].split("*")
            self.assertEqual("{:02X}".format(checksum(body)), given, sentence)

    def test_make_sentence_roundtrip(self):
        s = make_sentence("GNRMC,010203.00,A,3229.61600,S,13746.29600,E,1.00,,201026,,,A")
        self.assertIsNotNone(parse_line(s))

    def test_bad_checksum_rejected(self):
        self.assertIsNone(parse_line(GGA[:-2] + "00"))
        self.assertIsNone(parse_line("$GPGGA,1,2,3"))          # no checksum
        self.assertIsNone(parse_line("GPGGA,1*00"))            # no $
        self.assertIsNone(parse_line("$GPGGA,1*ZZ"))
        self.assertIsNone(parse_line(""))

    def test_parse_line_fields(self):
        s = parse_line(GGA + "\r\n")
        self.assertEqual((s.talker, s.kind), ("GP", "GGA"))
        self.assertEqual(s.fields[0], "123519")

    def test_proprietary_sentences_are_ignored_not_errors(self):
        self.assertIsNone(parse_line(make_sentence("PCAS03,1,1,1,1,1,1,1,1")))


class CoordinateTests(unittest.TestCase):
    def test_conversion_and_signs(self):
        self.assertAlmostEqual(nmea.parse_coordinate("4807.038", "N", 90), 48.1173, 5)
        self.assertAlmostEqual(nmea.parse_coordinate("01131.000", "E", 180), 11.516667, 5)
        self.assertAlmostEqual(nmea.parse_coordinate("3229.61600", "S", 90), -32.493600, 5)
        self.assertAlmostEqual(nmea.parse_coordinate("13746.29600", "W", 180), -137.771600, 5)

    def test_invalid(self):
        self.assertIsNone(nmea.parse_coordinate("", "N", 90))
        self.assertIsNone(nmea.parse_coordinate("4807.038", "", 90))
        self.assertIsNone(nmea.parse_coordinate("9507.038", "N", 90))
        self.assertIsNone(nmea.parse_coordinate("4875.000", "N", 90))   # minutes >= 60

    def test_format_latlon_roundtrip(self):
        for lat, lon in ((-32.4936, 137.7716), (51.5, -0.12), (-12.46, 130.84)):
            ls, ns = nmea.format_latlon(lat, True)
            os_, ew = nmea.format_latlon(lon, False)
            self.assertAlmostEqual(nmea.parse_coordinate(ls, ns, 90), lat, 5)
            self.assertAlmostEqual(nmea.parse_coordinate(os_, ew, 180), lon, 5)


class StateTests(unittest.TestCase):
    def test_all_four_sentences(self):
        g = GpsState()
        for line in (RMC, GGA, GSA, VTG):
            g.feed_line(line, 1000)
        self.assertAlmostEqual(g.latitude, 48.1173, 4)
        self.assertEqual(g.satellites, 8)
        self.assertAlmostEqual(g.hdop, 0.9)
        self.assertAlmostEqual(g.altitude_m, 545.4)
        self.assertAlmostEqual(g.speed_kmh, 5.5 * 1.852, 3)   # VTG came last
        self.assertAlmostEqual(g.course_deg, 54.7)
        self.assertAlmostEqual(g.vdop, 2.1)
        self.assertEqual(g.counts, {"RMC": 1, "GGA": 1, "GSA": 1, "VTG": 1})
        # RMC date 23-03-94 is year 2094; time 12:35:19 UTC.
        self.assertEqual(g.utc_epoch_s % 86400, 12 * 3600 + 35 * 60 + 19)

    def test_rmc_speed_in_knots_to_kmh(self):
        g = GpsState()
        g.feed_line(RMC, 5)
        self.assertAlmostEqual(g.speed_kmh, 22.4 * 1.852, 3)
        self.assertAlmostEqual(g.course_deg, 84.4)

    def test_no_fix_does_not_update_position(self):
        g = GpsState()
        g.feed_line(make_sentence("GNRMC,010203.00,V,,,,,,,201026,,,N"), 10)
        self.assertIsNone(g.location_ms)
        self.assertIsNone(g.speed_ms)
        self.assertIsNotNone(g.datetime_ms)            # time/date still taken
        g.feed_line(make_sentence("GNGGA,010203.00,,,,,0,00,99.99,,,,,,"), 10)
        self.assertIsNone(g.location_ms)
        self.assertEqual(g.satellites, 0)               # GGA always updates sats
        self.assertIsNotNone(g.satellites_ms)

    def test_vtg_with_invalid_mode_ignored(self):
        g = GpsState()
        g.feed_line(make_sentence("GNVTG,,T,,M,,N,,K,N"), 10)
        self.assertIsNone(g.speed_ms)

    def test_gsa_vdop_requires_fix_type(self):
        g = GpsState()
        g.feed_line(make_sentence("GNGSA,A,1,,,,,,,,,,,,,99.99,99.99,99.99,1"), 10)
        self.assertIsNone(g.vdop_ms)
        g.feed_line(make_sentence("GNGSA,A,3,01,03,06,09,11,14,17,19,22,,,,1.5,0.9,1.2,1"), 20)
        self.assertEqual(g.vdop, 1.2)

    def test_bad_sentences_counted(self):
        g = GpsState()
        g.feed_line(GGA[:-2] + "00", 1)
        g.feed_line("garbage", 1)
        g.feed_line(GGA, 1)
        self.assertEqual((g.sentences_ok, g.sentences_bad), (1, 1))


if __name__ == "__main__":
    unittest.main()
