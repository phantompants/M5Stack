"""Local Wi-Fi SHARE server: download logs, KML/GPX and ZIPs from a phone.

Open http://<device IP>:8226/ (or http://cardputerzero.local:8226/) in Safari.
Read-only and LAN-only by design: it serves just the daily log files, their
KML/GPX exports and the photos from the data directory - nothing else,
no uploads, no settings.

Photos: /photos is a gallery. Tapping one opens it at full size (served
inline), where Safari's share button offers "Save to Photos"; the GPS EXIF
comes along, so iPhone Photos shows where it was taken. /photos.zip has them
all for the Files app. There is no login, so turn it off (SHARE page, key 4)
on networks you do not trust. Standard library only.
"""

import datetime
import html
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from . import APP_TITLE, __version__, export

# 8226 for JP226. Not 8080: RaspyJack uses that on the same device.
DEFAULT_PORT = 8226
ZIP_DAY_CHOICES = (1, 3, 7, 14, 30, 0)   # 0 = all days

_LOG_FILE = re.compile(r"^(telemetry|waypoints|events)_\d{4}-\d{2}-\d{2}\.csv$")
_EXPORT_FILE = re.compile(r"^track_(\d{4}-\d{2}-\d{2})\.(kml|gpx)$")
_PHOTO_FILE = re.compile(r"^[A-Za-z0-9_-]{1,40}\.jpe?g$")
_TYPES = {
    ".csv": "text/csv; charset=utf-8",
    ".kml": "application/vnd.google-earth.kml+xml",
    ".gpx": "application/gpx+xml",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".zip": "application/zip",
}


def local_addresses() -> List[str]:
    """IPv4 addresses other devices can use, best guess first."""
    found: List[str] = []
    try:
        # A UDP "connect" picks the outgoing interface without sending anything.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("10.255.255.255", 1))
            found.append(probe.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if address not in found:
                found.append(address)
    except OSError:
        pass
    return [a for a in found if not a.startswith("127.")]


def interface_label(name: str) -> str:
    if name.startswith(("eth", "en", "usb")):
        return "WIRED"
    if name.startswith(("wlan", "wl")):
        return "WI-FI"
    return name.upper()[:8]


def parse_ip_addr(text: str) -> List[Tuple[str, str]]:
    """(interface, IPv4) pairs from `ip -4 -o addr show` output."""
    found = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[2] == "inet":
            name, address = parts[1], parts[3].split("/")[0]
            if name != "lo" and not address.startswith("127."):
                found.append((name, address))
    return found


def interface_addresses(run=subprocess.run) -> List[Tuple[str, str]]:
    """(label, IPv4) for every network the Zero is on, wired first; falls
    back to local_addresses() where `ip` is not available."""
    try:
        result = run(["ip", "-4", "-o", "addr", "show"], stdout=subprocess.PIPE,
                     stderr=subprocess.DEVNULL, text=True, timeout=3)
        pairs = parse_ip_addr(result.stdout)
    except (OSError, subprocess.SubprocessError):
        pairs = []
    if not pairs:
        return [("", a) for a in local_addresses()]
    pairs.sort(key=lambda p: (interface_label(p[0]) != "WIRED",
                              interface_label(p[0]) != "WI-FI", p[0]))
    return [(interface_label(name), address) for name, address in pairs]


def zip_label(days: int) -> str:
    return "ALL" if days <= 0 else str(days)


def _size(path: str) -> str:
    try:
        size = os.path.getsize(path)
    except OSError:
        return ""
    for unit in ("B", "KB", "MB"):
        if size < 1024 or unit == "MB":
            return "{:.0f} {}".format(size, unit) if unit == "B" else \
                "{:.1f} {}".format(size, unit)
        size /= 1024.0
    return ""


class _Handler(BaseHTTPRequestHandler):
    server_version = "JP226Share/" + __version__
    share: "WebShare" = None  # set per server

    def log_message(self, fmt, *args):  # keep the console quiet
        pass

    # -- routing ---------------------------------------------------------
    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        try:
            if path == "/":
                self._send_html(self.share.index_html())
            elif path == "/zip":
                query = parse_qs(url.query)
                try:
                    days = int(query.get("days", [self.share.zip_days()])[0])
                except ValueError:
                    days = self.share.zip_days()
                self._send_zip(days)
            elif path == "/photos":
                self._send_html(self.share.photos_html())
            elif path == "/photos.zip":
                self._send_photo_zip()
            elif path.startswith("/v/photos/"):
                self._send_photo(path[len("/v/photos/"):], thumbnail=False)
            elif path.startswith("/t/photos/"):
                self._send_photo(path[len("/t/photos/"):], thumbnail=True)
            elif path.startswith("/f/"):
                self._send_named(path[3:])
            else:
                self.send_error(404)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_HEAD(self):
        self.send_error(405)

    # -- responses -------------------------------------------------------
    def _send_html(self, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: str, download_name: str) -> None:
        ext = os.path.splitext(download_name)[1].lower()
        self.send_response(200)
        self.send_header("Content-Type", _TYPES.get(ext, "application/octet-stream"))
        self.send_header("Content-Disposition",
                         'attachment; filename="{}"'.format(download_name))
        self.send_header("Content-Length", str(os.path.getsize(path)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with open(path, "rb") as handle:
            shutil.copyfileobj(handle, self.wfile)

    def _send_photo(self, leaf: str, thumbnail: bool) -> None:
        """A photo shown in the page (inline), or its small preview."""
        if not _PHOTO_FILE.match(leaf):
            return self.send_error(404)
        path = os.path.join(self.share.photo_dir, leaf)
        if not os.path.isfile(path):
            return self.send_error(404)
        if thumbnail:
            path = self.share.thumbnail(path) or path
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Disposition",
                         'inline; filename="{}"'.format(leaf))
        self.send_header("Content-Length", str(os.path.getsize(path)))
        self.send_header("Cache-Control", "max-age=3600")
        self.end_headers()
        with open(path, "rb") as handle:
            shutil.copyfileobj(handle, self.wfile)

    def _send_photo_zip(self) -> None:
        names = self.share.photo_names()
        if not names:
            return self.send_error(404, "No photos yet")
        with tempfile.TemporaryFile() as spool:
            # JPEGs are already compressed: store them.
            with zipfile.ZipFile(spool, "w", zipfile.ZIP_STORED) as archive:
                for name in names:
                    archive.write(os.path.join(self.share.photo_dir, name),
                                  "photos/" + name)
            size = spool.tell()
            spool.seek(0)
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition",
                             'attachment; filename="jp226_photos.zip"')
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            shutil.copyfileobj(spool, self.wfile)

    def _send_named(self, name: str) -> None:
        data_dir = self.share.data_dir
        if name.startswith("photos/"):
            leaf = name[len("photos/"):]
            if not _PHOTO_FILE.match(leaf):
                return self.send_error(404)
            path = os.path.join(data_dir, "photos", leaf)
        elif name.startswith(export.EXPORT_DIR + "/"):
            leaf = name[len(export.EXPORT_DIR) + 1:]
            match = _EXPORT_FILE.match(leaf)
            if not match:
                return self.send_error(404)
            date = match.group(1)
            with self.share.export_lock:
                if export.needs_export(data_dir, date):
                    export.export_day(data_dir, date)
            path = os.path.join(export.export_dir(data_dir), leaf)
        elif _LOG_FILE.match(name):
            leaf = name
            path = os.path.join(data_dir, name)
        else:
            return self.send_error(404)
        if not os.path.isfile(path):
            return self.send_error(404)
        self._send_file(path, leaf)

    def _send_zip(self, days: int) -> None:
        dates = export.last_days(self.share.data_dir, days)
        if not dates:
            return self.send_error(404, "No logs yet")
        name = "jp226_logs_{}_to_{}.zip".format(dates[-1], dates[0])
        with tempfile.TemporaryFile() as spool:
            with self.share.export_lock:
                export.write_zip(self.share.data_dir, dates, spool)
            size = spool.tell()
            spool.seek(0)
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition",
                             'attachment; filename="{}"'.format(name))
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            shutil.copyfileobj(spool, self.wfile)


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>JP226 Logs</title>
<style>
:root{{color-scheme:light dark;--bg:#f4f5f8;--card:#fff;--text:#14161c;--dim:#667;
--accent:#1f6feb;--line:#dde0e6}}
@media (prefers-color-scheme:dark){{:root{{--bg:#08090f;--card:#161b28;--text:#e6ebf5;
--dim:#8b93a7;--accent:#5aaaff;--line:#252c3d}}}}
*{{box-sizing:border-box}}
body{{margin:0;font:16px -apple-system,system-ui,sans-serif;background:var(--bg);color:var(--text)}}
main{{max-width:640px;margin:0 auto;padding:16px}}
h1{{font-size:22px;margin:4px 0 2px}} .sub{{color:var(--dim);font-size:13px;margin-bottom:16px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;margin-bottom:14px}}
h2{{font-size:15px;margin:0 0 10px;color:var(--dim);text-transform:uppercase;letter-spacing:.04em}}
form{{display:flex;gap:8px;align-items:center;flex-wrap:wrap}}
select,button{{font:inherit;padding:10px 12px;border-radius:10px;border:1px solid var(--line);
background:var(--bg);color:var(--text)}}
button{{background:var(--accent);color:#fff;border:0;font-weight:600}}
.day{{padding:10px 0;border-top:1px solid var(--line)}} .day:first-of-type{{border-top:0}}
.date{{font-weight:600}} .links{{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}}
.links a{{text-decoration:none;color:var(--accent);border:1px solid var(--line);border-radius:8px;
padding:6px 9px;font-size:14px}} .size{{color:var(--dim);font-size:12px}}
p.note{{color:var(--dim);font-size:13px;margin:8px 0 0}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(96px,1fr));gap:6px}}
.grid a{{display:block;aspect-ratio:4/3;border-radius:8px;overflow:hidden;background:var(--line)}}
.grid img{{width:100%;height:100%;object-fit:cover;display:block}}
a.button{{display:inline-block;background:var(--accent);color:#fff;text-decoration:none;
font-weight:600;padding:10px 12px;border-radius:10px}}
</style></head><body><main>
<h1>{title}</h1><div class="sub">v{version} &middot; {now}</div>
<div class="card"><h2>Download as ZIP</h2>
<form action="/zip" method="get"><label for="days">Last</label>
<select id="days" name="days">{options}</select><button type="submit">Download ZIP</button></form>
<p class="note">Includes the CSV logs, Google Earth KML, GPX and waypoint photos.
Safari saves it to Files &rsaquo; Downloads.</p></div>
{photos}<div class="card"><h2>Days</h2>{days}</div>
</main></body></html>
"""

_PHOTOS_PAGE = """<div class="card"><h2>Photos ({count})</h2>
<div class="grid">{thumbs}</div>
<p class="note">Tap a photo, then the share button &rsaquo; <b>Save to Photos</b>.
The GPS location comes with it.</p>
<p><a class="button" href="/photos">All photos</a>
<a class="button" href="/photos.zip">Download all (ZIP)</a></p></div>
"""


def _thumbs(names) -> str:
    return "".join(
        '<a href="/v/photos/{0}"><img loading="lazy" src="/t/photos/{0}" '
        'alt="{0}"></a>'.format(html.escape(n)) for n in names)


class WebShare:
    """Starts/stops the HTTP server in a background thread."""

    def __init__(self, data_dir: str, port: int = DEFAULT_PORT,
                 zip_days_fn: Callable[[], int] = lambda: 7,
                 export_lock: Optional[threading.Lock] = None):
        self.data_dir = data_dir
        self.port = port
        self.zip_days = zip_days_fn
        self.export_lock = export_lock or threading.Lock()
        self.error = ""
        self._thumb_lock = threading.Lock()
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self._server is not None

    def start(self) -> bool:
        if self._server is not None:
            return True
        handler = type("Handler", (_Handler,), {"share": self})
        try:
            server = ThreadingHTTPServer(("0.0.0.0", self.port), handler)
        except OSError as exc:
            self.error = "PORT {} BUSY".format(self.port) if exc.errno in (98, 48, 10048) \
                else "WEB ERROR {}".format(exc.errno)
            return False
        server.daemon_threads = True
        self.port = server.server_address[1]
        self._server = server
        self.error = ""
        self._thread = threading.Thread(target=server.serve_forever,
                                        name="jp226-web", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

    def urls(self) -> List[str]:
        return [url for _label, url in self.labelled_urls()]

    def labelled_urls(self) -> List[Tuple[str, str]]:
        """(WIRED / WI-FI / interface, URL) for each address, wired first."""
        return [(label, "http://{}:{}/".format(address, self.port))
                for label, address in interface_addresses()]

    # -- photos ----------------------------------------------------------
    @property
    def photo_dir(self) -> str:
        return os.path.join(self.data_dir, "photos")

    def photo_names(self) -> List[str]:
        """Photo file names, newest first."""
        try:
            names = [n for n in os.listdir(self.photo_dir) if _PHOTO_FILE.match(n)]
        except OSError:
            return []
        def mtime(name):
            try:
                return os.path.getmtime(os.path.join(self.photo_dir, name))
            except OSError:
                return 0.0
        return sorted(names, key=lambda n: (mtime(n), n), reverse=True)

    def thumbnail(self, path: str) -> Optional[str]:
        """A 320-px preview, made once with Pillow and kept in photos/.thumbs.
        None (send the full photo) without Pillow."""
        try:
            from PIL import Image
        except ImportError:
            return None
        thumbs = os.path.join(self.photo_dir, ".thumbs")
        target = os.path.join(thumbs, os.path.basename(path))
        with self._thumb_lock:
            try:
                if (os.path.isfile(target) and
                        os.path.getmtime(target) >= os.path.getmtime(path)):
                    return target
                os.makedirs(thumbs, exist_ok=True)
                with Image.open(path) as image:
                    image.draft("RGB", (320, 240))
                    image = image.convert("RGB")
                    image.thumbnail((320, 240))
                    image.save(target + ".tmp", "JPEG", quality=80)
                os.replace(target + ".tmp", target)
                return target
            except (OSError, ValueError):
                return None

    def photos_html(self) -> str:
        names = self.photo_names()
        body = _PHOTOS_PAGE.format(
            count=len(names), thumbs=_thumbs(names)) if names else \
            '<div class="card"><h2>Photos</h2><p class="note">No photos yet.</p></div>'
        return _PAGE.format(
            title=html.escape(APP_TITLE), version=html.escape(__version__),
            now=html.escape(datetime.datetime.now().strftime("%Y-%m-%d %H:%M")),
            options="", days='<p class="note"><a href="/">Back to logs</a></p>',
            photos=body).replace(
                '<div class="card"><h2>Download as ZIP</h2>', '<div hidden>', 1)

    # -- page ------------------------------------------------------------
    def index_html(self) -> str:
        chosen = self.zip_days()
        choices = list(ZIP_DAY_CHOICES)
        if chosen not in choices:
            choices.insert(0, chosen)
        options = "".join(
            '<option value="{0}"{1}>{2}</option>'.format(
                d, " selected" if d == chosen else "",
                "All days" if d <= 0 else "{} day{}".format(d, "" if d == 1 else "s"))
            for d in choices)
        rows = []
        for date in export.list_days(self.data_dir):
            sources = export.day_files(self.data_dir, date)
            links = []
            if "telemetry" in sources:
                links.append(('Track CSV', "/f/" + os.path.basename(sources["telemetry"]),
                              _size(sources["telemetry"])))
            links.append(("Google Earth KML",
                          "/f/{}/track_{}.kml".format(export.EXPORT_DIR, date), ""))
            links.append(("GPX", "/f/{}/track_{}.gpx".format(export.EXPORT_DIR, date), ""))
            if "waypoints" in sources:
                links.append(("Waypoints CSV",
                              "/f/" + os.path.basename(sources["waypoints"]), ""))
            if "events" in sources:
                links.append(("Events CSV", "/f/" + os.path.basename(sources["events"]), ""))
            rows.append('<div class="day"><div class="date">{}</div><div class="links">{}'
                        "</div></div>".format(
                            html.escape(date),
                            "".join('<a href="{}">{}{}</a>'.format(
                                html.escape(href), html.escape(label),
                                ' <span class="size">{}</span>'.format(size) if size else "")
                                for label, href, size in links)))
        return _PAGE.format(
            title=html.escape(APP_TITLE), version=html.escape(__version__),
            now=html.escape(datetime.datetime.now().strftime("%Y-%m-%d %H:%M")),
            options=options,
            days="".join(rows) or '<p class="note">No logs yet.</p>',
            photos=self._recent_photos_card())

    def _recent_photos_card(self) -> str:
        names = self.photo_names()
        if not names:
            return ""
        return _PHOTOS_PAGE.format(count=len(names), thumbs=_thumbs(names[:8]))
