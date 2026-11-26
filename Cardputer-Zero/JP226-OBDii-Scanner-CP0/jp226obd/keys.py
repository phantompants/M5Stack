"""Keyboard input (evdev -> actions) for CardputerZero, including keys 4-8."""

from typing import Dict, List, Optional, Tuple

from . import hardware

NEXT_PAGE = "next"
PREV_PAGE = "prev"
UP = "up"
DOWN = "down"
DEC = "dec"
INC = "inc"
ACTION = "action"
REFRESH = "refresh"
QUIT = "quit"

# Contextual number keys 4-8 (positioned directly beneath the 320x170 LCD).
CYCLE_LINK = "cycle_link"
CYCLE_TARGET = "cycle_target"
CYCLE_PROTOCOL = "cycle_protocol"
CYCLE_MODE = "cycle_mode"
TOGGLE_SCAN = "toggle_scan"
TOGGLE_LIVE = "toggle_live"
GO_LIVE = "go_live"
GO_MODULES = "go_modules"
EV_SCAN = "ev_scan"
FULL_SCAN = "full_scan"
SCROLL_UP = "scroll_up"
SCROLL_DOWN = "scroll_down"
REFRESH_DEVS = "refresh_devs"
TOGGLE_WEB = "toggle_web"
CYCLE_WEB_PORT = "cycle_web_port"
GO_SHARE = "go_share"
GO_DINO = "go_dino"
DINO_JUMP = "dino_jump"
DINO_TOGGLE = "dino_toggle"

KIND_NAV = "nav"
KIND_EDIT = "edit"
KIND_RUN = "run"
KIND_DIM = "dim"

# page name -> ((key_name, short_label, action, kind), ...)
PAGE_ACTIONS: Dict[str, Tuple[Tuple[str, str, str, str], ...]] = {
    "CONNECT": (
        ("KEY_4", "LINK", CYCLE_LINK, KIND_EDIT),
        ("KEY_5", "TGT", CYCLE_TARGET, KIND_EDIT),
        ("KEY_6", "PROT", CYCLE_PROTOCOL, KIND_EDIT),
        ("KEY_7", "MODE", CYCLE_MODE, KIND_EDIT),
        ("KEY_8", "SCAN", TOGGLE_SCAN, KIND_RUN),
    ),
    "SCAN": (
        ("KEY_4", "RFRSH", REFRESH_DEVS, KIND_EDIT),
        ("KEY_5", "MODE", CYCLE_MODE, KIND_EDIT),
        ("KEY_7", "LIVE", GO_LIVE, KIND_NAV),
        ("KEY_8", "SCAN", TOGGLE_SCAN, KIND_RUN),
    ),
    "LIVE": (
        ("KEY_4", "LINK", CYCLE_LINK, KIND_EDIT),
        ("KEY_6", "SCAN", TOGGLE_SCAN, KIND_NAV),
        ("KEY_8", "LIVE", TOGGLE_LIVE, KIND_RUN),
    ),
    "LOGGER FIT": (
        ("KEY_4", "SCAN", TOGGLE_SCAN, KIND_NAV),
        ("KEY_6", "MODS", GO_MODULES, KIND_NAV),
        ("KEY_8", "LIVE", GO_LIVE, KIND_RUN),
    ),
    "EV SOUND FIT": (
        ("KEY_4", "EVSCN", EV_SCAN, KIND_RUN),
        ("KEY_6", "MODS", GO_MODULES, KIND_NAV),
        ("KEY_8", "LIVE", GO_LIVE, KIND_RUN),
    ),
    "MODULES": (
        ("KEY_4", "UP", SCROLL_UP, KIND_EDIT),
        ("KEY_5", "DOWN", SCROLL_DOWN, KIND_EDIT),
        ("KEY_6", "FULL", FULL_SCAN, KIND_EDIT),
        ("KEY_8", "SCAN", TOGGLE_SCAN, KIND_RUN),
    ),
    "SHARE": (
        ("KEY_4", "WEB", TOGGLE_WEB, KIND_RUN),
        ("KEY_5", "PORT", CYCLE_WEB_PORT, KIND_EDIT),
        ("KEY_8", "SCAN", TOGGLE_SCAN, KIND_NAV),
    ),
    "DIAG": (
        ("KEY_4", "RFRSH", REFRESH_DEVS, KIND_EDIT),
        ("KEY_6", "SHARE", GO_SHARE, KIND_NAV),
        ("KEY_8", "SCAN", TOGGLE_SCAN, KIND_RUN),
    ),
    "FOSSIL RECORD": (
        ("KEY_4", "SCAN", TOGGLE_SCAN, KIND_NAV),
        ("KEY_8", "DINO", GO_DINO, KIND_RUN),
    ),
    "DINO DASH": (
        ("KEY_4", "DINO", DINO_TOGGLE, KIND_EDIT),
        ("KEY_8", "JUMP", DINO_JUMP, KIND_RUN),
    ),
}


def action_for(key_name: str) -> Optional[str]:
    if key_name in hardware.KEYS_NEXT_PAGE:
        return NEXT_PAGE
    if key_name in hardware.KEYS_PREV_PAGE:
        return PREV_PAGE
    if key_name in hardware.KEYS_UP:
        return UP
    if key_name in hardware.KEYS_DOWN:
        return DOWN
    if key_name in hardware.KEYS_DEC:
        return DEC
    if key_name in hardware.KEYS_INC:
        return INC
    if key_name in hardware.KEYS_ACTION:
        return ACTION
    if key_name in hardware.KEYS_REFRESH:
        return REFRESH
    if key_name in hardware.KEYS_QUIT:
        return QUIT
    return None


def page_actions(page: str) -> Tuple[Tuple[str, str, str, str], ...]:
    return PAGE_ACTIONS.get(page, ())


def page_action_for(page: str, key_name: str) -> Optional[str]:
    """Return contextual button 4-8 action for the active page, or None."""
    for name, _label, action, _kind in page_actions(page):
        if name == key_name:
            return action
    return None


class EvdevKeys:
    """Non-blocking key-down reader across evdev keyboard devices."""

    def __init__(self):
        from evdev import InputDevice, ecodes, list_devices
        self._ecodes = ecodes
        self.devices = []
        supported = (
            ecodes.KEY_ESC, ecodes.KEY_Q, ecodes.KEY_LEFT, ecodes.KEY_RIGHT,
            ecodes.KEY_UP, ecodes.KEY_DOWN, ecodes.KEY_ENTER, ecodes.KEY_SPACE,
            ecodes.KEY_W, ecodes.KEY_E, ecodes.KEY_A, ecodes.KEY_D,
            ecodes.KEY_F, ecodes.KEY_X, ecodes.KEY_Z, ecodes.KEY_C,
            ecodes.KEY_R, ecodes.KEY_TAB, ecodes.KEY_B, ecodes.KEY_SEMICOLON,
            ecodes.KEY_4, ecodes.KEY_5, ecodes.KEY_6, ecodes.KEY_7,
            ecodes.KEY_8,
        )
        for path in list_devices():
            try:
                dev = InputDevice(path)
                caps = dev.capabilities().get(ecodes.EV_KEY, [])
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
