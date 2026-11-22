"""EVERY HARDWARE ASSUMPTION LIVES IN THIS FILE.

Nothing here has been tested on a CardputerZero: the device had not arrived
when this was written. Each item is marked [ASSUMED] (a guess to be confirmed
next week) or [DOCS] (taken from M5Stack / AppBuilder documentation but still
not verified by us). The README lists how to confirm each one on the device.

Everything is overridable from the config file / environment (settings.py), so
a wrong guess here should never need a code change.
"""

import os

# --------------------------------------------------------------------------
# GPS serial port.  [ASSUMED]
#
# The vehicle GPS is the ATGM336H-6N on the M5 Cap LoRa-1262 (NMEA, 115200
# baud in the ESP32 firmware, config::kCapGpsBaud). HOW THAT CAP (or any other
# NMEA receiver) ATTACHES TO THE CARDPUTERZERO IS UNKNOWN. M5Stack docs say the
# 14-pin expansion header has UART TX/RX on G14/G15, and there is a Grove port
# that can be a UART. Which /dev node those become is unknown (a Raspberry Pi
# CM0 usually exposes the PL011 as /dev/ttyAMA0 or via the /dev/serial0
# symlink, a mini-UART as /dev/ttyS0, and USB adapters as ttyUSB0/ttyACM0).
# So the app tries these in order and picks the first that produces valid NMEA.
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

# --------------------------------------------------------------------------
# Keyboard.  [ASSUMED]  46-key matrix read through evdev. We do not know the
# key codes the matrix driver emits (e.g. whether there is a Tab key or [ ]
# keys at all, or whether Fn layers send different codes). The DIAG page shows
# the last raw key name so the mapping can be fixed next week. Names are
# linux/input-event-codes.h names; change them here only.
# --------------------------------------------------------------------------
KEYS_NEXT_PAGE = ("KEY_TAB", "KEY_RIGHTBRACE")
KEYS_PREV_PAGE = ("KEY_LEFTBRACE",)
KEYS_WAYPOINT = ("KEY_W", "KEY_ENTER", "KEY_KPENTER")
KEYS_QUIT = ("KEY_ESC", "KEY_Q")

# --------------------------------------------------------------------------
# IMU.  [ASSUMED: NOT WIRED IN]  The CardputerZero has an IMU but we do NOT
# know its chip or bus, so none is read. imu_available is 0 and the IMU
# columns stay empty until a reader implementing imu.ImuReader is plugged in
# (see imu.py and the README).
# --------------------------------------------------------------------------
IMU_READER_FACTORY = None  # e.g. "mypkg.mychip:make_reader"; unused for now

# --------------------------------------------------------------------------
# Real-time clock / system clock.  [ASSUMED]  The CardputerZero has an RTC,
# but whether Linux exposes it as the system clock at boot is unknown. The
# logger therefore disciplines its own clock from GPS time (like the firmware)
# and only falls back to the system clock when no GPS time has been seen yet
# and the system year looks sane (>= 2024). Setting clock_source=gps forces
# GPS only.
# --------------------------------------------------------------------------
CLOCK_SOURCE_DEFAULT = "auto"  # auto | gps | system
