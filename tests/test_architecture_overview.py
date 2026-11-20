"""docs/architecture-overview.md names only files that exist (owner instruction 2026-11-20): every file path or module file it mentions resolves in the repository, so the overview cannot quietly rot."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = (ROOT / "docs" / "architecture-overview.md").read_text(encoding="utf-8")
EXTENSIONS = (".py", ".md", ".yaml", ".yml", ".json", ".sql")
PREFIXES = ("", "careflow/", "careflow/adapters/", "scripts/", "tests/", "docs/", "docs/design/", "docs/runbooks/", "db/migrations/", "config/", "ops/", ".github/workflows/")


def resolves(token: str) -> bool:
    if "*" in token or "<" in token:
        return True
    if token.endswith("/"):
        return (ROOT / token).is_dir()
    return any((ROOT / prefix / token).exists() for prefix in PREFIXES)


def test_every_file_the_overview_names_exists():
    tokens = {t for t in re.findall(r"`([^`\s]+)`", DOC) if t.endswith(EXTENSIONS) or (t.endswith("/") and "/" in t[:-1])}
    assert tokens, "the overview names no file"
    missing = sorted(t for t in tokens if not resolves(t))
    assert not missing, f"named in docs/architecture-overview.md but not in the repository: {missing}"


def test_migration_numbers_the_overview_cites_exist_and_the_count_is_current():
    cited = set(re.findall(r"migrations? (\d{4})|\b(00\d\d)\b", DOC))
    numbers = {a or b for a, b in cited}
    on_disk = {p.name[:4] for p in (ROOT / "db" / "migrations").glob("*.sql")}
    assert numbers <= on_disk, sorted(numbers - on_disk)
    stated = int(re.search(r"\((\d+) plain SQL files\)", DOC).group(1))  # type: ignore[union-attr]
    assert abs(stated - len(on_disk)) <= 5, f"the overview says {stated} migrations, the repository has {len(on_disk)}: update the number"


def test_it_makes_no_claim_of_approval_and_links_the_detailed_documents():
    assert "Nothing here is approved for real patients" in DOC
    for linked in ("docs/spec-sweep.md", "docs/design/pre-launch-checklist.md", "docs/demo-runbook.md", "docs/ops-runbook.md", "docs/config-reference.md"):
        assert linked in DOC and (ROOT / linked).exists()
