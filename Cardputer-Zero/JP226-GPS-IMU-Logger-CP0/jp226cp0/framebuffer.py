"""/dev/fb0 output (RGB565), following AppBuilder's Python_FrameBuffer_HelloWorld.

Linux only. mmap + ioctl + Pillow image -> RGB565 (numpy if present, a slow
pure-Python loop otherwise). UNVERIFIED on the CardputerZero: it copies the
example's approach but has never run on the device.
"""

import fcntl  # Linux only: importing this module on Windows raises ImportError
import mmap
import os
import struct

FBIOGET_VSCREENINFO = 0x4600
FBIOGET_FSCREENINFO = 0x4602
VAR_FMT = "8I"
FIX_FMT = "16sL" + "I" * 6 + "HHHI"

try:
    import numpy as np
except ImportError:  # optional
    np = None


def rgb_to_rgb565_bytes(img) -> bytes:
    if np is not None:
        arr = np.asarray(img, dtype=np.uint16)
        r = (arr[..., 0] >> 3) & 0x1F
        g = (arr[..., 1] >> 2) & 0x3F
        b = (arr[..., 2] >> 3) & 0x1F
        return ((r << 11) | (g << 5) | b).astype("<u2").tobytes()
    out = bytearray()
    px = img.load()
    w, h = img.size
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y]
            v = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
            out.append(v & 0xFF)
            out.append((v >> 8) & 0xFF)
    return bytes(out)


class Framebuffer:
    def __init__(self, device: str):
        self.device = device
        self.fd = os.open(device, os.O_RDWR)
        try:
            buf = bytearray(160)
            fcntl.ioctl(self.fd, FBIOGET_VSCREENINFO, buf)
            self.width, self.height, _, _, _, _, self.bpp, _ = struct.unpack_from(
                VAR_FMT, buf, 0)
            fix = bytearray(struct.calcsize(FIX_FMT))
            fcntl.ioctl(self.fd, FBIOGET_FSCREENINFO, fix)
            fields = struct.unpack(FIX_FMT, fix)
            smem_len, self.line_length = fields[2], fields[8]
            if self.bpp != 16:
                raise OSError("expected 16 bpp (RGB565), got {}".format(self.bpp))
            self.mm = mmap.mmap(self.fd, smem_len, mmap.MAP_SHARED,
                                mmap.PROT_READ | mmap.PROT_WRITE)
        except Exception:
            os.close(self.fd)
            raise

    def show(self, img) -> None:
        if img.size != (self.width, self.height):
            img = img.resize((self.width, self.height))
        data = rgb_to_rgb565_bytes(img)
        row_bytes = self.width * 2
        if self.line_length == row_bytes:
            self.mm.seek(0)
            self.mm.write(data)
            return
        for y in range(self.height):
            self.mm.seek(y * self.line_length)
            self.mm.write(data[y * row_bytes:(y + 1) * row_bytes])

    def close(self) -> None:
        try:
            self.mm.close()
        finally:
            os.close(self.fd)
