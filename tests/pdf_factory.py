"""Tiny synthetic PDFs, built byte by byte inside the test (no real patient data, no downloaded files, no extra dependency).

A page is text (drawn with the standard Helvetica font, so it has a real text layer), an embedded image (a 2x2 DCT-less raw RGB image stream; the bytes are fake pixels), both, or
nothing. Used only by tests.
"""

import zlib

TEXT_LONG = "Referral for review of a persistent cough lasting several weeks. Please advise on next steps."


def _stream(body: bytes, extra: str = "") -> bytes:
    return (
        f"<< /Length {len(body)} {extra}>>\nstream\n".encode() + body + b"\nendstream"
    )


def pdf(pages: list[tuple[str, bool]]) -> bytes:
    """pages: (text, has_image) per page. Empty text means no text layer."""
    objs: dict[int, bytes] = {}
    font = 3
    objs[font] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    kids, nxt = [], 4
    for text, has_image in pages:
        page_id, content_id = nxt, nxt + 1
        nxt += 2
        ops = ""
        if text:
            safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops += f"BT /F1 12 Tf 50 700 Td ({safe}) Tj ET\n"
        xobj = ""
        if has_image:
            image_id = nxt
            nxt += 1
            objs[image_id] = _stream(
                zlib.compress(bytes(range(12))),
                "/Type /XObject /Subtype /Image /Width 2 /Height 2 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode ",
            )
            ops += "q 100 0 0 100 50 500 cm /Im1 Do Q\n"
            xobj = f"/XObject << /Im1 {image_id} 0 R >>"
        objs[content_id] = _stream(ops.encode())
        objs[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_id} 0 R /Resources << /Font << /F1 {font} 0 R >> {xobj} >> >>".encode()
        )
        kids.append(page_id)
    objs[2] = (
        f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>".encode()
    )
    objs[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    out, offsets = bytearray(b"%PDF-1.4\n"), {}
    for number in sorted(objs):
        offsets[number] = len(out)
        out += f"{number} 0 obj\n".encode() + objs[number] + b"\nendobj\n"
    xref = len(out)
    size = max(objs) + 1
    out += f"xref\n0 {size}\n0000000000 65535 f \n".encode()
    for number in range(1, size):
        out += f"{offsets[number]:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(out)
