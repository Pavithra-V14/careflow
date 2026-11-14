"""Patient visit preparation (migration 0089), OFFLINE: the feature switch, how the clinic's own checklist becomes the items (nothing invented, one fixed sentence when nothing is configured), the shape of the answers, the
route wiring and a static read of the migration. The database half is tests/test_visit_prep_db_live.py and the role matrix. Synthetic words only."""

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from careflow import visit_prep_sql
from careflow.visit_prep_sql import PrepState
from careflow.visit_readiness_content import parse

ROOT = Path(__file__).resolve().parent.parent
CONTENT = '{"facts": {"zz_fact": "DEMO question"}, "visit_types": {"Visit": {"intake": ["zz_fact"], "checklist": [{"id": "zz_item_a", "text": "DEMO wording A"}, {"id": "zz_item_b", "text": "DEMO wording B"}]}}}'
NOW = datetime(2026, 11, 14, tzinfo=UTC)


def test_the_feature_is_off_unless_exactly_on():
    assert visit_prep_sql.enabled({visit_prep_sql.SETTING: "on"}) and visit_prep_sql.enabled({visit_prep_sql.SETTING: " ON "})
    for value in ("", "1", "true", "yes", "off", "onn"):
        assert not visit_prep_sql.enabled({visit_prep_sql.SETTING: value}), value
    assert not visit_prep_sql.enabled({})


def test_the_items_are_the_clinics_own_checklist_with_the_patients_marks_and_unmarked_is_unknown_not_not_done():
    content = parse(CONTENT)
    state = PrepState("Visit", NOW, {"zz_item_a": (True, NOW)})
    items = visit_prep_sql.items_for(content, state)
    assert [(i.item_id, i.text, i.done) for i in items] == [("zz_item_a", "DEMO wording A", True), ("zz_item_b", "DEMO wording B", None)]  # None: not marked yet, never False
    assert visit_prep_sql.items_for(content, PrepState("Visit", NOW, {"not_on_the_list": (True, NOW)}))[0].done is None  # a mark for an item that is not configured shows nowhere


def test_nothing_configured_gives_no_items_and_the_one_fixed_sentence_never_an_invented_item():
    for content, visit_type in ((None, "Visit"), (parse(CONTENT), "A visit type with no entry")):  # (an EMPTY checklist is refused when the setting is parsed: it must never read as "nothing to confirm")
        assert visit_prep_sql.items_for(content, PrepState(visit_type, NOW, {})) == []
    assert visit_prep_sql.NOTHING_TO_PREPARE == "Nothing to prepare for this visit."


def test_the_routes_check_who_you_are_before_the_setting_and_the_staff_route_checks_the_role_before_the_setting():
    source = (ROOT / "careflow" / "app.py").read_text(encoding="utf-8")
    patient = source[source.index("async def patient_visit_prep") :]
    patient = patient[: patient.index("\n@portal.post")]
    assert patient.index("_patient_account(conn, claims)") < patient.index("_patient_prep_on()")
    mark = source[source.index("async def patient_mark_visit_prep") :]
    mark = mark[: mark.index("\n\n\n")]
    assert mark.index("_patient_account(conn, claims)") < mark.index("_patient_prep_on()") and "IdempotencyKey" in source[source.index("async def patient_mark_visit_prep") : source.index("async def patient_mark_visit_prep") + 400]
    assert "if item_id not in {i for i, _ in visit_prep_sql.checklist_for(" in mark  # only the clinic's own items can be marked
    staff = source[source.index("async def staff_visit_prep") :]
    staff = staff[: staff.index("\n\n\n")]
    assert staff.index("load_visit(") < staff.index("_clinic_role_held(") < staff.index("_patient_prep_on()") < staff.index("visit_prep_sql.staff_prep(")


def test_the_migration_is_append_only_forced_rls_with_no_grant_no_policy_and_aal2_on_the_staff_read():
    sql = (ROOT / "db" / "migrations" / "0089_patient_visit_prep.sql").read_text(encoding="utf-8")
    code = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    assert "enable row level security" in code and "force row level security" in code and "create policy" not in code
    assert not re.search(r"grant\s+(select|insert|update|delete|all)[^;]*ops\.visit_prep_marks", code)
    assert "enable always trigger visit_prep_marks_no_change" in code and "enable always trigger visit_prep_marks_no_truncate" in code
    columns = set(re.findall(r"^\s{4}(\w+)\s", re.search(r"create table ops\.visit_prep_marks \((.*?)\n\);", code, re.DOTALL).group(1), re.MULTILINE)) - {"constraint", "foreign"}
    assert columns == {"id", "clinic_id", "origin", "visit_id", "patient_id", "item", "done", "marked_by", "created_at", "idempotency_key"}  # ids, a closed word and a flag: no text column
    for fn in ("patient_visit_prep", "patient_mark_prep", "staff_visit_prep"):
        block = code[code.index(f"create function sched.{fn}") :]
        block = block[: block.index("\n$$;")]
        assert "security definer set search_path = ''" in block and "auth.uid()" in block and "42501" in block
    staff = code[code.index("create function sched.staff_visit_prep") :]
    assert "iam.aal2_session()" in staff and "array['nurse', 'doctor']" in staff
    assert "starts_at > clock_timestamp()" in code[code.index("create function sched.patient_mark_prep") :]  # only a future visit can be marked
    assert "'sys:visit-prep:' || gen_random_uuid()::text" in code and "visit-prep:' || v_actor::text" in code


def test_the_new_live_file_is_in_exactly_one_ci_shard_and_the_schema_check_knows_the_migration():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert ci.count("tests/test_visit_prep_db_live.py") == 1
    assert "0089" in (ROOT / "careflow" / "schema_check.py").read_text(encoding="utf-8")
    assert pytest
