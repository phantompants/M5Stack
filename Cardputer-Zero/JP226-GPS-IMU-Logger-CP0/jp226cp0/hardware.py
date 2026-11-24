"""Hardware defaults for the verified CardputerZero setup."""

import os

# --------------------------------------------------------------------------
# The GPS receiver is connected through the Cap's serial interface. Keep the
# other standard Linux serial names as fallbacks for alternate setups.
# --------------------------------------------------------------------------
GPS_SERIAL_CANDIDATES = (
    "/dev/serial0",
    "/dev/ttyAMA0",
    "/dev/ttyS0",
    "/dev/ttyUSB0",
    "/dev/ttyACM0",
)

# config::kCapGpsBaud first (the Cap's receiver), then the firmware's Grove
# scan list config::kGroveGpsBaudCandidates {9600, 115200, 38400, 4800, 19200,
# 57600} with duplicates removed.
GPS_BAUD_CAP = 115200
GPS_BAUD_GROVE_CANDIDATES = (9600, 115200, 38400, 4800, 19200, 57600)
GPS_BAUD_CANDIDATES = (GPS_BAUD_CAP,) + tuple(
    b for b in GPS_BAUD_GROVE_CANDIDATES if b != GPS_BAUD_CAP)

# How long to listen on each device/baud pair during auto-detection, and how
# many checksum-valid NMEA sentences count as "this is the GPS".
GPS_PROBE_LISTEN_SEC = 2.5      # firmware kGpsBaudScanIntervalMs
GPS_PROBE_MIN_SENTENCES = 2

# --------------------------------------------------------------------------
# Display.  [DOCS]  320x170 ST7789V on /dev/fb0, RGB565 (AppBuilder example
# Python_FrameBuffer_HelloWorld). The real size/bpp are read from the fbdev
# ioctls at start-up, so these are only the fallback for screenshots.
# --------------------------------------------------------------------------
FRAMEBUFFER_DEVICE = os.environ.get("APPLAUNCH_LINUX_FBDEV_DEVICE", "/dev/fb0")
SCREEN_WIDTH = 320
SCREEN_HEIGHT = 170

# Keyboard key names are Linux input-event-codes names. The left/right arrows
# are page-navigation aliases; settings and game controls are page-specific.
# --------------------------------------------------------------------------
KEYS_NEXT_PAGE = ("KEY_TAB", "KEY_RIGHTBRACE", "KEY_RIGHT", "KEY_C")
KEYS_PREV_PAGE = ("KEY_LEFTBRACE", "KEY_LEFT", "KEY_Z")
KEYS_WAYPOINT = ("KEY_W", "KEY_ENTER", "KEY_KPENTER")
KEYS_QUIT = ("KEY_ESC", "KEY_Q")
KEYS_SETTING_PREV = ("KEY_UP", "KEY_F")
KEYS_SETTING_NEXT = ("KEY_DOWN", "KEY_X")
KEYS_SETTING_DEC = ("KEY_A", "KEY_MINUS", "KEY_W")
KEYS_SETTING_INC = ("KEY_D", "KEY_EQUAL", "KEY_E")
KEYS_DINO_JUMP = ("KEY_SPACE", "KEY_ENTER", "KEY_KPENTER", "KEY_SEMICOLON")
KEYS_DINO_TOGGLE = ("KEY_B",)

# --------------------------------------------------------------------------
# Real-time clock / system clock. The
# logger therefore disciplines its own clock from GPS time (like the firmware)
# and only falls back to the system clock when no GPS time has been seen yet
# and the system year looks sane (>= 2024). Setting clock_source=gps forces
# GPS only.
# --------------------------------------------------------------------------
CLOCK_SOURCE_DEFAULT = "auto"  # auto | gps | system
