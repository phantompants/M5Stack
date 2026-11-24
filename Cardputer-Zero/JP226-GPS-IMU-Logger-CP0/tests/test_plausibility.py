"""Ports of test/test_gps_plausibility/test_main.cpp."""

import unittest

from jp226cp0.plausibility import Plausibility


class PlausibilityTests(unittest.TestCase):
    def test_normal_driving_is_accepted(self):
        guard = Plausibility()
        lat = -33.0
        for i in range(20):
            lat += 0.00025   # ~28 m per second = 100 km/h
            ok, alt_ok = guard.accept(lat, 151.0, 100.0, True, 100.0, 1000 * (i + 1))
            self.assertTrue(ok)
            self.assertTrue(alt_ok)

    def test_teleport_while_parked_is_rejected_then_ignored(self):
        guard = Plausibility()
        self.assertTrue(guard.accept(-33.0, 151.0, 100.0, True, 0.0, 1000)[0])
        self.assertFalse(guard.accept(-33.0035, 151.0, 100.0, True, 0.0, 2000)[0])  # ~390 m
        # Back at the true spot: accepted.
        self.assertTrue(guard.accept(-33.00001, 151.0, 100.0, True, 0.0, 3000)[0])

    def test_same_bad_fix_is_counted_once(self):
        guard = Plausibility()
        self.assertTrue(guard.accept(-33.0, 151.0, 100.0, True, 0.0, 1000)[0])
        for i in range(50):   # many loop iterations, one bad fix
            self.assertFalse(
                guard.accept(-33.0035, 151.0, 100.0, True, 0.0, 2000 + i)[0])

    def test_persistent_new_position_is_eventually_accepted(self):
        guard = Plausibility()
        self.assertTrue(guard.accept(-33.0, 151.0, 100.0, True, 0.0, 1000)[0])
        accepted = False
        for i in range(8):
            if accepted:
                break
            accepted = guard.accept(-33.0035 + i * 1e-6, 151.0, 100.0, True, 0.0,
                                    2000 + 1000 * i)[0]
        self.assertTrue(accepted)

    def test_long_gap_reseeds(self):
        guard = Plausibility()
        self.assertTrue(guard.accept(-33.0, 151.0, 100.0, True, 0.0, 1000)[0])
        self.assertTrue(guard.accept(-34.0, 151.0, 100.0, True, 0.0, 100000)[0])

    def test_altitude_spike_keeps_position(self):
        guard = Plausibility()
        self.assertTrue(guard.accept(-33.0, 151.0, 100.0, True, 0.0, 1000)[0])
        ok, alt_ok = guard.accept(-33.00001, 151.0, 160.0, True, 0.0, 2000)
        self.assertTrue(ok)
        self.assertFalse(alt_ok)

    def test_non_finite_is_passed_through(self):
        guard = Plausibility()
        self.assertEqual(guard.accept(float("nan"), 151.0, 0, False, 0, 0), (True, True))

    def test_reset(self):
        guard = Plausibility()
        guard.accept(-33.0, 151.0, 100.0, True, 0.0, 1000)
        guard.reset()
        self.assertTrue(guard.accept(-34.0, 151.0, 100.0, True, 0.0, 2000)[0])


if __name__ == "__main__":
    unittest.main()
