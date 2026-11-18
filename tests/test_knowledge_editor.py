"""The knowledge editor (migration 0090), OFFLINE: the switch, the request shapes, the route wiring (clinic and role checks before the setting, a doctor-only decision, the worker not the approver indexes), the
sweep with no embedding route, and a static read of the migration. The database half is tests/test_knowledge_editor_db_live.py and the role matrix. Invented marker words only; nothing is seeded."""

import re
from pathlib import Path

import pytest

from careflow import knowledge_editor_sql

ROOT = Path(__file__).resolve().parent.parent


def test_the_editor_is_off_unless_exactly_on():
    assert knowledge_editor_sql.enabled({knowledge_editor_sql.SETTING: "on"})
    for value in ("", "1", "true", "yes", "off", "onn"):
        assert not knowledge_editor_sql.enabled({knowledge_editor_sql.SETTING: value}), value
    assert not knowledge_editor_sql.enabled({})


@pytest.mark.asyncio
async def test_with_no_embedding_route_nothing_is_indexed():
    assert await knowledge_editor_sql.index_sweep(None, None) == 0  # (the clinical profile has none: an approved card is stored and not searchable, and nothing is invented)


def _route(name: str) -> str:
    source = (ROOT / "careflow" / "app.py").read_text(encoding="utf-8")
    start = source.index(f"async def {name}")
    return source[start : source.index("\n\n\n", start)]


def test_every_editor_route_checks_the_clinic_and_role_before_it_answers_503_and_the_decision_is_a_doctors_only():
    for name in ("create_knowledge_draft", "edit_knowledge_draft", "submit_knowledge_draft", "decide_knowledge_draft", "list_knowledge_drafts", "list_knowledge_items"):
        body = _route(name)
        assert body.index("_console_clinic(") < body.index("_knowledge_editor_on()"), name
    assert "KNOWLEDGE_APPROVERS" in _route("decide_knowledge_draft") and 'KNOWLEDGE_APPROVERS = ("doctor",)' in (ROOT / "careflow" / "app.py").read_text(encoding="utf-8")
    source = (ROOT / "careflow" / "app.py").read_text(encoding="utf-8")
    for name in ("create_knowledge_draft", "edit_knowledge_draft", "submit_knowledge_draft", "decide_knowledge_draft"):
        signature = source[source.index(f"async def {name}") : source.index(f"async def {name}") + 300]
        assert "IdempotencyKey" in signature, name  # every write carries an idempotency key
    assert "knowledge_editor_sql.index_sweep" in source and "knowledge_editor_sql.index_card_embedding" not in source  # the WORKER indexes; no route calls the index function


def test_the_migration_two_person_rule_freeze_append_only_forced_rls_no_grant_no_policy_and_aal2_everywhere():
    sql = (ROOT / "db" / "migrations" / "0090_knowledge_editor.sql").read_text(encoding="utf-8")
    code = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    assert "force row level security" in code and "create policy" not in code
    assert not re.search(r"grant\s+(select|insert|update|delete|all)[^;]*kb\.knowledge_", code)
    assert "check (status <> 'approved' or decided_by <> author)" in code and "check (approved_by <> author)" in code  # the two-person rule is a CHECK
    assert "a submitted draft is frozen" in code and "enable always trigger knowledge_versions_no_change" in code and "enable always trigger knowledge_drafts_guard_update" in code
    assert "unique (clinic_id, kind, code, version)" in code.replace("\n", " ") or "knowledge_versions_one unique (clinic_id, kind, code, version)" in code
    functions = re.findall(r"create function (kb\.\w+)\(", code)
    assert set(functions) == {"kb.knowledge_versions_guard", "kb.knowledge_drafts_guard", "kb.knowledge_draft_create", "kb.knowledge_draft_edit", "kb.knowledge_draft_submit", "kb.knowledge_draft_decide", "kb.knowledge_drafts_list",
                              "kb.knowledge_versions_list", "kb.knowledge_versions_to_index", "kb.knowledge_current_cards"}
    for name in ("knowledge_draft_create", "knowledge_draft_edit", "knowledge_draft_submit", "knowledge_draft_decide", "knowledge_drafts_list", "knowledge_versions_list", "knowledge_versions_to_index", "knowledge_current_cards"):
        block = code[code.index(f"create function kb.{name}") :]
        block = block[: block.index("\n$$;")]
        assert "security definer set search_path = ''" in block and "auth.uid()" in block and "42501" in block, name
        if name not in ("knowledge_versions_to_index", "knowledge_current_cards"):
            assert "iam.aal2_session()" in block, name  # every staff function that writes or reads clinician drafts needs a verified second factor
    assert "array['doctor']" in code[code.index("create function kb.knowledge_draft_decide") :] and "v_d.author = v_actor" in code  # a doctor, never the author
    assert "array['queue_worker']" in code[code.index("create function kb.knowledge_versions_to_index") :]  # the worker, not the approver, reads what to index


def test_nothing_is_seeded_and_kb_cards_stays_empty():
    sql = (ROOT / "db" / "migrations" / "0090_knowledge_editor.sql").read_text(encoding="utf-8")
    code = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    assert not re.search(r"insert\s+into\s+kb\.knowledge_(drafts|versions)\s*\([^)]*\)\s*values\s*\(\s*(gen_random_uuid\(\)\s*,\s*)?'", code)  # no literal row is inserted outside a function body
    assert [p.name for p in (ROOT / "kb" / "cards").iterdir()] == [".gitkeep"]


def test_the_new_live_file_is_in_exactly_one_ci_shard_and_the_schema_check_knows_the_migration():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert ci.count("tests/test_knowledge_editor_db_live.py") == 1
    assert "0090" in (ROOT / "careflow" / "schema_check.py").read_text(encoding="utf-8")
