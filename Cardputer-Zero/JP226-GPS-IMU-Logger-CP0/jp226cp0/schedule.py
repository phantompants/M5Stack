"""Stopped-log schedule (port of src/LogSchedule.cpp)."""


def next_stopped_due(stop_utc: int, after_utc: int,
                     first_hour_interval_sec: int,
                     hourly_interval_sec: int) -> int:
    """First scheduled stopped-log epoch strictly after `after_utc`.

    The sequence is stop+15m, +30m, +45m, +60m, then +2h, +3h, ...
    """
    if stop_utc <= 0 or first_hour_interval_sec == 0 or hourly_interval_sec == 0:
        return 0
    if after_utc < stop_utc:
        return stop_utc + first_hour_interval_sec

    elapsed = after_utc - stop_utc
    if elapsed < hourly_interval_sec:
        slot = elapsed // first_hour_interval_sec + 1
        return stop_utc + slot * first_hour_interval_sec

    hour = elapsed // hourly_interval_sec + 1
    return stop_utc + hour * hourly_interval_sec


def persisted_stop_is_plausible(stop_utc: int, next_due_utc: int, now_utc: int,
                                maximum_age_sec: int,
                                maximum_future_due_sec: int) -> bool:
    """True only for a plausible saved stopped session, so a corrupt or very
    old state file cannot suppress a fresh stopped-session record."""
    if stop_utc <= 0 or next_due_utc <= stop_utc or now_utc <= 0:
        return False
    if stop_utc > now_utc + 5 * 60:
        return False
    if next_due_utc > now_utc + maximum_future_due_sec:
        return False
    return now_utc - stop_utc <= maximum_age_sec
