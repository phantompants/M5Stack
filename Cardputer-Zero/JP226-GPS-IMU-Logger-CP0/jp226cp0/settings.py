"""Settings: a simple key=value file plus environment overrides.

Search order for the file (first that exists wins):
  1. --config PATH on the command line
  2. $JP226_CP0_CONFIG
  3. ~/.config/jp226-gps-imu-logger/logger.cfg
  4. /etc/jp226-gps-imu-logger/logger.cfg

Keys mirror the firmware's logger.cfg where they exist:
  timezone       POSIX TZ rule (AEST-10AEDT,M10.1.0,M4.1.0/3) or an Australian
                 IANA name (or any IANA name if the system has tzdata)
  timezone_auto  true/false: follow the GPS position (default true)
New keys for this port:
  gps_device     serial device, e.g. /dev/ttyAMA0   (default: auto-detect)
  gps_baud       baud rate, e.g. 115200             (default: auto-detect)
  gps_source     cap | grove: LoRa-1262 GPS Cap or a Grove GPS (default cap;
                 the choice made on the GPS page is saved and wins)
  data_dir       where CSV files go                 (default: ~/telemetry)
  clock_source   auto | gps | system

Environment overrides (win over the file): JP226_CP0_TIMEZONE,
JP226_CP0_TIMEZONE_AUTO, JP226_CP0_GPS_DEVICE, JP226_CP0_GPS_BAUD,
JP226_CP0_DATA_DIR, JP226_CP0_CLOCK_SOURCE, JP226_CP0_GPS_SOURCE.

Wi-Fi is NOT configured here (the firmware's wifi_ssid/wifi_password keys are
deliberately ignored and never stored): the CardputerZero is a Linux machine,
so join networks with the OS tools. Never put a Wi-Fi password in this repo.
"""

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import gps_power, hardware

ENV_PREFIX = "JP226_CP0_"
IGNORED_KEYS = ("wifi_ssid", "wifi_password")


@dataclass
class Settings:
    timezone: str = ""
    timezone_auto: bool = True
    gps_device: str = ""          # "" = auto-detect
    gps_baud: int = 0             # 0 = auto-detect
    gps_source: str = gps_power.CAP   # cap | grove
    data_dir: str = ""            # "" = default_data_dir()
    clock_source: str = hardware.CLOCK_SOURCE_DEFAULT
    config_path: str = ""         # file actually used, "" if none
    warnings: List[str] = field(default_factory=list)


def parse_bool(text: str, fallback: bool) -> bool:
    value = text.strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    return fallback


def parse_cfg(text: str) -> Dict[str, str]:
    """key=value lines; '#' and ';' start comments; keys are case-insensitive."""
    result = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not key.strip():
            continue
        result[key.strip().lower()] = value.strip()
    return result


def default_config_paths(env) -> List[str]:
    paths = []
    if env.get(ENV_PREFIX + "CONFIG"):
        paths.append(env[ENV_PREFIX + "CONFIG"])
    paths.append(os.path.join(os.path.expanduser("~"), ".config",
                              "jp226-gps-imu-logger", "logger.cfg"))
    paths.append("/etc/jp226-gps-imu-logger/logger.cfg")
    return paths


def default_data_dir() -> str:
    """~/telemetry. When started with sudo (the AppBuilder run.sh does) the
    invoking user's home is used instead of /root. [ASSUMED: how APPLaunch
    starts the app, and which user, is unconfirmed - override with data_dir.]"""
    home = os.path.expanduser("~")
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and sudo_user != "root":
        try:
            import pwd
            home = pwd.getpwnam(sudo_user).pw_dir
        except (ImportError, KeyError):
            pass
    return os.path.join(home, "telemetry")


def load_settings(config_path: Optional[str] = None,
                  env: Optional[Dict[str, str]] = None) -> Settings:
    env = os.environ if env is None else env
    settings = Settings()
    candidates = [config_path] if config_path else default_config_paths(env)
    values: Dict[str, str] = {}
    for path in candidates:
        if path and os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    values = parse_cfg(handle.read())
                settings.config_path = path
            except OSError as exc:
                settings.warnings.append("cannot read {}: {}".format(path, exc))
            break
    else:
        if config_path:
            settings.warnings.append("config file not found: " + config_path)

    for key in IGNORED_KEYS:
        if key in values:
            values.pop(key)  # dropped immediately: never kept or logged
            settings.warnings.append(
                "{} ignored: configure Wi-Fi in the operating system".format(key))

    for name in ("timezone", "timezone_auto", "gps_device", "gps_baud",
                 "gps_source", "data_dir", "clock_source"):
        override = env.get(ENV_PREFIX + name.upper())
        if override is not None and override != "":
            values[name] = override

    settings.timezone = values.get("timezone", "")
    settings.timezone_auto = parse_bool(values.get("timezone_auto", "true"), True)
    settings.gps_device = values.get("gps_device", "")
    baud = values.get("gps_baud", "")
    if baud:
        if baud.isdigit() and int(baud) > 0:
            settings.gps_baud = int(baud)
        else:
            settings.warnings.append("gps_baud ignored (not a number): " + baud)
    settings.data_dir = values.get("data_dir", "")
    if "gps_source" in values:
        source = gps_power.normalise(values["gps_source"], "")
        if source:
            settings.gps_source = source
        else:
            settings.warnings.append(
                "gps_source ignored (use cap or grove): " + values["gps_source"])
    source = values.get("clock_source", settings.clock_source).lower()
    if source in ("auto", "gps", "system"):
        settings.clock_source = source
    else:
        settings.warnings.append("clock_source ignored: " + source)
    return settings
