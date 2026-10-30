**Spike preparation (owner request 2026-10-30). Tooling and a sample request only: no real PDF has been read, no model has been called.**

# Real-PDF spike: what is ready, what the owner supplies

**Why.** The extraction pipeline (`careflow/extraction.py`, `careflow/adapters/pdf.py`; `docs/design/p2-4-extraction.md`) was tried only on synthetic PDFs built byte by byte in tests. Real scanner and phone output is the part that breaks such readers: JBIG2 and CCITT compression, CMYK, odd fonts, rotation, hidden-text OCR layers, encryption. **No real referral is processed until this spike has cleared** (owner decision, p2-4 note), and `data_policy` stays `synthetic_only` until then.

## What is built

| Piece | Where | What it does |
|---|---|---|
| Synthetic scanner-style generator | `scripts/scanner_pdf_generator.py` | 15 pure-Python cases, every byte generated: CMYK, a large image beside text, a small logo, rotated text and rotated scan, image-only RGB/grey/JPEG scans, an image whose size cannot be measured, an empty page, an unparsable content stream, a four-page mixed document. |
| Offline harness | `scripts/pdf_spike_harness.py` | Runs the REAL pipeline (pypdf, tier 1, local) with a FAKE vision role and no layout tier, and prints the route of every page: `text`, `text+vision`, `vision`, or `nurse:<reason>`. Synthetic mode compares against the expected route and exits 1 on any mismatch; `--dir DIR` runs on the owner's samples. |
| Tests | `tests/test_pdf_spike_harness.py` | Every case routes as documented; every outcome is exercised; the JBIG2 case is labelled a placeholder; the harness prints **no extracted text and no file name** (a file name can hold a patient's name; samples are shown as `#n sha256:xxxxxxxx`). |

Run it: `python scripts/pdf_spike_harness.py` (add `--write DIR` to write the synthetic PDFs out and open them), and for the spike `python scripts/pdf_spike_harness.py --dir <folder of samples>`.

**What the synthetic run shows today (all 15 cases match):** a digital letter reads as `text`; an image-only page goes to `vision`; a large image beside a typed header is read by vision as well (the 20 percent rule); a small logo is recorded and not sent; CMYK, JBIG2-filtered and unparsable pages go to a person with the reason `image_unreadable`; an empty page is `no_text_layer`; any such page makes the document incomplete, so a nurse reads it all (unknown is never treated as clear).

## What the synthetic files cannot tell us

- **JBIG2.** No offline encoder exists in this project, so the JBIG2 case carries placeholder bytes behind a `/JBIG2Decode` filter. It proves how a page with an unhandled filter is routed (to a person, never silently), **not** that real JBIG2 is read. A real sample is required.
- **CCITT G4, JPX, indexed colour, ICC-based and predictor-encoded images**: the native reader hands on only JPEG streams and plain 8-bit grey or RGB; anything else is counted as unreadable and routed to a person. Real files show how often that happens.
- **Fonts and text quality.** pypdf's text layer can be present but useless (CID fonts without a ToUnicode map give gibberish); the "usable text layer" test is a count of letters and digits, so it may accept gibberish. Only real files show this.
- **Hidden-text OCR layers.** A scanner that adds an invisible OCR text layer over the page image gives a page with usable text and a large image: the pipeline will read it BOTH ways (text plus vision), paying for vision on a page that is already searchable. Whether that is wanted is a question the real samples will answer.
- **Vision quality and cost.** The vision role is a fake here; reading accuracy and price per page are a separate step that needs a provider approval (not given).

## The samples the owner makes (from dummy documents)

**Decided (owner, 2026-10-30): the samples are made from DUMMY documents.** Write or type a made-up referral letter (invented patient, invented findings), then print or write it and scan or photograph it the way a clinic would: the scanner, the app and the paper are real, the content is not. **Never take a real patient document and redact it by hand**: a hand redaction can leave names behind in a text layer, in metadata, in an image's hidden layers or in a file name, and the spike would then hold real patient data. If a real document is ever needed, that is a separate decision with its own privacy review.

**Where they live:** outside the repository (for example `E:\spike-samples\`), never committed, never uploaded anywhere, never pasted into a chat. **Count:** about 20 to 30 files, one or two of each kind below; 1 to 10 pages each, plus one long one.

**Making a sample look real without being real:** keep the layout, fonts, compression, resolution, colour mode and the scanner or app behaviour (those are what is being tested). Use invented names, dates of birth, phone numbers, addresses and IDs; invented signatures or stamps. Keep a short note per file (the source device or software, in a separate text file).

| # | Kind of file | Why it matters |
|---|---|---|
| 1 | Office multifunction printer scan to PDF, black and white (often JBIG2 or CCITT G4) | The most common clinic scan; a compression the native reader does not hand on |
| 2 | The same device in colour or greyscale (JPEG or "MRC" layered output) | Colour mode and layered images |
| 3 | Fax-quality or low-resolution scan (CCITT, 200 dpi) | Common for referral letters |
| 4 | Phone camera or scan-app PDF (JPEG, possibly rotated by a page flag) | Rotation and photographic images |
| 5 | A phone photo of a letter as JPEG and as PNG, plus an iPhone photo in its default format | The upload path for plain images; formats other than JPEG and PNG |
| 6 | A scan from a scanner that adds an invisible OCR text layer ("searchable PDF") | The double-read case above |
| 7 | A PDF exported from a hospital or clinic system or from a word processor | Embedded fonts, CID or Type 0 fonts, tagged PDF, PDF/A |
| 8 | A CMYK document (print-shop style export) | CMYK images and colour spaces |
| 9 | A typed letter with handwritten notes or a stamp scanned over it | Mixed pages: text layer plus a large image |
| 10 | A PDF with form fields or annotations filled in | Annotation text is not in the page content (a documented limit) |
| 11 | A password-protected PDF (owner password only, and one that needs a user password) | Must route to a person, not crash |
| 12 | A truncated or corrupt PDF (copy one and cut it short) | Fixed-word failure, no partial text as if complete |
| 13 | One long document (over 100 pages; the technical bound is 200) | The page-count bound |
| 14 | A page with very large or unusual dimensions, and a landscape page | Coverage measurement of images |
| 15 | A multi-column or table-heavy letter as a digital PDF | Text order (tier 2, layout, is empty) |

## What the owner gets back

The harness prints, per sample, the page count, whether the document is complete, and one route per page, plus totals. Together with a note per file this answers: how many pages would go to a nurse and for which reason; how many would go to vision (cost); whether any file crashes or is silently dropped (the answer must be no). Each unmet case becomes a decision for the owner, never a silent fix: a new image decoder or library is a new dependency (CLAUDE.md rule 13, ask first), a layout tier is the open Unstructured/pdfminer question in the p2-4 note, and a hosted vision route needs its own approval.

## What the spike does NOT do

No real model call; no real route; no change to `data_policy`. It does not read the extracted text aloud anywhere: the harness never prints or stores it.
