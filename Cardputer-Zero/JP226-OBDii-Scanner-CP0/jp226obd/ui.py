"""320x170 Pillow UI pages for JP226 OBD-II Scanner (CardputerZero)."""

import os
from typing import Dict, List, Tuple

from . import hardware, keys

PAGES = (
    "CONNECT",
    "SCAN",
    "LIVE",
    "LOGGER FIT",
    "EV SOUND FIT",
    "MODULES",
    "SHARE",
    "DIAG",
)

CONNECT_PAGE_INDEX = PAGES.index("CONNECT")
SCAN_PAGE_INDEX = PAGES.index("SCAN")
LIVE_PAGE_INDEX = PAGES.index("LIVE")
MODULES_PAGE_INDEX = PAGES.index("MODULES")

_BG = (8, 10, 16)
_PANEL = (22, 27, 40)
_TEXT = (230, 235, 245)
_DIM = (130, 140, 160)
_GOOD = (80, 220, 120)
_WARN = (255, 190, 60)
_BAD = (255, 90, 90)
_ACCENT = (90, 170, 255)

_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
)
_REGULAR_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
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
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


class _Fonts:
    def __init__(self):
        self.big = _load_font(22, True)
        self.row = _load_font(15, True)
        self.small = _load_font(12, False)
        self.tiny = _load_font(11, False)


_fonts = None


def _get_fonts() -> _Fonts:
    global _fonts
    if _fonts is None:
        _fonts = _Fonts()
    return _fonts


def _text(draw, x, y, s, font, fill, align="l"):
    w = draw.textlength(s, font=font)
    if align == "r":
        x -= w
    elif align == "c":
        x -= w / 2
    draw.text((x, y), s, font=font, fill=fill)


def _status_colour(status: str):
    if status in ("COMPLETE", "READY"):
        return _GOOD
    if status in ("ERROR", "NO ECU", "NONE"):
        return _BAD
    return _WARN


def _header(draw, f: _Fonts, title: str, index: int, m: Dict, width: int) -> None:
    draw.rectangle([0, 0, width, 21], fill=_PANEL)
    _text(draw, 6, 2, "{} {}/{}".format(title, index + 1, len(PAGES)), f.row, _TEXT)
    badge = "LIVE" if m["live_active"] else str(m["status"])
    col = _GOOD if m["live_active"] else _status_colour(badge)
    _text(draw, width - 16, 4, badge, f.small, col, "r")
    draw.ellipse([width - 12, 6, width - 5, 13], fill=col)


_KIND_COLOURS = {
    keys.KIND_NAV: _ACCENT,
    keys.KIND_EDIT: _WARN,
    keys.KIND_RUN: _GOOD,
    keys.KIND_DIM: _DIM,
}


def footer_slots(index: int) -> List[Tuple[int, str, str]]:
    """5 fixed button columns aligned under the screen for keys 4-8."""
    by_key = {
        key_name: (label, kind)
        for key_name, label, _action, kind in keys.page_actions(PAGES[index % len(PAGES)])
    }
    slots: List[Tuple[int, str, str]] = []
    for col, key_name in enumerate(hardware.KEYS_PAGE_ACTIONS):
        num = key_name[len("KEY_"):]
        if key_name in by_key:
            label, kind = by_key[key_name]
            slots.append((col, "{} {}".format(num, label), kind))
        else:
            slots.append((col, num, keys.KIND_DIM))
    return slots


def _footer(draw, f: _Fonts, index: int, width: int, height: int) -> None:
    draw.rectangle([0, height - 15, width, height], fill=_PANEL)
    slot_w = width / len(hardware.KEYS_PAGE_ACTIONS)
    for col, text, kind in footer_slots(index):
        x = round(slot_w * (col + 0.5))
        _text(draw, x, height - 13, text, f.tiny, _KIND_COLOURS[kind], "c")


def _page_connect(draw, f: _Fonts, m: Dict, w: int, _h: int) -> None:
    _text(draw, 8, 24, "4-7 quick set  F/X row  W/E val", f.small, _DIM)
    for idx, (label, val) in enumerate(m["connect_rows"]):
        y = 42 + idx * 21
        sel = idx == m["connect_selection"]
        if sel:
            draw.rectangle([4, y - 1, w - 4, y + 19], fill=_PANEL)
        col = _ACCENT if sel else _TEXT
        _text(draw, 10, y + 2, label, f.small, col)
        _text(draw, w - 10, y + 1, str(val)[:22], f.row, col, "r")
    _text(draw, 8, 130, m["progress"][:42], f.small, _status_colour(m["status"]))


def _page_scan(draw, f: _Fonts, m: Dict, w: int, _h: int) -> None:
    col = _status_colour(m["status"])
    _text(draw, 8, 26, m["status"], f.big, col)
    _text(draw, w - 8, 30, "PROTO " + str(m["protocol"]), f.row, _ACCENT, "r")
    _text(draw, 8, 54, m["progress"][:42], f.small, _TEXT)

    rows = [
        ("LINK", str(m["link_name"])[:24]),
        ("VIN", str(m["vin"] or "--")[:20]),
        ("TYPE", "{}  |  {} PIDs  |  {} ECUs".format(
            m["powertrain"], m["pid_count"], m["module_count"])),
        ("PROFILE", str(m["profile_file"] or "--")[:30]),
    ]
    for i, (k, v) in enumerate(rows):
        y = 74 + i * 18
        _text(draw, 8, y, k, f.small, _DIM)
        _text(draw, 76, y, v, f.small, _TEXT)
    _text(draw, w // 2, 141, "ENTER / 8: Start / Cancel Scan", f.tiny, _DIM, "c")


def _page_live(draw, f: _Fonts, m: Dict, w: int, _h: int) -> None:
    live = m["live"]
    vals = live.get("values", {})
    hdr_col = _GOOD if m["live_active"] else _DIM
    _text(draw, 8, 25, "RATE {:.1f} Hz ({:.0f} ms)".format(
        live.get("hz", 0.0), live.get("latency_ms", 0.0)), f.small, hdr_col)
    _text(draw, w - 8, 25, "ENTER: {}".format("STOP" if m["live_active"] else "START"),
          f.small, _ACCENT, "r")

    def _v(key, unit=""):
        if key not in vals or vals[key] is None:
            return "--"
        v = vals[key]
        if isinstance(v, float):
            return "{:.1f}{}".format(v, unit)
        return "{}{}".format(v, unit)

    pedal = _v("PEDAL_D", "%") if "PEDAL_D" in vals else _v("THROTTLE", "%")
    torque = _v("ACT_TRQ", "%") if "ACT_TRQ" in vals else _v("LOAD", "%")
    energy = _v("HV_SOC", "%") if "HV_SOC" in vals else _v("VOLT", "V")
    energy_lbl = "SOC" if "HV_SOC" in vals else "VOLT"
    temp = _v("COOLANT", "C") if "COOLANT" in vals else _v("AMBIENT", "C")

    cards = [
        ("SPEED", _v("SPEED", " km/h")),
        ("RPM", _v("RPM", "")),
        ("PEDAL", pedal),
        ("TORQ/LD", torque),
        (energy_lbl, energy),
        ("TEMP", temp),
    ]
    for idx, (label, val) in enumerate(cards):
        col_i, row_i = idx % 2, idx // 2
        x = 8 + col_i * 158
        y = 44 + row_i * 34
        draw.rectangle([x - 2, y, x + 148, y + 30], fill=_PANEL)
        _text(draw, x + 4, y + 2, label, f.tiny, _DIM)
        _text(draw, x + 142, y + 12, val, f.row, _TEXT, "r")

    if live.get("error"):
        _text(draw, 8, 146, str(live["error"])[:42], f.tiny, _BAD)


_SHORT_LOGGER_NAMES = {
    "RPM": "RPM",
    "SPEED": "SPD",
    "COOLANT": "CLT",
    "THROTTLE": "THR",
    "LOAD": "LOD",
    "INTAKE": "IAT",
    "MAP": "MAP",
    "BARO": "BAR",
    "FUEL": "FUL",
    "AMBIENT": "AMB",
    "OIL": "OIL",
    "VOLTAGE": "VLT",
}


def _page_logger_fit(draw, f: _Fonts, m: Dict, w: int, _h: int) -> None:
    lf = m["logger_fit"]
    cnt = lf.get("supported_count", 0)
    tot = lf.get("total_count", 12)
    ready_col = _GOOD if lf.get("ready") else _WARN
    _text(draw, 8, 25, "JP226 GPS-IMU LOGGER", f.small, _ACCENT)
    _text(draw, w - 8, 25, "{}/{} CORE".format(cnt, tot), f.row, ready_col, "r")

    metrics = lf.get("metrics", [])
    for idx, item in enumerate(metrics):
        col_i = idx % 3
        row_i = idx // 3
        x = 8 + col_i * 104
        y = 46 + row_i * 20
        ok = bool(item.get("supported"))
        col = _GOOD if ok else (75, 82, 100)
        mark = "+" if ok else "-"
        short = _SHORT_LOGGER_NAMES.get(item["name"], item["name"][:4])
        val = item.get("value")
        if val is None:
            val_txt = ""
        elif isinstance(val, float) and short == "VLT":
            val_txt = " {:.1f}".format(val)
        elif isinstance(val, (int, float)):
            val_txt = " {:.0f}".format(val)
        else:
            val_txt = " {}".format(val)
        _text(draw, x, y, "{} {}{}".format(mark, short, val_txt[:6]), f.tiny, col)

    tpms = "YES ({}w)".format(lf.get("tpms_pressure_count", 0)) if lf.get("tpms_ready") else "NO"
    dtc = "YES" if lf.get("dtc_supported") else "NO"
    _text(draw, 8, 128, "MIL:{}   DTC:{}   FORD TPMS:{}".format(
        "YES" if lf.get("mil_supported") else "NO", dtc, tpms), f.small, _TEXT)
    _text(draw, 8, 141, "Compatible with CP0 & Cardputer ADV", f.tiny, _DIM)


def _page_ev_fit(draw, f: _Fonts, m: Dict, w: int, _h: int) -> None:
    ev = m["ev_fit"]
    grade = str(ev.get("grade", "NONE"))
    _text(draw, 8, 24, "EV SOUND EMULATOR", f.small, _ACCENT)
    _text(draw, w - 8, 23, "{} ({})".format(grade, ev.get("powertrain_hint", "?")),
          f.row, _status_colour(grade), "r")

    channels = ev.get("channels", {})
    rows = [
        ("SPEED", channels.get("speed")),
        ("PEDAL", channels.get("pedal_or_throttle")),
        ("RPM/MTR", channels.get("rpm_or_motor")),
        ("TRQ/LD", channels.get("torque_or_load")),
        ("SOC/12V", channels.get("power_or_soc")),
    ]
    for idx, (label, info) in enumerate(rows):
        y = 43 + idx * 17
        _text(draw, 8, y, label, f.small, _DIM)
        if info:
            txt = "{} ({})".format(info["source"], info.get("value", ""))
            _text(draw, 82, y, txt[:34], f.small, _GOOD)
        else:
            _text(draw, 82, y, "NOT DETECTED", f.small, (95, 102, 120))

    _text(draw, 8, 128, "RATE: {} Hz   MULTI-PID: {}".format(
        ev.get("effective_hz", 0.0),
        "YES" if ev.get("multi_pid_supported") else "NO"), f.small, _TEXT)
    rec = " ".join(ev.get("recommended_fast_pids", [])) or "none"
    _text(draw, 8, 141, "FAST PIDS: " + rec[:30], f.tiny, _DIM)


def _page_modules(draw, f: _Fonts, m: Dict, w: int, _h: int) -> None:
    mods = m["modules"]
    dtcs = m["dtcs"]
    stored = dtcs.get("stored", []) if isinstance(dtcs, dict) else []
    pending = dtcs.get("pending", []) if isinstance(dtcs, dict) else []
    _text(draw, 8, 24, "ECUs: {}   F/X scroll".format(len(mods)), f.small, _DIM)
    _text(draw, w - 8, 24, "DTC S:{} P:{}".format(len(stored), len(pending)),
          f.small, _WARN if (stored or pending) else _GOOD, "r")

    keys = sorted(mods.keys())
    scroll = min(m["module_scroll"], max(0, len(keys) - 4))
    visible = keys[scroll:scroll + 4]
    if not visible:
        _text(draw, w // 2, 74, "No UDS modules scanned yet", f.row, _DIM, "c")
    for idx, hdr in enumerate(visible):
        info = mods[hdr]
        y = 44 + idx * 22
        draw.rectangle([4, y - 1, w - 4, y + 19], fill=_PANEL)
        dids = info.get("dids", {}) if isinstance(info, dict) else {}
        resp = ",".join(info.get("responders", [])) if isinstance(info, dict) else ""
        label = info.get("label", "") if isinstance(info, dict) else ""
        _text(draw, 10, y + 2, "{}->{}".format(hdr, resp or "?"), f.small, _ACCENT)
        right = "{} DIDs {}".format(len(dids), label[:10]).strip()
        _text(draw, w - 10, y + 2, right, f.small, _TEXT, "r")

    dtc_line = "Stored: " + (" ".join(stored[:4]) or "none")
    _text(draw, 8, 138, dtc_line[:44], f.tiny, _TEXT if stored else _DIM)


def _page_share(draw, f: _Fonts, m: Dict, w: int, _h: int) -> None:
    running = bool(m.get("web_running"))
    _text(draw, 8, 25, "IPHONE / WI-FI LOG TRANSFER", f.small, _ACCENT)
    _text(draw, w - 8, 24, "ON" if running else "OFF", f.row,
          _GOOD if running else _BAD, "r")
    if running:
        draw.rectangle([4, 44, w - 4, 88], fill=_PANEL)
        _text(draw, w // 2, 48, str(m.get("web_url", "--")), f.row, _GOOD, "c")
        _text(draw, w // 2, 68, str(m.get("web_local_url", "--")), f.small, _TEXT, "c")
    elif m.get("web_error"):
        _text(draw, 8, 54, str(m["web_error"])[:40], f.small, _BAD)
    else:
        _text(draw, w // 2, 58, "Server stopped (press 4 WEB)", f.small, _WARN, "c")

    _text(draw, 8, 98, "FILES: {}   DOWNLOADS: {}".format(
        m.get("web_files_count", 0), m.get("web_downloads", 0)), f.small, _TEXT)
    _text(draw, 8, 118, "Open URL in iPhone Safari on same Wi-Fi", f.small, _DIM)
    _text(draw, 8, 136, "Tap Download or ZIP -> Files app", f.tiny, _DIM)


def _page_diag(draw, f: _Fonts, m: Dict, w: int, _h: int) -> None:
    ad = m["adapter_details"]
    rows = [
        ("VERSION", "v{} ({})".format(m["version"], m["build_git_hash"])),
        ("ADAPTER", str(ad.get("ATI") or ad.get("AT@1") or "--")[:28]),
        ("VOLTAGE", str(ad.get("ATRV") or "--")),
        ("PROTO", str(ad.get("ATDP") or m["protocol"])[:28]),
        ("LAST KEY", str(m["last_key"] or "--")),
        ("OUT DIR", str(m["data_dir"])[-28:]),
    ]
    for idx, (k, v) in enumerate(rows):
        y = 26 + idx * 19
        _text(draw, 8, y, k, f.small, _DIM)
        _text(draw, 88, y, v, f.small, _TEXT)
    _text(draw, 8, 141, "Build {}   Q/Esc quits".format(m["build_date"]), f.tiny, _DIM)


_PAGE_FUNCS = (
    _page_connect,
    _page_scan,
    _page_live,
    _page_logger_fit,
    _page_ev_fit,
    _page_modules,
    _page_share,
    _page_diag,
)


def render_page(
    index: int,
    model: Dict,
    width: int = hardware.SCREEN_WIDTH,
    height: int = hardware.SCREEN_HEIGHT,
):
    from PIL import Image, ImageDraw
    index %= len(PAGES)
    f = _get_fonts()
    image = Image.new("RGB", (width, height), _BG)
    draw = ImageDraw.Draw(image)
    _header(draw, f, "OBD  " + PAGES[index], index, model, width)
    _PAGE_FUNCS[index](draw, f, model, width, height)
    _footer(draw, f, index, width, height)
    return image


def save_screenshots(model: Dict, directory: str) -> List[str]:
    os.makedirs(directory, exist_ok=True)
    paths: List[str] = []
    for idx, name in enumerate(PAGES):
        path = os.path.join(
            directory, "{}_{}.png".format(idx + 1, name.lower().replace(" ", "_"))
        )
        render_page(idx, model).save(path)
        paths.append(path)
    return paths
