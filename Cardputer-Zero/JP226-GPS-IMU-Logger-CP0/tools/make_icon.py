#!/usr/bin/env python3
"""Generate packaging/icon.png (256x256 RGBA): a map pin on a dark rounded tile.

Pure standard library (own PNG writer with zlib), so it runs without Pillow:

    python -I tools/make_icon.py [output_path]

A placeholder-quality icon: replace it with a designed one before publishing.
"""

import math
import os
import struct
import sys
import zlib

SIZE = 256
SS = 3  # 3x3 supersampling for smooth edges

BG_TOP = (22, 27, 40)
BG_BOTTOM = (8, 10, 16)
PIN = (90, 170, 255)
DOT = (80, 220, 120)
HOLE = (12, 15, 24)


def inside_round_rect(x, y, radius=44):
    cx = min(max(x, radius), SIZE - radius)
    cy = min(max(y, radius), SIZE - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2


def make_pin():
    cx, cy, r = 128.0, 104.0, 58.0
    tip = (128.0, 214.0)
    theta = math.acos(r / (tip[1] - cy))
    p1 = (cx - r * math.sin(theta), cy + r * math.cos(theta))
    p2 = (cx + r * math.sin(theta), cy + r * math.cos(theta))

    def in_triangle(x, y):
        def sign(a, b, c):
            return (a[0] - c[0]) * (b[1] - c[1]) - (b[0] - c[0]) * (a[1] - c[1])
        d1, d2, d3 = sign((x, y), p1, p2), sign((x, y), p2, tip), sign((x, y), tip, p1)
        neg = d1 < 0 or d2 < 0 or d3 < 0
        pos = d1 > 0 or d2 > 0 or d3 > 0
        return not (neg and pos)

    def in_pin(x, y):
        return (x - cx) ** 2 + (y - cy) ** 2 <= r * r or in_triangle(x, y)

    def in_hole(x, y):
        return (x - cx) ** 2 + (y - cy) ** 2 <= 24.0 ** 2

    def in_dot(x, y):
        return (x - cx) ** 2 + (y - cy) ** 2 <= 11.0 ** 2

    return in_pin, in_hole, in_dot


def render():
    in_pin, in_hole, in_dot = make_pin()
    rows = []
    for py in range(SIZE):
        row = bytearray([0])  # PNG filter type 0
        for px in range(SIZE):
            r = g = b = a = 0
            for sy in range(SS):
                for sx in range(SS):
                    x = px + (sx + 0.5) / SS
                    y = py + (sy + 0.5) / SS
                    if not inside_round_rect(x, y):
                        continue
                    t = y / SIZE
                    colour = tuple(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t for i in range(3))
                    if in_pin(x, y):
                        colour = PIN
                        if in_hole(x, y):
                            colour = DOT if in_dot(x, y) else HOLE
                    r += colour[0]
                    g += colour[1]
                    b += colour[2]
                    a += 255
            n = SS * SS
            covered = a // 255
            if covered:
                row += bytes((int(r / covered), int(g / covered), int(b / covered),
                              int(a / n)))
            else:
                row += bytes((0, 0, 0, 0))
        rows.append(bytes(row))
    return b"".join(rows)


def png_bytes(raw):
    def chunk(kind, data):
        body = kind + data
        return (struct.pack(">I", len(data)) + body +
                struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) +
            chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def main(argv):
    default = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                           "packaging", "icon.png")
    path = argv[1] if len(argv) > 1 else default
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(png_bytes(render()))
    print("wrote", os.path.abspath(path))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
