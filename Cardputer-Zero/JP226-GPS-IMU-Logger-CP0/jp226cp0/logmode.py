"""Motion / logging state machine (port of updateLoggingMode() and friends).

States (names are written to the CSV `log_state` column):
  WAITING_FIX     boot, before a usable fix - no rows
  MOVING          1 Hz rows
  STOPPED_15M     row on stop confirmation, then stop+15, +30, +45, +60 min
  STOPPED_HOURLY  the same stop has lasted >= 1 h: +2 h, +3 h, hourly
  FIX_LOST        no approved fix for FIX_LOSS_TIMEOUT_MS: one row a minute

Starting is quick (>= MOVE_START_INSTANT_KMH at once, >= MOVE_START_KMH after
START_CONFIRM_MS, and a single >= MOVE_START_KMH sample while already MOVING);
stopping is debounced (<= MOVE_STOP_KMH for STOP_CONFIRM_MS). Bad or stale
fixes never manufacture a stop. The stop session (start and next due UTC) is
persisted so a restart continues the stopped schedule instead of resetting it.
"""

from . import config
from .schedule import next_stopped_due, persisted_stop_is_plausible
from .snapshot import GpsSnapshot
from .statestore import StateStore

WAITING_FIX = "WAITING_FIX"
MOVING = "MOVING"
STOPPED_15M = "STOPPED_15M"
STOPPED_HOURLY = "STOPPED_HOURLY"
FIX_LOST = "FIX_LOST"

ALL_STATES = (WAITING_FIX, MOVING, STOPPED_15M, STOPPED_HOURLY, FIX_LOST)

# Values of the persisted "state" key (NVS "state": 1 = moving, 2 = stopped).
_PERSIST_MOVING = 1
_PERSIST_STOPPED = 2


class LogStateMachine:
    def __init__(self, store: StateStore = None):
        self.store = store if store is not None else StateStore(None)
        self.mode = WAITING_FIX
        self.have_seen_valid_fix = False
        self.last_valid_fix_ms = 0
        self.start_candidate_active = False
        self.start_candidate_ms = 0
        self.stop_candidate_active = False
        self.stop_candidate_ms = 0
        self.stop_candidate_utc = 0
        self.stop_start_utc = 0
        self.next_stopped_due_utc = 0
        self.immediate_log_requested = False
        self.last_log_ms = 0
        self.last_fix_lost_log_ms = 0
        self.persisted_stop_pending = False
        self.persisted_stop_start_utc = 0
        self.persisted_next_due_utc = 0
        if self.store.get("state", 0) == _PERSIST_STOPPED:
            self.persisted_stop_start_utc = int(self.store.get("stop_utc", 0))
            self.persisted_next_due_utc = int(self.store.get("next_utc", 0))
            self.persisted_stop_pending = (self.persisted_stop_start_utc > 0 and
                                           self.persisted_next_due_utc > 0)

    # -- persistence ------------------------------------------------------
    def _save_stopped_state(self) -> None:
        self.store.put(state=_PERSIST_STOPPED, stop_utc=int(self.stop_start_utc),
                       next_utc=int(self.next_stopped_due_utc))

    def _clear_stopped_state(self) -> None:
        self.store.put(state=_PERSIST_MOVING, stop_utc=0, next_utc=0)
        self.stop_start_utc = 0
        self.next_stopped_due_utc = 0
        self.persisted_stop_pending = False

    # -- transitions ------------------------------------------------------
    def _set_stopped_phase(self, now_utc: int) -> None:
        if (self.stop_start_utc > 0 and
                now_utc - self.stop_start_utc >= config.STOPPED_HOURLY_INTERVAL_SEC):
            self.mode = STOPPED_HOURLY
        else:
            self.mode = STOPPED_15M

    def _enter_moving(self) -> None:
        changed = self.mode != MOVING
        if changed:
            self.mode = MOVING
            self.immediate_log_requested = True
        self.stop_candidate_active = False
        self.start_candidate_active = False
        if (changed or self.stop_start_utc != 0 or
                self.next_stopped_due_utc != 0 or self.persisted_stop_pending):
            self._clear_stopped_state()

    def _enter_stopped(self, detected_stop_utc: int, now_utc: int,
                       allow_persisted: bool) -> None:
        if (allow_persisted and self.persisted_stop_pending and
                persisted_stop_is_plausible(
                    self.persisted_stop_start_utc, self.persisted_next_due_utc,
                    now_utc, config.PERSISTED_STOP_MAX_AGE_SEC,
                    config.STOPPED_HOURLY_INTERVAL_SEC)):
            self.stop_start_utc = self.persisted_stop_start_utc
            self.next_stopped_due_utc = self.persisted_next_due_utc
        else:
            self.stop_start_utc = detected_stop_utc if detected_stop_utc > 0 else now_utc
            self.next_stopped_due_utc = next_stopped_due(
                self.stop_start_utc, now_utc,
                config.STOPPED_FIRST_HOUR_INTERVAL_SEC,
                config.STOPPED_HOURLY_INTERVAL_SEC)
        self.persisted_stop_pending = False
        self._set_stopped_phase(now_utc)
        self.stop_candidate_active = False
        self.immediate_log_requested = True
        self._save_stopped_state()

    # -- main update ------------------------------------------------------
    def update(self, sample: GpsSnapshot, now_ms: int, now_utc: int) -> None:
        if sample.fix_valid:
            self.have_seen_valid_fix = True
            self.last_valid_fix_ms = now_ms

            if sample.speed_kmh >= config.MOVE_START_KMH:
                if (self.mode == MOVING or
                        sample.speed_kmh >= config.MOVE_START_INSTANT_KMH):
                    self._enter_moving()
                    return
                if not self.start_candidate_active:
                    self.start_candidate_active = True
                    self.start_candidate_ms = now_ms
                elif now_ms - self.start_candidate_ms >= config.START_CONFIRM_MS:
                    self._enter_moving()
                    return
            else:
                self.start_candidate_active = False

            if self.mode == MOVING:
                if sample.speed_kmh <= config.MOVE_STOP_KMH:
                    if not self.stop_candidate_active:
                        self.stop_candidate_active = True
                        self.stop_candidate_ms = now_ms
                        self.stop_candidate_utc = now_utc
                    elif now_ms - self.stop_candidate_ms >= config.STOP_CONFIRM_MS:
                        self._enter_stopped(self.stop_candidate_utc, now_utc, False)
                else:
                    self.stop_candidate_active = False
                return

            if self.mode in (STOPPED_15M, STOPPED_HOURLY):
                self._set_stopped_phase(now_utc)
                return

            if sample.speed_kmh <= config.MOVE_STOP_KMH:
                self._enter_stopped(now_utc, now_utc, True)
            return

        self.start_candidate_active = False
        if not self.have_seen_valid_fix:
            timed_out = now_ms >= config.FIX_LOSS_TIMEOUT_MS
        else:
            timed_out = now_ms - self.last_valid_fix_ms >= config.FIX_LOSS_TIMEOUT_MS
        if timed_out and self.mode != FIX_LOST:
            self.mode = FIX_LOST
            self.stop_candidate_active = False
            self.immediate_log_requested = True

    # -- logging schedule -------------------------------------------------
    def log_is_due(self, now_ms: int, now_utc: int) -> bool:
        if self.immediate_log_requested:
            return True
        if self.mode == MOVING:
            return now_ms - self.last_log_ms >= config.MOVING_LOG_INTERVAL_MS
        if self.mode in (STOPPED_15M, STOPPED_HOURLY):
            return self.next_stopped_due_utc > 0 and now_utc >= self.next_stopped_due_utc
        if self.mode == FIX_LOST:
            return now_ms - self.last_fix_lost_log_ms >= config.FIX_LOST_LOG_INTERVAL_MS
        return False

    def on_log_succeeded(self, now_ms: int, now_utc: int) -> None:
        self.immediate_log_requested = False
        self.last_log_ms = now_ms
        if self.mode == FIX_LOST:
            self.last_fix_lost_log_ms = now_ms
        elif self.mode in (STOPPED_15M, STOPPED_HOURLY):
            self.next_stopped_due_utc = next_stopped_due(
                self.stop_start_utc, now_utc,
                config.STOPPED_FIRST_HOUR_INTERVAL_SEC,
                config.STOPPED_HOURLY_INTERVAL_SEC)
            self._set_stopped_phase(now_utc)
            self._save_stopped_state()
