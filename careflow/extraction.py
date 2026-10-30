"""The extraction pipeline: from a stored document to text with page and element provenance (BUILD-SPEC.md §10.4, §15.3, §16.2; CLAUDE.md rules 1, 2, 3, 6, 7). Package P2-4.

TIERS, cheapest first, and a page moves to the next tier ONLY if the earlier one failed it (§15.3: "billed per page, only for pages that actually failed the earlier tiers"):
  1. NATIVE: pypdf reads the text layer (the `NativeExtractor` port; adapter careflow/adapters/pdf.py). Milliseconds, local, no model.
  2. LAYOUT: tables and columns (the `LayoutPort`). Unstructured was the plan; it cannot run in its light "fast" mode without torch and friends (docs/design/p2-4-extraction.md),
     so no adapter is wired: the slot exists, is tested with a fake, and a page simply moves on when there is none.
  3. VISION: a page with no usable text layer is read from its embedded image through the `VisionPort` (the `vlm` role; the real adapter goes through the gateway, so patient data
     reaches a model only on a phi_approved route). Images on a page whose text layer WAS read are not sent (cost) but are COUNTED and recorded (`skipped_images`), never silent.
  4. MANUAL: a page that no tier could read, a document that cannot be opened, or a vision route that is not approved: the result says so (`complete == False` with fixed-word reasons)
     and the Front Door turns that into a nurse's review task showing the document (the existing hold path): never silent (§16.2).

UNKNOWN IS NOT CLEAR. A page nobody could read has not been screened by anything, so the pipeline never returns partial text as if it were the whole letter: `PipelineTextSource` raises
`ExtractionNeedsManual` unless EVERY page was read. The red-flag rules then run on complete text only.

PROVENANCE. Every piece of text is an `Element`: page number (1-based, the physical page), index on the page, the tier that read it, and its character span in the merged text, so a
citation (a span in the merged text) points back to a physical page (`ExtractionResult.locate`).

HEAVY WORK RUNS IN THE WORKER, NEVER IN THE REQUEST PATH: `ExtractionPipeline.run` calls `require_worker()`, which raises unless the caller set `worker_context()` (the Front Door job
handler does). The review route resumes only the nodes after the paused step and never reads a document, and this guard keeps it that way.

Nothing here logs. Page bytes, text and image bytes are patient data (`repr=False` everywhere); reasons are fixed words from a closed vocabulary.
"""

import asyncio
import dataclasses
import re
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Generator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Final, Literal, Protocol

from careflow.budget import BudgetExceeded
from careflow.documents import DocumentHeld
from careflow.gateway import AllRoutesFailed, NoApprovedRoute, SpendCeilingReached
from careflow.ports import (
    LayoutPort,
    PermanentError,
    TransientError,
    VisionPort,
    VisionRequest,
)

# Technical sanity bounds for "this page has usable text", NOT clinical values: a text layer of under 20 non-space characters, or one that is mostly symbols
# (a broken font encoding), counts as no text layer. Tuning constants; changing them needs no clinician.
MIN_PAGE_CHARS: Final = 20
MIN_ALNUM_RATIO: Final = 0.5
MEMO_DOCUMENTS: Final = 8  # documents whose text the in-process memo keeps; the oldest is dropped (technical bound)
# PENDING OWNER CONFIRMATION (a technical setting, not a clinical value): an image covering at least this share of the page area is "large". A page whose text layer was read AND
# that holds a large image is not complete without the image (a scanned referral with a typed header), so the image goes to vision or the page goes to a nurse. Smaller
# images (logos, stamps) are ignored. An image whose size cannot be determined counts as large: unknown is not a value.
IMAGE_COVERAGE_THRESHOLD: Final = 0.20
MAX_PAGES: Final = 200  # a technical bound on one document's work in one job

# The closed vocabulary of reasons this module can produce (a subset of careflow.referral.FIXED_REASONS, which a test pins).
R_DOCUMENT_UNREADABLE: Final = "document_unreadable"
R_NO_TEXT_LAYER: Final = "no_text_layer"
R_PAGES_NEED_VISION: Final = "pages_need_vision"
R_VISION_UNAVAILABLE: Final = "vision_unavailable"
R_IMAGE_UNREADABLE: Final = "image_unreadable"
R_EXTRACTION_FAILED: Final = "extraction_failed"
REASONS: Final = (
    R_DOCUMENT_UNREADABLE,
    R_NO_TEXT_LAYER,
    R_PAGES_NEED_VISION,
    R_VISION_UNAVAILABLE,
    R_IMAGE_UNREADABLE,
    R_EXTRACTION_FAILED,
)

IMAGE_TYPES: Final = ("image/jpeg", "image/png")
PDF_TYPE: Final = "application/pdf"

Tier = Literal["native", "layout", "vision"]


class ExtractionError(Exception):
    """Fixed words only; never page text."""


class NotInWorker(ExtractionError):
    """Extraction was asked for outside the worker (the request path)."""


class ExtractionFailed(ExtractionError):
    """The native reader could not open the document (corrupt, truncated, encrypted). `reason` is one of REASONS."""

    def __init__(self, reason: str = R_DOCUMENT_UNREADABLE) -> None:
        super().__init__(reason)
        self.reason = reason


class ExtractionNeedsManual(ExtractionError):
    """At least one page could not be read by any tier: the whole referral goes to a person, who is shown the document. `reasons` are fixed words; `pages` are page numbers."""

    def __init__(self, reasons: Sequence[str], pages: Sequence[int] = ()) -> None:
        super().__init__("extraction needs a person")
        self.reasons, self.pages = tuple(dict.fromkeys(reasons)), tuple(pages)


# ---- the worker-only guard -------------------------------------------------------------------------------------------------------------------

_IN_WORKER: ContextVar[bool] = ContextVar("careflow_in_worker", default=False)


@contextmanager
def worker_context() -> Generator[None]:
    token = _IN_WORKER.set(True)
    try:
        yield
    finally:
        _IN_WORKER.reset(token)


def require_worker() -> None:
    if not _IN_WORKER.get():
        raise NotInWorker(
            "document extraction runs in the worker, never in the request path"
        )


# ---- what the native reader returns ----------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PageImage:
    content_type: str  # image/png or image/jpeg
    data: bytes = field(repr=False)
    coverage: float | None = None  # share of the page area the image is drawn over; None = could not be measured (counts as large)

    @property
    def large(self) -> bool:
        return self.coverage is None or self.coverage >= IMAGE_COVERAGE_THRESHOLD


@dataclass(frozen=True)
class NativePage:
    number: int  # 1-based physical page
    text: str = field(repr=False)
    images: tuple[PageImage, ...] = ()
    unreadable_images: int = 0  # embedded images in a form this reader cannot hand to a vision model (an unsupported filter)


class NativeExtractor(Protocol):
    def read(self, content: bytes) -> list[NativePage]:
        """Tier 1. Synchronous and CPU-bound (the pipeline runs it in a thread). Raises ExtractionFailed if the document cannot be opened."""
        ...


# ---- the result ---------------------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Element:
    page: int
    index: int  # position on the page, from 0
    tier: Tier
    start: int  # character span in ExtractionResult.text
    end: int
    kind: Literal["text", "image_text"] = "text"


@dataclass(frozen=True)
class PageOutcome:
    page: int
    tier: Tier | None  # None: nobody read it
    reason: str | None = None  # why the page needs a person, when tier is None
    skipped_images: int = 0  # small images (logos, stamps) on a page whose text layer was read: not sent to vision, but recorded
    vision_images: int = 0  # large images on a page whose text layer was read, transcribed by vision and merged after the page's own text


@dataclass(frozen=True)
class ExtractionResult:
    text: str = field(repr=False)
    elements: tuple[Element, ...]
    pages: tuple[PageOutcome, ...]
    reasons: tuple[str, ...] = ()
    vision_calls: int = 0

    @property
    def manual_pages(self) -> tuple[int, ...]:
        return tuple(p.page for p in self.pages if p.tier is None)

    @property
    def complete(self) -> bool:
        """True only if the document opened, it has at least one page, and EVERY page was read by some tier."""
        return (
            bool(self.pages)
            and not self.manual_pages
            and R_DOCUMENT_UNREADABLE not in self.reasons
        )

    def locate(self, offset: int) -> Element | None:
        """The element whose span holds this character offset of `text`: a citation's way back to a physical page."""
        return next((e for e in self.elements if e.start <= offset < e.end), None)


# ---- the pipeline ----------------------------------------------------------------------------------------------------------------------------


def useful(text: str) -> bool:
    """Does this page have a usable text layer? A technical test: enough characters, mostly letters and digits."""
    compact = re.sub(r"\s", "", text or "")
    if len(compact) < MIN_PAGE_CHARS:
        return False
    return sum(c.isalnum() for c in compact) / len(compact) >= MIN_ALNUM_RATIO


def _blocks(text: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return parts or ([text.strip()] if text.strip() else [])


# A spend ceiling is a visible manual outcome like any other vision failure: the safety path is never budget-limited (15.2), and a re-raise would leave the letter
# unscreened and unheld until the job dead-letters.
_VISION_FAILURES = (NoApprovedRoute, AllRoutesFailed, SpendCeilingReached, BudgetExceeded, TransientError, PermanentError)


class ExtractionPipeline:
    def __init__(
        self,
        native: NativeExtractor,
        *,
        layout: LayoutPort | None = None,
        vision: VisionPort | None = None,
    ) -> None:
        self._native, self._layout, self._vision = native, layout, vision

    async def run(
        self, content: bytes, content_type: str, *, clinic_id: uuid.UUID
    ) -> ExtractionResult:
        require_worker()
        if (
            content_type in IMAGE_TYPES
        ):  # an uploaded photo: one page with no text layer and one image
            pages = [NativePage(1, "", (PageImage(content_type, content),))]
        elif content_type == PDF_TYPE:
            try:
                pages = await asyncio.to_thread(self._native.read, content)
            except ExtractionFailed as exc:
                return ExtractionResult("", (), (), (exc.reason,))
        else:
            return ExtractionResult("", (), (), (R_DOCUMENT_UNREADABLE,))
        if not pages or len(pages) > MAX_PAGES:
            return ExtractionResult("", (), (), (R_DOCUMENT_UNREADABLE,))

        texts: list[tuple[NativePage, Tier | None, str, str | None, int, str]] = []
        calls = 0
        for page in pages:
            if useful(page.text):
                large = tuple(i for i in page.images if i.large)
                if not large and not page.unreadable_images:
                    # cheap tier succeeded and nothing large sits beside the text: no further tier, no vision. Small images are recorded, never silent.
                    texts.append((page, "native", page.text, None, len(page.images), ""))
                    continue
                # A usable text layer is not the whole page when a large image sits beside it. An image that cannot be handed to vision counts as large too.
                beside = dataclasses.replace(page, images=large)
                extra, reason, made = await self._vision_text(beside, clinic_id)
                calls += made
                if extra:
                    texts.append((page, "native", page.text, None, len(page.images) - len(large), extra))
                else:
                    texts.append((page, None, "", reason, 0, ""))
                continue
            if self._layout is not None and not page.unreadable_images:  # an image nobody can read is not a page a layout tier may call done
                try:
                    laid_out = await self._layout.read_page(content, page.number)
                except (TransientError, PermanentError):
                    laid_out = None
                if laid_out and useful(laid_out):
                    texts.append((page, "layout", laid_out, None, 0, ""))
                    continue
            text, reason, made = await self._vision_text(page, clinic_id)
            calls += made
            texts.append(
                (page, "vision" if text else None, text, None if text else reason, 0, "")
            )

        return self._merge(texts, calls)

    async def _vision_text(
        self, page: NativePage, clinic_id: uuid.UUID
    ) -> tuple[str, str, int]:
        """(text, reason when empty, vision calls made). Every image of the page is read; if ANY cannot be, the page is not read (a half-read page is not a page)."""
        if not page.images and not page.unreadable_images:
            return "", R_NO_TEXT_LAYER, 0
        if page.unreadable_images:
            return "", R_IMAGE_UNREADABLE, 0
        if self._vision is None:
            return "", R_VISION_UNAVAILABLE, 0
        parts, made = [], 0
        for image in page.images:
            try:
                made += 1
                result = await self._vision.read(
                    VisionRequest(
                        image.data, image.content_type, page.number, clinic_id
                    )
                )
            except _VISION_FAILURES:
                return (
                    "",
                    R_VISION_UNAVAILABLE,
                    made,
                )  # no approved route, an outage, a spend ceiling: the next tier is a person
            if not useful(result.text):
                return (
                    "",
                    R_EXTRACTION_FAILED,
                    made,
                )  # the model answered with nothing readable
            parts.append(result.text.strip())
        return "\n\n".join(parts), "", made

    @staticmethod
    def _merge(
        items: list[tuple[NativePage, Tier | None, str, str | None, int, str]],
        calls: int,
    ) -> ExtractionResult:
        """Document order, page by page, element by element, with spans into the merged text. A page's own text comes first, then the transcription of its large images."""
        out: list[str] = []
        elements: list[Element] = []
        outcomes: list[PageOutcome] = []
        reasons: list[str] = []
        cursor = 0
        for page, tier, text, reason, skipped, extra in sorted(
            items, key=lambda it: it[0].number
        ):
            outcomes.append(
                PageOutcome(
                    page.number, tier, reason, skipped, len(page.images) - skipped if extra else 0
                )
            )
            if tier is None:
                reasons.append(reason or R_EXTRACTION_FAILED)
                continue
            if out:
                out.append("\n\n")
                cursor += 2
            index = 0
            for segment_tier, segment in ((tier, text), ("vision", extra)):
                for block in _blocks(segment):
                    if index:
                        out.append("\n")
                        cursor += 1
                    out.append(block)
                    elements.append(
                        Element(
                            page.number,
                            index,
                            segment_tier,  # type: ignore[arg-type]
                            cursor,
                            cursor + len(block),
                            "image_text" if segment_tier == "vision" else "text",
                        )
                    )
                    cursor += len(block)
                    index += 1
        if (
            any(o.tier is None for o in outcomes)
            and R_PAGES_NEED_VISION not in reasons
            and any(r in (R_VISION_UNAVAILABLE, R_IMAGE_UNREADABLE) for r in reasons)
        ):
            reasons.append(R_PAGES_NEED_VISION)
        return ExtractionResult(
            "".join(out),
            tuple(elements),
            tuple(outcomes),
            tuple(dict.fromkeys(reasons)),
            calls,
        )


# ---- the text source the Front Door reads -----------------------------------------------------------------------------------------------------

Loader = Callable[
    [uuid.UUID], Awaitable[tuple[bytes, str, uuid.UUID]]
]  # document id -> (verified bytes, content type, clinic id)


class PipelineTextSource:
    """`front_door.TextSource` over the pipeline. The loader returns the stored bytes AFTER the re-hash (`DocumentIntake.read_verified`). Results are memoised in this process only
    (a node asks for the text three times per run, and vision is billed): nothing is persisted, so a crash-resume reads again. A persisted, encrypted cache is a later, reviewed schema."""

    def __init__(self, pipeline: ExtractionPipeline, loader: Loader) -> None:
        self._pipeline, self._loader = pipeline, loader
        self._memo: OrderedDict[uuid.UUID, ExtractionResult] = OrderedDict()  # bounded: patient text is not kept for the life of the worker
        self._locks: dict[uuid.UUID, asyncio.Lock] = {}

    async def result_for(self, document_id: uuid.UUID) -> ExtractionResult:
        lock = self._locks.setdefault(document_id, asyncio.Lock())
        async with lock:
            if document_id not in self._memo:
                try:
                    content, content_type, clinic_id = await self._loader(document_id)
                except DocumentHeld as held:  # not scanned, or flagged: nothing is read; a person is shown the document (the existing manual outcome)
                    raise ExtractionNeedsManual((held.reason,)) from None
                self._memo[document_id] = await self._pipeline.run(
                    content, content_type, clinic_id=clinic_id
                )
                while len(self._memo) > MEMO_DOCUMENTS:
                    evicted, _ = self._memo.popitem(last=False)
                    self._locks.pop(evicted, None)
            self._memo.move_to_end(document_id)
            return self._memo[document_id]

    async def text_for(self, document_id: uuid.UUID) -> str:
        result = await self.result_for(document_id)
        if not result.complete:
            raise ExtractionNeedsManual(
                result.reasons or (R_EXTRACTION_FAILED,), result.manual_pages
            )
        return result.text
