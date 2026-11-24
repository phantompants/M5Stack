import unittest

from jp226cp0.nmea import GpsState
from jp226cp0.plausibility import Plausibility
from jp226cp0.snapshot import take_gps_snapshot

from .helpers import nmea_burst


def fed(t=0, now_ms=0, **kw):
    state = GpsState()
    for line in nmea_burst(t, **kw):
        state.feed_line(line, now_ms)
    return state


class FixGateTests(unittest.TestCase):
    def snap(self, state, now_ms, guard=None):
        return take_gps_snapshot(state, guard or Plausibility(), now_ms)

    def test_good_fix_is_valid(self):
        s = self.snap(fed(kmh=50), 100)
        self.assertTrue(s.fix_valid)
        self.assertAlmostEqual(s.speed_kmh, 50.0, 1)
        self.assertEqual(s.satellites, 9)
        self.assertAlmostEqual(s.vdop, 1.2)
        self.assertTrue(s.utc_valid)
        self.assertGreater(s.utc_epoch_ms, 0)

    def test_satellite_threshold(self):
        self.assertFalse(self.snap(fed(sats=3), 0).fix_valid)
        self.assertTrue(self.snap(fed(sats=4), 0).fix_valid)

    def test_hdop_threshold(self):
        self.assertTrue(self.snap(fed(hdop=5.0), 0).fix_valid)
        self.assertFalse(self.snap(fed(hdop=5.1), 0).fix_valid)

    def test_position_and_speed_freshness(self):
        state = fed()
        self.assertTrue(self.snap(state, 3000).fix_valid)    # age == limit
        s = self.snap(state, 3001)                            # one ms too old
        self.assertFalse(s.fix_valid)
        self.assertFalse(s.position_fresh)
        self.assertFalse(s.speed_fresh)

    def test_date_time_freshness(self):
        state = fed()
        # Keep position fresh with a later GGA, but not the RMC date/time pair.
        for line in nmea_burst(0):
            if "GGA" in line:
                state.feed_line(line, 5001)
        s = self.snap(state, 5001)
        self.assertFalse(s.utc_valid)
        self.assertFalse(s.fix_valid)

    def test_date_time_age_limit_is_5s(self):
        state = fed()
        # Keep speed fresh via VTG so only the RMC date/time can fail.
        for line in nmea_burst(0):
            if "GGA" in line or "VTG" in line:
                state.feed_line(line, 5000)
        self.assertTrue(self.snap(state, 5000).fix_valid)
        for line in nmea_burst(0):
            if "GGA" in line or "VTG" in line:
                state.feed_line(line, 5001)
        self.assertFalse(self.snap(state, 5001).fix_valid)

    def test_no_fix_status_is_invalid(self):
        s = self.snap(fed(status="V", gga_quality=0), 0)
        self.assertFalse(s.fix_valid)
        self.assertFalse(s.position_fresh)

    def test_missing_gga_means_no_satellite_info(self):
        state = GpsState()
        state.feed_line(nmea_burst(0)[0], 0)      # RMC only
        self.assertFalse(self.snap(state, 0).fix_valid)

    def test_fix_age_none_until_first_position(self):
        self.assertIsNone(self.snap(GpsState(), 0).fix_age_ms)
        self.assertEqual(self.snap(fed(), 250).fix_age_ms, 250)

    def test_implausible_year_gives_no_clock_time(self):
        state = GpsState()
        for line in nmea_burst(0, start=(2006, 1, 1, 0, 0, 0)):
            state.feed_line(line, 0)
        s = self.snap(state, 0)
        self.assertEqual(s.utc_epoch_ms, 0)       # the clock must not sync to this

    def test_plausibility_guard_marks_teleport_invalid(self):
        guard = Plausibility()
        a = fed(0, 0, lat=-33.0, lon=151.0)
        self.assertTrue(take_gps_snapshot(a, guard, 100).fix_valid)
        b = fed(1, 1000, lat=-33.0035, lon=151.0)      # ~390 m in one second
        self.assertFalse(take_gps_snapshot(b, guard, 1100).fix_valid)

    def test_altitude_spike_only_blanks_altitude(self):
        guard = Plausibility()
        take_gps_snapshot(fed(0, 0, alt=100.0), guard, 100)
        # Different coordinates, or the guard returns its earlier verdict.
        s = take_gps_snapshot(fed(1, 1000, lat=-32.49359, alt=160.0), guard, 1100)
        self.assertTrue(s.fix_valid)
        self.assertFalse(s.altitude_fresh)


if __name__ == "__main__":
    unittest.main()
