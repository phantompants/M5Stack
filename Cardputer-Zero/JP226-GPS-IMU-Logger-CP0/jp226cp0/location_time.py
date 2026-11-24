"""Local time zone selection (port of the zone logic in LocationTime.cpp).

Resolution order, like the firmware:
  1. default Australia/Sydney (DEFAULT);
  2. the last zone saved in the state file (SAVED);
  3. `timezone=` from the config file (CONFIG);
  4. with timezone_auto, the offline table in tzfallback follows the GPS
     position (GPS): the same answer must come up on OFFLINE_ZONE_CONFIRM_CHECKS
     checks OFFLINE_ZONE_CHECK_INTERVAL_MS apart before the zone changes, so
     noise on a border cannot flip it back and forth;
  5. an online lookup result (GPS+NET), if one is ever provided through
     note_online_lookup(), stands within OFFLINE_ZONE_TRUST_KM of the place it
     answered; further away the table takes over again.

The online HTTP lookup itself (firmware: timeapi.io, only while stopped and
online) is NOT implemented here: see TODO in lookup_online(). The CardputerZero
runs Linux, so a later version can use any HTTP client and call
note_online_lookup() with the result.
"""

import datetime as _dt
from typing import Optional

from . import config
from .posixtz import PosixTz, fixed_offset_rule
from .statestore import StateStore
from .tzfallback import IANA_TO_RULE, distance_km, zone_for_position


class ZoneInfoTz:
    """Wraps zoneinfo for IANA names that are not in the built-in table.
    Only used when the system has a tz database."""

    def __init__(self, name: str):
        import zoneinfo  # may raise ImportError / ZoneInfoNotFoundError
        self.rule = name
        self._zone = zoneinfo.ZoneInfo(name)

    def utcoffset(self, epoch_s: int) -> int:
        when = _dt.datetime.fromtimestamp(int(epoch_s), tz=_dt.timezone.utc)
        return int(when.astimezone(self._zone).utcoffset().total_seconds())


def make_tz(spec: str):
    """POSIX rule, known Australian IANA name, or (if the system has tzdata)
    any IANA name. Raises ValueError if none of those works."""
    spec = spec.strip()
    if spec in IANA_TO_RULE:
        return PosixTz(IANA_TO_RULE[spec])
    try:
        return PosixTz(spec)
    except ValueError:
        pass
    if "/" in spec:
        try:
            return ZoneInfoTz(spec)
        except Exception as exc:  # ImportError, ZoneInfoNotFoundError, ...
            raise ValueError("unknown time zone {!r}: {}".format(spec, exc))
    raise ValueError("unknown time zone {!r}".format(spec))


class LocationTime:
    def __init__(self, store: Optional[StateStore] = None,
                 configured_timezone: str = "", timezone_auto: bool = True):
        self.store = store if store is not None else StateStore(None)
        self.timezone_auto = timezone_auto
        self.zone_name = config.DEFAULT_ZONE_NAME
        self.zone_rule = ""
        self.zone_source = "DEFAULT"
        self.tz = None
        self.messages = []

        saved_rule = self.store.get("tz_rule", "")
        saved_name = self.store.get("tz_name", "")
        if saved_rule and self._apply(saved_rule, saved_name or "Last known",
                                      "SAVED", False):
            pass
        else:
            self._apply(config.DEFAULT_POSIX_TIMEZONE, config.DEFAULT_ZONE_NAME,
                        "DEFAULT", False)
        if configured_timezone:
            if not self._apply(configured_timezone, configured_timezone,
                               "CONFIG", False):
                self.messages.append(
                    "ignored unusable timezone={!r}".format(configured_timezone))

        self._have_lookup_position = False
        self._lookup_lat = 0.0
        self._lookup_lon = 0.0
        self._last_offline_check_ms = 0
        self._pending_rule = ""
        self._pending_count = 0

    # ------------------------------------------------------------------
    def offset_at(self, epoch_s: int) -> int:
        return self.tz.utcoffset(epoch_s)

    def _apply(self, rule: str, name: str, source: str, persist: bool) -> bool:
        if not rule:
            return False
        try:
            tz = make_tz(rule)
        except ValueError:
            return False
        self.tz = tz
        self.zone_rule = tz.rule  # canonical rule (IANA names are mapped)
        self.zone_name = name
        self.zone_source = source
        if persist:
            self.store.put(tz_rule=tz.rule, tz_name=name)
        return True

    # ------------------------------------------------------------------
    def update(self, latitude: float, longitude: float, location_fresh: bool,
               speed_kmh: float, now_ms: int) -> None:
        if not self.timezone_auto or not location_fresh:
            return
        # Most of the outback has no Wi-Fi, so the zone also follows the
        # position from the built-in table; an online lookup overrides it near
        # the place it last answered.
        self._update_offline_zone(latitude, longitude, now_ms)
        # TODO(online): when the network is up and speed_kmh <= MOVE_STOP_KMH,
        # call lookup_online(latitude, longitude) at most every
        # kTimezoneLookupRetryMs / kTimezoneLookupIntervalMs (15 min / 6 h).

    def _update_offline_zone(self, latitude: float, longitude: float,
                             now_ms: int) -> None:
        if (self._last_offline_check_ms != 0 and
                now_ms - self._last_offline_check_ms <
                config.OFFLINE_ZONE_CHECK_INTERVAL_MS):
            return
        self._last_offline_check_ms = now_ms if now_ms != 0 else 1

        if (self._have_lookup_position and
                distance_km(self._lookup_lat, self._lookup_lon, latitude,
                            longitude) < config.OFFLINE_ZONE_TRUST_KM):
            self._pending_count = 0
            return

        zone = zone_for_position(latitude, longitude)
        if zone is None or self.zone_rule == zone.rule:
            self._pending_count = 0
            return
        if self._pending_rule != zone.rule:
            self._pending_rule = zone.rule
            self._pending_count = 1
            return
        self._pending_count += 1
        if self._pending_count < config.OFFLINE_ZONE_CONFIRM_CHECKS:
            return
        self._pending_count = 0
        self._apply(zone.rule, zone.name, "GPS", True)

    # ------------------------------------------------------------------
    def lookup_online(self, latitude: float, longitude: float) -> bool:
        """TODO: not implemented (optional). Would GET
        https://timeapi.io/api/timezone/coordinate?latitude=..&longitude=..
        and pass timeZone + currentUtcOffset.seconds to note_online_lookup().
        Left out so the logger makes no network traffic."""
        return False

    def note_online_lookup(self, iana_zone: str, utc_offset_s: int,
                           latitude: float, longitude: float) -> None:
        """Apply an online answer and remember where it was valid."""
        rule = IANA_TO_RULE.get(iana_zone) or fixed_offset_rule(utc_offset_s)
        if self._apply(rule, iana_zone, "GPS+NET", True):
            self._have_lookup_position = True
            self._lookup_lat = latitude
            self._lookup_lon = longitude
