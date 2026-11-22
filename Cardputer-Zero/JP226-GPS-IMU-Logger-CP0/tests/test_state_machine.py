import unittest

from jp226cp0 import config, logmode
from jp226cp0.logmode import LogStateMachine
from jp226cp0.statestore import StateStore

from .helpers import make_snapshot

UTC0 = 1_800_000_000          # arbitrary plausible "now" in epoch seconds


def moving_machine(store=None):
    m = LogStateMachine(store)
    m.update(make_snapshot(50), 0, UTC0)
    assert m.mode == logmode.MOVING
    return m


class EntryTests(unittest.TestCase):
    def test_state_names_match_firmware(self):
        self.assertEqual(logmode.ALL_STATES, ("WAITING_FIX", "MOVING", "STOPPED_15M",
                                              "STOPPED_HOURLY", "FIX_LOST"))

    def test_starts_waiting_and_does_not_log(self):
        m = LogStateMachine()
        self.assertEqual(m.mode, "WAITING_FIX")
        self.assertFalse(m.log_is_due(5000, UTC0))

    def test_stationary_first_fix_enters_stopped(self):
        m = LogStateMachine()
        m.update(make_snapshot(0.0), 1000, UTC0)
        self.assertEqual(m.mode, "STOPPED_15M")
        self.assertEqual(m.stop_start_utc, UTC0)
        self.assertEqual(m.next_stopped_due_utc, UTC0 + 900)
        self.assertTrue(m.log_is_due(1000, UTC0))      # immediate row

    def test_speed_between_thresholds_while_waiting_does_nothing(self):
        m = LogStateMachine()
        m.update(make_snapshot(2.0), 1000, UTC0)       # > stop (1.5), < start (3.0)
        self.assertEqual(m.mode, "WAITING_FIX")

    def test_instant_start_at_8_kmh(self):
        m = LogStateMachine()
        m.update(make_snapshot(config.MOVE_START_INSTANT_KMH), 1000, UTC0)
        self.assertEqual(m.mode, "MOVING")
        self.assertTrue(m.immediate_log_requested)

    def test_start_between_3_and_8_needs_5_seconds(self):
        m = LogStateMachine()
        m.update(make_snapshot(0.0), 0, UTC0)           # stopped first
        self.assertEqual(m.mode, "STOPPED_15M")
        m.update(make_snapshot(4.0), 1000, UTC0 + 1)
        m.update(make_snapshot(4.0), 5999, UTC0 + 5)
        self.assertEqual(m.mode, "STOPPED_15M")
        m.update(make_snapshot(4.0), 6000, UTC0 + 6)    # 5000 ms after candidate
        self.assertEqual(m.mode, "MOVING")

    def test_start_candidate_resets_when_speed_drops(self):
        m = LogStateMachine()
        m.update(make_snapshot(0.0), 0, UTC0)
        m.update(make_snapshot(4.0), 1000, UTC0 + 1)
        m.update(make_snapshot(1.0), 3000, UTC0 + 3)    # below start: candidate gone
        m.update(make_snapshot(4.0), 4000, UTC0 + 4)    # new candidate
        m.update(make_snapshot(4.0), 8999, UTC0 + 9)
        self.assertEqual(m.mode, "STOPPED_15M")
        m.update(make_snapshot(4.0), 9000, UTC0 + 9)
        self.assertEqual(m.mode, "MOVING")

    def test_moving_clears_stopped_state(self):
        store = StateStore(None)
        m = LogStateMachine(store)
        m.update(make_snapshot(0.0), 0, UTC0)
        self.assertEqual(store.get("state"), 2)
        m.update(make_snapshot(30.0), 1000, UTC0 + 1)
        self.assertEqual((store.get("state"), store.get("stop_utc")), (1, 0))
        self.assertEqual(m.stop_start_utc, 0)


class StopTests(unittest.TestCase):
    def test_stop_needs_10_seconds_at_or_below_1_5(self):
        m = moving_machine()
        m.update(make_snapshot(1.5), 1000, UTC0 + 1)    # candidate (<= 1.5)
        m.update(make_snapshot(1.5), 10999, UTC0 + 10)
        self.assertEqual(m.mode, "MOVING")
        m.update(make_snapshot(1.5), 11000, UTC0 + 11)
        self.assertEqual(m.mode, "STOPPED_15M")
        self.assertEqual(m.stop_start_utc, UTC0 + 1)    # when it first stopped
        self.assertEqual(m.next_stopped_due_utc, UTC0 + 1 + 900)

    def test_speed_above_stop_threshold_cancels_candidate(self):
        m = moving_machine()
        m.update(make_snapshot(0.0), 1000, UTC0 + 1)
        m.update(make_snapshot(1.6), 6000, UTC0 + 6)    # above 1.5: not a stop
        m.update(make_snapshot(0.0), 7000, UTC0 + 7)    # restart the 10 s
        m.update(make_snapshot(0.0), 16999, UTC0 + 16)
        self.assertEqual(m.mode, "MOVING")
        m.update(make_snapshot(0.0), 17000, UTC0 + 17)
        self.assertEqual(m.mode, "STOPPED_15M")

    def test_single_good_fix_resumes_moving(self):
        m = moving_machine()
        for i in range(1, 13):
            m.update(make_snapshot(0.0), i * 1000, UTC0 + i)
        self.assertEqual(m.mode, "STOPPED_15M")
        m.update(make_snapshot(9.0), 13000, UTC0 + 13)
        self.assertEqual(m.mode, "MOVING")

    def test_bad_fixes_never_manufacture_a_stop(self):
        m = moving_machine()
        for i in range(1, 9):       # 8 s of invalid fixes (< 10 s timeout)
            m.update(make_snapshot(0.0, valid=False), i * 1000, UTC0 + i)
        self.assertEqual(m.mode, "MOVING")
        self.assertFalse(m.stop_candidate_active)

    def test_stopped_schedule_and_phase_change(self):
        m = LogStateMachine()
        m.update(make_snapshot(0.0), 0, UTC0)
        m.on_log_succeeded(0, UTC0)                      # the immediate row
        self.assertFalse(m.log_is_due(1000, UTC0 + 899))
        self.assertTrue(m.log_is_due(2000, UTC0 + 900))
        m.on_log_succeeded(2000, UTC0 + 900)
        self.assertEqual(m.next_stopped_due_utc, UTC0 + 1800)
        # 45 minutes -> due at +60 min, still the 15-minute phase until then.
        m.on_log_succeeded(3000, UTC0 + 2700)
        self.assertEqual(m.next_stopped_due_utc, UTC0 + 3600)
        self.assertEqual(m.mode, "STOPPED_15M")
        m.on_log_succeeded(4000, UTC0 + 3600)
        self.assertEqual(m.mode, "STOPPED_HOURLY")
        self.assertEqual(m.next_stopped_due_utc, UTC0 + 7200)

    def test_stopped_to_hourly_via_update(self):
        m = LogStateMachine()
        m.update(make_snapshot(0.0), 0, UTC0)
        m.update(make_snapshot(0.0), 1000, UTC0 + 3600)
        self.assertEqual(m.mode, "STOPPED_HOURLY")

    def test_moving_log_rate_is_1_hz(self):
        m = moving_machine()
        m.on_log_succeeded(0, UTC0)
        self.assertFalse(m.log_is_due(999, UTC0))
        self.assertTrue(m.log_is_due(1000, UTC0 + 1))


class FixLostTests(unittest.TestCase):
    def test_fix_lost_after_10_seconds(self):
        m = moving_machine()
        m.update(make_snapshot(0.0, valid=False), 9999, UTC0 + 9)
        self.assertEqual(m.mode, "MOVING")
        m.update(make_snapshot(0.0, valid=False), 10000, UTC0 + 10)
        self.assertEqual(m.mode, "FIX_LOST")
        self.assertTrue(m.immediate_log_requested)

    def test_fix_lost_before_any_fix(self):
        m = LogStateMachine()
        m.update(make_snapshot(0.0, valid=False), 9999, UTC0)
        self.assertEqual(m.mode, "WAITING_FIX")
        m.update(make_snapshot(0.0, valid=False), 10000, UTC0)
        self.assertEqual(m.mode, "FIX_LOST")

    def test_fix_lost_rate_is_60_seconds(self):
        m = moving_machine()
        m.update(make_snapshot(0.0, valid=False), 10000, UTC0 + 10)
        m.on_log_succeeded(10000, UTC0 + 10)
        self.assertFalse(m.log_is_due(69999, UTC0 + 69))
        self.assertTrue(m.log_is_due(70000, UTC0 + 70))

    def test_recovery_goes_to_moving_or_stopped(self):
        m = moving_machine()
        m.update(make_snapshot(0.0, valid=False), 10000, UTC0 + 10)
        m.update(make_snapshot(60.0), 11000, UTC0 + 11)
        self.assertEqual(m.mode, "MOVING")
        m = moving_machine()
        m.update(make_snapshot(0.0, valid=False), 10000, UTC0 + 10)
        m.update(make_snapshot(0.0), 11000, UTC0 + 11)
        self.assertEqual(m.mode, "STOPPED_15M")


class PersistenceTests(unittest.TestCase):
    def stopped_store(self, stop_utc, next_utc):
        store = StateStore(None)
        store.put(state=2, stop_utc=stop_utc, next_utc=next_utc)
        return store

    def test_restart_continues_saved_stop_schedule(self):
        store = self.stopped_store(UTC0 - 1000, UTC0 + 800)
        m = LogStateMachine(store)
        self.assertTrue(m.persisted_stop_pending)
        m.update(make_snapshot(0.0), 5000, UTC0)
        self.assertEqual(m.mode, "STOPPED_15M")
        self.assertEqual(m.stop_start_utc, UTC0 - 1000)       # not reset
        self.assertEqual(m.next_stopped_due_utc, UTC0 + 800)

    def test_old_saved_stop_is_rejected(self):
        old = UTC0 - config.PERSISTED_STOP_MAX_AGE_SEC - 10
        m = LogStateMachine(self.stopped_store(old, old + 900))
        m.update(make_snapshot(0.0), 5000, UTC0)
        self.assertEqual(m.stop_start_utc, UTC0)               # fresh session

    def test_corrupt_saved_stop_is_rejected(self):
        m = LogStateMachine(self.stopped_store(UTC0 - 100, UTC0 - 200))
        m.update(make_snapshot(0.0), 5000, UTC0)
        self.assertEqual(m.stop_start_utc, UTC0)

    def test_stop_state_written_when_stopping(self):
        store = StateStore(None)
        m = LogStateMachine(store)
        m.update(make_snapshot(0.0), 0, UTC0)
        self.assertEqual((store.get("state"), store.get("stop_utc"),
                          store.get("next_utc")), (2, UTC0, UTC0 + 900))

    def test_moving_after_restart_clears_saved_stop(self):
        store = self.stopped_store(UTC0 - 100, UTC0 + 800)
        m = LogStateMachine(store)
        m.update(make_snapshot(40.0), 0, UTC0)
        self.assertEqual(store.get("state"), 1)
        self.assertFalse(m.persisted_stop_pending)


if __name__ == "__main__":
    unittest.main()
