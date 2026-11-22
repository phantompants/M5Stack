"""Minimal POSIX TZ rule engine (std/dst names, offsets, Mm.w.d rules).

Needed because the CardputerZero's tz database is unknown and Windows Python
has none: the Australian zones used by the firmware are plain POSIX strings
such as "AEST-10AEDT,M10.1.0,M4.1.0/3" and are evaluated here directly, so
local dates and offsets never depend on zoneinfo/tzdata.

Supported: "<name><offset>[<dstname>[<dstoffset>],<start>[/time],<end>[/time]]"
with rules in the Mm.w.d form only (all the firmware uses). The POSIX sign is
inverted (AEST-10 means UTC+10).
"""

import datetime as _dt
import re
from typing import Optional

from .timeutil import days_from_civil

_NAME = r"(?:[A-Za-z]{3,}|<[A-Za-z0-9+\-]+>)"
_OFFSET = r"[+-]?\d{1,2}(?::\d{2}(?::\d{2})?)?"
_RULE = r"M\d{1,2}\.\d\.\d(?:/[+-]?\d{1,3}(?::\d{2}(?::\d{2})?)?)?"
_TZ_RE = re.compile(
    r"^(?P<std>{n})(?P<stdoff>{o})"
    r"(?:(?P<dst>{n})(?P<dstoff>{o})?(?:,(?P<start>{r}),(?P<end>{r}))?)?$"
    .format(n=_NAME, o=_OFFSET, r=_RULE))


def _hms_to_seconds(text: str) -> int:
    sign = -1 if text.startswith("-") else 1
    text = text.lstrip("+-")
    parts = [int(p) for p in text.split(":")]
    while len(parts) < 3:
        parts.append(0)
    return sign * (parts[0] * 3600 + parts[1] * 60 + parts[2])


def _parse_rule(text: str):
    body, _, time_part = text.partition("/")
    month, week, weekday = (int(x) for x in body[1:].split("."))
    if not (1 <= month <= 12 and 1 <= week <= 5 and 0 <= weekday <= 6):
        raise ValueError("bad POSIX TZ rule: " + text)
    seconds = _hms_to_seconds(time_part) if time_part else 2 * 3600
    return month, week, weekday, seconds


def _rule_local_seconds(year: int, rule) -> int:
    """Seconds since the epoch of the rule's wall-clock instant as if the
    local clock were UTC (the caller subtracts the proper offset)."""
    month, week, weekday, seconds = rule
    first = _dt.date(year, month, 1)
    first_posix_wd = (first.weekday() + 1) % 7  # Monday=0 -> Sunday=0 numbering
    day = 1 + (weekday - first_posix_wd) % 7 + (week - 1) * 7
    if month == 12:
        days_in_month = 31
    else:
        days_in_month = (_dt.date(year, month + 1, 1) - first).days
    while day > days_in_month:
        day -= 7
    return days_from_civil(year, month, day) * 86400 + seconds


class PosixTz:
    """Offset lookup for one POSIX TZ string."""

    def __init__(self, rule: str):
        match = _TZ_RE.match(rule.strip())
        if not match:
            raise ValueError("unsupported POSIX TZ string: {!r}".format(rule))
        self.rule = rule.strip()
        self.std_name = match.group("std").strip("<>")
        self.std_offset = -_hms_to_seconds(match.group("stdoff"))
        self.dst_name: Optional[str] = None
        self.dst_offset = self.std_offset
        self._start = self._end = None
        if match.group("dst"):
            self.dst_name = match.group("dst").strip("<>")
            if match.group("dstoff"):
                self.dst_offset = -_hms_to_seconds(match.group("dstoff"))
            else:
                self.dst_offset = self.std_offset + 3600
            if match.group("start"):
                self._start = _parse_rule(match.group("start"))
                self._end = _parse_rule(match.group("end"))
            else:
                # POSIX default (US) rules are not supported; refuse rather
                # than give silently wrong answers.
                raise ValueError("DST zone without rules is unsupported: " + rule)

    @property
    def has_dst(self) -> bool:
        return self._start is not None

    def is_dst(self, epoch_s: int) -> bool:
        if not self.has_dst:
            return False
        year = (_dt.datetime(1970, 1, 1) +
                _dt.timedelta(seconds=int(epoch_s) + self.std_offset)).year
        # Start is given in standard local time, end in daylight local time.
        start = _rule_local_seconds(year, self._start) - self.std_offset
        end = _rule_local_seconds(year, self._end) - self.dst_offset
        if start < end:  # northern hemisphere
            return start <= epoch_s < end
        return epoch_s >= start or epoch_s < end  # southern hemisphere

    def utcoffset(self, epoch_s: int) -> int:
        return self.dst_offset if self.is_dst(epoch_s) else self.std_offset

    def abbreviation(self, epoch_s: int) -> str:
        if self.is_dst(epoch_s) and self.dst_name:
            return self.dst_name
        return self.std_name


def fixed_offset_rule(offset_s: int) -> str:
    """Firmware fixedOffsetRule(): 'LOC-10:30' style fixed-offset zone."""
    east = offset_s >= 0
    magnitude = abs(int(offset_s))
    return "LOC{}{}:{:02d}".format("-" if east else "+", magnitude // 3600,
                                   (magnitude % 3600) // 60)
