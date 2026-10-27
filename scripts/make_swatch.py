"""Write a solid-colour RGB PNG (stdlib only), e.g. the 256x256 red vision fixture.

    .venv/bin/python scripts/make_swatch.py fixtures/images/red-256.png 256 255 0 0
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path


def solid_png(size: int, rgb: tuple[int, int, int]) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    row = b"\x00" + bytes(rgb) * size  # filter type 0 + pixels
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8-bit RGB
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(row * size, 9)) + chunk(b"IEND", b""))


if __name__ == "__main__":
    out, size, r, g, b = sys.argv[1], *map(int, sys.argv[2:6])
    Path(out).write_bytes(solid_png(size, (r, g, b)))
