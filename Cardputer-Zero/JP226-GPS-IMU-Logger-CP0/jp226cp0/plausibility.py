"""GPS plausibility guard (port of src/GpsPlausibility.cpp).

Rejects fixes that the receiver reports as valid but that the vehicle cannot
have reached: a position that jumps further than speed and elapsed time allow,
or an altitude that steps by tens of metres in a second.

One instance per receiver. A fix is judged once: calling again with the same
coordinates returns the earlier verdict, so a loop that reads the snapshot many
times per second does not count one bad fix more than once.
"""

import math
from typing import Tuple

from .tzfallback import distance_km

# After this long without an accepted fix the next one is simply trusted: the
# vehicle may genuinely have moved a long way (ferry, tow, tunnel).
RESEED_AFTER_MS = 30_000
# This many rejections in a row means the receiver is right and the old
# position is wrong, so the new one is accepted.
MAX_CONSECUTIVE_REJECTS = 5
MINIMUM_ALLOWED_JUMP_M = 80.0
JUMP_MARGIN_M = 60.0
MAX_ALTITUDE_STEP_M = 30.0
MAX_ALTITUDE_REJECTS = 3


class Plausibility:
    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._have_last = False
        self._last_lat = 0.0
        self._last_lon = 0.0
        self._last_alt = 0.0
        self._last_alt_valid = False
        self._last_speed_ms = 0.0
        self._last_accepted_ms = 0
        self._position_rejects = 0
        self._altitude_rejects = 0
        self._have_evaluated = False
        self._eval_lat = 0.0
        self._eval_lon = 0.0
        self._eval_position_ok = True
        self._eval_altitude_ok = True

    def accept(self, latitude: float, longitude: float, altitude_m: float,
               altitude_valid: bool, speed_kmh: float,
               now_ms: int) -> Tuple[bool, bool]:
        """Returns (position_ok, altitude_ok). When position_ok is False the
        fix should be treated as invalid; altitude_ok False means only the
        altitude looks wrong and the position still stands."""
        if not (math.isfinite(latitude) and math.isfinite(longitude)):
            return True, True

        if (self._have_evaluated and latitude == self._eval_lat and
                longitude == self._eval_lon):
            return self._eval_position_ok, self._eval_altitude_ok

        speed_ms = speed_kmh / 3.6 if math.isfinite(speed_kmh) else 0.0
        position_ok = True
        altitude_ok = True

        if self._have_last and now_ms - self._last_accepted_ms <= RESEED_AFTER_MS:
            dt = max(0.05, (now_ms - self._last_accepted_ms) / 1000.0)
            jump_m = distance_km(self._last_lat, self._last_lon,
                                 latitude, longitude) * 1000.0
            allowed_m = max(MINIMUM_ALLOWED_JUMP_M,
                            1.5 * max(speed_ms, self._last_speed_ms) * dt +
                            JUMP_MARGIN_M)
            if jump_m > allowed_m and self._position_rejects < MAX_CONSECUTIVE_REJECTS:
                position_ok = False
                self._position_rejects += 1
            else:
                self._position_rejects = 0

            if (position_ok and altitude_valid and self._last_alt_valid and
                    abs(altitude_m - self._last_alt) > MAX_ALTITUDE_STEP_M and
                    self._altitude_rejects < MAX_ALTITUDE_REJECTS):
                altitude_ok = False
                self._altitude_rejects += 1
            else:
                self._altitude_rejects = 0
        else:
            self._position_rejects = 0
            self._altitude_rejects = 0

        if position_ok:
            self._have_last = True
            self._last_lat = latitude
            self._last_lon = longitude
            self._last_speed_ms = speed_ms
            self._last_accepted_ms = now_ms
            if altitude_ok:
                self._last_alt = altitude_m
                self._last_alt_valid = altitude_valid

        self._have_evaluated = True
        self._eval_lat = latitude
        self._eval_lon = longitude
        self._eval_position_ok = position_ok
        self._eval_altitude_ok = altitude_ok
        return position_ok, altitude_ok
