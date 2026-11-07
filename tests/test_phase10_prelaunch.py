"""Phase 10 stays honest (offline): every runbook the plan names exists and says it is not a record of anything done; the breach runbook invents no legal or contact detail; the capability sign-off sheet
lists every `clinical` gap of BUILD-SPEC.md 2 and ships with every answer cell blank; the committed evidence claims no drill. Documents only; nothing is executed."""

import re
from pathlib import Path

import yaml

from careflow import predeploy

ROOT = Path(__file__).resolve().parent.parent
RUNBOOKS = ROOT / "docs" / "runbooks"
NAMED = ("overdue-red-flag", "dead-letter-growth", "model-outage", "wrong-merge", "breach", "rollback-drill", "restore-drill", "sms-dlt-registration")


def test_every_runbook_the_plan_names_exists_and_none_is_empty():
    for name in NAMED:
        text = (RUNBOOKS / f"{name}.md").read_text(encoding="utf-8")
        assert len(text) > 400, name


def test_no_runbook_claims_a_drill_or_a_registration_was_done():
    for name in ("rollback-drill", "restore-drill", "sms-dlt-registration"):
        text = (RUNBOOKS / f"{name}.md").read_text(encoding="utf-8")
        assert re.search(r"\*\*Status:[^*]*(NOT been run|NOTHING HAS BEEN REGISTERED)", text), name


def test_the_breach_runbook_invents_no_legal_deadline_regulator_or_contact():
    text = (RUNBOOKS / "breach.md").read_text(encoding="utf-8")
    assert "SKELETON" in text and "____" in text
    assert not re.search(r"\b\d+\s*(hours?|days?)\b", text, re.IGNORECASE), "a deadline would be invented here"
    assert not re.search(r"@|https?://|\+\d{6,}", text), "no contact or link may be invented"


def test_the_capability_sheet_lists_every_clinical_gap_of_the_spec_and_every_answer_cell_is_blank():
    text = (ROOT / "docs" / "design" / "phase10-pre-launch.md").read_text(encoding="utf-8")
    sheet = text[text.index("## The `clinical`-profile capability gaps") : text.index("## Decisions that are the owner")]
    rows = [line for line in sheet.splitlines() if line.startswith("| ") and not line.startswith("| Capability") and not line.startswith("|---")]
    assert len(rows) == 8
    for row in rows:
        cells = [c.strip() for c in row.strip("|").split("|")]
        assert len(cells) == 4 and cells[1] and cells[2] == "" and cells[3] == "", row  # the answers are the owner's


def test_every_non_deterministic_capability_in_the_spec_table_is_on_the_sheet():
    spec = (ROOT / "BUILD-SPEC.md").read_text(encoding="utf-8")
    table = spec[spec.index("| Capability | `demo` profile"): spec.index("**This table is the honest statement")]
    off = [line for line in table.splitlines() if "| Off" in line or "extraction needs an LLM" in line or "reranker off" in line]
    sheet = (ROOT / "docs" / "design" / "phase10-pre-launch.md").read_text(encoding="utf-8").lower()
    for line in off:
        first = line.split("|")[1].strip().lower()
        key = {"card matching": "reranker", "reading typed pdf referrals": "extraction", "photographed referrals (gemini vision)": "vision", "tamil translation (sarvam)": "tamil translation",
               "care-plan free-text parsing": "care-plan", "pre-visit brief generation, adaptive intake conversation": "pre-visit brief", "voice channel (gemini live)": "voice channel"}[first]
        assert key in sheet, first


def test_the_committed_predeploy_evidence_still_claims_no_drill():
    evidence = yaml.safe_load((ROOT / "ops" / "predeploy-evidence.yaml").read_text(encoding="utf-8"))
    assert "rollback_drill" not in evidence and evidence.get("scorecard") == {}
    assert not predeploy.go(predeploy.evaluate_checks(evidence, predeploy.load_rubric()))
