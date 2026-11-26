"""Keyboard input: evdev (optional) -> actions.

The key-name -> action mapping is pure logic (action_for) and unit-tested; the
evdev reader is UNVERIFIED on the CardputerZero (the matrix keyboard's key codes
are not known - see hardware.py; the DIAG page shows the last raw key name).
"""

from typing import Dict, List, Optional, Tuple

from . import hardware

NEXT_PAGE = "next"
PREV_PAGE = "prev"
WAYPOINT = "waypoint"
QUIT = "quit"

# Number keys 4-8 act on the current page; they never change pages.
CHANGE_MAKE = "change_make"
CHANGE_MODEL = "change_model"
CHANGE_YEAR = "change_year"
SAVE_PROFILE = "save_profile"
OBD_CONNECT = "obd_connect"
SAVE_SETUP = "save_setup"
GPS_SOURCE = "gps_source"
SHARE_WEB = "share_web"
ZIP_DAYS_DOWN = "zip_days_down"
ZIP_DAYS_UP = "zip_days_up"
EXPORT_NOW = "export_now"
HOTSPOT = "hotspot"
TAKE_PHOTO = "take_photo"
GALLERY_PREV = "gallery_prev"
GALLERY_NEXT = "gallery_next"
ENV_GROVE = "env_grove"

# Footer colour groups (see ui._KIND_COLOURS).
KIND_NAV = "nav"
KIND_EDIT = "edit"
KIND_SAVE = "save"
KIND_UNAVAILABLE = "unavailable"

_WAYPOINT_ACTIONS = (("KEY_7", "WP", WAYPOINT, KIND_SAVE),)

# page name -> ((key name, short label, action, kind), ...)
PAGE_ACTIONS: Dict[str, Tuple[Tuple[str, str, str, str], ...]] = {
    "SPEED": _WAYPOINT_ACTIONS,
    "GPS": (("KEY_4", "SOURCE", GPS_SOURCE, KIND_EDIT),) + _WAYPOINT_ACTIONS,
    "IMU": _WAYPOINT_ACTIONS,
    "ENV": (("KEY_4", "GROVE", ENV_GROVE, KIND_EDIT),) + _WAYPOINT_ACTIONS,
    "ESP-NOW": _WAYPOINT_ACTIONS,
    "LOGGER": _WAYPOINT_ACTIONS,
    "SHARE": (
        ("KEY_4", "WEB", SHARE_WEB, KIND_EDIT),
        ("KEY_5", "DAYS-", ZIP_DAYS_DOWN, KIND_EDIT),
        ("KEY_6", "DAYS+", ZIP_DAYS_UP, KIND_EDIT),
        ("KEY_7", "EXPORT", EXPORT_NOW, KIND_SAVE),
        ("KEY_8", "HOTSPOT", HOTSPOT, KIND_EDIT),
    ),
    "WAYPOINT": _WAYPOINT_ACTIONS,
    "CAMERA": (("KEY_7", "PHOTO", TAKE_PHOTO, KIND_SAVE),),
    "GALLERY": (
        ("KEY_4", "PREV", GALLERY_PREV, KIND_EDIT),
        ("KEY_5", "NEXT", GALLERY_NEXT, KIND_EDIT),
    ),
    "SETUP": (("KEY_7", "SAVE", SAVE_SETUP, KIND_SAVE),),
    "OBD": (
        ("KEY_4", "MAKE", CHANGE_MAKE, KIND_EDIT),
        ("KEY_5", "MODEL", CHANGE_MODEL, KIND_EDIT),
        ("KEY_6", "YEAR", CHANGE_YEAR, KIND_EDIT),
        ("KEY_7", "SAVE", SAVE_PROFILE, KIND_SAVE),
        ("KEY_8", "LINK", OBD_CONNECT, KIND_UNAVAILABLE),
    ),
    "DIAG": _WAYPOINT_ACTIONS,
    "FOSSIL RECORD": _WAYPOINT_ACTIONS,
    "DINO DASH": (),
}


def action_for(key_name: str) -> Optional[str]:
    if key_name in hardware.KEYS_NEXT_PAGE:
        return NEXT_PAGE
    if key_name in hardware.KEYS_PREV_PAGE:
        return PREV_PAGE
    if key_name in hardware.KEYS_WAYPOINT:
        return WAYPOINT
    if key_name in hardware.KEYS_QUIT:
        return QUIT
    return None


def page_actions(page: str) -> Tuple[Tuple[str, str, str, str], ...]:
    return PAGE_ACTIONS.get(page, ())


def page_action_for(page: str, key_name: str) -> Optional[str]:
    """Contextual number-key action on this page, or None (never a page jump)."""
    for name, _label, action, _kind in page_actions(page):
        if name == key_name:
            return action
    return None


class EvdevKeys:
    """Non-blocking key presses from every evdev device that has letter keys."""

    def __init__(self):
        from evdev import InputDevice, ecodes, list_devices  # optional import
        self._ecodes = ecodes
        self.devices = []
        for path in list_devices():
            try:
                dev = InputDevice(path)
                caps = dev.capabilities().get(ecodes.EV_KEY, [])
                supported = (
                    ecodes.KEY_ESC, ecodes.KEY_Q, ecodes.KEY_LEFT,
                    ecodes.KEY_RIGHT, ecodes.KEY_W, ecodes.KEY_ENTER,
                    ecodes.KEY_F, ecodes.KEY_Z, ecodes.KEY_X, ecodes.KEY_C,
                    ecodes.KEY_E, ecodes.KEY_B, ecodes.KEY_UP, ecodes.KEY_DOWN,
                    ecodes.KEY_4, ecodes.KEY_5, ecodes.KEY_6, ecodes.KEY_7,
                    ecodes.KEY_8,
                )
                if any(code in caps for code in supported):
                    self.devices.append(dev)
                else:
                    dev.close()
            except (OSError, PermissionError):
                pass

    def _name(self, code: int) -> str:
        name = self._ecodes.KEY.get(code, "KEY_{}".format(code))
        return name[0] if isinstance(name, (list, tuple)) else name

    def poll(self) -> List[str]:
        """Names of keys pressed since the last call (key-down only)."""
        import select
        pressed: List[str] = []
        if not self.devices:
            return pressed
        ready, _, _ = select.select([d.fd for d in self.devices], [], [], 0)
        for dev in self.devices:
            if dev.fd in ready:
                try:
                    for ev in dev.read():
                        if ev.type == self._ecodes.EV_KEY and ev.value == 1:
                            pressed.append(self._name(ev.code))
                except BlockingIOError:
                    pass
        return pressed

    def close(self) -> None:
        for dev in self.devices:
            try:
                dev.close()
            except OSError:
                pass
