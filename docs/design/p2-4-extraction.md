# P2-4 — the extraction worker (BUILD-SPEC §10.4, §15.3, §16.2)

Code: `careflow/extraction.py` (pipeline, result types, worker-only guard, `PipelineTextSource`), `careflow/adapters/pdf.py` (tier 1), `careflow/adapters/vision.py` (the `vlm` role through the gateway), `FakeVision`/`FakeLayout` in `careflow/adapters/fake.py`, `tests/pdf_factory.py` (synthetic PDFs built byte by byte in the test), `tests/test_extraction.py`. The Front Door reads letters through it (`careflow/front_door.py`).

## Tiers, and when a page moves on

| Tier | What | Approved? | Cost |
|---|---|---|---|
| 1 native | pypdf reads the text layer; images are pulled out of the page resources | yes (pure Python, no network; **no Pillow**: image streams are read from the PDF directly, JPEG as is, plain 8-bit Gray/RGB re-wrapped as PNG by 15 lines of zlib) | none |
| 2 layout | the `LayoutPort` slot (tables, columns) | **no adapter** (see the Unstructured finding) | none |
| 3 vision | the `vlm` role through the gateway, PHI data class | **no real route** (Gemini not approved): fake only | billed per page |
| 4 manual | `complete == False` with fixed-word reasons -> `extraction_unavailable` -> nurse hold task showing the document | n/a | a person |

**Cascade (15.3).** A page goes to the next tier only if the earlier one failed it: a page with a usable text layer (>= 20 non-space characters, mostly letters/digits; technical constants, not clinical) never reaches vision, even when it holds an embedded image. Such images are counted (`PageOutcome.skipped_images`), never silent. A test proves vision is called for exactly the failed page and once per document across the graph's three reads.

**Unknown is not clear.** The pipeline never hands back partial text as if it were the letter. `PipelineTextSource.text_for` raises `ExtractionNeedsManual` unless EVERY page was read; the Front Door turns that into the existing `extraction_unavailable` status (nurse task, fixed-word reasons) **before the screen, the extractor or the retriever run**. Reasons: `document_unreadable`, `no_text_layer`, `pages_need_vision`, `vision_unavailable`, `image_unreadable`, `extraction_failed` (all in the closed hold vocabulary).

**Provenance.** Every piece of text is an `Element` (physical page, index on the page, tier, character span in the merged text). `ExtractionResult.locate(offset)` maps a citation span back to its page. Merge is in document order.

**Worker only.** `ExtractionPipeline.run` calls `require_worker()` (a contextvar the `front_door_run` handler sets); the request path (`/run` enqueues, `/review` resumes after the pause and reads no document) can never trigger heavy work. Tests: refusal outside the worker, no leak into other tasks, the handler sets and clears it.

**Clinical profile, no approved vision route.** `GatewayVision` always sends `DataClass.PHI`, so only a `phi_approved` registry row can see a page; with none, `NoApprovedRoute` -> the page is a visible manual outcome. A spend stop (`BudgetExceeded`) is NOT swallowed into a manual task: it stops the run, as everywhere else.

**Data-flow register.** No new external call exists: pypdf is local; vision goes only through the gateway and only to a `phi_approved` row (none exists). No register entry is needed; the one that will be needed for a real vision provider is named in section "Still open".

## The Unstructured finding (stop-and-report, nothing installed)

Owner's rule: Unstructured only in its light "fast" mode, no OCR, no layout models, no torch/detectron/tesseract/poppler; if it cannot work without them, stop and report.

Finding (PyPI metadata and the package's module imports, 2026-10-26; **the install itself was not performed**, so installed size on disk and runtime RAM were **not measured**; the figures below are from published wheel sizes):

- Base `unstructured`: ~104 MB of wheels across ~72 packages (incl. NLTK, numpy, lxml, requests, ...). For PDFs it needs the `[pdf]` extra.
- `unstructured[pdf]`: ~422 MB of wheels across ~125 packages, including **torch (~124 MB)**, onnx runtime, pdfminer.six, pdf2image, pi_heif, Pillow, unstructured-inference. Its PDF partitioner imports pdfminer, `pi_heif` and PIL at module level, so "fast" mode cannot be loaded without the `[pdf]` extra and its heavy dependencies; `strategy="fast"` only skips running the layout model after import.
- First-run network downloads: NLTK data downloads at first use and the layout model weights (hi_res only) are fetched from a hosted model hub; each would need a data-flow-register entry and register test.

Conclusion: the light mode requested **cannot exist** without the heavy extras. Not installed. Tier 2 is an empty port with a fake. Options (owner decision): leave tier 2 empty; or add `pdfminer.six` alone (pure Python, no torch) as the layout tier, which is what Unstructured's fast PDF path wraps anyway.

## Still open (owner)

1. Vision transcription is a model call that must happen BEFORE the deterministic red-flag screen can read an image-only page (rules cannot read pixels). Mitigations in place: vision only on a `phi_approved` route, its output is screened before any extraction or decision model, a failure is a nurse task. Rule 2's wording ("before any model call, on every path") conflicts for this one input; confirm the design.
2. A persisted, encrypted extraction cache (a crash-resume currently re-reads; vision would be billed again) needs a schema and a privacy review; for now an in-process memo only.
3. A real vision provider (Gemini) needs approval, a key, a registry row, a data-flow-register entry and a register test.
4. Embedded images on pages whose text layer succeeded are recorded but not read (15.3); confirm that is acceptable (a clinically meaningful chart image would be missed by design).

## Review findings (spec-compliance-checker, 2026-10-27)

Fixed: a vision spend stop (`BudgetExceeded`) is now the visible manual outcome, not a re-raise that would leave a letter unscreened and unheld (§15.2: the safety path is never budget-limited); a mis-registered vision adapter fails the page (`PermanentError`) instead of crashing the job; the in-process memo keeps at most 8 documents.

Decided 2026-10-27: (a) the rules-first carve-out for image-to-text transcription is approved as NARROW and drafted (DRAFT, owner confirmation pending, commit 91d5289), with an order test (after the transcription the rules run before any other model, on the success and failure paths); (b) a page with a usable text layer and a large image (>= 20% of the page area, a named setting pending confirmation; unmeasurable counts as large) sends the image to vision, or holds the page for a nurse when there is no usable vision route; (c) the vision timeout stays the registry row's `timeout_seconds`, required with no default via `vlm_row`; (d) the real-PDF spike is a pre-launch blocker (phase2-status item 9).

Superseded by those decisions (kept for the record):
- Rule 2 carve-out for image-only pages (vision runs before the screen can read the page) was approved by the owner as a NARROW carve-out and drafted in the spec (91d5289, 2df1ec9); it stays DRAFT until the owner confirms the wording; the offline invariant test does not see the vision call because the fake writes no model event.
- (Resolved 2026-10-27: the 20% large-image rule was adopted.) The reviewer argued the skipped-images case is "unknown treated as clear": a scanned referral with a typed header has a text layer of 20+ characters, so the image body is never read. Suggested minimum: a page with a usable text layer AND an image covering most of the page (a technical threshold) goes to vision or a nurse. Awaiting your call; today such images are only counted.
- §16.1 gives vision a 60 s per-page timeout: `ModelRow.timeout_seconds` is the gateway's lever; confirm the registry row sets it when a real route is approved.
- pypdf was tried only on synthetic PDFs; real scanner output (JBIG2, CMYK, odd fonts) routes to a nurse via `image_unreadable`. A spike on real-world-like files is needed before launch.

## Third review (2026-10-27): closed, and documented limits

Closed: images inside form XObjects (and inline images inside them) hold the page; a content stream that cannot be parsed counts as an unreadable image, never as no image; a page with an unreadable image never takes the layout path (relevant once a layout tier is approved). pypdf's reader copies inherited /Resources, /MediaBox and /CropBox onto each page (checked in its source).

DOCUMENTED LIMITS, accepted by the owner on 2026-10-27 (rule 3 gaps that are low probability; see pre-launch blocker 9): images in annotation appearance streams (/Annots, /AP), in tiling patterns and in Type3 glyph procedures are not examined. They are covered by the real-PDF spike (pre-launch blocker 9), which should decide whether to hold any page that has annotations or patterns carrying images.
