"""A DUMMY synthetic letter as a PNG, drawn in pure Python (no image library, no new dependency), for the opt-in provider live check (scripts/provider_live_check.py).

It exists so the owner never has to find or make an image, and so a REAL document can never be used by mistake: the only text it can draw is a made-up, obviously dummy sentence in a small block font. Not a clinical
document, not a template for one, no patient, no value. The glyph set is deliberately tiny: only the characters the dummy text needs.
"""

import struct
import zlib
from typing import Final

# 5 columns by 7 rows per glyph; "1" is ink.
_GLYPHS: Final[dict[str, tuple[str, ...]]] = {
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "I": ("01110", "00100", "00100", "00100", "00100", "00100", "01110"),
    "N": ("10001", "11001", "10101", "10101", "10011", "10001", "10001"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "11011", "10001"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    " ": ("00000",) * 7,
}
DUMMY_LINES: Final = ("DUMMY TEST LETTER", "ROUTINE REVIEW 123")
EXPECTED_WORDS: Final = ("DUMMY", "TEST", "LETTER", "ROUTINE", "REVIEW")  # what a reader of the image should find: the check counts matches, it never prints the text
SCALE: Final = 6
MARGIN: Final = 4


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def png_bytes(lines: tuple[str, ...] = DUMMY_LINES) -> bytes:
    """A grayscale PNG: black block letters on white. Raises ValueError for a character outside the tiny glyph set (so nothing but the dummy text can be drawn)."""
    for line in lines:
        for ch in line:
            if ch not in _GLYPHS:
                raise ValueError("this block font can only draw the dummy text")
    cell_w, cell_h = 6 * SCALE, 9 * SCALE
    width = max(len(line) for line in lines) * cell_w + 2 * MARGIN * SCALE
    height = len(lines) * cell_h + 2 * MARGIN * SCALE
    rows = [[255] * width for _ in range(height)]
    for li, line in enumerate(lines):
        for ci, ch in enumerate(line):
            for ry, pattern in enumerate(_GLYPHS[ch]):
                for rx, bit in enumerate(pattern):
                    if bit == "1":
                        for dy in range(SCALE):
                            y = MARGIN * SCALE + li * cell_h + ry * SCALE + dy
                            for dx in range(SCALE):
                                rows[y][MARGIN * SCALE + ci * cell_w + rx * SCALE + dx] = 0
    raw = b"".join(b"\x00" + bytes(r) for r in rows)  # filter type 0 per scanline
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)) + _chunk(b"IDAT", zlib.compress(raw, 9)) + _chunk(b"IEND", b"")


def words_found(text: str) -> int:
    """How many of the expected dummy words a reader's output contains (case-insensitive). The count is all the live check ever shows."""
    upper = text.upper()
    return sum(1 for w in EXPECTED_WORDS if w in upper)
