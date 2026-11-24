#!/usr/bin/env python3
"""Generate packaging/icon.png (256x256 RGBA): an OBD-II connector + pulse.

Pure standard library (built-in zlib PNG writer), runs without Pillow:
    python -I tools/make_icon.py [output_path]
"""

import os
import struct
import sys
import zlib

SIZE = 256
SS = 2

BG_TOP = (22, 27, 40)
BG_BOTTOM = (8, 10, 16)
SHELL = (90, 170, 255)
INNER = (16, 20, 30)
PIN = (80, 220, 120)
PULSE = (255, 190, 60)


def _inside_round_rect(x: float, y: float, radius: float = 44.0) -> bool:
    cx = min(max(x, radius), SIZE - radius)
    cy = min(max(y, radius), SIZE - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2


def _in_trapezoid(x: float, y: float, top_w: float, bot_w: float,
                  y0: float, y1: float) -> bool:
    if y < y0 or y > y1:
        return False
    t = (y - y0) / (y1 - y0)
    half_w = (top_w + (bot_w - top_w) * t) * 0.5
    return abs(x - 128.0) <= half_w


def _in_pins(x: float, y: float) -> bool:
    for row_y in (92.0, 120.0):
        if abs(y - row_y) <= 5.5:
            for col in range(8):
                px = 68.0 + col * 17.1
                if abs(x - px) <= 4.2:
                    return True
    return False


def _in_pulse(x: float, y: float) -> bool:
    # Crisp speed/audio pulse bar below the OBD plug
    segs = (
        (56, 184, 88, 184),
        (88, 184, 104, 160),
        (104, 160, 124, 202),
        (124, 202, 142, 166),
        (142, 166, 158, 184),
        (158, 184, 200, 184),
    )
    for x1, y1, x2, y2 in segs:
        dx, dy = x2 - x1, y2 - y1
        seg_len2 = dx * dx + dy * dy
        if seg_len2 == 0:
            continue
        t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / seg_len2))
        nx, ny = x1 + t * dx, y1 + t * dy
        if (x - nx) ** 2 + (y - ny) ** 2 <= 4.5 ** 2:
            return True
    return False


def render() -> bytes:
    rows = []
    for py in range(SIZE):
        row = bytearray([0])
        for px in range(SIZE):
            r = g = b = a = 0
            for sy in range(SS):
                for sx in range(SS):
                    x = px + (sx + 0.5) / SS
                    y = py + (sy + 0.5) / SS
                    if not _inside_round_rect(x, y):
                        continue
                    t = y / SIZE
                    col = (
                        BG_TOP[0] + (BG_BOTTOM[0] - BG_TOP[0]) * t,
                        BG_TOP[1] + (BG_BOTTOM[1] - BG_TOP[1]) * t,
                        BG_TOP[2] + (BG_BOTTOM[2] - BG_TOP[2]) * t,
                    )
                    if _in_trapezoid(x, y, 168.0, 140.0, 66.0, 144.0):
                        col = SHELL
                        if _in_trapezoid(x, y, 150.0, 124.0, 74.0, 136.0):
                            col = INNER
                            if _in_pins(x, y):
                                col = PIN
                    if _in_pulse(x, y):
                        col = PULSE
                    r += int(col[0])
                    g += int(col[1])
                    b += int(col[2])
                    a += 255
            n = SS * SS
            covered = a // 255
            if covered:
                row += bytes((r // covered, g // covered, b // covered, a // n))
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
