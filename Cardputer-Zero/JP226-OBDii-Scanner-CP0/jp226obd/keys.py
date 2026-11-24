"""Keyboard input (evdev -> actions) for CardputerZero."""

from typing import List, Optional

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
            ecodes.KEY_R, ecodes.KEY_TAB,
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
