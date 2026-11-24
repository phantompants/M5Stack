"""Optional Raspberry Pi camera capture for waypoint photos."""

import os
import posixpath
import shutil
import subprocess
from typing import Optional


class CameraCapture:
    def __init__(self, data_dir: str, command: Optional[str] = None):
        self.data_dir = data_dir
        self.command = command or shutil.which("rpicam-still")

    @property
    def available(self) -> bool:
        return bool(self.command)

    def capture_waypoint(self, waypoint_id: str) -> str:
        if not self.command:
            raise RuntimeError("rpicam-still is not installed")
        photo_dir = os.path.join(self.data_dir, "photos")
        os.makedirs(photo_dir, exist_ok=True)
        filename = waypoint_id + ".jpg"
        path = os.path.join(photo_dir, filename)
        try:
            subprocess.run(
                [self.command, "--nopreview", "--timeout", "1000",
                 "--width", "1640", "--height", "1232",
                 "--output", path],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=20)
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip()
            raise RuntimeError("camera capture failed: {}".format(
                detail or "rpicam-still returned an error")) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("camera capture timed out") from exc
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            raise RuntimeError("camera produced no photo")
        return posixpath.join("photos", filename)

    def remove_photo(self, photo_reference: str) -> None:
        path = os.path.join(self.data_dir, *photo_reference.split("/"))
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
