"""Synthetic scanner-style test PDFs for the real-PDF spike preparation (docs/design/real-pdf-spike.md). SYNTHETIC ONLY: every byte is generated here, no real document, no real patient, no download,
no extra dependency (pure Python, like tests/pdf_factory.py). The pixels and the page text are meaningless placeholders.

What it can build, and what it deliberately cannot:
* CMYK images, a large image beside a text layer, a small image (a logo), rotated pages, an image-only page, a page with an unparsable content stream, a JPEG-filtered image (placeholder bytes: the
  native reader hands JPEG streams on as they are and never decodes them), a page with several images, an empty page.
* JBIG2 (`/JBIG2Decode`): there is NO offline encoder here, so the image bytes are PLACEHOLDERS. That exercises how the pipeline ROUTES a page whose image uses a filter it cannot hand to a model
  (to a person, never silently); it proves nothing about decoding real JBIG2. A real scanner's JBIG2 file needs a real sample (the list the user supplies is in the spike document).

Each case carries the route the cascade of BUILD-SPEC 15.3 and docs/design/p2-4-extraction.md should give every page: `text` (the text layer alone), `vision` (the page is read by the vision
role), `text+vision` (the text layer plus a large image beside it, read by vision), or `nurse:<reason>` (a person; the fixed reason word).
"""

import zlib
from dataclasses import dataclass, field

TEXT = "Referral for review of a persistent cough lasting several weeks. Please advise on next steps."
PLACEHOLDER_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"  # SOI .. EOI, not a decodable picture


@dataclass(frozen=True)
class Img:
    """One embedded image. `draw` is its side in points (None = listed in the page resources but never drawn: its size cannot be measured)."""

    colorspace: str = "/DeviceRGB"
    bpc: int = 8
    filter: str = "/FlateDecode"
    data: bytes | None = None
    width: int = 2
    height: int = 2
    draw: float | None = 100

    def body(self) -> bytes:
        if self.data is not None:
            return self.data
        channels = {"/DeviceCMYK": 4, "/DeviceGray": 1}.get(self.colorspace, 3)
        return zlib.compress(bytes(range(self.width * self.height * channels)))


@dataclass(frozen=True)
class Page:
    text: str = ""
    images: tuple[Img, ...] = ()
    rotate: int = 0
    raw_content: bytes | None = None  # replaces the generated content stream (to build an unparsable one)


@dataclass(frozen=True)
class Case:
    name: str
    pages: tuple[Page, ...]
    expected: tuple[str, ...]  # one route per page
    note: str = ""
    pdf: bytes = field(default=b"", repr=False)


def _stream(body: bytes, extra: str = "") -> bytes:
    return f"<< /Length {len(body)} {extra}>>\nstream\n".encode() + body + b"\nendstream"


def build(pages: tuple[Page, ...]) -> bytes:
    objs: dict[int, bytes] = {3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"}
    kids, nxt = [], 4
    for page in pages:
        page_id, content_id = nxt, nxt + 1
        nxt += 2
        ops, xobjects = "", []
        if page.text:
            safe = page.text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops += f"BT /F1 12 Tf 50 700 Td ({safe}) Tj ET\n"
        for n, img in enumerate(page.images, start=1):
            image_id = nxt
            nxt += 1
            objs[image_id] = _stream(
                img.body(), f"/Type /XObject /Subtype /Image /Width {img.width} /Height {img.height} /ColorSpace {img.colorspace} /BitsPerComponent {img.bpc} /Filter {img.filter} "
            )
            xobjects.append(f"/Im{n} {image_id} 0 R")
            if img.draw is not None:
                ops += f"q {img.draw} 0 0 {img.draw} 50 50 cm /Im{n} Do Q\n"
        objs[content_id] = _stream(page.raw_content if page.raw_content is not None else ops.encode())
        xo = f"/XObject << {' '.join(xobjects)} >>" if xobjects else ""
        rot = f"/Rotate {page.rotate}" if page.rotate else ""
        objs[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] {rot} /Contents {content_id} 0 R /Resources << /Font << /F1 3 0 R >> {xo} >> >>".encode()
        )
        kids.append(page_id)
    objs[2] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>".encode()
    objs[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    out, offsets = bytearray(b"%PDF-1.4\n"), {}
    for number in sorted(objs):
        offsets[number] = len(out)
        out += f"{number} 0 obj\n".encode() + objs[number] + b"\nendobj\n"
    xref, size = len(out), max(objs) + 1
    out += f"xref\n0 {size}\n0000000000 65535 f \n".encode()
    for number in range(1, size):
        out += f"{offsets[number]:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


BIG = 400  # points: a 400 x 400 image on a 612 x 792 page covers about a third of it (the rule's threshold is a fifth)
SMALL = 40


def cases() -> list[Case]:
    unparsable = b"BI /W 2 /H 2 /CS /G /BPC 8 ID \x10\x20"  # an inline image that never ends
    specs: list[tuple[str, tuple[Page, ...], tuple[str, ...], str]] = [
        ("text_only", (Page(TEXT), Page(TEXT)), ("text", "text"), "a clean digital letter"),
        ("scan_image_only_rgb", (Page(images=(Img(draw=BIG),)),), ("vision",), "a flatbed scan: no text layer, one big image"),
        ("scan_image_only_gray", (Page(images=(Img("/DeviceGray", draw=BIG),)),), ("vision",), "8-bit grey scan"),
        ("scan_image_only_jpeg", (Page(images=(Img(filter="/DCTDecode", data=PLACEHOLDER_JPEG, draw=BIG),)),), ("vision",), "a JPEG-compressed scan (placeholder bytes)"),
        ("scan_cmyk", (Page(images=(Img("/DeviceCMYK", draw=BIG),)),), ("nurse:image_unreadable",), "CMYK image: the native reader cannot re-wrap it, so a person reads the page"),
        ("scan_jbig2_placeholder", (Page(images=(Img("/DeviceGray", 1, "/JBIG2Decode", b"\x00placeholder-not-jbig2", draw=BIG),)),), ("nurse:image_unreadable",),
         "JBIG2 filter on PLACEHOLDER bytes: routing only, NOT a decoding test; a real sample is needed"),
        ("large_image_beside_text", (Page(TEXT, (Img(draw=BIG),)),), ("text+vision",), "typed header plus a big scanned body: the image is read too (the 20 percent rule)"),
        ("small_logo_beside_text", (Page(TEXT, (Img(draw=SMALL),)),), ("text",), "a logo or stamp: recorded, not sent to vision"),
        ("cmyk_beside_text", (Page(TEXT, (Img("/DeviceCMYK", draw=BIG),)),), ("nurse:image_unreadable",), "text layer plus an image nobody can read: the page is not called read"),
        ("image_not_drawn_directly", (Page(TEXT, (Img(draw=None),)),), ("text+vision",), "an image whose size cannot be measured counts as large"),
        ("rotated_text", (Page(TEXT, rotate=90), Page(TEXT, rotate=270)), ("text", "text"), "rotated pages with a text layer"),
        ("rotated_scan", (Page(images=(Img(draw=BIG),), rotate=90),), ("vision",), "a scan fed sideways (rotation does not change the route)"),
        ("empty_page", (Page(),), ("nurse:no_text_layer",), "no text and no image: a person looks"),
        ("unparsable_content", (Page(TEXT, raw_content=unparsable),), ("nurse:image_unreadable",), "a content stream that cannot be parsed counts as an unreadable image, never as no image"),
        ("mixed_document", (Page(TEXT), Page(images=(Img(draw=BIG),)), Page(images=(Img("/DeviceCMYK", draw=BIG),)), Page()), ("text", "vision", "nurse:image_unreadable", "nurse:no_text_layer"),
         "four pages, four routes; the document is incomplete, so a person reads it all"),
    ]
    return [Case(name, pages, expected, note, build(pages)) for name, pages, expected, note in specs]
