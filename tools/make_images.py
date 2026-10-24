#!/usr/bin/env python3
"""Draw the mark, the favicons and the still grain into backend/static/img (spec 3.3, 3.6).

Standard library only. Rerun it after changing MARK_ROWS. From the repo root:
  python3 tools/make_images.py

The PNG bytes depend on the zlib build, so on another machine a rerun can change the files'
bytes while every pixel stays the same. backend/pages/tests_images.py reads pixels, not
bytes; commit regenerated PNGs only when the pixels changed.
"""
import random
import re
import struct
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
IMG = REPO / "backend" / "static" / "img"
MARK_ROWS = ("####", "###.", "##.#", "#.#.")  # "Let go": # is a filled cell, on a 4x4 grid
MARK_PATH = "M0 0H560V140H420V280H280V420H140V560H0Z M420 280H560V420H420Z M280 420H420V560H280Z"
PAPER = (0xEB, 0xE6, 0xDE)
INK = (0x2C, 0x29, 0x25)


def png(width, height, pixel):
    """An 8-bit RGBA PNG; pixel(x, y) returns (r, g, b, a). Every row uses filter 0."""
    rows = b"".join(b"\x00" + b"".join(bytes(pixel(x, y)) for x in range(width)) for y in range(height))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b"")


def mark_tile(size, padding):
    """The mark in ink on a paper square, with `padding` px of paper around it."""
    cell = (size - 2 * padding) // 4

    def pixel(x, y):
        column, row = (x - padding) // cell, (y - padding) // cell
        if 0 <= column < 4 and 0 <= row < 4 and MARK_ROWS[row][column] == "#":
            return (*INK, 255)
        return (*PAPER, 255)

    return pixel


def mark_card(width, height, mark):
    """The mark, centred in ink on a paper card (the link-preview image). Each cell is a whole number of
    pixels, so the mark is `mark // 4 * 4` px square: 280 for 280 or 283, nothing at all below 4."""
    cell = mark // 4
    size = cell * 4
    left, top = (width - size) // 2, (height - size) // 2

    def pixel(x, y):
        if 0 <= x - left < size and 0 <= y - top < size and MARK_ROWS[(y - top) // cell][(x - left) // cell] == "#":
            return (*INK, 255)
        return (*PAPER, 255)

    return pixel


def veil_constant(name):
    """A number the shader is tuned by, `const NAME = 0.025;` in backend/static/js/veil.js."""
    source = (REPO / "backend" / "static" / "js" / "veil.js").read_text(encoding="utf-8")
    return float(re.search(rf"^const {name} = ([0-9.]+);", source, re.M).group(1))


def composited(ground, speck, alpha):
    """What a speck of colour `speck` at `alpha` (0 to 255) over `ground` shows, in 8 bits a channel as a browser blends it."""
    return tuple(round(c * (1 - alpha / 255) + speck * alpha / 255) for c in ground)


def luma(colour):
    return 0.2126 * colour[0] + 0.7152 * colour[1] + 0.0722 * colour[2]


def grain(ground, peak, size=128, seed=7):
    """The still grain over one appearance's ground: pure white or black specks whose alpha moves the ground by the
    shader's film grain (spec 3.1, 3.3): the sum of three flat values, so bell-shaped, zero-mean and within +-peak of
    luma (veil.js GRAIN_PAPER, GRAIN_INK: a fraction of 1, here taken in levels of 255). Each speck takes the alpha
    whose blended result, in whole levels, lies nearest the grain's value, so the tile's spread is the shader's (which
    rounds once, in its 8-bit output) and not that of a value rounded twice."""
    rng = random.Random(seed)
    level = luma(ground)
    specks = []
    for _ in range(size * size):
        delta = (rng.random() + rng.random() + rng.random() - 1.5) / 1.5 * peak * 255
        speck = 255 if delta >= 0 else 0
        guess = round(abs(delta) / (255 - level if delta >= 0 else level) * 255)
        alpha = min(range(max(0, guess - 3), min(255, guess + 3) + 1),
                    key=lambda a: abs(luma(composited(ground, speck, a)) - level - delta))
        specks.append((speck, speck, speck, alpha))
    return lambda x, y: specks[y * size + x]


def main():
    IMG.mkdir(parents=True, exist_ok=True)
    svg_open = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 560 560" shape-rendering="crispEdges">'
    (IMG / "mark.svg").write_text(f'{svg_open}<path fill="#2c2925" d="{MARK_PATH}"/></svg>\n', encoding="utf-8")
    (IMG / "favicon.svg").write_text(
        f"{svg_open}<style>path{{fill:#2c2925}}@media (prefers-color-scheme:dark){{path{{fill:#e6e0d6}}}}</style>"
        f'<path d="{MARK_PATH}"/></svg>\n',
        encoding="utf-8",
    )
    for name, size, padding in (("favicon-16.png", 16, 0), ("favicon-32.png", 32, 0), ("apple-touch-icon.png", 180, 30)):
        (IMG / name).write_bytes(png(size, size, mark_tile(size, padding)))
    (IMG / "og.png").write_bytes(png(1200, 630, mark_card(1200, 630, 280)))
    (IMG / "grain-paper.png").write_bytes(png(128, 128, grain((0xEB, 0xE6, 0xDE), veil_constant("GRAIN_PAPER"))))
    (IMG / "grain-ink.png").write_bytes(png(128, 128, grain((0x1B, 0x19, 0x17), veil_constant("GRAIN_INK"))))
    print(f"Wrote eight images to {IMG}")


if __name__ == "__main__":
    main()
