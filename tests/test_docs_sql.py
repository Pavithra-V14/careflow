"""Migration 0017 (docs.documents), read as TEXT: no database. The live checks are in tests/test_rls_docs_live.py."""

import re
from pathlib import Path

SQL = (Path(__file__).resolve().parent.parent / "db" / "migrations" / "0017_docs_documents.sql").read_text(encoding="utf-8")
CODE = "\n".join(line for line in SQL.splitlines() if not line.strip().startswith("--"))


def _statements_about_the_table() -> list[str]:
    return [st.strip() for st in CODE.split(";") if re.search(r"\bon\s+docs\.documents\b", st, flags=re.IGNORECASE) and st.strip().lower().startswith("grant")]


def test_forced_rls_select_and_a_column_limited_insert_only_and_no_seed_row():
    low = CODE.lower()
    assert "alter table docs.documents force row level security" in low
    grants = _statements_about_the_table()
    assert [g.split()[1].split("(")[0] for g in grants] == ["select", "insert"]
    insert_columns = re.search(r"grant insert \(([^)]*)\)", CODE, flags=re.IGNORECASE).group(1)
    assert not {"created_at", "status"} & {c.strip() for c in insert_columns.split(",")}
    assert not re.search(r"insert\s+into\s+docs\.documents", CODE, flags=re.IGNORECASE)


def test_the_path_is_tied_to_the_row_the_key_is_per_clinic_and_the_table_is_append_only():
    low = CODE.lower()
    assert "documents_path_is_this_rows_own" in CODE and "'clinic/' || clinic_id::text || '/doc/' || id::text" in CODE
    assert "unique (clinic_id, idempotency_key)" in low
    assert "documents_no_update_delete" in low and "documents_no_truncate" in low


def test_exactly_an_insert_and_a_select_policy_exist_and_neither_admits_admin_owner_or_auditor():
    policies = re.findall(r"create policy (\w+) on docs\.documents for (\w+)(.*?);", CODE, flags=re.IGNORECASE | re.DOTALL)
    assert sorted(kind for _, kind, _ in policies) == ["insert", "select"]
    for _, _, body in policies:
        assert "admin" not in body and "business_owner" not in body and "auditor" not in body
