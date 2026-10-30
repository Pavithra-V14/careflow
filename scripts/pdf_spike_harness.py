"""Offline harness for the real-PDF spike (docs/design/real-pdf-spike.md): runs the REAL extraction pipeline (tier 1 pypdf, pure Python, local) with a FAKE vision role and NO layout tier, and reports
which route every page takes: `text`, `text+vision`, `vision`, or `nurse:<reason>`.

    python scripts/pdf_spike_harness.py                  # the synthetic scanner-style cases: observed against expected, exit 1 on any mismatch
    python scripts/pdf_spike_harness.py --write DIR      # also write the synthetic PDFs into DIR (so they can be opened and inspected)
    python scripts/pdf_spike_harness.py --dir DIR        # the SPIKE: every *.pdf in DIR (de-identified samples the owner supplies); routes only

No network, no model, no database, no key. The fake vision role returns a fixed placeholder, so a page marked `vision` here means "the pipeline would send this page to the vision role", not that
anything was read. EXTRACTED TEXT IS NEVER PRINTED OR WRITTEN, and with `--dir` a file is shown by its position and the first 8 characters of its sha256, never by its name (a file name can hold
a patient's name).
"""

import argparse
import asyncio
import hashlib
import sys
import uuid
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for entry in (str(ROOT), str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from scanner_pdf_generator import cases

from careflow.adapters.fake import FakeVision
from careflow.adapters.pdf import PypdfExtractor
from careflow.extraction import (
    ExtractionPipeline,
    ExtractionResult,
    worker_context,
)

CLINIC = uuid.UUID(int=1)
PDF = "application/pdf"


def routes(result: ExtractionResult) -> list[str]:
    """One route word per page, from the pipeline's own page outcomes. A document that did not open has no pages: one `nurse:<reason>` for the whole file."""
    if not result.pages:
        return [f"nurse:{reason}" for reason in result.reasons] or ["nurse:unknown"]
    out = []
    for page in result.pages:
        if page.tier is None:
            out.append(f"nurse:{page.reason}")
        elif page.tier == "native":
            out.append("text+vision" if page.vision_images else "text")
        else:
            out.append(page.tier)  # "vision" (or "layout", which no adapter provides here)
    return out


async def analyse(content: bytes) -> tuple[list[str], ExtractionResult, FakeVision]:
    vision = FakeVision()
    with worker_context():
        result = await ExtractionPipeline(PypdfExtractor(), vision=vision).run(content, PDF, clinic_id=CLINIC)
    return routes(result), result, vision


def run_synthetic(write_to: Path | None) -> int:
    mismatches = 0
    print(f"{'case':28} {'page':>4}  {'expected':24} {'observed':24} ok")
    for case in cases():
        observed, result, _ = asyncio.run(analyse(case.pdf))
        if write_to is not None:
            write_to.mkdir(parents=True, exist_ok=True)
            (write_to / f"{case.name}.pdf").write_bytes(case.pdf)
        for n in range(max(len(observed), len(case.expected))):
            want = case.expected[n] if n < len(case.expected) else "-"
            got = observed[n] if n < len(observed) else "-"
            mismatches += want != got
            print(f"{case.name:28} {n + 1:>4}  {want:24} {got:24} {'yes' if want == got else 'NO'}")
        print(f"{'':28} {'':>4}  complete={result.complete}  vision calls={result.vision_calls}  ({case.note})")
    print(f"\n{mismatches} mismatches")
    return 1 if mismatches else 0


def run_directory(directory: Path) -> int:
    files = sorted(directory.glob("*.pdf"))
    if not files:
        print(f"no *.pdf files in {directory}")
        return 1
    totals: Counter[str] = Counter()
    for index, path in enumerate(files, start=1):
        content = path.read_bytes()
        observed, result, _ = asyncio.run(analyse(content))
        totals.update(observed)
        label = f"#{index} sha256:{hashlib.sha256(content).hexdigest()[:8]}"
        print(f"{label}  pages={len(result.pages)}  complete={result.complete}  routes={','.join(observed)}")
    print("\nroute totals: " + ", ".join(f"{route}={n}" for route, n in sorted(totals.items())))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", type=Path, help="also write the synthetic PDFs into this directory")
    parser.add_argument("--dir", type=Path, help="run on every *.pdf in this directory (the real spike); routes only")
    args = parser.parse_args()
    return run_directory(args.dir) if args.dir else run_synthetic(args.write)


if __name__ == "__main__":
    raise SystemExit(main())
