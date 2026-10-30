"""Tier 1: native text extraction with pypdf (BUILD-SPEC.md §10.4). Local, no network, no model: patient bytes never leave the process.

Pure functions of the bytes; synchronous and CPU-bound, so the pipeline runs `read` in a thread. Nothing is logged and no exception carries page text: a document pypdf cannot
open (corrupt, truncated, password-protected) is the fixed `ExtractionFailed("document_unreadable")`.
"""

import dataclasses
import io
import struct
import zlib

from pypdf import PdfReader
from pypdf.errors import PyPdfError
from pypdf.generic import ContentStream

from careflow.extraction import (
    R_DOCUMENT_UNREADABLE,
    ExtractionFailed,
    NativePage,
    PageImage,
)


def _png(width: int, height: int, channels: int, raw: bytes) -> bytes:
    """A PNG from raw 8-bit pixels, by hand with zlib (pypdf's own image path needs Pillow, a dependency nobody approved)."""

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body))
        )

    rows = b"".join(
        b"\x00" + raw[y * width * channels : (y + 1) * width * channels]
        for y in range(height)
    )
    header = struct.pack(
        ">IIBBBBB", width, height, 8, 2 if channels == 3 else 0, 0, 0, 0
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def _image(obj) -> PageImage | None:
    """JPEG streams are the file already; plain 8-bit Gray/RGB streams become a PNG; anything else (CMYK, indexed, JBIG2, predictors) is None and is counted as unreadable."""
    flt = obj.get("/Filter")
    if isinstance(flt, list) and len(flt) == 1:
        flt = flt[0]
    if flt == "/DCTDecode":
        return PageImage("image/jpeg", obj._data)
    channels = {"/DeviceRGB": 3, "/DeviceGray": 1}.get(str(obj.get("/ColorSpace")))
    if (
        flt in (None, "/FlateDecode")
        and channels
        and obj.get("/BitsPerComponent") == 8
        and "/DecodeParms" not in obj
        and "/SMask" not in obj
    ):
        width, height = int(obj["/Width"]), int(obj["/Height"])
        data = obj.get_data()
        if len(data) == width * height * channels:
            return PageImage("image/png", _png(width, height, channels, data))
    return None


MAX_FORM_DEPTH = 5  # nested forms deeper than this are counted as one unknown image (a technical bound)


def _images_in_form(form, pdf, depth: int = 0) -> int:
    """How many images a form holds: image XObjects (looking inside nested forms) and inline images in its own content stream. Past the depth bound, or if the form cannot be read,
    the answer is 1: unknown is not zero."""
    if depth >= MAX_FORM_DEPTH:
        return 1
    try:
        count = sum(1 for _, operator in ContentStream(form, pdf).operations if operator == b"INLINE IMAGE")
        resources = form.get("/Resources")
        xobjects = resources.get_object().get("/XObject") if resources is not None else None
        if xobjects is None:
            return count
        xobjects = xobjects.get_object()
        for name in xobjects:
            inner = xobjects[name].get_object()
            if inner.get("/Subtype") == "/Image":
                count += 1
            elif inner.get("/Subtype") == "/Form":
                count += _images_in_form(inner, pdf, depth + 1)
        return count
    except (PyPdfError, ValueError, KeyError, TypeError, AttributeError, RecursionError, IndexError):
        return 1


def _coverage(page) -> tuple[dict[str, float], int]:
    """(share of the page area each image XObject is drawn over, by name; count of inline images). Reads the page's content stream: the current transformation matrix at each `Do`
    gives the drawn size (|a*d - b*c| of the unit square). A name that is never drawn directly (it sits inside a form XObject) is absent: its size is unknown and counts as large.
    Any failure to measure returns an empty map AND one unreadable image: unknown is not zero (an inline image we could not parse must never look like no image)."""
    try:
        # The visible page is the CropBox when there is one: measure against the smaller of the two so a cropped scan is never under-counted.
        areas = [abs(float(b.width) * float(b.height)) for b in (page.mediabox, page.cropbox)]
        area = min((a for a in areas if a > 0), default=0.0)
        if area <= 0:
            return {}, 1
        stream = ContentStream(page.get_contents(), page.pdf)
        stack: list[tuple[float, ...]] = []
        ctm: tuple[float, ...] = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        drawn: dict[str, float] = {}
        inline = 0
        for operands, operator in stream.operations:
            if operator == b"q":
                stack.append(ctm)
            elif operator == b"Q" and stack:
                ctm = stack.pop()
            elif operator == b"cm" and len(operands) == 6:
                a, b, c, d, e, f = (float(x) for x in operands)
                m = ctm  # new CTM = this matrix x the current one
                ctm = (
                    a * m[0] + b * m[2], a * m[1] + b * m[3], c * m[0] + d * m[2],
                    c * m[1] + d * m[3], e * m[0] + f * m[2] + m[4], e * m[1] + f * m[3] + m[5],
                )
            elif operator == b"Do" and operands:
                drawn[str(operands[0])] = drawn.get(str(operands[0]), 0.0) + abs(ctm[0] * ctm[3] - ctm[1] * ctm[2]) / area
            elif operator == b"INLINE IMAGE":
                inline += 1
        return drawn, inline
    except (PyPdfError, ValueError, KeyError, TypeError, IndexError, AttributeError, RecursionError):
        return {}, 1


class PypdfExtractor:
    def read(self, content: bytes) -> list[NativePage]:
        try:
            reader = PdfReader(io.BytesIO(content))
            if reader.is_encrypted:  # a password we do not have: a person must open it
                raise ExtractionFailed(R_DOCUMENT_UNREADABLE)
            return [
                self._page(number, page)
                for number, page in enumerate(reader.pages, start=1)
            ]
        except ExtractionFailed:
            raise
        except (
            PyPdfError,
            ValueError,
            KeyError,
            TypeError,
            RecursionError,
            OSError,
            AssertionError,
        ):
            raise ExtractionFailed(R_DOCUMENT_UNREADABLE) from None

    @staticmethod
    def _page(number: int, page) -> NativePage:
        try:
            text = page.extract_text() or ""
        except (PyPdfError, ValueError, KeyError, TypeError, RecursionError):
            text = ""  # a page whose text cannot be decoded is a page with no text layer: the next tier gets it
        images: list[PageImage] = []
        drawn, inline = _coverage(page)
        unreadable = inline  # an inline image cannot be handed to vision: counted, and unknown size counts as large
        try:
            resources = page.get("/Resources")
            xobjects = (
                resources.get_object().get("/XObject")
                if resources is not None
                else None
            )
            xobjects = xobjects.get_object() if xobjects is not None else {}
            for name in xobjects:
                obj = xobjects[name].get_object()
                if obj.get("/Subtype") == "/Form":
                    # An image drawn inside a form XObject (a common scanner and PDF-wrapper shape) is not listed in the page's own resources and its size cannot be measured here:
                    # it is counted as an image that cannot be handed to vision, which holds the page for a person. A form with no image inside is ignored.
                    unreadable += _images_in_form(obj, page.pdf)
                    continue
                if obj.get("/Subtype") != "/Image":
                    continue
                try:
                    image = _image(obj)
                except (
                    PyPdfError,
                    ValueError,
                    KeyError,
                    TypeError,
                    OSError,
                    NotImplementedError,
                ):
                    image = None
                if image is None:
                    unreadable += 1  # counted, never silently dropped
                else:
                    images.append(dataclasses.replace(image, coverage=drawn.get(str(name))))
        except (PyPdfError, ValueError, KeyError, TypeError, AttributeError):
            unreadable += 1
        return NativePage(number, text, tuple(images), unreadable)
