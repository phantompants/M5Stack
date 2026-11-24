"""320x170 dashboard and dinosaur pages drawn with Pillow."""

import os
from typing import Dict, Optional

from . import APP_TITLE, hardware

PAGES = ("SPEED", "GPS", "IMU", "LOGGER", "WAYPOINT", "SETUP", "DIAG",
         "FOSSIL RECORD", "DINO DASH")
SETTINGS_PAGE_INDEX = PAGES.index("SETUP")
DINO_PAGE_INDEX = PAGES.index("DINO DASH")

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


def _page_imu(draw, f, m, w, h):
    sample = m["imu_sample"]
    if not m["imu_available"] or sample is None or not sample.valid:
        message = m["imu_error"] or "IMU NOT READY"
        _text(draw, w // 2, 58, message[:34], f.row, _WARN, "c")
        _text(draw, w // 2, 82, m["imu"], f.small, _DIM, "c")
        return
    _rows(draw, f, [
        ("ACC m/s2", "{:.2f}  {:.2f}  {:.2f}".format(
            sample.ax_mps2, sample.ay_mps2, sample.az_mps2), _TEXT),
        ("GYRO dps", "{:.1f}  {:.1f}  {:.1f}".format(
            sample.gx_dps, sample.gy_dps, sample.gz_dps), _TEXT),
        ("PITCH/ROLL", "{:.1f} / {:.1f} deg".format(
            sample.pitch_deg, sample.roll_deg), _TEXT),
        ("VIB RMS", "{:.3f} m/s2".format(sample.vibration_rms_mps2), _TEXT),
        ("SAMPLES", str(sample.sample_count), _DIM),
    ])


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
            ("PHOTO", os.path.basename(last.get("photo", "")) or m["camera_message"] or "--",
             _TEXT),
            ("ROAD", last.get("road_type") or "--", _TEXT),
        ], top=76, step=18)
    else:
        _text(draw, w // 2, 100, "no waypoint yet", f.row, _DIM, "c")


def _page_setup(draw, f, m, w, h):
    _text(draw, 8, 24, "UP/DOWN select   A/D edit", f.small, _DIM)
    for i, (label, value) in enumerate(m["vehicle_settings"]):
        y = 42 + i * 12
        selected = i == m["vehicle_selection"]
        if selected:
            draw.rectangle([4, y - 1, w - 4, y + 11], fill=_PANEL)
        colour = _ACCENT if selected else _TEXT
        _text(draw, 10, y, label, f.small, colour)
        _text(draw, w - 10, y, value, f.small, colour, "r")
    _text(draw, w // 2, 154, "Saved automatically   W=done", f.small, _DIM, "c")


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
        ("CAMERA", m["camera_message"] or
         ("READY" if m["camera_available"] else "NOT FOUND"), _TEXT),
    ], step=17)
    if m["imu_error"]:
        _text(draw, 8, 150, m["imu_error"][:44], f.small, _WARN)
    else:
        _text(draw, 8, 150, "v{}  {}".format(m["version"], m["data_dir"][-30:]),
              f.small, _DIM)


def _page_fossil(draw, f, m, w, h):
    _text(draw, 8, 30, "JP226 GPS-IMU LOGGER", f.row, _ACCENT)
    _text(draw, 8, 51, "VERSION", f.small, _DIM)
    _text(draw, 106, 49, "v" + m["version"], f.row, _TEXT)
    _text(draw, 8, 72, "BUILD", f.small, _DIM)
    _text(draw, 106, 70, m["build_git_hash"], f.row, _TEXT)
    _text(draw, 8, 93, "DATE", f.small, _DIM)
    _text(draw, 106, 91, m["build_date"], f.row, _TEXT)
    _text(draw, w // 2, 124, m["fossil_joke"], f.small, _WARN, "c")
    _text(draw, w - 8, 148, "Python port", f.small, _DIM, "r")


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


def _draw_dino(draw, x, y, scale, frame):
    rows = _DINO_FRAMES[frame % len(_DINO_FRAMES)]
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
    ground_y = 126
    now_ms = m["now_ms"]
    _text(draw, w // 2, 25, m["dino_status"] or
          ("AUTORUN  >5 KM/H" if m["dino_moving"] else "PARKED: PRESS SPACE"),
          f.small, _WARN, "c")
    if m["dino_moving"]:
        span = play_width + 24 * scale
        x = left + int((now_ms // 25) % span) - 24 * scale
        _draw_dino(draw, x, ground_y - 16 * scale, scale,
                   (now_ms // 150) % 2)
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
        _draw_dino(draw, left + 4 * scale, y, scale,
                   (now_ms // 150) % 2 if m["dino_state"] == "PLAYING" else 0)
    hint = "AUTORUN WHILE MOVING" if m["dino_moving"] else \
        "SPACE / ENTER / ;  JUMP"
    _text(draw, w // 2, 134, hint, f.small, _DIM, "c")
    _text(draw, w // 2, 150, "Brought to you by JP226Prints", f.small,
          _ACCENT, "c")


_PAGE_FUNCS = (
    _page_speed, _page_gps, _page_imu, _page_logger, _page_waypoint,
    _page_setup, _page_diag, _page_fossil, _page_dino,
)


def render_page(index: int, model: Dict,
                width: int = hardware.SCREEN_WIDTH,
                height: int = hardware.SCREEN_HEIGHT):
    from PIL import Image, ImageDraw
    index %= len(PAGES)
    f = _get_fonts()
    image = Image.new("RGB", (width, height), _BG)
    draw = ImageDraw.Draw(image)
    _header(draw, f, "{}  {}".format(APP_TITLE.split()[0], PAGES[index]), model, width)
    if index == PAGES.index("FOSSIL RECORD"):
        page_model = dict(model)
        page_model["fossil_joke"] = _JOKES[
            (model["now_ms"] // 4000) % len(_JOKES)]
    else:
        page_model = model
    _PAGE_FUNCS[index](draw, f, page_model, width, height)
    _footer(draw, index, width, height)
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
