"""Calendar helpers that do not depend on the platform C library or tz data."""

import datetime as _dt

_EPOCH = _dt.datetime(1970, 1, 1)


def days_from_civil(year: int, month: int, day: int) -> int:
    """Days since 1970-01-01 for a proleptic Gregorian date."""
    return _dt.date(year, month, day).toordinal() - 719163


def civil_from_epoch(epoch_s: int, offset_s: int = 0) -> _dt.datetime:
    """Naive datetime of (epoch + offset)."""
    return _EPOCH + _dt.timedelta(seconds=int(epoch_s) + int(offset_s))


def format_offset(offset_s: int) -> str:
    """+10:30 style offset."""
    sign = "+" if offset_s >= 0 else "-"
    magnitude = abs(int(offset_s))
    return "{}{:02d}:{:02d}".format(sign, magnitude // 3600, (magnitude % 3600) // 60)


def format_utc(epoch_ms: int) -> str:
    """2026-10-20T01:02:03.456Z"""
    seconds, ms = divmod(int(epoch_ms), 1000)
    t = civil_from_epoch(seconds)
    return "{:04d}-{:02d}-{:02d}T{:02d}:{:02d}:{:02d}.{:03d}Z".format(
        t.year, t.month, t.day, t.hour, t.minute, t.second, ms)


def format_local(epoch_ms: int, offset_s: int) -> str:
    """2026-10-20T11:32:03.456+10:30"""
    seconds, ms = divmod(int(epoch_ms), 1000)
    t = civil_from_epoch(seconds, offset_s)
    return "{:04d}-{:02d}-{:02d}T{:02d}:{:02d}:{:02d}.{:03d}{}".format(
        t.year, t.month, t.day, t.hour, t.minute, t.second, ms,
        format_offset(offset_s))


def local_date_text(epoch_s: int, offset_s: int) -> str:
    t = civil_from_epoch(epoch_s, offset_s)
    return "{:04d}-{:02d}-{:02d}".format(t.year, t.month, t.day)
