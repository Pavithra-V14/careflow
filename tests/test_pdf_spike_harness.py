"""The real-PDF spike preparation (scripts/scanner_pdf_generator.py, scripts/pdf_spike_harness.py), offline: the synthetic scanner-style cases route as documented, the harness prints routes only (never
extracted text, never a file name), and the placeholders are honest about what they are."""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
_spec = importlib.util.spec_from_file_location("pdf_spike_harness", ROOT / "scripts" / "pdf_spike_harness.py")
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)
generator = sys.modules["scanner_pdf_generator"]


@pytest.mark.parametrize("case", generator.cases(), ids=lambda c: c.name)
def test_every_synthetic_case_routes_each_page_as_documented(case):
    observed, _, _ = harness.asyncio.run(harness.analyse(case.pdf))
    assert tuple(observed) == case.expected, case.note


def test_the_cases_cover_what_the_spike_asked_for():
    names = {c.name for c in generator.cases()}
    assert {"scan_cmyk", "large_image_beside_text", "rotated_text", "rotated_scan", "scan_image_only_rgb", "unparsable_content", "scan_jbig2_placeholder"} <= names
    routes = {r.split(":")[0] for c in generator.cases() for r in c.expected}
    assert routes == {"text", "text+vision", "vision", "nurse"}  # every outcome is exercised
    assert all(c.pdf.startswith(b"%PDF-") for c in generator.cases())


def test_the_jbig2_case_says_it_is_a_placeholder_and_proves_routing_only():
    case = next(c for c in generator.cases() if c.name == "scan_jbig2_placeholder")
    assert b"/JBIG2Decode" in case.pdf and "PLACEHOLDER" in case.note and "NOT a decoding test" in case.note


def test_the_harness_prints_no_extracted_text_and_no_file_name(tmp_path, capsys):
    secret = "Patient Asha Rao reports a persistent cough lasting several weeks"
    (tmp_path / "Asha_Rao_referral.pdf").write_bytes(generator.build((generator.Page(secret), generator.Page(images=(generator.Img(draw=400),)))))
    assert harness.run_directory(tmp_path) == 0
    out = capsys.readouterr().out
    assert "routes=text,vision" in out and "sha256:" in out
    assert "Asha" not in out and "Rao" not in out and "cough" not in out and "referral" not in out  # neither the page text nor the file name
    assert harness.run_directory(tmp_path / "missing") == 1


def test_the_synthetic_run_exits_zero_and_a_wrong_expectation_would_be_a_mismatch(capsys):
    assert harness.run_synthetic(None) == 0
    assert "0 mismatches" in capsys.readouterr().out
