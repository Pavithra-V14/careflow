"""A DUMMY synthetic letter as a PNG (clean or scan-style) or a small PDF, drawn in pure Python (no image library, no new dependency), for the opt-in provider live check (scripts/provider_live_check.py)
and the real-model demo run (scripts/demo_real_models.py).

It exists so the owner never has to find or make an image, and so a REAL document can never be used by mistake: the only text it can draw is a made-up, obviously dummy sentence in a small block font. Not a clinical
document, not a template for one, no patient, no value. The glyph set is deliberately small: capital letters, digits and `: , . -`, enough for a made-up referral letter whose "patient" is an INVENTED token
name ("DEMO KILO SAMPLE") with an invented date of birth; never a real name, number or clinical statement. The scan-style page adds a grey paper tone, speckle noise and a dark edge from a fixed seed (same bytes every run).
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
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01110", "10001", "10000", "10000", "10000", "10001", "01110"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01110", "10001", "10000", "10111", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "J": ("00111", "00010", "00010", "00010", "00010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "01100"),
    ":": ("00000", "00100", "00100", "00000", "00100", "00100", "00000"),
    ",": ("00000", "00000", "00000", "00000", "00110", "00100", "01000"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    ".": ("00000", "00000", "00000", "00000", "00000", "01100", "01100"),
    " ": ("00000",) * 7,
}
DUMMY_LINES: Final = ("DUMMY TEST LETTER", "ROUTINE REVIEW 123")
EXPECTED_WORDS: Final = ("DUMMY", "TEST", "LETTER", "ROUTINE", "REVIEW")  # what a reader of the image should find: the check counts matches, it never prints the text
SCALE: Final = 6
MARGIN: Final = 4


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def _check(lines: tuple[str, ...]) -> None:
    for line in lines:
        for ch in line:
            if ch not in _GLYPHS:
                raise ValueError("this block font can only draw the dummy text")


def _bitmap(lines: tuple[str, ...], scale: int) -> list[list[int]]:
    """Rows of pixels: 0 ink on 255 paper."""
    cell_w, cell_h = 6 * scale, 9 * scale
    width = max(len(line) for line in lines) * cell_w + 2 * MARGIN * scale
    height = len(lines) * cell_h + 2 * MARGIN * scale
    rows = [[255] * width for _ in range(height)]
    for li, line in enumerate(lines):
        for ci, ch in enumerate(line):
            for ry, pattern in enumerate(_GLYPHS[ch]):
                for rx, bit in enumerate(pattern):
                    if bit == "1":
                        for dy in range(scale):
                            y = MARGIN * scale + li * cell_h + ry * scale + dy
                            for dx in range(scale):
                                rows[y][MARGIN * scale + ci * cell_w + rx * scale + dx] = 0
    return rows


def _png(rows: list[list[int]]) -> bytes:
    height, width = len(rows), len(rows[0])
    raw = b"".join(b"\x00" + bytes(r) for r in rows)  # filter type 0 per scanline
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)) + _chunk(b"IDAT", zlib.compress(raw, 9)) + _chunk(b"IEND", b"")


def png_bytes(lines: tuple[str, ...] = DUMMY_LINES, *, scale: int = SCALE) -> bytes:
    """A grayscale PNG: black block letters on white. Raises ValueError for a character outside the tiny glyph set (so nothing but the dummy text can be drawn)."""
    _check(lines)
    return _png(_bitmap(lines, scale))


def scan_png_bytes(lines: tuple[str, ...] = DUMMY_LINES, *, scale: int = SCALE) -> bytes:
    """The same page made to LOOK scanned, but SMALL: grey paper that darkens down the page, a faint shade per scan LINE (one value per pixel row, which compresses to almost nothing), a few dust marks and a dark edge. A fixed
    seed, so the same bytes every time; the text stays readable. (The first version put noise on every pixel: 113,677 bytes for a 1304 by 140 page, about 150 times the live check's page, and a live run of the
    hosted vision model timed out twice on it. Per-pixel noise is what made the PNG huge and hard to read; `MAX_SCAN_BYTES` is asserted by a test.)"""
    _check(lines)
    rows = _bitmap(lines, scale)
    state = 12345
    height, width = len(rows), len(rows[0])
    for y, row in enumerate(rows):
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        line_shade = (state >> 16) % 5 - 2  # -2 .. 2, the same for the whole scan line
        paper = 238 - (18 * y) // max(height, 1) + line_shade  # a gentle shade down the page
        for x, value in enumerate(row):
            pixel = 40 if value == 0 else paper
            if x < 3 or x >= width - 3 or y < 3 or y >= height - 3:
                pixel = 60  # the scanner's dark edge
            row[x] = pixel
    for k in range(12):  # a few dust specks, in fixed places
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        y, x = 4 + (state >> 8) % max(height - 8, 1), 4 + (state >> 16) % max(width - 8, 1)
        if rows[y][x] > 200:
            rows[y][x] = 170
    return _png(rows)


def pdf_bytes(lines: tuple[str, ...] = DUMMY_LINES) -> bytes:
    """A one-page PDF whose page has a real TEXT LAYER (Helvetica), so the application reads it locally with no model. Built by hand: five objects and a cross-reference table."""
    _check(lines)
    stream = "BT /F1 12 Tf 18 TL 50 750 Td " + " T* ".join(f"({line}) Tj" for line in lines) + " ET"  # the font set has no brackets or backslashes, so nothing needs escaping
    bodies = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(bodies, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode("ascii")
    xref_at = len(out)
    out += f"xref\n0 {len(bodies) + 1}\n0000000000 65535 f \n".encode("ascii")
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode("ascii")
    return out + f"trailer\n<< /Size {len(bodies) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode("ascii")


# ---- the dummy REFERRAL letter the real-model demo reads ----------------------------------------------------------------------------------------

LETTER_NAME: Final = "DEMO KILO SAMPLE"  # an invented token name, in the same "Demo ... Sample" style as the seeded fictional patients
LETTER_DOB: Final = "1990-01-01"
LETTER_CLEAN: Final = ("DUMMY REFERRAL LETTER", f"PATIENT: {LETTER_NAME}, DOB {LETTER_DOB}", "REF ZQ-4821. PLEASE SEE THIS PATIENT. ROUTINE REVIEW.")
LETTER_SCALE: Final = 4
MAX_SCAN_BYTES: Final = 30_000  # the scan-style page must stay small: a test asserts it


def letter_lines(*, flagged: bool = False) -> tuple[str, ...]:
    """The dummy letter's lines. With `flagged`, one more line carries the seeded DEMO rule's invented token (ZZDEMOALPHA, scripts/seed_dev_demo_data.py), so the deterministic rules fire on it: it shows the rules running
    first and no model being asked. The tokens mean nothing; they are not medical words."""
    return (*LETTER_CLEAN, "NOTE ZZDEMOALPHA") if flagged else LETTER_CLEAN


def letter_scan(*, flagged: bool = False) -> tuple[bytes, str]:
    """(PNG bytes of the scan-style page, content type)."""
    return scan_png_bytes(letter_lines(flagged=flagged), scale=LETTER_SCALE), "image/png"


def letter_pdf(*, flagged: bool = False) -> tuple[bytes, str]:
    return pdf_bytes(letter_lines(flagged=flagged)), "application/pdf"


def words_found(text: str) -> int:
    """How many of the expected dummy words a reader's output contains (case-insensitive). The count is all the live check ever shows."""
    upper = text.upper()
    return sum(1 for w in EXPECTED_WORDS if w in upper)
