"""Local Wi-Fi HTTP server for transferring OBD logs & profiles to an iPhone.

Uses only the Python standard library (ThreadingHTTPServer + zipfile).
Serves a mobile-friendly page with one-tap file downloads and a full ZIP
export so iOS Safari can save files directly to the iPhone Files app.
"""

import html
import io
import json
import os
import re
import socket
import subprocess
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote

_ALLOWED_RE = re.compile(r"^obd_(scan|profile|live)_[A-Za-z0-9_.-]+\.(jsonl|json)$")


def detect_wifi_ip() -> str:
    """Return the best local IPv4 address (preferring wlan0 on Linux)."""
    try:
        out = subprocess.check_output(
            ["ip", "-4", "-o", "addr", "show"],
            stderr=subprocess.DEVNULL, text=True, timeout=1.5,
        )
        wlan_ips: List[str] = []
        other_ips: List[str] = []
        for line in out.splitlines():
            parts = line.split()
            if "inet" not in parts:
                continue
            iface = parts[1]
            ip = parts[parts.index("inet") + 1].split("/")[0]
            if ip.startswith("127."):
                continue
            if iface.startswith("wl"):
                wlan_ips.append(ip)
            else:
                other_ips.append(ip)
        if wlan_ips:
            return wlan_ips[0]
        if other_ips:
            return other_ips[0]
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        pass

    for target in ("192.168.1.1", "172.20.10.1", "8.8.8.8"):
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.connect((target, 80))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
        except OSError:
            pass
        finally:
            if sock is not None:
                sock.close()
    return "127.0.0.1"


def list_log_files(data_dir: str) -> List[Dict[str, object]]:
    """Return downloadable OBD scan, profile, and live log files, newest first."""
    if not os.path.isdir(data_dir):
        return []
    entries: List[Dict[str, object]] = []
    for name in os.listdir(data_dir):
        if not _ALLOWED_RE.match(name):
            continue
        path = os.path.join(data_dir, name)
        if not os.path.isfile(path):
            continue
        st = os.stat(path)
        kind = (
            "profile" if name.startswith("obd_profile_") else
            "live" if name.startswith("obd_live_") else "scan"
        )
        entries.append({
            "name": name,
            "kind": kind,
            "size": st.st_size,
            "mtime": st.st_mtime,
        })
    entries.sort(key=lambda item: float(item["mtime"]), reverse=True)
    return entries


def _fmt_size(n: int) -> str:
    if n < 1024:
        return "{} B".format(n)
    if n < 1024 * 1024:
        return "{:.1f} KB".format(n / 1024.0)
    return "{:.2f} MB".format(n / (1024.0 * 1024.0))


def build_zip_bytes(data_dir: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for item in list_log_files(data_dir):
            name = str(item["name"])
            zf.write(os.path.join(data_dir, name), arcname=name)
    return buf.getvalue()


def render_index_html(data_dir: str, host_label: str,
                      latest_profile: Optional[Dict[str, object]] = None) -> str:
    files = list_log_files(data_dir)
    summary_html = ""
    if latest_profile and isinstance(latest_profile, dict):
        veh = latest_profile.get("vehicle", {})
        lg = latest_profile.get("logger_fit", {})
        ev = latest_profile.get("ev_sound_fit", {})
        fast = " ".join(ev.get("recommended_fast_pids", [])) or "--"
        summary_html = (
            "<div class='card'>"
            "<h2>Latest Vehicle Profile</h2>"
            "<div class='grid'>"
            "<div><span>VIN</span><b>{vin}</b></div>"
            "<div><span>Type</span><b>{ptype}</b></div>"
            "<div><span>Logger Fit</span><b>{lg_cnt}/12</b></div>"
            "<div><span>EV Sound</span><b>{ev_grade} ({hz} Hz)</b></div>"
            "</div>"
            "<div class='sub'>Fast PIDs: <code>{fast}</code></div>"
            "</div>"
        ).format(
            vin=html.escape(str(veh.get("vin") or "--")),
            ptype=html.escape(str(veh.get("powertrain_hint") or "--")),
            lg_cnt=int(lg.get("supported_count", 0)),
            ev_grade=html.escape(str(ev.get("grade") or "--")),
            hz=html.escape(str(ev.get("effective_hz") or 0)),
            fast=html.escape(fast),
        )

    rows = []
    for item in files:
        name = str(item["name"])
        qname = html.escape(name)
        badge = str(item["kind"]).upper()
        size_txt = _fmt_size(int(item["size"]))
        rows.append(
            "<div class='file'>"
            "<div class='meta'>"
            "<span class='tag tag-{kind}'>{badge}</span>"
            "<span class='fname'>{name}</span>"
            "<span class='fsize'>{size}</span>"
            "</div>"
            "<div class='btns'>"
            "<a class='btn view' href='/view/{name}'>View</a>"
            "<a class='btn dl' href='/download/{name}' download='{name}'>Download</a>"
            "</div>"
            "</div>".format(
                kind=html.escape(str(item["kind"])),
                badge=html.escape(badge),
                name=qname,
                size=html.escape(size_txt),
            )
        )
    files_html = "".join(rows) or "<p class='empty'>No OBD logs saved yet. Run a scan first.</p>"
    zip_btn = (
        "<a class='btn zip' href='/download-all.zip' download='jp226_obd_logs.zip'>"
        "Download All ({count} files) as ZIP</a>".format(count=len(files))
        if files else ""
    )

    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>JP226 OBD-II Logs</title>
<style>
  body{{margin:0;padding:16px;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#080a10;color:#e6ebf5}}
  .wrap{{max-width:640px;margin:0 auto}}
  h1{{font-size:20px;margin:0 0 4px}}
  h2{{font-size:15px;margin:0 0 10px;color:#5aafff}}
  .host{{color:#828ca0;font-size:13px;margin-bottom:14px}}
  .card{{background:#161b28;border-radius:12px;padding:14px;margin-bottom:14px;border:1px solid #252e44}}
  .grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}
  .grid span{{display:block;font-size:11px;color:#828ca0;text-transform:uppercase}}
  .grid b{{font-size:15px}}
  .sub{{margin-top:10px;font-size:13px;color:#b8c2d8}}
  code{{background:#0d111a;padding:2px 6px;border-radius:5px;color:#50dc78}}
  .btn{{display:inline-block;text-decoration:none;padding:10px 14px;border-radius:9px;font-weight:600;font-size:14px;text-align:center}}
  .zip{{display:block;background:#50dc78;color:#08100a;margin-bottom:14px}}
  .file{{background:#161b28;border-radius:10px;padding:12px;margin-bottom:10px;border:1px solid #222a3e}}
  .meta{{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-bottom:10px}}
  .fname{{font-family:ui-monospace,SFMono-Regular,monospace;font-size:13px;word-break:break-all}}
  .fsize{{margin-left:auto;color:#828ca0;font-size:12px}}
  .tag{{font-size:10px;font-weight:700;padding:3px 7px;border-radius:999px;background:#26324a;color:#9cc8ff}}
  .tag-profile{{background:#183a28;color:#50dc78}}
  .tag-live{{background:#3c2d12;color:#ffbe3c}}
  .btns{{display:flex;gap:8px}}
  .view{{flex:1;background:#252e44;color:#e6ebf5}}
  .dl{{flex:2;background:#5aafff;color:#06101c}}
  .empty{{color:#828ca0;text-align:center;padding:24px 0}}
</style>
</head>
<body>
<div class="wrap">
  <h1>JP226 OBD-II Scanner</h1>
  <div class="host">CardputerZero Wi-Fi Transfer &middot; {host}</div>
  {summary}
  {zip_btn}
  {files}
</div>
</body>
</html>""".format(
        host=html.escape(host_label),
        summary=summary_html,
        zip_btn=zip_btn,
        files=files_html,
    )


class LogWebServer:
    """Background HTTP server exposing OBD logs & profiles over local Wi-Fi."""

    def __init__(self, data_dir: str, port: int = 8080,
                 profile_getter=None):
        self.data_dir = data_dir
        self.requested_port = port
        self.port = port
        self.profile_getter = profile_getter or (lambda: None)
        self.downloads = 0
        self.running = False
        self.error = ""
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def urls(self) -> Tuple[str, str]:
        ip = detect_wifi_ip()
        host = socket.gethostname()
        return (
            "http://{}:{}".format(ip, self.port),
            "http://{}.local:{}".format(host, self.port),
        )

    def start(self) -> None:
        if self.running:
            return
        parent = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, _fmt, *_args):
                pass

            def _send(self, code: int, body: bytes, ctype: str,
                      filename: Optional[str] = None) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                if filename:
                    self.send_header(
                        "Content-Disposition",
                        'attachment; filename="{}"'.format(filename),
                    )
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = self.path.split("?", 1)[0]
                if path == "/" or path == "/index.html":
                    ip_url, _ = parent.urls()
                    page = render_index_html(
                        parent.data_dir, ip_url, parent.profile_getter()
                    )
                    self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
                    return
                if path == "/api/files":
                    payload = json.dumps(list_log_files(parent.data_dir)).encode("utf-8")
                    self._send(200, payload, "application/json; charset=utf-8")
                    return
                if path == "/download-all.zip":
                    data = build_zip_bytes(parent.data_dir)
                    parent.downloads += 1
                    self._send(200, data, "application/zip", "jp226_obd_logs.zip")
                    return
                if path.startswith("/download/") or path.startswith("/view/"):
                    is_dl = path.startswith("/download/")
                    raw_name = unquote(path.split("/", 2)[2])
                    if not _ALLOWED_RE.match(raw_name):
                        self._send(404, b"Not found", "text/plain; charset=utf-8")
                        return
                    fpath = os.path.join(parent.data_dir, raw_name)
                    if not os.path.isfile(fpath):
                        self._send(404, b"Not found", "text/plain; charset=utf-8")
                        return
                    with open(fpath, "rb") as fh:
                        data = fh.read()
                    if is_dl:
                        parent.downloads += 1
                        ctype = (
                            "application/json; charset=utf-8"
                            if raw_name.endswith(".json")
                            else "application/x-ndjson; charset=utf-8"
                        )
                        self._send(200, data, ctype, raw_name)
                    else:
                        self._send(200, data, "text/plain; charset=utf-8")
                    return
                self._send(404, b"Not found", "text/plain; charset=utf-8")

        class _Server(ThreadingHTTPServer):
            allow_reuse_address = True
            daemon_threads = True

        try:
            self._httpd = _Server(("0.0.0.0", self.requested_port), _Handler)
            self.port = int(self._httpd.server_address[1])
            self.running = True
            self.error = ""
            self._thread = threading.Thread(
                target=self._httpd.serve_forever,
                name="jp226-obd-web",
                daemon=True,
            )
            self._thread.start()
        except OSError as exc:
            self.running = False
            self.error = str(exc)

    def stop(self) -> None:
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except OSError:
                pass
            self._httpd = None
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None
        self.running = False
