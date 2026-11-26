"""320x170 dashboard and dinosaur pages drawn with Pillow."""

import os
from typing import Dict, Optional

from . import APP_TITLE, hardware, keys

PAGES = ("SPEED", "GPS", "IMU", "ENV", "ESP-NOW", "LOGGER", "SHARE", "WAYPOINT",
         "CAMERA", "GALLERY", "SETUP", "OBD", "DIAG", "FOSSIL RECORD",
         "DINO DASH")
SETTINGS_PAGE_INDEX = PAGES.index("SETUP")
OBD_PAGE_INDEX = PAGES.index("OBD")
DINO_PAGE_INDEX = PAGES.index("DINO DASH")
CAMERA_PAGE_INDEX = PAGES.index("CAMERA")
GALLERY_PAGE_INDEX = PAGES.index("GALLERY")
# Pages drawn without the title bar, to give the picture the room.
_NO_HEADER = ("CAMERA",)

# Values a future OBD connection could show. None are connected or logged.
PLANNED_OBD_READINGS = ("RPM", "LOAD", "COOLANT", "FUEL")
PLANNED_VEHICLE_READINGS = ("TPMS PSI/TEMP", "12V", "IGN TIMES")

_BG = (8, 10, 16)
_PANEL = (22, 27, 40)
_TEXT = (230, 235, 245)
_DIM = (130, 140, 160)
_GOOD = (80, 220, 120)
_WARN = (255, 190, 60)
_BAD = (255, 90, 90)
_ACCENT = (90, 170, 255)

_KIND_COLOURS = {
    keys.KIND_NAV: _ACCENT, keys.KIND_EDIT: _WARN,
    keys.KIND_SAVE: _GOOD, keys.KIND_UNAVAILABLE: _DIM,
}

_STATE_COLOURS = {
    "MOVING": _GOOD, "STOPPED_15M": _ACCENT, "STOPPED_HOURLY": _ACCENT,
    "FIX_LOST": _BAD, "WAITING_FIX": _WARN,
}

_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",   # device (example)
    "DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "/Library/Fonts/Arial Bold.ttf",
)
_REGULAR_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "/Library/Fonts/Arial.ttf",
)


def pillow_available() -> bool:
    try:
        import PIL  # noqa: F401
        return True
    except ImportError:
        return False


def _load_font(size: int, bold: bool):
    from PIL import ImageFont
    for path in (_FONT_CANDIDATES if bold else _REGULAR_CANDIDATES):
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    try:
        return ImageFont.load_default(size)  # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


class _Fonts:
    def __init__(self):
        self.huge = _load_font(70, True)
        self.big = _load_font(24, True)
        self.row = _load_font(16, True)
        self.small = _load_font(14, False)
        self.tiny = _load_font(9, False)


_fonts: Optional[_Fonts] = None


def _get_fonts() -> _Fonts:
    global _fonts
    if _fonts is None:
        _fonts = _Fonts()
    return _fonts


def _text(draw, x, y, s, font, fill, align="l"):
    width = draw.textlength(s, font=font)
    if align == "r":
        x -= width
    elif align == "c":
        x -= width / 2
    draw.text((x, y), s, font=font, fill=fill)


def _fmt(value, spec, none="--"):
    return none if value is None else format(value, spec)


def _header(draw, f, title, model, width):
    draw.rectangle([0, 0, width, 22], fill=_PANEL)
    state = model["log_state"]
    colour = _STATE_COLOURS.get(state, _DIM)
    state_width = draw.textlength(state, font=f.small)
    text, font = _fit(draw, title, (f.row, f.small),
                      width - 18 - state_width - 14)
    _text(draw, 6, 2 if font is f.row else 4, text, font, _TEXT)
    _text(draw, width - 18, 3, state, f.small, colour, "r")
    dot = _GOOD if model["fix_valid"] else _BAD
    draw.ellipse([width - 14, 6, width - 6, 14], fill=dot)


# The physical number keys sit under the screen: its left edge is the middle
# of key 3 and its right edge reaches key 9, so only 4-8 are usable. Each label
# is drawn straight above its key (x centre in 320-px screen coordinates).
FOOTER_KEY_CENTRES = {
    "KEY_4": 49, "KEY_5": 105, "KEY_6": 161, "KEY_7": 217, "KEY_8": 273,
}


def footer_items(index: int):
    """(key name, label, kind) for this page's number-key actions."""
    return [(key_name, label, kind) for key_name, label, _action, kind
            in keys.page_actions(PAGES[index % len(PAGES)])]


def _footer(draw, f, index, width, height):
    draw.rectangle([0, height - 14, width, height], fill=_PANEL)
    for key_name, label, kind in footer_items(index):
        x = round(FOOTER_KEY_CENTRES[key_name] * width / hardware.SCREEN_WIDTH)
        _text(draw, x, height - 12, "{} {}".format(key_name[len("KEY_"):], label),
              f.tiny, _KIND_COLOURS[kind], "c")


def _rows(draw, f, rows, top=28, step=21):
    for i, (label, value, colour) in enumerate(rows):
        y = top + i * step
        _text(draw, 8, y + 2, label, f.small, _DIM)
        _text(draw, 84, y, value, f.row, colour)


def _fit(draw, text, fonts, width):
    """(text, font) that fits width: try each font, then shorten with '...'."""
    for font in fonts:
        if draw.textlength(text, font=font) <= width:
            return text, font
    font = fonts[-1]
    while text and draw.textlength(text + "...", font=font) > width:
        text = text[:-1]
    return text + "...", font


def _two_columns(draw, f, rows, top=34, step=35):
    """Label left, value right-aligned. A row whose right item is None gives
    the left item the full width. Values never overlap their labels."""
    for row_index, (left, right) in enumerate(rows):
        y = top + row_index * step
        for column, item in enumerate((left, right)):
            if item is None:
                continue
            label, value, colour = item
            x0 = 8 if column == 0 else 166
            x1 = 154 if column == 0 and right is not None else 312
            _text(draw, x0, y + 2, label, f.small, _DIM)
            room = x1 - x0 - draw.textlength(label, font=f.small) - 8
            text, font = _fit(draw, value, (f.row, f.small), room)
            _text(draw, x1, y + (0 if font is f.row else 2), text, font,
                  colour, "r")


def _page_speed(draw, f, m, w, h):
    speed = m["speed_kmh"]
    if m["fix_valid"] and speed is not None:
        _text(draw, w // 2, 22, "{:.0f}".format(speed), f.huge, _TEXT, "c")
    else:
        _text(draw, w // 2, 22, "--", f.huge, _DIM, "c")
    _text(draw, w // 2, 100, "km/h", f.big, _DIM, "c")
    sats = _fmt(m["satellites"], "d")
    hdop = _fmt(m["hdop"], ".1f")
    fix = "FIX" if m["fix_valid"] else "NO FIX"
    _text(draw, 8, 126, "{}  SAT {}  HDOP {}".format(fix, sats, hdop), f.small,
          _GOOD if m["fix_valid"] else _BAD)
    _text(draw, 8, 144, m["local_text"] or "no time yet", f.tiny, _DIM)
    _text(draw, w - 8, 144, m["zone_name"], f.tiny, _DIM, "r")


def _page_gps(draw, f, m, w, h):
    fix_ok = m["fix_valid"]
    _two_columns(draw, f, [
        (("FIX", "VALID" if fix_ok else "NO FIX", _GOOD if fix_ok else _BAD),
         ("SATS", _fmt(m["satellites"], "d"), _TEXT)),
        (("LAT", _fmt(m["lat"], ".5f"), _TEXT),
         ("LON", _fmt(m["lon"], ".5f"), _TEXT)),
        (("ALT", "{} m".format(_fmt(m["alt"], ".0f")), _TEXT),
         ("HEADING", "{} deg".format(_fmt(m["heading"], ".0f")), _TEXT)),
        (("HDOP", _fmt(m["hdop"], ".2f"), _TEXT),
         ("VDOP", _fmt(m["vdop"], ".2f"), _TEXT)),
        (("UTC", m["utc_text"] or "--", _DIM), None),
        (("SOURCE", m["gps_source"], _WARN), None),
    ], top=29, step=21)


def _page_logger(draw, f, m, w, h):
    state = m["log_state"]
    err = m["storage_error"]
    _two_columns(draw, f, [
        (("STATE", state, _STATE_COLOURS.get(state, _DIM)),
         ("ROWS", str(m["rows_written"]), _TEXT)),
        (("NEXT", m["next_due"] or "--", _TEXT),
         ("STORAGE", "ERROR" if err else "OK", _BAD if err else _GOOD)),
        (("FILE", m["file_name"] or "--", _TEXT), None),
        (("ZONE", m["zone_name"].replace("Australia/", ""), _TEXT), None),
    ], top=32, step=28)


def _page_imu(draw, f, m, w, h):
    sample = m["imu_sample"]
    if not m["imu_available"] or sample is None or not sample.valid:
        message = m["imu_error"] or "IMU NOT READY"
        _text(draw, w // 2, 58, message[:34], f.row, _WARN, "c")
        _text(draw, w // 2, 82, m["imu"], f.small, _DIM, "c")
        return
    axes = (_ACCENT, _GOOD, _WARN)
    _two_columns(draw, f, [
        (("ACC X", "{:.2f}".format(sample.ax_mps2), axes[0]),
         ("ACC Y", "{:.2f}".format(sample.ay_mps2), axes[1])),
        (("ACC Z", "{:.2f}".format(sample.az_mps2), axes[2]),
         ("GYRO X", "{:.1f}".format(sample.gx_dps), axes[0])),
        (("GYRO Y", "{:.1f}".format(sample.gy_dps), axes[1]),
         ("GYRO Z", "{:.1f}".format(sample.gz_dps), axes[2])),
        (("PITCH", "{:.1f} deg".format(sample.pitch_deg), _TEXT),
         ("ROLL", "{:.1f} deg".format(sample.roll_deg), _TEXT)),
        (("VIB RMS", "{:.3f}".format(sample.vibration_rms_mps2), _TEXT),
         ("SAMPLES", str(sample.sample_count), _DIM)),
    ], top=35, step=24)


_qr_cache: Dict[str, list] = {}


def _qr_matrix(text: str):
    """QR modules (True = dark), or None without the qrcode package."""
    if text not in _qr_cache:
        try:
            import qrcode  # optional, device only (python3-qrcode)
        except ImportError:
            return None
        code = qrcode.QRCode(border=2,
                             error_correction=qrcode.constants.ERROR_CORRECT_L)
        code.add_data(text)
        code.make(fit=True)
        _qr_cache[text] = code.get_matrix()
    return _qr_cache[text]


def _draw_qr(draw, matrix, x, y, size):
    scale = max(1, size // len(matrix))
    side = scale * len(matrix)
    draw.rectangle([x, y, x + side - 1, y + side - 1], fill=(255, 255, 255))
    for row, cells in enumerate(matrix):
        for col, dark in enumerate(cells):
            if dark:
                draw.rectangle([x + col * scale, y + row * scale,
                                x + col * scale + scale - 1,
                                y + row * scale + scale - 1], fill=(0, 0, 0))
    return side


def _page_hotspot(draw, f, m, w, h):
    """SHARE page while the hotspot is on: join, then open the page."""
    from .hotspot import wifi_qr_text
    url = "http://{}:{}/".format(m["hotspot_address"] or "10.42.0.1",
                                 m["share_port"])
    join_step = (m["now_ms"] // 5000) % 2 == 0
    text = (wifi_qr_text(m["hotspot_ssid"], m["hotspot_password"])
            if join_step else url)
    matrix = _qr_matrix(text)
    if matrix is not None:
        side = _draw_qr(draw, matrix, 6, 26, 126)
        _text(draw, 6 + side // 2, 26 + side + 1,
              "1 SCAN TO JOIN" if join_step else "2 SCAN TO OPEN",
              f.tiny, _ACCENT, "c")
        x = 6 + side + 10
    else:
        x = 8
    _text(draw, x, 26, "HOTSPOT ON", f.row, _GOOD)
    _text(draw, x, 48, "WI-FI", f.tiny, _DIM)
    ssid, font = _fit(draw, m["hotspot_ssid"], (f.row, f.small), w - x - 6)
    _text(draw, x, 58, ssid, font, _TEXT)
    _text(draw, x, 80, "PASSWORD", f.tiny, _DIM)
    password, font = _fit(draw, m["hotspot_password"] or "--",
                          (f.row, f.small), w - x - 6)
    _text(draw, x, 90, password, font, _WARN)
    _text(draw, x, 112, "THEN OPEN", f.tiny, _DIM)
    shown, font = _fit(draw, url.replace("http://", ""), (f.small, f.tiny),
                       w - x - 6)
    _text(draw, x, 122, shown, font, _ACCENT)
    _text(draw, x, 141, "8 turns the hotspot off", f.tiny, _DIM)


def _page_share(draw, f, m, w, h):
    if m["hotspot_on"]:
        return _page_hotspot(draw, f, m, w, h)
    on = m["share_on"]
    _text(draw, 8, 27, "WEB", f.small, _DIM)
    state = "ON" if on else (m["share_error"] or "OFF")
    _text(draw, 50, 25, state, f.row, _GOOD if on else
          (_BAD if m["share_error"] else _DIM))
    days = m["zip_days"]
    zip_text = ("ZIP ALL DAYS" if days == "ALL" else
                "ZIP LAST 1 DAY" if days == "1" else "ZIP LAST {} DAYS".format(days))
    _text(draw, w - 8, 27, zip_text, f.small, _TEXT, "r")
    # The addresses to type in Safari, one per network, then the name.
    _text(draw, 8, 46, "OPEN IN SAFARI" if on else "PRESS 4, THEN OPEN",
          f.tiny, _DIM)
    urls = list(m["share_urls"])[:2] or [("", "no network")]
    urls.append(("NAME", "http://cardputerzero.local:{}/".format(m["share_port"])))
    for i, (label, url) in enumerate(urls):
        y = 57 + i * 19
        _text(draw, 8, y + 2, label or "LAN", f.tiny, _DIM)
        text, font = _fit(draw, url.replace("http://", ""), (f.row, f.small),
                          w - 64)
        _text(draw, 56, y, text, font, _ACCENT if on else _DIM)
    status = m["export_status"]
    colour = _BAD if "ERROR" in status else _GOOD if status.startswith(
        ("EXPORTED", "UP TO DATE")) else _WARN
    _text(draw, 8, 120, "KML/GPX", f.small, _DIM)
    _text(draw, w - 8, 120, status + "  (nightly)", f.small, colour, "r")
    if m["hotspot_busy"]:
        note, colour = "HOTSPOT: SWITCHING...", _WARN
    elif m["hotspot_error"]:
        note, colour = "HOTSPOT: " + m["hotspot_error"], _BAD
    elif not m["hotspot_available"]:
        note, colour = "Read-only. Turn off on untrusted Wi-Fi.", _DIM
    else:
        note, colour = "Read-only. 8: own Wi-Fi hotspot for a phone.", _DIM
    _text(draw, 8, 140, note, f.tiny, colour)


def _page_waypoint(draw, f, m, w, h):
    last = m["waypoint_last"]
    msg = m["waypoint_message"]
    colour = _BAD if "ERROR" in msg else (_GOOD if "SAVED" in msg else _DIM)
    _text(draw, w // 2, 32, msg or "READY", f.big, colour, "c")
    _text(draw, w // 2, 56, "W/Enter saves waypoint + photo", f.small, _DIM, "c")
    if last:
        _rows(draw, f, [
            ("LAST", last["id"], _TEXT),
            ("LAT", _fmt(last["lat"], ".6f"), _TEXT),
            ("LON", _fmt(last["lon"], ".6f"), _TEXT),
            ("PHOTO", last.get("photo", "").split("/")[-1] or
             m["camera_message"] or "--",
             _TEXT),
            ("ROAD", last.get("road_type") or "--", _TEXT),
        ], top=72, step=15)
    else:
        _text(draw, w // 2, 100, "no waypoint yet", f.row, _DIM, "c")


def _fit_image(image, width, height):
    """`image` scaled to fit width x height, keeping its shape."""
    scale = min(width / image.width, height / image.height)
    size = (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
    return image if size == image.size else image.resize(size)


def _info_column(draw, f, m, x, top):
    """Fix, satellites, position and local time, for the picture pages."""
    fix_ok = m["fix_valid"]
    _text(draw, x, top, "GPS FIX" if fix_ok else "NO FIX", f.row,
          _GOOD if fix_ok else _BAD)
    _text(draw, x, top + 20, "SATS {}".format(_fmt(m["satellites"], "d")),
          f.small, _TEXT)
    _text(draw, x, top + 37, _fmt(m["lat"], ".5f"), f.small, _TEXT)
    _text(draw, x, top + 53, _fmt(m["lon"], ".5f"), f.small, _TEXT)
    local = (m["local_text"] or "no time yet").split(" ")
    _text(draw, x, top + 70, local[-1] if len(local) > 1 else local[0],
          f.small, _DIM)


def _page_camera(draw, f, m, w, h):
    canvas = m.get("_canvas")
    frame = m["camera_frame"]
    area_w, area_h = 208, h - 14
    if frame is not None and canvas is not None:
        picture = _fit_image(frame, area_w, area_h)
        canvas.paste(picture, ((area_w - picture.width) // 2,
                               (area_h - picture.height) // 2))
    else:
        draw.rectangle([0, 0, area_w - 1, area_h - 1], fill=_PANEL)
        status = m["camera_live"]
        _text(draw, area_w // 2, area_h // 2 - 10,
              "STARTING CAMERA" if status in ("OFF", "STARTING", "LIVE")
              else status[:26], f.small,
              _DIM if status in ("OFF", "STARTING", "LIVE") else _BAD, "c")
    x = area_w + 8
    _text(draw, x, 4, "CAMERA", f.row, _TEXT)
    _info_column(draw, f, m, x, 26)
    message = m["photo_message"]
    if message:
        colour = _GOOD if message.startswith("SAVED") else _BAD
        text, font = _fit(draw, message, (f.small, f.tiny), w - x - 4)
        _text(draw, x, 116, text, font, colour)
    if m["last_photo"]:
        text, font = _fit(draw, m["last_photo"], (f.tiny,), w - x - 4)
        _text(draw, x, 134, text, font, _DIM)


_thumb_cache: Dict[str, tuple] = {}


def _gallery_item(path: str):
    """(thumbnail, (lat, lon) or None) for one photo, cached by mtime."""
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        return None, None
    cached = _thumb_cache.get(path)
    if cached and cached[0] == stamp:
        return cached[1], cached[2]
    from PIL import Image
    from .exif import read_gps
    try:
        with open(path, "rb") as handle:
            head = handle.read(65536)
        with Image.open(path) as image:
            image.draft("RGB", (320, 240))   # fast JPEG decode at 1/4 or 1/8
            thumb = _fit_image(image.convert("RGB"), 176, 132)
    except (OSError, ValueError):
        return None, None
    if len(_thumb_cache) > 24:
        _thumb_cache.clear()
    _thumb_cache[path] = (stamp, thumb, read_gps(head))
    return thumb, _thumb_cache[path][2]


def _page_gallery(draw, f, m, w, h):
    photos = m["gallery_photos"]
    if not photos:
        _text(draw, w // 2, 60, "No photos yet", f.row, _DIM, "c")
        _text(draw, w // 2, 84, "Take them on the CAMERA page", f.small,
              _DIM, "c")
        return
    index = m["gallery_index"] % len(photos)
    name = photos[index]
    path = os.path.join(m["photo_dir"], name)
    thumb, gps = _gallery_item(path)
    canvas = m.get("_canvas")
    if thumb is not None and canvas is not None:
        canvas.paste(thumb, (4 + (176 - thumb.width) // 2,
                             24 + (132 - thumb.height) // 2))
    else:
        draw.rectangle([4, 24, 179, 155], fill=_PANEL)
        _text(draw, 92, 84, "CANNOT OPEN", f.small, _BAD, "c")
    x = 188
    _text(draw, x, 26, "{} / {}".format(index + 1, len(photos)), f.row, _TEXT)
    stem = os.path.splitext(name)[0]
    text, font = _fit(draw, stem, (f.small, f.tiny), w - x - 4)
    _text(draw, x, 48, text, font, _ACCENT)
    if gps is not None:
        _text(draw, x, 70, "GPS IN PHOTO", f.tiny, _GOOD)
        _text(draw, x, 82, "{:.5f}".format(gps[0]), f.small, _TEXT)
        _text(draw, x, 98, "{:.5f}".format(gps[1]), f.small, _TEXT)
    else:
        _text(draw, x, 70, "NO GPS IN PHOTO", f.tiny, _WARN)
    try:
        size_kb = os.path.getsize(path) / 1024.0
        _text(draw, x, 118, "{:.0f} KB".format(size_kb), f.small, _DIM)
    except OSError:
        pass
    _text(draw, x, 138, "SHARE page to copy", f.tiny, _DIM)


def _age_text(age_ms):
    if age_ms is None:
        return ""
    seconds = age_ms // 1000
    return "{}s".format(seconds) if seconds < 120 else "{}m".format(seconds // 60)


def _page_env(draw, f, m, w, h):
    env = m["env"]
    _text(draw, 8, 26, "GROVE ENV", f.small, _DIM)
    _text(draw, w // 2 - 6, 26, env["status"], f.small,
          _GOOD if env["status"].startswith("OK") else
          (_DIM if env["status"] in ("OFF", "SEARCHING") else _WARN), "r")
    for i, (label, value) in enumerate((
            ("TEMP", "{} C".format(_fmt(env["temp_c"], ".1f"))),
            ("HUMID", "{} %".format(_fmt(env["humidity"], ".0f"))),
            ("PRESS", "{} hPa".format(_fmt(env["pressure_hpa"], ".1f"))))):
        y = 46 + i * 24
        _text(draw, 8, y + 2, label, f.small, _DIM)
        room = w // 2 - 10 - 8 - draw.textlength(label, font=f.small) - 6
        text, font = _fit(draw, value, (f.row, f.small), room)
        _text(draw, w // 2 - 10, y + (0 if font is f.row else 2), text, font,
              _TEXT, "r")
    draw.line([w // 2, 28, w // 2, 134], fill=_PANEL)
    # Right half: accessory probes (DS18B20) through the AtomS3.
    x = w // 2 + 6
    _text(draw, x, 26, "PROBES", f.small, _DIM)
    rows = m["probes"]
    if not rows:
        _text(draw, x, 48, "none heard", f.small, _DIM)
        _text(draw, x, 66, "(Dial / Echo / black box", f.tiny, _DIM)
        _text(draw, x, 77, " probes via the AtomS3)", f.tiny, _DIM)
    for i, (label, celsius, age_ms) in enumerate(rows[:5]):
        y = 46 + i * 20
        _text(draw, x, y + 2, label, f.small, _DIM)
        value = "--" if celsius is None else "{:.1f} C".format(celsius)
        colour = _TEXT if age_ms is not None and age_ms < 30_000 else _DIM
        _text(draw, w - 8, y, value, f.row, colour, "r")
    if env["status"] == "OFF":
        _text(draw, 8, 142, "4: power the Grove port for an ENV unit",
              f.tiny, _DIM)
    elif env.get("sensors"):
        _text(draw, 8, 142, env["sensors"], f.tiny, _DIM)


def _page_espnow(draw, f, m, w, h):
    """JP226 devices heard through the AtomS3 USB bridge."""
    if m["bridge_connected"]:
        _text(draw, 8, 26, "ATOMS3 BRIDGE  CH {}".format(m["bridge_channel"]),
              f.small, _GOOD)
    else:
        _text(draw, 8, 26, m["bridge_status"] or "NO ATOMS3", f.small, _BAD)
    rows = m["radio_devices"]
    _text(draw, w - 8, 26, "{} HEARD".format(len(rows)), f.small, _DIM, "r")
    if not rows:
        _text(draw, w // 2, 74, "No ESP-NOW devices heard", f.row, _DIM, "c")
        _text(draw, w // 2, 96, "Dial, Echo, black box, Atom GPS, loggers",
              f.small, _DIM, "c")
        return
    _text(draw, 8, 44, "TYPE", f.tiny, _DIM)
    _text(draw, 132, 44, "MAC", f.tiny, _DIM)
    _text(draw, 222, 44, "RSSI", f.tiny, _DIM, "r")
    _text(draw, 264, 44, "AGE", f.tiny, _DIM, "r")
    for i, (kind, mac, rssi, age_ms, paired) in enumerate(rows[:5]):
        y = 55 + i * 19
        colour = (_GOOD if age_ms < 5_000 else
                  _WARN if age_ms < 30_000 else _DIM)
        _text(draw, 8, y, kind, f.row, colour)
        _text(draw, 132, y + 2, mac[-4:], f.small, _TEXT)
        _text(draw, 222, y + 2, str(rssi), f.small, _TEXT, "r")
        _text(draw, 264, y + 2, _age_text(age_ms), f.small, _TEXT, "r")
        if paired:
            _text(draw, w - 6, y + 2, "PAIRED", f.tiny, _ACCENT, "r")
    if len(rows) > 5:
        _text(draw, w - 8, 145, "+{} more".format(len(rows) - 5), f.tiny,
              _DIM, "r")


def _page_setup(draw, f, m, w, h):
    _text(draw, 8, 24, "F/X select   W/E change", f.small, _DIM)
    for index, (label, value) in enumerate(m["vehicle_settings"]):
        column, row = divmod(index, 5)
        x0 = 8 if column == 0 else 166
        x1 = 154 if column == 0 else 312
        y = 43 + row * 20
        selected = index == m["vehicle_selection"]
        if selected:
            draw.rectangle([x0 - 4, y - 1, x1, y + 18], fill=_PANEL)
        colour = _ACCENT if selected else _TEXT
        _text(draw, x0, y, label, f.small, colour)
        _text(draw, x1 - 4, y, value, f.row, colour, "r")


def _page_obd(draw, f, m, w, h):
    _text(draw, 8, 24, "F/X select  W/E change  4/5/6 next", f.small, _DIM)
    for index, (label, value) in enumerate(m["obd_profile"]):
        y = 41 + index * 18
        selected = index == m["obd_selection"]
        if selected:
            draw.rectangle([4, y - 1, w - 4, y + 17], fill=_PANEL)
        colour = _ACCENT if selected else _TEXT
        _text(draw, 10, y, label, f.small, colour)
        _text(draw, w - 10, y, value, f.row, colour, "r")
    message = m["obd_message"]
    colour = (_GOOD if "SAVED" in message and "UNSAVED" not in message else
              _WARN if "UNSAVED" in message else _DIM)
    _text(draw, 8, 96, "LINK " + m["obd_status"], f.small, _DIM)
    _text(draw, w - 8, 96, message, f.small, colour, "r")
    _text(draw, 8, 115, "PLANNED - NOT CONNECTED OR LOGGED", f.tiny, _WARN)
    _text(draw, 8, 127, "OBD-II: " + "  ".join(PLANNED_OBD_READINGS),
          f.tiny, _TEXT)
    _text(draw, 8, 139, "VEHICLE: " + "  ".join(PLANNED_VEHICLE_READINGS) +
          "  (varies by vehicle)", f.tiny, _TEXT)


def _page_diag(draw, f, m, w, h):
    counts = m["sentence_counts"]
    seen = " ".join("{}{}".format(k, counts[k]) for k in
                    ("GGA", "RMC", "GSA", "VTG") if k in counts) or "none"
    clock = "{}{}".format(m["clock_source"], "+GPS" if m["clock_gps_synced"] else "")
    _two_columns(draw, f, [
        (("GPS", m["gps_status"] or "--", _TEXT), None),
        (("NMEA OK", str(m["sentences_ok"]), _TEXT),
         ("NMEA BAD", str(m["sentences_bad"]), _TEXT)),
        (("SEEN", seen, _DIM), None),
        (("IMU", m["imu"], _TEXT),
         ("CAMERA", "READY" if m["camera_available"] else "NONE", _TEXT)),
        (("CLOCK", clock, _TEXT),
         ("KEY", (m["last_key"] or "--").replace("KEY_", ""), _TEXT)),
        (("GPS PWR", m["gps_power_status"] or "--", _TEXT),
         ("ATOMS3", m["bridge_status"] or "--", _TEXT)),
    ], top=25, step=19)
    if m["imu_error"]:
        _text(draw, 8, 142, m["imu_error"][:52], f.tiny, _WARN)
    else:
        _text(draw, 8, 142, "v{}  {}".format(m["version"], m["data_dir"][-36:]),
              f.tiny, _DIM)


def _page_fossil(draw, f, m, w, h):
    """Left: version and the dinosaurs. Right: system monitor."""
    half = w // 2
    _text(draw, 8, 27, "JP226 LOGGER", f.row, _ACCENT)
    for i, (label, value) in enumerate((("VER", "v" + m["version"]),
                                        ("BUILD", m["build_git_hash"]),
                                        ("DATE", m["build_date"]))):
        y = 48 + i * 16
        _text(draw, 8, y + 1, label, f.tiny, _DIM)
        text, font = _fit(draw, value, (f.small, f.tiny), half - 52)
        _text(draw, 48, y, text, font, _TEXT)
    frame = (m["now_ms"] // 150) % 2
    _draw_sprite(draw, 10, 98, 2, frame, "BRONTO")
    _draw_sprite(draw, 92, 98, 2, frame, "TREX")
    text, font = _fit(draw, m["fossil_joke"], (f.tiny,), half - 8)
    _text(draw, half // 2, 143, text, font, _ACCENT, "c")

    draw.line([half, 26, half, 152], fill=_PANEL)
    s = m["system"]
    x = half + 8
    _text(draw, x, 27, "SYSTEM", f.row, _ACCENT)

    def level(value, warn, bad):
        return _DIM if value is None else (
            _BAD if value >= bad else _WARN if value >= warn else _GOOD)

    battery = s["battery_pct"]
    battery_text = "--" if battery is None else "{}%".format(battery)
    if s["battery_status"] == "CHARGING":
        battery_text += " CHG"
    elif s["battery_mv"]:
        battery_text += " {:.2f}V".format(s["battery_mv"] / 1000.0)
    from .sysmon import uptime_text
    rows = (
        ("CPU", "{} %".format(_fmt(s["cpu_pct"], ".0f")),
         level(s["cpu_pct"], 70, 90)),
        ("TEMP", "{} C".format(_fmt(s["temp_c"], ".0f")),
         level(s["temp_c"], 70, 80)),
        ("RAM", "{} %".format(_fmt(s["mem_pct"], ".0f")),
         level(s["mem_pct"], 75, 90)),
        ("DISK", "{} GB free".format(_fmt(s["disk_free_gb"], ".1f")),
         level(s["disk_pct"], 85, 95)),
        ("BATT", battery_text, _DIM if battery is None else
         (_BAD if battery < 15 else _WARN if battery < 30 else _GOOD)),
        ("UP", uptime_text(s["uptime_s"]), _TEXT),
    )
    for i, (label, value, colour) in enumerate(rows):
        y = 48 + i * 17
        _text(draw, x, y + 1, label, f.tiny, _DIM)
        text, font = _fit(draw, value, (f.small, f.tiny), w - x - 48)
        _text(draw, w - 8, y, text, font, colour, "r")
    if s["throttled"]:
        _text(draw, x, 152 - 10, "THROTTLED 0x{:X}".format(s["throttled"]),
              f.tiny, _BAD)


_DINO_FRAMES = (
    (
        "...................GGGGG", "..................GGGWKG",
        ".................GGGGGGG", "..................GGGGGG",
        "...............GGGGGGGGG", ".............GGGGGGGGGGG",
        ".......D.D.D.GGGGGGGGGG.", "....GGGGGGGGGGGGGGGG....",
        "..GGJGGJGGJGGGGGGGGG....", "...GGDGGDGGDGGGGGGG.....",
        "....GGGGGGGGGGGGGG......", "......GGGGGGGGGGGG......",
        ".......GG....GG.........", ".......GG....GG.........",
        "......GGGG..GGGG........", "........................",
    ),
    (
        "...................GGGGG", "..................GGGWKG",
        ".................GGGGGGG", "..................GGGGGG",
        "...............GGGGGGGGG", ".............GGGGGGGGGGG",
        ".......D.D.D.GGGGGGGGGG.", "....GGGGGGGGGGGGGGGG....",
        "..GGJGGJGGJGGGGGGGGG....", "...GGDGGDGGDGGGGGGG.....",
        "....GGGGGGGGGGGGGG......", "......GGGGGGGGGGGG......",
        "......GG....GG..........", "......GG....GG..........",
        ".....GGGG..GGGG.........", "........................",
    ),
)
_BRONTO_FRAMES = (
    (
        "..................GGGG..", ".................GGGWKG.",
        ".................GGGGGGG", ".................GGG....",
        "................GGG.....", "...............GGG......",
        ".......D.D.D..GGG.......", ".....GGGGGGGGGGGG.......",
        "...GGGDGGGGDGGGGG.......", "..GGGGGGGGGGGGGGG.......",
        ".GG.GGGGGGGGGGGGG.......", "GG...GGGGGGGGGGG........",
        ".....GG..GG..GG..GG.....", ".....GG..GG..GG..GG.....",
        "....GGG.GGG.GGG.GGG.....", "........................",
    ),
    (
        "..................GGGG..", ".................GGGWKG.",
        ".................GGGGGGG", ".................GGG....",
        "................GGG.....", "...............GGG......",
        ".......D.D.D..GGG.......", ".....GGGGGGGGGGGG.......",
        "...GGGDGGGGDGGGGG.......", "..GGGGGGGGGGGGGGG.......",
        ".GG.GGGGGGGGGGGGG.......", "GG...GGGGGGGGGGG........",
        "......GG.GG....GG.GG....", "......GG.GG....GG.GG....",
        ".....GGGGGG...GGGGGG....", "........................",
    ),
)
_DINO_COLOURS = {
    "G": (60, 180, 75), "D": (30, 110, 40),
    "J": (150, 210, 115), "W": (255, 255, 255), "K": (0, 0, 0),
}
_JOKES = (
    "No dinos were harmed", "Small arms, big logs", "Runs on fossil fuel",
    "Rawr means logging", "Data finds a way", "Clever girl.",
    "Must go faster!", "Bugs now extinct*", "T-Rex approved",
    "Jurassic parking OK", "Tracks since 65M BC", "Dino-mite firmware",
)


def _draw_sprite(draw, x, y, scale, frame, character):
    rows = (_BRONTO_FRAMES if character == "BRONTO" else _DINO_FRAMES)
    rows = rows[frame % len(rows)]
    for row, line in enumerate(rows):
        for col, pixel in enumerate(line):
            colour = _DINO_COLOURS.get(pixel)
            if colour:
                left, top = x + col * scale, y + row * scale
                draw.rectangle([left, top, left + scale - 1, top + scale - 1],
                               fill=colour)


def _page_dino(draw, f, m, w, h):
    scale = 2
    left = 4
    play_width = w - 8
    ground_y = 116
    now_ms = m["now_ms"]
    _text(draw, w // 2, 25, m["dino_status"] or
          ("AUTORUN  >5 KM/H" if m["dino_moving"] else "PARKED: PRESS SPACE"),
          f.small, _WARN, "c")
    if m["dino_moving"]:
        span = play_width + 24 * scale
        x = left + int((now_ms // 25) % span) - 24 * scale
        _draw_sprite(draw, x, ground_y - 16 * scale, scale,
                     (now_ms // 150) % 2, m["dino_character"])
        for gx in range(left - int((now_ms // 25) % (4 * scale)),
                        left + play_width, 4 * scale):
            draw.line([gx, ground_y, gx + 2 * scale, ground_y],
                      fill=(123, 123, 123), width=1)
    else:
        draw.line([left, ground_y, left + play_width, ground_y],
                  fill=(123, 123, 123), width=1)
        for obstacle in m["dino_obstacles"]:
            x = left + int(obstacle["x"] * scale)
            width = obstacle["width"] * scale
            height = obstacle["height"] * scale
            if x >= left + play_width or x + width <= left:
                continue
            if obstacle["cactus"]:
                draw.rectangle([x + 2 * scale, ground_y - height,
                                x + 3 * scale, ground_y - 1], fill=(40, 160, 60))
                draw.rectangle([x, ground_y - 6 * scale,
                                x + 2 * scale - 1, ground_y - 5 * scale],
                               fill=(40, 160, 60))
                draw.rectangle([x, ground_y - 8 * scale,
                                x + scale - 1, ground_y - 5 * scale],
                               fill=(40, 160, 60))
                draw.rectangle([x + 4 * scale, ground_y - 4 * scale,
                                x + 6 * scale - 1, ground_y - 3 * scale],
                               fill=(40, 160, 60))
            else:
                draw.rounded_rectangle(
                    [x, ground_y - height, x + width - 1, ground_y - 1],
                    radius=scale, fill=(132, 132, 132))
        y = ground_y - 16 * scale - int(m["dino_y"] * scale)
        _draw_sprite(draw, left + 4 * scale, y, scale,
                     (now_ms // 150) % 2 if m["dino_state"] == "PLAYING" else 0,
                     m["dino_character"])
    hint = ("B: T-REX / BRONTO" if m["dino_moving"] else
            "B: CHANGE DINO   SPACE: JUMP")
    _text(draw, w // 2, 120, hint, f.small, _DIM, "c")
    _text(draw, w // 2, 143, "Brought to you by JP226Prints", f.tiny,
          _ACCENT, "c")


_PAGE_FUNCS = (
    _page_speed, _page_gps, _page_imu, _page_env, _page_espnow, _page_logger,
    _page_share,
    _page_waypoint, _page_camera, _page_gallery, _page_setup, _page_obd,
    _page_diag, _page_fossil, _page_dino,
)


def render_page(index: int, model: Dict,
                width: int = hardware.SCREEN_WIDTH,
                height: int = hardware.SCREEN_HEIGHT):
    from PIL import Image, ImageDraw
    index %= len(PAGES)
    f = _get_fonts()
    image = Image.new("RGB", (width, height), _BG)
    draw = ImageDraw.Draw(image)
    if PAGES[index] not in _NO_HEADER:
        _header(draw, f, "{}  {}".format(APP_TITLE.split()[0], PAGES[index]),
                model, width)
    page_model = dict(model)
    page_model["_canvas"] = image
    if index == PAGES.index("FOSSIL RECORD"):
        page_model["fossil_joke"] = _JOKES[
            (model["now_ms"] // 4000) % len(_JOKES)]
    _PAGE_FUNCS[index](draw, f, page_model, width, height)
    _footer(draw, f, index, width, height)
    return image


def save_screenshots(model: Dict, directory: str) -> list:
    """Render every page to <directory>/<n>_<page>.png (320x170)."""
    os.makedirs(directory, exist_ok=True)
    paths = []
    for i, name in enumerate(PAGES):
        path = os.path.join(
            directory, "{}_{}.png".format(i + 1, name.lower().replace(" ", "_")))
        render_page(i, model).save(path)
        paths.append(path)
    return paths
