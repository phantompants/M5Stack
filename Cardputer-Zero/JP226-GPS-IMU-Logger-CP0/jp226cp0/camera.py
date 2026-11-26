"""Optional Raspberry Pi camera: waypoint photos, and the CAMERA page's live
preview and photos.

Two ways in, never at the same time (only one program can hold the camera):

- rpicam-still, for a waypoint photo when no preview is running.
- LiveCamera (picamera2), while the CAMERA page is open. It streams 320x240
  frames for the screen and takes full-size photos from the same session;
  a waypoint saved meanwhile (key, M5Dial) uses it too.

Verified on the device 2026-11-26: preview at about 13 frames/s, a 1640x1232
photo in 0.4 s, back to preview in 0.1 s.
"""

import os
import posixpath
import shutil
import subprocess
import threading
import time
from typing import Callable, Optional

PHOTO_DIR = "photos"
PREVIEW_SIZE = (320, 240)
PHOTO_SIZE = (1640, 1232)


def picamera2_available() -> bool:
    try:
        import picamera2  # noqa: F401
        return True
    except ImportError:
        return False


class LiveCamera:
    """Background thread that owns picamera2: preview frames plus stills."""

    def __init__(self, factory: Optional[Callable[[], object]] = None):
        self._factory = factory
        self._lock = threading.Lock()
        self._frame = None
        self._frames = 0
        self._request: Optional[list] = None     # [path, done Event, error]
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.status = "OFF"

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._frame = None
        self.status = "STARTING"
        self._thread = threading.Thread(target=self._run, name="camera",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None
        self._frame = None
        self.status = "OFF"

    def frame(self):
        """The newest preview frame as a Pillow image, or None."""
        with self._lock:
            return self._frame

    def capture(self, path: str, timeout_s: float = 8.0) -> None:
        """Take a full-size JPEG to `path` from the running preview."""
        if not self.running:
            raise RuntimeError("camera preview is not running")
        request = [path, threading.Event(), None]
        with self._lock:
            self._request = request
        if not request[1].wait(timeout_s):
            raise RuntimeError("camera capture timed out")
        if request[2]:
            raise RuntimeError(request[2])

    def _open(self):
        if self._factory is not None:
            return self._factory()
        from picamera2 import Picamera2  # optional, device only
        return Picamera2()

    def _run(self) -> None:
        try:
            from PIL import Image
            camera = self._open()
        except Exception as exc:  # ImportError, camera busy, no camera
            self.status = "NO CAMERA: {}".format(exc)[:40]
            return
        try:
            preview = camera.create_preview_configuration(
                main={"size": PREVIEW_SIZE, "format": "RGB888"}, buffer_count=3)
            still = camera.create_still_configuration(main={"size": PHOTO_SIZE})
            camera.configure(preview)
            camera.start()
            self.status = "LIVE"
            while not self._stop.is_set():
                with self._lock:
                    request, self._request = self._request, None
                if request is not None:
                    try:
                        camera.switch_mode_and_capture_file(still, request[0])
                    except Exception as exc:
                        request[2] = "camera capture failed: {}".format(exc)
                    request[1].set()
                    continue
                array = camera.capture_array()
                # picamera2's "RGB888" is stored B, G, R.
                image = Image.frombuffer(
                    "RGB", (array.shape[1], array.shape[0]), array.tobytes(),
                    "raw", "BGR", 0, 1)
                with self._lock:
                    self._frame = image
                    self._frames += 1
        except Exception as exc:
            self.status = "CAMERA ERROR: {}".format(exc)[:40]
        finally:
            with self._lock:
                request, self._request = self._request, None
            if request is not None:
                request[2] = "camera stopped"
                request[1].set()
            try:
                camera.stop()
            except Exception:
                pass
            try:
                camera.close()
            except Exception:
                pass


class CameraCapture:
    def __init__(self, data_dir: str, command: Optional[str] = None,
                 live: Optional[LiveCamera] = None):
        self.data_dir = data_dir
        self.command = command or shutil.which("rpicam-still")
        self.live = live if live is not None else LiveCamera()

    @property
    def available(self) -> bool:
        return bool(self.command) or self.live.running

    @property
    def photo_dir(self) -> str:
        return os.path.join(self.data_dir, PHOTO_DIR)

    def capture_waypoint(self, waypoint_id: str) -> str:
        return self.capture_named(waypoint_id)

    def capture_named(self, name: str) -> str:
        """Take a photo to photos/<name>.jpg; returns 'photos/<name>.jpg'."""
        if not self.live.running and not self.command:
            raise RuntimeError("rpicam-still is not installed")
        os.makedirs(self.photo_dir, exist_ok=True)
        filename = name + ".jpg"
        path = os.path.join(self.photo_dir, filename)
        if self.live.running:
            self.live.capture(path)
        else:
            self._rpicam_still(path)
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            raise RuntimeError("camera produced no photo")
        return posixpath.join(PHOTO_DIR, filename)

    def _rpicam_still(self, path: str) -> None:
        if not self.command:
            raise RuntimeError("rpicam-still is not installed")
        try:
            subprocess.run(
                [self.command, "--nopreview", "--timeout", "1000",
                 "--width", str(PHOTO_SIZE[0]), "--height", str(PHOTO_SIZE[1]),
                 "--output", path],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=20)
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip()
            raise RuntimeError("camera capture failed: {}".format(
                detail or "rpicam-still returned an error")) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("camera capture timed out") from exc

    def path_of(self, photo_reference: str) -> str:
        return os.path.join(self.data_dir, *photo_reference.split("/"))

    def remove_photo(self, photo_reference: str) -> None:
        try:
            os.remove(self.path_of(photo_reference))
        except FileNotFoundError:
            pass

    def list_photos(self):
        """Photo file names, newest first."""
        try:
            names = [n for n in os.listdir(self.photo_dir)
                     if n.lower().endswith((".jpg", ".jpeg"))]
        except OSError:
            return []
        def mtime(name):
            try:
                return os.path.getmtime(os.path.join(self.photo_dir, name))
            except OSError:
                return 0.0
        return sorted(names, key=lambda n: (mtime(n), n), reverse=True)


def wait_for_frame(live: LiveCamera, timeout_s: float = 3.0):
    """First preview frame (tests and the device check)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        frame = live.frame()
        if frame is not None:
            return frame
        time.sleep(0.02)
    return None
