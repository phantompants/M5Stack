"""JP226 GPS-IMU Logger for the M5Stack CardputerZero (Linux, Python 3).

Port of the ESP32 firmware at https://github.com/phantompants/JP226-GPS-IMU-Logger.
Only the standard library is needed for the core; Pillow, numpy, evdev and
pyserial are optional imports used on the device (or for screenshots).
"""

__version__ = "2026.11.06"
APP_TITLE = "JP226 GPS-IMU Logger"
