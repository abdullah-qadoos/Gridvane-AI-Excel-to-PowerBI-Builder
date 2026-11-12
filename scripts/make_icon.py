"""Render the vector brand geometry into Windows ICO frames without dependencies."""

import math
import struct
import zlib
from pathlib import Path


def rounded(x, y, left, top, width, height, radius):
    dx = max(left + radius - x, 0, x - (left + width - radius))
    dy = max(top + radius - y, 0, y - (top + height - radius))
    return dx * dx + dy * dy <= radius * radius


def color(x, y):
    if not rounded(x, y, 8, 8, 176, 176, 44):
        return (0, 0, 0, 0)
    result = (47, 90, 67, 255)
    if 57 <= math.hypot(x - 96, y - 96) <= 71:
        result = (71, 115, 91, 255)
    for left in (57, 76):
        for top in (71, 90, 109):
            if rounded(x, y, left, top, 13, 13, 3):
                result = (243, 236, 221, 255)
    for left, top, height, rgb in [
        (99, 106, 16, (200, 101, 47)),
        (117, 90, 32, (227, 181, 140)),
        (134, 74, 48, (243, 236, 221)),
    ]:
        if rounded(x, y, left, top, 11, height, 3):
            result = (*rgb, 255)
    return result


def chunk(kind, content):
    return (
        struct.pack(">I", len(content))
        + kind
        + content
        + struct.pack(">I", zlib.crc32(kind + content))
    )


def png(size):
    raw = bytearray()
    for row in range(size):
        raw.append(0)
        for column in range(size):
            samples = [
                color((column + (sx + 0.5) / 4) * 192 / size, (row + (sy + 0.5) / 4) * 192 / size)
                for sx in range(4)
                for sy in range(4)
            ]
            alpha = sum(sample[3] for sample in samples)
            raw.extend(
                [
                    round(sum(sample[c] * sample[3] for sample in samples) / alpha) if alpha else 0
                    for c in range(3)
                ]
                + [round(alpha / len(samples))]
            )
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def main():
    sizes = [16, 32, 48, 64, 256]
    frames = [png(size) for size in sizes]
    offset = 6 + 16 * len(frames)
    directory = bytearray(struct.pack("<HHH", 0, 1, len(frames)))
    for size, frame in zip(sizes, frames, strict=True):
        directory.extend(
            struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(frame), offset)
        )
        offset += len(frame)
    target = Path(__file__).resolve().parents[1] / "sheetforge/web/pivotra.ico"
    target.write_bytes(directory + b"".join(frames))


if __name__ == "__main__":
    main()
