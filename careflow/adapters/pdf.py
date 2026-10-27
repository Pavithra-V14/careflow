"""Tier 1: native text extraction with pypdf (BUILD-SPEC.md §10.4). Local, no network, no model: patient bytes never leave the process.

Pure functions of the bytes; synchronous and CPU-bound, so the pipeline runs `read` in a thread. Nothing is logged and no exception carries page text: a document pypdf cannot
open (corrupt, truncated, password-protected) is the fixed `ExtractionFailed("document_unreadable")`.
"""

import io
import struct
import zlib

from pypdf import PdfReader
from pypdf.errors import PyPdfError

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
        unreadable = 0
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
                    images.append(image)
        except (PyPdfError, ValueError, KeyError, TypeError, AttributeError):
            unreadable += 1
        return NativePage(number, text, tuple(images), unreadable)
