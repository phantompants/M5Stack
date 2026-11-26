#!/usr/bin/env python3
"""Generate a funky 256x256 RGBA packaging/icon.png for JP226 OBD-II Scanner.

Pure standard library (built-in zlib PNG writer), runs without Pillow:
    python -I tools/make_icon.py [output_path]
"""

import math
import os
import struct
import sys
import zlib

SIZE = 256
SS = 3


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * max(0.0, min(1.0, t))


def _lerp_rgb(c1, c2, t: float):
    t = max(0.0, min(1.0, t))
    return (
        _lerp(c1[0], c2[0], t),
        _lerp(c1[1], c2[1], t),
        _lerp(c1[2], c2[2], t),
    )


def _round_rect_dist(x: float, y: float, radius: float = 48.0) -> float:
    cx = min(max(x, radius), SIZE - radius)
    cy = min(max(y, radius), SIZE - radius)
    dx = abs(x - cx)
    dy = abs(y - cy)
    if radius <= x <= SIZE - radius and radius <= y <= SIZE - radius:
        return min(x, SIZE - x, y, SIZE - y)
    return radius - math.hypot(dx, dy)


def _in_trapezoid(x: float, y: float, top_w: float, bot_w: float,
                  y0: float, y1: float, cx: float = 128.0) -> bool:
    if y < y0 or y > y1:
        return False
    t = (y - y0) / (y1 - y0)
    half_w = (top_w + (bot_w - top_w) * t) * 0.5
    return abs(x - cx) <= half_w


def _point_in_poly(x: float, y: float, pts) -> bool:
    inside = False
    n = len(pts)
    j = n - 1
    for i in range(n):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi
        ):
            inside = not inside
        j = i
    return inside


_BOLT = (
    (196, 26), (226, 26), (210, 56), (230, 56),
    (186, 102), (199, 68), (180, 68),
)


def _sample(x: float, y: float):
    d = _round_rect_dist(x, y, 48.0)
    if d < 0.0:
        return None

    # Neon cyan-to-magenta outer rim
    if d < 6.5:
        rim_t = (x + y) / (2.0 * SIZE)
        return _lerp_rgb((30, 240, 255), (255, 50, 190), rim_t)

    # Synthwave gradient background with subtle CRT scanlines + stars
    bg_t = (0.65 * y + 0.35 * x) / SIZE
    if bg_t < 0.55:
        col = _lerp_rgb((18, 11, 48), (68, 20, 108), bg_t / 0.55)
    else:
        col = _lerp_rgb((68, 20, 108), (16, 48, 86), (bg_t - 0.55) / 0.45)

    # Soft radial synth glow behind the plug
    glow = max(0.0, 1.0 - math.hypot(x - 128.0, y - 108.0) / 115.0)
    col = _lerp_rgb(col, (255, 60, 175), glow * 0.28)

    # Subtle scanlines
    if int(y) % 8 < 2:
        col = (col[0] * 0.88, col[1] * 0.88, col[2] * 0.88)

    # Retro corner sparkles
    for sx, sy, sr, scol in (
        (40, 44, 3.2, (255, 225, 90)),
        (56, 34, 2.0, (90, 245, 255)),
        (36, 162, 2.2, (255, 90, 200)),
        (220, 152, 2.5, (90, 245, 255)),
    ):
        if abs(x - sx) + abs(y - sy) <= sr * 1.4:
            col = scol

    # Drop shadow under OBD plug
    if _in_trapezoid(x, y, 184, 152, 54, 160, cx=132.0):
        col = (col[0] * 0.35, col[1] * 0.35, col[2] * 0.45)

    # OBD-II plug shell (neon cyan-to-lime border, dark metallic interior)
    if _in_trapezoid(x, y, 184, 152, 48, 154):
        shell_t = (x - 36.0) / 184.0
        col = _lerp_rgb((40, 245, 255), (90, 255, 145), shell_t)
        if _in_trapezoid(x, y, 164, 134, 57, 145):
            col = (18, 22, 34)
            # Center notch key
            if 118 <= x <= 138 and 57 <= y <= 65:
                col = (55, 225, 245)

            # Funky retro shades across upper pin row!
            in_left_lens = 64 <= x <= 120 and 71 <= y <= 104
            in_right_lens = 136 <= x <= 192 and 71 <= y <= 104
            in_bridge = 118 <= x <= 138 and 75 <= y <= 84
            in_brow = 58 <= x <= 198 and 69 <= y <= 76
            if in_left_lens or in_right_lens or in_bridge or in_brow:
                col = (255, 55, 175)  # hot-pink neon frames
                if (68 <= x <= 116 and 75 <= y <= 100) or (
                    140 <= x <= 188 and 75 <= y <= 100
                ):
                    col = (12, 12, 22)
                    # Diagonal white-cyan lens shine
                    if 0 <= (x - 74) - (y - 76) * 0.9 <= 8 or (
                        0 <= (x - 146) - (y - 76) * 0.9 <= 8
                    ):
                        col = (195, 250, 255)

            # Bottom 8 pins as a colorful neon grin!
            pin_colours = (
                (70, 245, 255), (85, 255, 145), (255, 225, 70), (255, 145, 55),
                (255, 145, 55), (255, 225, 70), (85, 255, 145), (70, 245, 255),
            )
            for idx in range(8):
                px = 75.0 + idx * 15.1
                if abs(x - px) <= 4.3 and 117 <= y <= 132:
                    col = pin_colours[idx]

    # Funky EV sound equalizer bars at the bottom!
    bars = (
        (56, 28, (255, 65, 175)),
        (78, 44, (255, 130, 55)),
        (100, 34, (255, 215, 65)),
        (122, 52, (95, 255, 140)),
        (144, 38, (60, 235, 255)),
        (166, 48, (145, 120, 255)),
        (188, 26, (255, 75, 195)),
    )
    base_y = 222.0
    for bx, bh, bcol in bars:
        if bx <= x <= bx + 14.0 and (base_y - bh) <= y <= base_y:
            seg = int((base_y - y) // 9)
            if (base_y - y) - seg * 9 < 6.5:
                col = bcol

    # Lightning bolt badge (top-right)
    if _point_in_poly(x + 2, y + 2, _BOLT):
        col = (20, 12, 40)
    if _point_in_poly(x, y, _BOLT):
        col = _lerp_rgb((255, 245, 100), (255, 145, 35), (y - 26.0) / 76.0)

    return col


def render() -> bytes:
    rows = []
    n = SS * SS
    for py in range(SIZE):
        row = bytearray([0])
        for px in range(SIZE):
            r = g = b = 0.0
            covered = 0
            for sy in range(SS):
                for sx in range(SS):
                    rgb = _sample(px + (sx + 0.5) / SS, py + (sy + 0.5) / SS)
                    if rgb is not None:
                        r += rgb[0]
                        g += rgb[1]
                        b += rgb[2]
                        covered += 1
            if covered:
                row += bytes((
                    min(255, int(r / covered)),
                    min(255, int(g / covered)),
                    min(255, int(b / covered)),
                    (covered * 255) // n,
                ))
            else:
                row += bytes((0, 0, 0, 0))
        rows.append(bytes(row))
    return b"".join(rows)


def png_bytes(raw: bytes) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return (
            struct.pack(">I", len(data))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def main(argv):
    default = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "packaging", "icon.png"
    )
    path = argv[1] if len(argv) > 1 else default
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(png_bytes(render()))
    print("wrote", os.path.abspath(path))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
