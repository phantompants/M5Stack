"""Ports of test/test_schedule/test_main.cpp."""

import unittest

from jp226cp0.schedule import next_stopped_due, persisted_stop_is_plausible


class ScheduleTests(unittest.TestCase):
    def test_stopped_schedule_boundaries(self):
        stop, quarter, hour = 1_000_000, 900, 3600
        self.assertEqual(stop + 900, next_stopped_due(stop, stop, quarter, hour))
        self.assertEqual(stop + 900, next_stopped_due(stop, stop + 899, quarter, hour))
        self.assertEqual(stop + 1800, next_stopped_due(stop, stop + 900, quarter, hour))
        self.assertEqual(stop + 3600, next_stopped_due(stop, stop + 3599, quarter, hour))
        self.assertEqual(stop + 7200, next_stopped_due(stop, stop + 3600, quarter, hour))
        self.assertEqual(stop + 10800, next_stopped_due(stop, stop + 7200, quarter, hour))

    def test_before_stop_and_invalid_inputs(self):
        self.assertEqual(1_000_900, next_stopped_due(1_000_000, 5, 900, 3600))
        self.assertEqual(0, next_stopped_due(0, 5, 900, 3600))
        self.assertEqual(0, next_stopped_due(1_000_000, 5, 0, 3600))
        self.assertEqual(0, next_stopped_due(1_000_000, 5, 900, 0))

    def test_persisted_stop_sanity_checks(self):
        stop = 1_000_000
        self.assertTrue(persisted_stop_is_plausible(stop, stop + 900, stop + 1, 86400, 3600))
        self.assertFalse(persisted_stop_is_plausible(stop, stop, stop + 1, 86400, 3600))
        self.assertFalse(persisted_stop_is_plausible(stop, stop + 900, stop + 86401, 86400, 3600))
        self.assertFalse(persisted_stop_is_plausible(stop + 301, stop + 1200, stop, 86400, 3600))
        self.assertFalse(persisted_stop_is_plausible(stop, stop + 7200, stop + 1, 86400, 3600))


if __name__ == "__main__":
    unittest.main()
