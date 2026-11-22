import unittest

from jp226cp0.clock import Clock
from jp226cp0.nmea import GpsState, make_sentence
from jp226cp0.plausibility import Plausibility
from jp226cp0.snapshot import take_gps_snapshot

from .helpers import utc_ms

T0 = utc_ms(2026, 10, 20, 1, 30, 0)


class ClockTests(unittest.TestCase):
    def test_gps_source_is_not_ready_until_synced(self):
        clock = Clock("gps", system_time_fn=lambda: 1.8e9)
        self.assertFalse(clock.ready(0))
        self.assertIsNone(clock.now_utc_ms(0))
        self.assertTrue(clock.sync_from_gps(True, T0, 5000))
        self.assertTrue(clock.ready(5000))
        self.assertEqual(clock.now_utc_ms(6000), T0 + 1000)

    def test_auto_uses_system_clock_until_gps_then_gps(self):
        clock = Clock("auto", system_time_fn=lambda: (T0 - 5000) / 1000.0)
        self.assertEqual(clock.now_utc_ms(0), T0 - 5000)
        clock.sync_from_gps(True, T0, 100)
        # GPS wins; the (earlier) system value is not used any more and the
        # clock never goes backwards from what was already handed out.
        self.assertGreaterEqual(clock.now_utc_ms(200), T0 - 5000)
        self.assertEqual(clock.now_utc_ms(1100), T0 + 1000)

    def test_implausible_system_year_is_not_ready(self):
        clock = Clock("auto", system_time_fn=lambda: 0.0)   # 1970
        self.assertFalse(clock.ready(0))

    def test_system_source_ignores_gps(self):
        clock = Clock("system", system_time_fn=lambda: T0 / 1000.0)
        self.assertFalse(clock.sync_from_gps(True, T0 + 99999, 0))
        self.assertEqual(clock.now_utc_ms(0), T0)

    def test_invalid_gps_time_is_ignored(self):
        clock = Clock("gps")
        self.assertFalse(clock.sync_from_gps(False, T0, 0))
        self.assertFalse(clock.sync_from_gps(True, 0, 0))
        self.assertFalse(clock.ready(0))

    def test_resync_at_most_every_60_seconds(self):
        clock = Clock("gps")
        self.assertTrue(clock.sync_from_gps(True, T0, 1000))
        self.assertFalse(clock.sync_from_gps(True, T0 + 30000, 31000))
        self.assertFalse(clock.sync_from_gps(True, T0 + 59999, 60999))
        self.assertTrue(clock.sync_from_gps(True, T0 + 60000, 61000))

    def test_time_never_goes_backwards_after_a_step_back_resync(self):
        clock = Clock("gps")
        clock.sync_from_gps(True, T0, 0)
        last = 0
        for ms in range(0, 130000, 250):
            if ms == 60000:   # the resync says GPS time is 1.5 s earlier
                clock.sync_from_gps(True, T0 + 60000 - 1500, ms)
            value = clock.now_utc_ms(ms)
            self.assertGreaterEqual(value, last)
            last = value
        # ...and it follows the corrected clock once real time catches up.
        self.assertEqual(clock.now_utc_ms(130000), T0 + 130000 - 1500)

    def test_unknown_source_rejected(self):
        with self.assertRaises(ValueError):
            Clock("bogus")

    def test_utc_includes_sentence_age(self):
        state = GpsState()
        for line in ("$GNRMC,013000.50,A,3229.61600,S,13746.29600,E,0.00,,201026,,,A",):
            state.feed_line(make_sentence(line[1:]), 1000)
        snap = take_gps_snapshot(state, Plausibility(), 1300)
        self.assertEqual(snap.utc_epoch_ms, T0 + 500 + 300)


if __name__ == "__main__":
    unittest.main()
