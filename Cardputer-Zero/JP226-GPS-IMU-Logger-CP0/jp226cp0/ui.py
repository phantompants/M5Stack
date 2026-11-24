"""320x170 dashboard and dinosaur pages drawn with Pillow."""

import os
from typing import Dict, Optional

from . import APP_TITLE, hardware, keys

PAGES = ("SPEED", "GPS", "IMU", "LOGGER", "WAYPOINT", "SETUP", "OBD",
         "DIAG", "FOSSIL RECORD", "DINO DASH")
SETTINGS_PAGE_INDEX = PAGES.index("SETUP")
OBD_PAGE_INDEX = PAGES.index("OBD")
DINO_PAGE_INDEX = PAGES.index("DINO DASH")

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
    _text(draw, 6, 2, title, f.row, _TEXT)
    state = model["log_state"]
    colour = _STATE_COLOURS.get(state, _DIM)
    _text(draw, width - 18, 3, state, f.small, colour, "r")
    dot = _GOOD if model["fix_valid"] else _BAD
    draw.ellipse([width - 14, 6, width - 6, 14], fill=dot)


def footer_items(index: int):
    """(text, kind) footer labels: page navigation, then this page's actions."""
    items = [("Z/C PAGE", keys.KIND_NAV)]
    for key_name, label, _action, kind in keys.page_actions(PAGES[index % len(PAGES)]):
        items.append(("{} {}".format(key_name[len("KEY_"):], label), kind))
    return items


def _footer(draw, f, index, width, height):
    draw.rectangle([0, height - 14, width, height], fill=_PANEL)
    items = footer_items(index)
    slot = width / max(len(items), 2)
    for i, (text, kind) in enumerate(items):
        x = round(slot * (i + 0.5))
        _text(draw, x, height - 12, text, f.tiny, _KIND_COLOURS[kind], "c")


def _rows(draw, f, rows, top=28, step=21):
    for i, (label, value, colour) in enumerate(rows):
        y = top + i * step
        _text(draw, 8, y + 2, label, f.small, _DIM)
        _text(draw, 84, y, value, f.row, colour)


def _two_columns(draw, f, rows, top=34, step=35):
    for row_index, (left, right) in enumerate(rows):
        y = top + row_index * step
        for column, item in enumerate((left, right)):
            if item is None:
                continue
            label, value, colour = item
            x0 = 8 if column == 0 else 166
            x1 = 154 if column == 0 else 312
            _text(draw, x0, y + 2, label, f.small, _DIM)
            _text(draw, x1, y, value, f.row, colour, "r")


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
        (("UTC", m["utc_text"] or "--", _DIM),
         ("SOURCE", m["gps_source"], _WARN)),
    ], top=35, step=24)


def _page_logger(draw, f, m, w, h):
    state = m["log_state"]
    err = m["storage_error"]
    _two_columns(draw, f, [
        (("STATE", state, _STATE_COLOURS.get(state, _DIM)),
         ("ROWS", str(m["rows_written"]), _TEXT)),
        (("NEXT", m["next_due"] or "--", _TEXT),
         ("STORAGE", "ERROR" if err else "OK", _BAD if err else _GOOD)),
        (("FILE", m["file_name"] or "--", _TEXT),
         ("ZONE", m["zone_name"].replace("Australia/", ""), _TEXT)),
    ], top=40, step=34)


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
    _two_columns(draw, f, [
        (("GPS PORT", "{} @ {}".format(os.path.basename(m["gps_device"]) or "--",
                                       m["gps_baud"] or "--"), _TEXT),
         ("NMEA OK", str(m["sentences_ok"]), _TEXT)),
        (("STATUS", m["gps_status"] or "--", _TEXT),
         ("NMEA BAD", str(m["sentences_bad"]), _TEXT)),
        (("SEEN", seen, _DIM), ("LAST KEY", m["last_key"] or "--", _TEXT)),
        (("IMU", m["imu"], _TEXT),
         ("CAMERA", "READY" if m["camera_available"] else "NOT FOUND", _TEXT)),
        (("CLOCK", "{}{}".format(m["clock_source"],
                                  " GPS" if m["clock_gps_synced"] else ""), _TEXT),
         ("GPS PWR", m["gps_power_status"] or "--", _TEXT)),
    ], top=29, step=21)
    if m["imu_error"]:
        _text(draw, 8, 144, m["imu_error"][:44], f.tiny, _WARN)
    else:
        _text(draw, 8, 144, "v{}  {}".format(m["version"], m["data_dir"][-30:]),
              f.tiny, _DIM)


def _page_fossil(draw, f, m, w, h):
    _text(draw, 8, 30, "JP226 GPS-IMU LOGGER", f.row, _ACCENT)
    _text(draw, 8, 51, "VERSION", f.small, _DIM)
    _text(draw, 106, 49, "v" + m["version"], f.row, _TEXT)
    _text(draw, 8, 72, "BUILD", f.small, _DIM)
    _text(draw, 106, 70, m["build_git_hash"], f.row, _TEXT)
    _text(draw, 8, 93, "DATE", f.small, _DIM)
    _text(draw, 106, 91, m["build_date"], f.row, _TEXT)
    _draw_sprite(draw, 14, 111, 2, (m["now_ms"] // 150) % 2, "BRONTO")
    _draw_sprite(draw, 244, 111, 2, (m["now_ms"] // 150) % 2, "TREX")
    _text(draw, 64, 144, "BRONTO", f.tiny, _GOOD, "c")
    _text(draw, 292, 144, "T-REX", f.tiny, _WARN, "c")
    _text(draw, w // 2, 121, m["fossil_joke"], f.small, _ACCENT, "c")


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
    _page_speed, _page_gps, _page_imu, _page_logger, _page_waypoint,
    _page_setup, _page_obd, _page_diag, _page_fossil, _page_dino,
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
