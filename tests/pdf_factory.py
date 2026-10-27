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


def pdf(pages: list[tuple[str, bool | float | str]], *, cropbox: str | None = None) -> bytes:
    """pages: (text, image) per page. Empty text means no text layer. image: False (none), True (a 100-point square), a number (that many points square), or "unmeasured"
    (listed in the page resources but never drawn directly: its size cannot be determined), or "in_form" (an image drawn inside a form XObject that the page draws).
    cropbox: e.g. "0 0 306 396", applied to every page."""
    objs: dict[int, bytes] = {}
    font = 3
    objs[font] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    kids, nxt = [], 4
    for text, image in pages:
        page_id, content_id = nxt, nxt + 1
        nxt += 2
        ops = ""
        if text:
            safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops += f"BT /F1 12 Tf 50 700 Td ({safe}) Tj ET\n"
        xobj = ""
        if image:
            image_id = nxt
            nxt += 1
            objs[image_id] = _stream(
                zlib.compress(bytes(range(12))),
                "/Type /XObject /Subtype /Image /Width 2 /Height 2 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode ",
            )
            side = 100 if image is True else image
            if image in ("in_form", "inline_in_form"):
                form_id = nxt
                nxt += 1
                if image == "in_form":
                    form_ops, form_res = b"q 400 0 0 400 0 0 cm /Im1 Do Q\n", f"/XObject << /Im1 {image_id} 0 R >>"
                else:  # an inline image (BI ... ID <bytes> EI) in the form's own content stream, listed nowhere
                    form_ops, form_res = b"q 400 0 0 400 0 0 cm BI /W 2 /H 2 /CS /G /BPC 8 ID \x10\x20\x30\x40 EI Q\n", ""
                objs[form_id] = _stream(form_ops, f"/Type /XObject /Subtype /Form /BBox [0 0 612 792] /Resources << {form_res} >> ")
                ops += "/Fm1 Do\n"
                xobj = f"/XObject << /Fm1 {form_id} 0 R >>"  # the page lists only the form, never the image
            else:
                if image != "unmeasured":
                    ops += f"q {side} 0 0 {side} 50 50 cm /Im1 Do Q\n"
                xobj = f"/XObject << /Im1 {image_id} 0 R >>"
        objs[content_id] = _stream(ops.encode())
        crop = f"/CropBox [{cropbox}]" if cropbox else ""
        objs[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] {crop} /Contents {content_id} 0 R /Resources << /Font << /F1 {font} 0 R >> {xobj} >> >>".encode()
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
