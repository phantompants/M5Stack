"""Optional Wi-Fi hotspot, so a phone can reach the SHARE page with no other
network (in the car, in the bush).

NetworkManager does the work (``nmcli``): a connection profile called
JP226-Hotspot puts wlan0 in access-point mode with WPA2 and its own DHCP
(``ipv4.method shared``, so the Zero is 10.42.0.1). The password is made on
the device the first time and kept only in that NetworkManager profile, like
any other OS Wi-Fi setting; the app never saves it. While the hotspot is on,
wlan0 is not on your normal Wi-Fi; turning it off lets NetworkManager rejoin.

The app runs as a background user service, which polkit does not treat as the
active local user, so the package installs a narrow polkit rule that lets
``pi`` change Wi-Fi (packaging/polkit-hotspot.rules).
"""

import secrets
import shutil
import subprocess
import threading
from typing import Callable, List, Optional

CONNECTION = "JP226-Hotspot"
INTERFACE = "wlan0"
# No 0/O or 1/l/I, so the password is easy to read off the screen.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"


def make_password(length: int = 10) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def ssid_for(mac: str) -> str:
    tail = mac.replace(":", "").upper()[-4:]
    return "JP226-CZ-" + (tail or "0000")


def wifi_qr_text(ssid: str, password: str) -> str:
    """What an iPhone camera reads as 'join this network'."""
    def escape(text):
        return "".join("\\" + c if c in '\\;,:"' else c for c in text)
    return "WIFI:T:WPA;S:{};P:{};;".format(escape(ssid), escape(password))


class Hotspot:
    def __init__(self, run: Callable = subprocess.run,
                 which: Callable[[str], Optional[str]] = shutil.which,
                 read_mac: Optional[Callable[[], str]] = None):
        self._run = run
        self.nmcli = which("nmcli")
        self._read_mac = read_mac or self._mac_from_sysfs
        self.ssid = ""
        self.password = ""
        self.address = ""
        self.active = False
        self.busy = False
        self.error = ""
        self.last_detail = ""
        self._thread: Optional[threading.Thread] = None

    @property
    def available(self) -> bool:
        return bool(self.nmcli)

    @staticmethod
    def _mac_from_sysfs() -> str:
        try:
            with open("/sys/class/net/{}/address".format(INTERFACE)) as handle:
                return handle.read().strip()
        except OSError:
            return ""

    def _nmcli(self, *args: str, secret: bool = False) -> str:
        command = [self.nmcli] + (["-s"] if secret else []) + list(args)
        result = self._run(command, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, text=True, timeout=30)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            if "not authorized" in detail.lower() or "permission" in detail.lower():
                raise PermissionError(detail)
            raise RuntimeError(detail or "nmcli failed")
        return result.stdout

    # -- background toggling (nmcli up/down takes a few seconds) -----------
    def toggle(self) -> None:
        if self.busy or not self.available:
            if not self.available:
                self.error = "NO NETWORKMANAGER"
            return
        self.busy = True
        target = self.stop if self.active else self.start
        self._thread = threading.Thread(target=self._guarded, args=(target,),
                                        name="hotspot", daemon=True)
        self._thread.start()

    def _guarded(self, action: Callable[[], None]) -> None:
        try:
            action()
        finally:
            self.busy = False

    def start(self) -> bool:
        self.error = ""
        try:
            if CONNECTION not in self._profiles():
                self.ssid = ssid_for(self._read_mac())
                self.password = make_password()
                self._nmcli(
                    "connection", "add", "type", "wifi", "ifname", INTERFACE,
                    "con-name", CONNECTION, "autoconnect", "no",
                    "ssid", self.ssid, "mode", "ap",
                    "802-11-wireless.band", "bg", "ipv4.method", "shared",
                    "ipv6.method", "disabled",
                    "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.proto", "rsn",
                    "wifi-sec.pairwise", "ccmp", "wifi-sec.group", "ccmp",
                    "wifi-sec.psk", self.password)
            self._nmcli("connection", "up", CONNECTION)
            self.refresh()
            return self.active
        except PermissionError:
            self.error = "NO PERMISSION"
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            self.error = "HOTSPOT ERROR"
            self.last_detail = str(exc)
        return False

    def stop(self) -> None:
        self.error = ""
        try:
            self._nmcli("connection", "down", CONNECTION)
        except PermissionError:
            self.error = "NO PERMISSION"
        except (RuntimeError, OSError, subprocess.SubprocessError):
            pass  # already down
        self.refresh()

    def _profiles(self) -> List[str]:
        return self._nmcli("-t", "-f", "NAME", "connection", "show").splitlines()

    def refresh(self) -> None:
        """Re-read state, SSID, password and address from NetworkManager."""
        if not self.available:
            self.active = False
            return
        try:
            active = self._nmcli("-t", "-f", "NAME", "connection", "show",
                                 "--active").splitlines()
            self.active = CONNECTION in active
            if CONNECTION in self._profiles():
                values = self._nmcli(
                    "-g", "802-11-wireless.ssid,802-11-wireless-security.psk",
                    "connection", "show", CONNECTION, secret=True).splitlines()
                if values:
                    self.ssid = values[0].replace("\\:", ":")
                if len(values) > 1 and values[1]:
                    self.password = values[1].replace("\\:", ":")
            self.address = ""
            if self.active:
                for line in self._nmcli("-g", "IP4.ADDRESS", "device", "show",
                                        INTERFACE).splitlines():
                    if line.strip():
                        self.address = line.split("/")[0].strip()
                        break
        except PermissionError:
            self.error = "NO PERMISSION"
        except (RuntimeError, OSError, subprocess.SubprocessError):
            self.active = False
