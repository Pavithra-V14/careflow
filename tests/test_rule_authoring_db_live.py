"""Migration 0076 against the real database: a doctor writes a DRAFT red-flag rule in the restricted language (literal phrases and token sets, never a regular expression). A draft is never in force in the clinical
profile; the author cannot approve it; two other doctors can; token sets are part of the immutable, hashed content. Inside ONE never-committed outer transaction, as the restricted
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
AUTHOR = "select kb.author_red_flag_rule_draft(:c, :code, cast(:ph as text[]), cast(:ts as jsonb), :k)"
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
    await h.execute("insert into iam.clinics (id, name, origin) values ($1, 'RW-A', 'test'), ($2, 'RW-B', 'test')", ids["clinic"], ids["other_clinic"])
    for k in ("doc1", "doc2", "doc3", "nurse", "admin", "outsider_doc"):
        await h.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test')", ids[k])
    for k, role in (("doc1", "doctor"), ("doc2", "doctor"), ("doc3", "doctor"), ("nurse", "nurse"), ("admin", "admin")):
        await h.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, $3, 'test')", ids[k], ids["clinic"], role)
    await h.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, 'doctor', 'test')", ids["outsider_doc"], ids["other_clinic"])
    return ids


async def author(db, w, who="doc1", code="zz_draft", ph=("zzmarker",), ts=(("zzalpha", "zzbeta"),), key=None, aal2=True, clinic="clinic"):
    return await as_app(db, w[who], AUTHOR, aal2=aal2, c=w[clinic], code=code, ph=list(ph), ts=json.dumps([list(t) for t in ts]), k=key or f"k-{uuid.uuid4()}")


async def active(db, w, profile):
    return [tuple(r) for r in await owner(db, "select rule_code, version_id, has_criteria from kb.active_red_flag_rules(:c, 'test', :p) order by 1, 2", c=w["clinic"], p=profile)]


async def test_a_doctor_writes_a_draft_that_is_never_active_in_the_clinical_profile(db):
    w = await world(db)
    made = await author(db, w, key="a1")
    rid = made[0][0]
    row = (await db.execute(text("select status, patterns, phrases, token_sets, authored_by, origin, version_id from kb.red_flag_rules where id = :i"), {"i": rid})).one()
    assert (row.status, list(row.patterns), list(row.phrases), row.token_sets, row.authored_by, row.origin, row.version_id) == ("draft_unreviewed", [], ["zzmarker"], [["zzalpha", "zzbeta"]], w["doc1"], "test", 1)
    assert await active(db, w, "clinical") == [] and await active(db, w, "demo") == [("zz_draft", 1, True)]
    assert await author(db, w, key="a1") == made  # a replay is the same draft
    second = await author(db, w, key="a2")
    assert (await db.execute(text("select version_id from kb.red_flag_rules where id = :i"), {"i": second[0][0]})).scalar() == 2  # the next version, never an edit
    assert await author(db, w, who="doc2", key="a1") != made  # another person's key is their own
    audits = [r[0] for r in await owner(db, "select action from audit.log where resource_id = :r", r=str(rid))]
    assert audits == ["red_flag_rule.draft_create"]


async def test_only_a_doctor_of_the_clinic_with_a_second_factor_writes_a_draft(db):
    w = await world(db)
    for who in ("nurse", "admin", "outsider_doc"):
        assert await author(db, w, who=who) == "42501", who
    assert await author(db, w, aal2=False) == "42501"
    assert await owner(db, "select count(*) from kb.red_flag_rules where rule_code = 'zz_draft'") == [(0,)]


async def test_a_shape_outside_the_language_is_refused(db):
    w = await world(db)
    bad = [
        {"ph": (), "ts": ()},  # no criterion
        {"ph": ("x",) * 101},
        {"ph": ("x" * 201,)},
        {"ph": ("  ",)},
        {"ph": ("a" + chr(7) + "b",)},  # a control character
        {"ph": (), "ts": [["only"]]},  # a set of one
        {"ph": (), "ts": [["zz alpha", "zzbeta"]]},  # a token with a space
        {"ph": (), "ts": [["a" * 41, "b"]]},
        {"ph": (), "ts": [[f"w{i}" for i in range(11)]]},
        {"ph": (), "ts": [["aa", "bb"]] * 51},
        {"code": "Bad Code"},
        {"ph": ("zz" + chr(0x200B) + "zz",)},  # an invisible format character the engine would strip
        {"ph": (), "ts": [["fever", chr(0x200B)]]},
        {"ph": (), "ts": [["a" + chr(0xA0) + "b", "cc"]]},  # a no-break space inside a token
        {"ph": (), "ts": [["same", "SAME"]]},  # two tokens that are one word
    ]
    for i, kw in enumerate(bad):
        assert await author(db, w, key=f"bad{i}", **kw) == "22023", kw


async def test_an_authored_draft_is_approved_only_by_two_other_doctors_and_a_token_set_only_rule_is_then_in_force(db):
    w = await world(db)
    rid = (await author(db, w, ph=(), ts=(("zzalpha", "zzbeta"),), key="p1"))[0][0]
    assert await as_app(db, w["doc1"], DECIDE, r=rid, d="approve", k="x1") == [("author_cannot_decide",)]  # not the author
    assert await as_app(db, w["doc2"], DECIDE, r=rid, d="approve", k="x2") == [("recorded",)]
    assert await active(db, w, "clinical") == []
    assert await as_app(db, w["doc3"], DECIDE, r=rid, d="approve", k="x3") == [("approved",)]
    assert await active(db, w, "clinical") == [("zz_draft", 1, True)]  # criteria are counted even with only token sets
    h = (await db.execute(text("select content_hash from kb.red_flag_rule_decisions where rule_id = :r"), {"r": rid})).all()
    assert len(h) == 2 and h[0][0] == h[1][0]


async def test_token_sets_are_immutable_content_and_a_token_set_match_survives_the_provenance_filter(db):
    w = await world(db)
    rid = (await author(db, w, key="i1"))[0][0]
    assert await owner(db, "update kb.red_flag_rules set token_sets = '[]'::jsonb where id = :r", r=rid) == "23001"
    match = {"rule_id": str(rid), "rule_code": "zz_draft", "version_id": 1, "rule_status": "draft_unreviewed", "kind": "token_set", "index": 0, "start": 0, "end": 5}
    out = await owner(db, "select msg.sanitise_matches(cast(:p as jsonb))", p=json.dumps({"matches": [match]}))
    assert out[0][0]["dropped"] == 0 and len(out[0][0]["matches"]) == 1
    bad = await owner(db, "select msg.sanitise_matches(cast(:p as jsonb))", p=json.dumps({"matches": [{**match, "kind": "regex"}]}))
    assert bad[0][0]["dropped"] == 1
    assert await as_app(db, w["doc1"], "insert into kb.red_flag_rules (rule_code, version_id, governance_metadata, origin, idempotency_key) values ('x', 1, '{}', 'test', 'k')") == "42501"  # still no direct write


async def test_the_same_key_with_a_different_draft_is_a_conflict_not_a_silent_replay(db):
    w = await world(db)
    assert len(await author(db, w, key="c1")) == 1
    assert await author(db, w, key="c1", ph=("zzother",)) == "23505"
    assert await author(db, w, key="c1", code="zz_other") == "23505"
