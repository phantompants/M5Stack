"""Constants ported 1:1 from include/Config.h of the ESP32 firmware.

Names are the firmware's kCamelCase names in UPPER_SNAKE_CASE. Hardware
specific values (serial ports, framebuffer, keys) are NOT here: see
hardware.py.
"""

# Motion has hysteresis: movement starts once speed stays at/above
# MOVE_START_KMH for START_CONFIRM_MS, or at once at/above
# MOVE_START_INSTANT_KMH; stopping needs speed at/below MOVE_STOP_KMH for
# STOP_CONFIRM_MS.
MOVE_START_KMH = 3.0
MOVE_START_INSTANT_KMH = 8.0
START_CONFIRM_MS = 5_000
MOVE_STOP_KMH = 1.5
STOP_CONFIRM_MS = 10_000

# Logging rates.
MOVING_LOG_INTERVAL_MS = 1_000
STOPPED_FIRST_HOUR_INTERVAL_SEC = 15 * 60
STOPPED_HOURLY_INTERVAL_SEC = 60 * 60
FIX_LOST_LOG_INTERVAL_MS = 60_000

# GPS quality gates.
MAX_FIX_AGE_MS = 3_000
MAX_DATE_TIME_AGE_MS = 5_000
FIX_LOSS_TIMEOUT_MS = 10_000
MINIMUM_SATELLITES = 4
MAXIMUM_HDOP = 5.0

CLOCK_RESYNC_INTERVAL_MS = 60_000
PERSISTED_STOP_MAX_AGE_SEC = 30 * 24 * 60 * 60
DISPLAY_INTERVAL_MS = 500
# Firmware kSdRetryIntervalMs: how long to wait before retrying a failed
# storage write (here: a failed file open/write on the data directory).
STORAGE_RETRY_INTERVAL_MS = 10_000

# Serial scanning timings (kGpsBaudScanIntervalMs, kGpsSourceStaleMs).
GPS_BAUD_SCAN_INTERVAL_MS = 2_500
GPS_SOURCE_STALE_MS = 5_000

# Australia/Sydney incl. DST; the firmware default zone (kPosixTimezone).
DEFAULT_POSIX_TIMEZONE = "AEST-10AEDT,M10.1.0,M4.1.0/3"
DEFAULT_ZONE_NAME = "Australia/Sydney"

# Offline timezone: same answer on this many checks this far apart, and an
# online lookup is trusted within this radius (LocationTime::updateOfflineZone).
OFFLINE_ZONE_CHECK_INTERVAL_MS = 30_000
OFFLINE_ZONE_CONFIRM_CHECKS = 3
OFFLINE_ZONE_TRUST_KM = 100.0

FILE_PREFIX = "telemetry_"
WAYPOINT_PREFIX = "waypoints_"
EVENTS_PREFIX = "events_"
# A waypoint within this distance becomes the "poi" of telemetry rows.
POI_RADIUS_M = 2_000.0

STANDARD_GRAVITY_MPS2 = 9.80665
