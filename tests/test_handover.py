"""docs/handover.md is an index that stays true (owner instruction 2026-11-20): every file it names exists, every script in its command lines exists, the five approval files exist, and it claims no approval."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = (ROOT / "docs" / "handover.md").read_text(encoding="utf-8")
EXTENSIONS = (".py", ".md", ".yaml", ".yml", ".json", ".sql", ".ps1", ".docx")
PREFIXES = ("", "careflow/", "scripts/", "tests/", "docs/", "docs/design/", "docs/runbooks/", "db/migrations/", "docs/approval-pack/", "docs/approval-pack/export/")


def exists(token: str) -> bool:
    if "*" in token or "<" in token or token.startswith("python ") or " " in token:
        return True
    return any((ROOT / prefix / token).exists() for prefix in PREFIXES)


def test_every_file_the_handover_names_exists():
    tokens = {t for t in re.findall(r"`([^`]+)`", DOC) if t.endswith(EXTENSIONS) or (t.endswith("/") and "/" in t[:-1])}
    assert len(tokens) > 30
    missing = sorted(t for t in tokens if not ((ROOT / t).is_dir() if t.endswith("/") else exists(t)))
    assert not missing, f"named in docs/handover.md but not in the repository: {missing}"


def test_every_script_in_a_command_line_exists_and_the_start_line_is_the_documented_one():
    scripts = set(re.findall(r"scripts[\\/]+([A-Za-z0-9_]+\.(?:py|ps1))", DOC))
    assert {"run_demo_backend.ps1", "demo_smoke.py", "seed_demo_placeholders.py", "seed_demo_family_rules.py", "seed_dev_portal_patient.py", "demo_real_models.py"} <= scripts
    assert not [s for s in scripts if not (ROOT / "scripts" / s).exists()]
    assert r"cd E:\Claude\careflow-run ; .\scripts\run_demo_backend.ps1" in DOC


def test_it_names_the_five_approval_files_and_claims_no_approval():
    for name in ("priya-clinician", "counsel", "privacy-owner", "clinic-owner", "tamil-reader"):
        assert f"docs/approval-pack/export/{name}.docx" in DOC and (ROOT / "docs" / "approval-pack" / "export" / f"{name}.docx").exists()
    assert "Nothing here is approved for real patients" in DOC and "Nothing in them is approved until the named person signs" in DOC
    for decision in ("SMS", "E-mail", "Voice", "hosting", "observability", "malware"):
        assert decision.lower() in DOC.lower()
