"""320x170 pages drawn with Pillow (optional dependency).

render_page(index, model) -> PIL.Image. `model` is LoggerApp.ui_model(). Pages:
SPEED, GPS, LOGGER, WAYPOINT, DIAG. DIAG exists for hardware bring-up: it shows
the serial device/baud that was detected, NMEA counters, the last raw key name
and the data directory, which is what has to be confirmed on the real device.
"""

import os
from typing import Dict, Optional

from . import APP_TITLE, hardware

PAGES = ("SPEED", "GPS", "LOGGER", "WAYPOINT", "DIAG")

_BG = (8, 10, 16)
_PANEL = (22, 27, 40)
_TEXT = (230, 235, 245)
_DIM = (130, 140, 160)
_GOOD = (80, 220, 120)
_WARN = (255, 190, 60)
_BAD = (255, 90, 90)
_ACCENT = (90, 170, 255)

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
        self.big = _load_font(20, True)
        self.row = _load_font(14, True)
        self.small = _load_font(12, False)


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
    _text(draw, 6, 2, title, f.row, _TEXT)
    state = model["log_state"]
    colour = _STATE_COLOURS.get(state, _DIM)
    _text(draw, width - 18, 3, state, f.small, colour, "r")
    dot = _GOOD if model["fix_valid"] else _BAD
    draw.ellipse([width - 14, 6, width - 6, 14], fill=dot)


def _footer(draw, index, width, height):
    n = len(PAGES)
    x0 = width // 2 - (n * 12) // 2 + 6
    for i in range(n):
        colour = _ACCENT if i == index else (60, 66, 84)
        draw.ellipse([x0 + i * 12 - 3, height - 9, x0 + i * 12 + 3, height - 3],
                     fill=colour)


def _rows(draw, f, rows, top=28, step=21):
    for i, (label, value, colour) in enumerate(rows):
        y = top + i * step
        _text(draw, 8, y + 2, label, f.small, _DIM)
        _text(draw, 84, y, value, f.row, colour)


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
    _text(draw, 8, 130, "{}  SAT {}  HDOP {}".format(fix, sats, hdop), f.row,
          _GOOD if m["fix_valid"] else _BAD)
    _text(draw, 8, 148, m["local_text"] or "no time yet", f.small, _DIM)
    _text(draw, w - 8, 148, m["zone_name"], f.small, _DIM, "r")


def _page_gps(draw, f, m, w, h):
    fix_ok = m["fix_valid"]
    _rows(draw, f, [
        ("FIX", "VALID" if fix_ok else "NO FIX", _GOOD if fix_ok else _BAD),
        ("SATS/HDOP", "{} / {}   VDOP {}".format(
            _fmt(m["satellites"], "d"), _fmt(m["hdop"], ".2f"),
            _fmt(m["vdop"], ".2f")), _TEXT),
        ("LAT", _fmt(m["lat"], ".6f"), _TEXT),
        ("LON", _fmt(m["lon"], ".6f"), _TEXT),
        ("ALT / HDG", "{} m   {} deg".format(_fmt(m["alt"], ".0f"),
                                              _fmt(m["heading"], ".0f")), _TEXT),
        ("UTC", m["utc_text"] or "--", _DIM),
    ])


def _page_logger(draw, f, m, w, h):
    state = m["log_state"]
    err = m["storage_error"]
    _rows(draw, f, [
        ("STATE", state, _STATE_COLOURS.get(state, _DIM)),
        ("FILE", m["file_name"] or "--", _TEXT),
        ("ROWS", str(m["rows_written"]), _TEXT),
        ("NEXT", m["next_due"] or "--", _TEXT),
        ("ZONE", "{} ({})".format(m["zone_name"].replace("Australia/", ""),
                                  m["zone_source"]), _TEXT),
        ("STORAGE", "ERROR" if err else "OK", _BAD if err else _GOOD),
    ])


def _page_waypoint(draw, f, m, w, h):
    last = m["waypoint_last"]
    msg = m["waypoint_message"]
    colour = _GOOD if msg.endswith("SAVED") else (_BAD if msg else _DIM)
    _text(draw, w // 2, 32, msg or "READY", f.big, colour, "c")
    _text(draw, w // 2, 58, "press W or Enter to save", f.small, _DIM, "c")
    if last:
        _rows(draw, f, [
            ("LAST", last["id"], _TEXT),
            ("LAT", _fmt(last["lat"], ".6f"), _TEXT),
            ("LON", _fmt(last["lon"], ".6f"), _TEXT),
        ], top=84)
    else:
        _text(draw, w // 2, 100, "no waypoint yet", f.row, _DIM, "c")


def _page_diag(draw, f, m, w, h):
    counts = m["sentence_counts"]
    seen = " ".join("{}{}".format(k, counts[k]) for k in
                    ("GGA", "RMC", "GSA", "VTG") if k in counts) or "none"
    _rows(draw, f, [
        ("GPS PORT", "{} @ {}".format(os.path.basename(m["gps_device"]) or "--",
                                      m["gps_baud"] or "--"), _TEXT),
        ("STATUS", m["gps_status"] or "--", _TEXT),
        ("NMEA", "ok {}  bad {}".format(m["sentences_ok"], m["sentences_bad"]), _TEXT),
        ("SEEN", seen, _DIM),
        ("LAST KEY", m["last_key"] or "--", _TEXT),
        ("IMU/CLK", "{} / {}{}".format(m["imu"], m["clock_source"],
                                       " (gps ok)" if m["clock_gps_synced"] else ""),
         _TEXT),
    ], step=20)
    _text(draw, 8, 150, "v{}  HW UNVERIFIED".format(m["version"]), f.small, _WARN)


_PAGE_FUNCS = (_page_speed, _page_gps, _page_logger, _page_waypoint, _page_diag)


def render_page(index: int, model: Dict,
                width: int = hardware.SCREEN_WIDTH,
                height: int = hardware.SCREEN_HEIGHT):
    from PIL import Image, ImageDraw
    index %= len(PAGES)
    f = _get_fonts()
    image = Image.new("RGB", (width, height), _BG)
    draw = ImageDraw.Draw(image)
    _header(draw, f, "{}  {}".format(APP_TITLE.split()[0], PAGES[index]), model, width)
    _PAGE_FUNCS[index](draw, f, model, width, height)
    _footer(draw, index, width, height)
    return image


def save_screenshots(model: Dict, directory: str) -> list:
    """Render every page to <directory>/<n>_<page>.png (320x170)."""
    os.makedirs(directory, exist_ok=True)
    paths = []
    for i, name in enumerate(PAGES):
        path = os.path.join(directory, "{}_{}.png".format(i + 1, name.lower()))
        render_page(i, model).save(path)
        paths.append(path)
    return paths
