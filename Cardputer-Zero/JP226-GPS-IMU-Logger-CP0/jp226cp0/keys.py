"""Keyboard input: evdev (optional) -> actions.

The key-name -> action mapping is pure logic (action_for) and unit-tested; the
evdev reader is UNVERIFIED on the CardputerZero (the matrix keyboard's key codes
are not known - see hardware.py; the DIAG page shows the last raw key name).
"""

from typing import List, Optional

from . import hardware

NEXT_PAGE = "next"
PREV_PAGE = "prev"
WAYPOINT = "waypoint"
QUIT = "quit"


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
