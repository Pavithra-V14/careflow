"""The dummy letter for the provider live check (careflow/synthetic_letter.py): pure Python, deterministic, can draw nothing but the dummy text, and the live check script never prints content. Offline."""

import struct
import zlib
from pathlib import Path

import pytest

from careflow import synthetic_letter

ROOT = Path(__file__).resolve().parent.parent


def test_the_png_is_a_valid_grayscale_image_with_ink_and_is_deterministic():
    data = synthetic_letter.png_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and data == synthetic_letter.png_bytes()
    pos, chunks = 8, {}
    while pos < len(data):
        (n,) = struct.unpack(">I", data[pos : pos + 4])
        kind, body = data[pos + 4 : pos + 8], data[pos + 8 : pos + 8 + n]
        (crc,) = struct.unpack(">I", data[pos + 8 + n : pos + 12 + n])
        assert crc == zlib.crc32(kind + body) & 0xFFFFFFFF
        chunks[kind] = body
        pos += 12 + n
    width, height, depth, color = struct.unpack(">IIBB", chunks[b"IHDR"][:10])
    assert (depth, color) == (8, 0) and width > 100 and height > 50
    raw = zlib.decompress(chunks[b"IDAT"])
    assert len(raw) == height * (width + 1) and 0 in raw and 255 in raw  # black ink on white


def test_only_the_dummy_text_can_be_drawn():
    with pytest.raises(ValueError):
        synthetic_letter.png_bytes(("a real name",))  # lowercase is outside the small glyph set: nothing real can be drawn by accident


def test_the_word_counter_returns_a_number_never_the_text():
    assert synthetic_letter.words_found("dummy test letter routine review") == 5
    assert synthetic_letter.words_found("nothing here") == 0


def test_the_live_check_script_prints_status_timing_and_cost_but_no_content_and_no_key():
    source = (ROOT / "scripts" / "provider_live_check.py").read_text(encoding="utf-8")
    assert "refusing to run under CI" in source and "--yes-synthetic" in source and '"all"' in source
    scrubbed = source.replace("len(out.value.text)", "").replace("words_found(out.value.text)", "")
    assert "out.value.text" not in scrubbed.replace("out.value.text.strip()", "")  # the reply text is only ever counted or tested for emptiness, never printed
    assert "print(key" not in source and "{key}" not in source  # a key is never interpolated into output
