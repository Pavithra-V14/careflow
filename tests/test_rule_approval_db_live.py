"""Migration 0072 against the real database: the two-person approval of red-flag rule versions. Nothing is approved by default; two DIFFERENT doctors of the clinic, each with a verified second factor, approve a draft;
one reject closes it; the content of a version is never edited; the ledger is append-only; a draft is never in force in the clinical profile. Inside ONE never-committed outer transaction, as the restricted
`careflow_app` role. The rules used here are MEANINGLESS marker words (no clinical content).
"""

import json
import os
import time
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from careflow.care_gap_sql import sqlstate
from tests.db_helpers import ensure_app_role_sa

ROOT = Path(__file__).resolve().parent.parent
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(ROOT / ".env").get("DATABASE_URL_MIGRATOR")
pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured")]
DECIDE = "select kb.decide_red_flag_rule(:r, :d, :k)"
PLACEHOLDER = "# CLINICAL-PLACEHOLDER: needs clinician sign-off"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _conn():
    engine = create_async_engine(make_url(MIGRATOR or "").set(drivername="postgresql+asyncpg"), connect_args={"statement_cache_size": 0})
    conn = await engine.connect()
    outer = await conn.begin()
    try:
        await ensure_app_role_sa(conn)
        yield conn
    finally:
        await outer.rollback()
        await conn.close()
        await engine.dispose()


@pytest_asyncio.fixture(loop_scope="module")
async def db(_conn):
    sp = await _conn.begin_nested()
    try:
        yield _conn
    finally:
        await sp.rollback()


async def pg(db):
    return (await db.get_raw_connection()).driver_connection


def claims_for(person, aal2=True):
    c = {"sub": str(person), "role": "authenticated"}
    if aal2:
        c.update({"aal": "aal2", "amr": [{"method": "totp", "timestamp": int(time.time()) - 60}]})
    return json.dumps(c)


async def as_app(db, person, sql, aal2=True, **params):
    try:
        async with db.begin_nested():
            await db.exec_driver_sql("set local role careflow_app")
            await db.execute(text("select set_config('request.jwt.claims', :c, true)"), {"c": claims_for(person, aal2)})
            result = await db.execute(text(sql), params)
            value = [tuple(r) for r in result.all()] if result.returns_rows else result.rowcount
            await db.exec_driver_sql("reset role")
            return value
    except DBAPIError as exc:
        return sqlstate(exc)


async def owner(db, sql, **params):
    try:
        async with db.begin_nested():
            result = await db.execute(text(sql), params)
            return [tuple(r) for r in result.all()] if result.returns_rows else result.rowcount
    except DBAPIError as exc:
        return sqlstate(exc)


async def world(db):
    h = await pg(db)
    ids = {k: uuid.uuid4() for k in ("clinic", "other_clinic", "doc1", "doc2", "doc3", "nurse", "admin", "outsider_doc")}
    await h.execute("insert into iam.clinics (id, name, origin) values ($1, 'RA-A', 'test'), ($2, 'RA-B', 'test')", ids["clinic"], ids["other_clinic"])
    for k in ("doc1", "doc2", "doc3", "nurse", "admin", "outsider_doc"):
        await h.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test')", ids[k])
    for k, role in (("doc1", "doctor"), ("doc2", "doctor"), ("doc3", "doctor"), ("nurse", "nurse"), ("admin", "admin")):
        await h.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, $3, 'test')", ids[k], ids["clinic"], role)
    await h.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, 'doctor', 'test')", ids["outsider_doc"], ids["other_clinic"])
    return ids


async def draft(db, w, code="zz_marker", version=1, phrases=("zzmarker",), clinic: str | None = "clinic", origin="test", author=None):
    rid = uuid.uuid4()
    h = await pg(db)
    await h.execute(
        "insert into kb.red_flag_rules (id, clinic_id, rule_code, version_id, status, phrases, governance_metadata, authored_by, origin, idempotency_key) "
        "values ($1, $2, $3, $4, 'draft_unreviewed', $5, $6::jsonb, $7, $8, $9)",
        rid, w[clinic] if clinic else None, code, version, list(phrases), json.dumps({"placeholder": PLACEHOLDER}), w[author] if author else None, origin, f"ra-{rid}",
    )
    return rid


async def status_of(db, rid):
    return (await db.execute(text("select status from kb.red_flag_rules where id = :i"), {"i": rid})).scalar()


async def active_codes(db, w, profile):
    rows = await owner(db, "select rule_code, version_id from kb.active_red_flag_rules(:c, 'test', :p) order by 1, 2", c=w["clinic"], p=profile)
    return [tuple(r) for r in rows]


async def test_nothing_is_approved_by_default_and_a_draft_is_never_in_force_in_the_clinical_profile(db):
    w = await world(db)
    rid = await draft(db, w)
    assert await status_of(db, rid) == "draft_unreviewed"
    assert await active_codes(db, w, "clinical") == []
    assert await active_codes(db, w, "demo") == [("zz_marker", 1)]
    assert await active_codes(db, w, "anything-else") == []  # an unknown profile is treated as clinical


async def test_two_different_doctors_approve_and_only_then_is_the_rule_in_force(db):
    w = await world(db)
    rid = await draft(db, w)
    assert await as_app(db, w["doc1"], DECIDE, r=rid, d="approve", k="k1") == [("recorded",)]
    assert await status_of(db, rid) == "draft_unreviewed" and await active_codes(db, w, "clinical") == []
    assert await as_app(db, w["doc1"], DECIDE, r=rid, d="approve", k="k1") == [("recorded",)]  # a replay
    assert await as_app(db, w["doc1"], DECIDE, r=rid, d="approve", k="another") == [("recorded",)]  # the same person again is still one approval
    assert await status_of(db, rid) == "draft_unreviewed"
    assert await as_app(db, w["doc2"], DECIDE, r=rid, d="approve", k="k2") == [("approved",)]
    assert await status_of(db, rid) == "approved" and await active_codes(db, w, "clinical") == [("zz_marker", 1)]
    row = (await db.execute(text("select approved_by_1, approved_by_2, approved_at from kb.red_flag_rules where id = :i"), {"i": rid})).one()
    assert (row.approved_by_1, row.approved_by_2) == (w["doc1"], w["doc2"]) and row.approved_at is not None
    assert await as_app(db, w["doc2"], DECIDE, r=rid, d="approve", k="k2") == [("approved",)]  # a replay after approval
    assert await as_app(db, w["doc3"], DECIDE, r=rid, d="approve", k="k3") == [("not_draft",)]
    audits = (await db.execute(text("select action, actor_id, details from audit.log where resource_type = 'red_flag_rule' and resource_id = :r order by id"), {"r": str(rid)})).all()
    assert [a.action for a in audits] == ["red_flag_rule.approve_recorded", "red_flag_rule.approve"]
    assert audits[0].actor_id == w["doc1"] and audits[1].actor_id == w["doc2"]
    assert set(audits[1].details) == {"rule_code", "version_id", "content_hash", "first_approver"} and "zzmarker" not in json.dumps(audits[1].details)  # ids and a hash, never the rule text
    ledger = (await db.execute(text("select decision, decided_by, content_hash from kb.red_flag_rule_decisions where rule_id = :r order by created_at, id"), {"r": rid})).all()
    assert [(r.decision, r.decided_by) for r in ledger] == [("approve", w["doc1"]), ("approve", w["doc2"])] and ledger[0].content_hash == ledger[1].content_hash


async def test_who_may_decide(db):
    w = await world(db)
    rid = await draft(db, w)
    for who in ("nurse", "admin", "outsider_doc"):
        assert await as_app(db, w[who], DECIDE, r=rid, d="approve", k=f"k-{who}") == "42501", who  # only a doctor of the clinic
    assert await as_app(db, w["doc1"], DECIDE, aal2=False, r=rid, d="approve", k="k-noaal") == "42501"  # no second factor
    assert await as_app(db, w["doc1"], DECIDE, r=rid, d="maybe", k="k-bad") == "42501"
    assert await as_app(db, w["doc1"], DECIDE, r=uuid.uuid4(), d="approve", k="k-missing") == "42501"  # no such version: the same generic refusal
    assert await as_app(db, w["doc1"], DECIDE, r=rid, d="approve", k=" ") == "42501"
    assert (await db.execute(text("select count(*) from kb.red_flag_rule_decisions where rule_id = :r"), {"r": rid})).scalar() == 0
    # an admin and a doctor of the clinic read the ledger; a nurse does not
    assert await as_app(db, w["doc1"], DECIDE, r=rid, d="approve", k="k1") == [("recorded",)]
    assert len(await as_app(db, w["admin"], "select 1 from kb.red_flag_rule_decisions where rule_id = :r", r=rid)) == 1
    assert await as_app(db, w["nurse"], "select 1 from kb.red_flag_rule_decisions where rule_id = :r", r=rid) == []
    assert await as_app(db, w["outsider_doc"], "select 1 from kb.red_flag_rule_decisions where rule_id = :r", r=rid) == []


async def test_the_author_cannot_decide_and_a_key_belongs_to_one_rule(db):
    w = await world(db)
    mine = await draft(db, w, code="zz_authored", author="doc1")
    assert await as_app(db, w["doc1"], DECIDE, r=mine, d="approve", k="a1") == [("author_cannot_decide",)]
    assert await as_app(db, w["doc1"], DECIDE, r=mine, d="reject", k="a2") == [("author_cannot_decide",)]
    other = await draft(db, w, code="zz_other")
    assert await as_app(db, w["doc2"], DECIDE, r=other, d="approve", k="same") == [("recorded",)]
    third = await draft(db, w, code="zz_third")
    assert await as_app(db, w["doc2"], DECIDE, r=third, d="approve", k="same") == "42501"  # the key was used for another rule


async def test_one_reject_closes_the_version(db):
    w = await world(db)
    rid = await draft(db, w)
    assert await as_app(db, w["doc1"], DECIDE, r=rid, d="reject", k="r1") == [("rejected",)]
    assert await as_app(db, w["doc1"], DECIDE, r=rid, d="reject", k="r1") == [("rejected",)]
    assert await as_app(db, w["doc1"], DECIDE, r=rid, d="approve", k="r1b") == [("already_decided",)]
    assert await as_app(db, w["doc2"], DECIDE, r=rid, d="approve", k="r2") == [("rejected_earlier",)]
    assert await as_app(db, w["doc3"], DECIDE, r=rid, d="approve", k="r3") == [("rejected_earlier",)]
    assert await status_of(db, rid) == "draft_unreviewed" and await active_codes(db, w, "clinical") == []
    assert [r[0] for r in await owner(db, "select action from audit.log where resource_id = :r order by id", r=str(rid))] == ["red_flag_rule.reject"]


async def test_what_can_never_be_approved(db):
    w = await world(db)
    h = await pg(db)
    empty = uuid.uuid4()
    await h.execute("insert into kb.red_flag_rules (id, clinic_id, rule_code, version_id, governance_metadata, origin, idempotency_key) values ($1, $2, 'zz_empty', 1, $3::jsonb, 'test', $4)",
                    empty, w["clinic"], json.dumps({"placeholder": PLACEHOLDER}), f"ra-{empty}")
    assert await as_app(db, w["doc1"], DECIDE, r=empty, d="approve", k="e1") == [("empty_rule",)]
    live_rule = await draft(db, w, code="zz_live", origin="live")  # a test clinic's rule tagged live: the origins differ
    assert await as_app(db, w["doc1"], DECIDE, r=live_rule, d="approve", k="e2") == [("origin_mismatch",)]
    platform = await draft(db, w, code="zz_platform", clinic=None)
    assert await as_app(db, w["doc1"], DECIDE, r=platform, d="approve", k="e3") == [("platform_rule",)]
    assert await as_app(db, w["nurse"], DECIDE, r=platform, d="approve", k="e4") == "42501"
    for rid in (empty, live_rule, platform):
        assert await status_of(db, rid) == "draft_unreviewed"


async def test_a_newer_draft_never_replaces_an_approved_version_and_a_newer_approved_one_does(db):
    w = await world(db)
    v1 = await draft(db, w, code="zz_ver", version=1, phrases=("zzone",))
    await as_app(db, w["doc1"], DECIDE, r=v1, d="approve", k="v1a")
    await as_app(db, w["doc2"], DECIDE, r=v1, d="approve", k="v1b")
    v2 = await draft(db, w, code="zz_ver", version=2, phrases=("zztwo",))
    assert await active_codes(db, w, "clinical") == [("zz_ver", 1)]  # the draft v2 is not in force; v1 still is
    await as_app(db, w["doc1"], DECIDE, r=v2, d="approve", k="v2a")
    assert await active_codes(db, w, "clinical") == [("zz_ver", 1)]
    await as_app(db, w["doc3"], DECIDE, r=v2, d="approve", k="v2b")
    assert await active_codes(db, w, "clinical") == [("zz_ver", 2)]


async def test_the_content_is_never_edited_the_ledger_is_append_only_and_the_application_cannot_write(db):
    w = await world(db)
    rid = await draft(db, w)
    await as_app(db, w["doc1"], DECIDE, r=rid, d="approve", k="g1")
    for stmt in (
        "update kb.red_flag_rules set phrases = array['changed'] where id = :r",
        "update kb.red_flag_rules set rule_code = 'zz_renamed' where id = :r",
        "update kb.red_flag_rules set status = 'draft_unreviewed' where id = :r",
        "update kb.red_flag_rules set status = 'approved', approved_by_1 = '" + str(w["doc2"]) + "', approved_by_2 = '" + str(w["doc3"]) + "', approved_at = now() where id = :r",  # real accounts but no ledger rows: not through the door
        "delete from kb.red_flag_rules where id = :r",
        "truncate kb.red_flag_rules",
        "update kb.red_flag_rule_decisions set decision = 'reject' where rule_id = :r",
        "delete from kb.red_flag_rule_decisions where rule_id = :r",
        "truncate kb.red_flag_rule_decisions",
    ):
        assert await owner(db, stmt, **({} if stmt.startswith("truncate") else {"r": rid})) in ("23001", "23503", "23514", "42501", "0A000"), stmt
    assert await status_of(db, rid) == "draft_unreviewed"
    for stmt in (
        "insert into kb.red_flag_rules (rule_code, version_id, governance_metadata, origin, idempotency_key) values ('x', 1, '{}', 'test', 'k')",
        "update kb.red_flag_rules set status = 'approved' where id = :r",
        "delete from kb.red_flag_rules where id = :r",
        "insert into kb.red_flag_rule_decisions (rule_id, clinic_id, origin, decision, decided_by, decided_role, content_hash, idempotency_key) values (:r, gen_random_uuid(), 'test', 'approve', gen_random_uuid(), 'doctor', repeat('0', 64), 'k')",
        "update kb.red_flag_rule_decisions set decision = 'reject'",
        "delete from kb.red_flag_rule_decisions",
    ):
        assert await as_app(db, w["doc1"], stmt, r=rid) == "42501", stmt
    triggers = await owner(
        db,
        "select tgname, tgenabled from pg_trigger where tgrelid in ('kb.red_flag_rules'::regclass, 'kb.red_flag_rule_decisions'::regclass) and not tgisinternal and tgname like '%no_%' order by 1",
    )
    assert triggers and all(t[1] == "A" for t in triggers)  # ENABLE ALWAYS: they hold under replica mode too
