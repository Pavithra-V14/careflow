"""The red-flag engine against the real hosted project (careflow/rules.py; BUILD-SPEC.md §5.2, §11.6, §12.1,
§20.2; CLAUDE.md rules 1, 2, 8, 11).

# CLINICAL-PLACEHOLDER: needs clinician sign-off

Every rule phrase and pattern here is a SYNTHETIC test token (`zzalpha`, `zz beta`, `zz\\d{2} gamma`); none is
a clinical term. Rules are inserted as the migrator in origin 'test' inside a transaction that is always
rolled back; the engine itself runs as the restricted `careflow_app` role with verified claims set per
transaction, so row-level security is genuinely in the path. All tests share one never-committed outer
transaction, each in its own savepoint; migration 0007 is executed inside it if it is not applied yet.
"""

import logging
import os
import socket
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from careflow import rules
from careflow.db import set_request_claims
from careflow.rules import RedFlagEngine, evaluate_with

ROOT = Path(__file__).resolve().parent.parent
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(ROOT / ".env").get("DATABASE_URL_MIGRATOR")
SQL_0007 = (ROOT / "db" / "migrations" / "0007_red_flag_rules_schema.sql").read_text(encoding="utf-8")
SQL_0013 = (ROOT / "db" / "migrations" / "0013_msg_screenings.sql").read_text(encoding="utf-8")

pytestmark = [
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured"),
]


def _u() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture(scope="module")
def engine():
    return RedFlagEngine()


async def raw(conn, sql):
    handle = await conn.get_raw_connection()
    await handle.driver_connection.execute(sql)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _conn():
    eng = create_async_engine(make_url(MIGRATOR or "").set(drivername="postgresql+asyncpg"), connect_args={"statement_cache_size": 0})
    conn = await eng.connect()
    outer = await conn.begin()
    try:
        if not (await conn.execute(text("select to_regclass('kb.red_flag_rules') is null"))).scalar_one():
            n = (await conn.execute(text(
                "select count(*) from kb.red_flag_rules where origin = 'test' and status = 'approved' and (cardinality(phrases) > 0 or cardinality(patterns) > 0)"
            ))).scalar_one()
            if n:  # read-only precondition; nothing is deleted
                pytest.fail(f"This suite assumes the database holds NO committed approved origin='test' red-flag rule with criteria (it inserts its own inside a rolled-back transaction); found {n}. Such a rule changes what screenings and rule-set hashes the tests expect. Nothing was deleted: an engineer with the migrator credential must retire or remove those rules deliberately, then re-run.", pytrace=False)
        await conn.exec_driver_sql("grant careflow_app to current_user with set true")
        if (await conn.execute(text("select to_regclass('kb.red_flag_rules') is null"))).scalar_one():
            await raw(conn, SQL_0007)
        # Message-sourced tasks and the screening function are 0013's (msg.* itself is 0011's, applied before this suite runs).
        if (await conn.execute(text("select to_regprocedure('msg.record_screening(uuid,text,text,integer,text,jsonb,text)') is null"))).scalar_one():
            await raw(conn, SQL_0013)
        yield conn
    finally:
        await outer.rollback()
        await conn.close()
        await eng.dispose()


@pytest_asyncio.fixture(loop_scope="module")
async def db(_conn):
    sp = await _conn.begin_nested()
    try:
        yield _conn
    finally:
        await sp.rollback()


async def act(conn, uid, fn, *args, **kwargs):
    """Call `fn(conn, ...)` as a request would: app role, claims set (empty for "no identity"), one savepoint."""
    async with conn.begin_nested():
        await conn.exec_driver_sql("set local role careflow_app")
        if uid is not None:
            await set_request_claims(conn, {"sub": str(uid), "role": "authenticated"})
        else:
            await conn.execute(text("select set_config('request.jwt.claims', '', true)"))
        result = await fn(conn, *args, **kwargs)
        await conn.exec_driver_sql("reset role")
        return result


async def rows(conn, sql, **params):
    return (await conn.execute(text(sql), params)).all()


async def one(conn, sql, **params):
    return (await rows(conn, sql, **params))[0][0]


class World:
    pass


async def world(conn) -> World:
    w = World()
    w.a, w.b = _u(), _u()
    spec = {"worker": (w.a, "queue_worker", "staff"), "nurse": (w.a, "nurse", "staff"), "doctor": (w.a, "doctor", "staff"),
            "fd": (w.a, "front_desk", "staff"), "auditor": (w.a, "auditor", "staff"), "worker_b": (w.b, "queue_worker", "staff"),
            "nurse_b": (w.b, "nurse", "staff"), "patient": (None, None, "patient")}
    for k in spec:
        setattr(w, k, _u())
    staff = {k: v for k, v in spec.items() if v[0]}
    await raw(
        conn,
        f"insert into iam.clinics (id, name, origin) values ('{w.a}', 'Clinic A', 'test'), ('{w.b}', 'Clinic B', 'test'); "
        "insert into iam.accounts (id, account_type, status, origin) values "
        + ", ".join(f"('{getattr(w, k)}', '{v[2]}', 'active', 'test')" for k, v in spec.items())
        + "; insert into iam.staff_roles (account_id, clinic_id, role, origin) values "
        + ", ".join(f"('{getattr(w, k)}', '{v[0]}', '{v[1]}', 'test')" for k, v in staff.items())
        + ";",
    )
    return w


async def add_rule(conn, w, code, *, phrases=(), patterns=(), status="approved", version=1, clinic="a", origin="test"):
    """Insert a rule as the migrator. Approved rules get two distinct named approvers (the DB requires it)."""
    clinic_id = {"a": w.a, "b": w.b, "global": None}[clinic]
    approval = {"approved_by_1": w.doctor, "approved_by_2": w.nurse, "approved_at": "now()"} if status == "approved" else {}
    meta = '{"placeholder": "' + rules.PLACEHOLDER + '"}' if status == "draft_unreviewed" else '{"note": "synthetic test rule"}'
    retired = "now()" if status == "retired" else None
    await conn.execute(
        text(
            f"""insert into kb.red_flag_rules (clinic_id, rule_code, version_id, status, phrases, patterns, governance_metadata,
                   authored_by, approved_by_1, approved_by_2, approved_at, retired_at, origin, idempotency_key)
                values (:c, :code, :v, :st, :ph, :pa, cast(:m as jsonb), :au, :a1, :a2,
                        {'now()' if approval else 'null'}, {retired or 'null'}, :o, :k)"""
        ),
        {"c": clinic_id, "code": code, "v": version, "st": status, "ph": list(phrases), "pa": list(patterns), "m": meta,
         "au": w.doctor, "a1": approval.get("approved_by_1"), "a2": approval.get("approved_by_2"), "o": origin, "k": f"r-{_u()}"},
    )


TEXT = "please note ZZAlpha   is mentioned here"


async def stored_message(conn, w, *, topic="care"):
    """A real stored message (patient, thread, message) in clinic A, written as the owner with the nurse's claims (the
    erased-patient guard also fires for the owner). The body is a dummy ciphertext: these tests never decrypt it."""
    patient, thread, message = _u(), _u(), _u()
    ct = "'\\x" + "01" * 40 + "'::bytea"
    claims = '{"sub": "' + str(w.nurse) + '", "role": "authenticated"}'
    await raw(
        conn,
        "insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, origin, idempotency_key) "
        f"values ('{patient}', '{w.a}', {ct}, 1, {ct}, 1, 'test', 'p-{patient}'); "
        f"select set_config('request.jwt.claims', '{claims}', true); "
        "insert into msg.threads (id, clinic_id, patient_id, topic, created_by, origin, idempotency_key) "
        f"values ('{thread}', '{w.a}', '{patient}', '{topic}', '{w.nurse}', 'test', 't-{thread}'); "
        "insert into msg.messages (id, clinic_id, origin, topic, thread_id, sender_kind, recorded_by, body_ct, body_key_version, idempotency_key) "
        f"values ('{message}', '{w.a}', 'test', '{topic}', '{thread}', 'staff', '{w.nurse}', {ct}, 1, 'm-{message}'); "
        "select set_config('request.jwt.claims', '', true);",
    )
    return message


# ---- the stub and the table's guarantees -----------------------------------------------------------


async def test_the_foundational_stub_is_an_inert_draft_carrying_the_placeholder(db):
    (row,) = await rows(db, "select * from kb.red_flag_rules where rule_code = 'unauthored_stub'")
    m = row._mapping
    assert (m["clinic_id"], m["status"], m["version_id"], m["phrases"], m["patterns"], m["vitals_criteria"]) == (None, "draft_unreviewed", 1, [], [], {})
    assert m["governance_metadata"]["placeholder"] == rules.PLACEHOLDER == "# CLINICAL-PLACEHOLDER: needs clinician sign-off"
    assert m["approved_by_1"] is None and m["approved_by_2"] is None


async def test_a_rule_defaults_to_draft_unreviewed_and_origin_has_no_default(db):
    w = await world(db)
    await db.execute(text("insert into kb.red_flag_rules (clinic_id, rule_code, version_id, governance_metadata, origin, idempotency_key) "
                          "values (:c, 'defaulted', 1, cast(:m as jsonb), 'test', :k)"), {"c": w.a, "m": '{"placeholder": "' + rules.PLACEHOLDER + '"}', "k": f"k-{_u()}"})
    assert await one(db, "select status from kb.red_flag_rules where rule_code = 'defaulted'") == "draft_unreviewed"
    default = await one(db, "select column_default from information_schema.columns where table_schema = 'kb' and table_name = 'red_flag_rules' and column_name = 'origin'")
    assert default is None
    with pytest.raises(DBAPIError, match="origin"):
        async with db.begin_nested():
            await db.execute(text("insert into kb.red_flag_rules (rule_code, version_id, governance_metadata, idempotency_key) values ('no_origin', 1, '{}', 'x')"))


@pytest.mark.parametrize(
    "bad",
    [
        "status = 'draft_unreviewed', governance_metadata = '{}'",  # an unreviewed rule must carry the placeholder
        "status = 'approved', approved_by_1 = {a}, approved_at = now()",  # one approver is not two
        "status = 'approved', approved_by_1 = {a}, approved_by_2 = {a}, approved_at = now()",  # the same person twice
        "status = 'approved', approved_by_1 = {a}, approved_by_2 = {b}",  # no approval time
        "status = 'retired'",  # retired with no retirement time
        "status = 'published'",
        "version_id = 0",
    ],
)
async def test_the_database_refuses_a_rule_that_breaks_the_governance_rules(db, bad):
    w = await world(db)
    base = {"status": "'draft_unreviewed'", "meta": "cast('{\"placeholder\": \"" + rules.PLACEHOLDER + "\"}' as jsonb)"}
    await db.execute(text("insert into kb.red_flag_rules (id, clinic_id, rule_code, version_id, governance_metadata, origin, idempotency_key) "
                          f"values (:i, :c, 'gov', 1, {base['meta']}, 'test', :k)"), {"i": _u(), "c": w.a, "k": f"k-{_u()}"})
    with pytest.raises(DBAPIError):
        async with db.begin_nested():
            await db.execute(text(f"update kb.red_flag_rules set {bad.replace('{a}', chr(39) + str(w.doctor) + chr(39)).replace('{b}', chr(39) + str(w.nurse) + chr(39))} where rule_code = 'gov'"))


async def test_two_distinct_approvers_make_a_rule_approved(db):
    w = await world(db)
    await add_rule(db, w, "ok_rule", phrases=["zzalpha"], status="approved")
    assert await one(db, "select status from kb.red_flag_rules where rule_code = 'ok_rule'") == "approved"


async def test_versions_are_unique_per_rule(db):
    w = await world(db)
    await add_rule(db, w, "v_rule", version=1)
    with pytest.raises(DBAPIError):
        async with db.begin_nested():
            await add_rule(db, w, "v_rule", version=1)


async def test_the_application_role_can_only_read_rules(db):
    w = await world(db)
    await add_rule(db, w, "ro_rule", phrases=["zzalpha"])
    assert not any([await one(db, f"select has_table_privilege('careflow_app', 'kb.red_flag_rules', '{p}')") for p in ("insert", "update", "delete")])
    for stmt in ("insert into kb.red_flag_rules (rule_code, version_id, governance_metadata, origin, idempotency_key) values ('x', 1, '{}', 'test', 'k')",
                 "update kb.red_flag_rules set status = 'approved'", "delete from kb.red_flag_rules"):
        with pytest.raises(DBAPIError, match="permission denied"):
            await act(db, w.doctor, _exec, stmt)
    relforce = await rows(db, "select relrowsecurity, relforcerowsecurity from pg_class where oid = 'kb.red_flag_rules'::regclass")
    assert tuple(relforce[0]) == (True, True)


async def _exec(conn, stmt):
    await conn.execute(text(stmt))


async def test_who_can_read_which_rules(db, engine):
    w = await world(db)
    await add_rule(db, w, "a_rule", phrases=["zzalpha"], clinic="a")
    await add_rule(db, w, "b_rule", phrases=["zzbeta"], clinic="b")
    await add_rule(db, w, "g_rule", phrases=["zzgamma"], clinic="global")
    codes = lambda uid: act(db, uid, _codes)
    seen = {r.rule_code for r in await codes(w.worker)}
    assert {"a_rule", "g_rule"} <= seen and "b_rule" not in seen  # own clinic plus platform-wide, never another clinic's
    assert "a_rule" not in {r.rule_code for r in await codes(w.worker_b)}
    assert "g_rule" in {r.rule_code for r in await codes(w.nurse_b)}
    for blind in (w.auditor, w.patient, None):
        assert await codes(blind) == [], blind


async def _codes(conn):
    return (await conn.execute(text("select rule_code from kb.red_flag_rules"))).all()


# ---- evaluation, as the engine runs it -------------------------------------------------------------


async def evaluate(db, engine, w, who, text_content=TEXT, *, clinic=None, origin="test", profile="clinical"):
    return await act(db, who or w.worker, engine.evaluate_text, clinic or w.a, origin, text_content, profile=profile)


async def test_an_explicit_phrase_is_a_red_flag_with_full_provenance(db, engine):
    w = await world(db)
    await add_rule(db, w, "synthetic_one", phrases=["zzalpha"], version=2)
    result = await evaluate(db, engine, w, None)
    assert result.outcome == "red_flag" and not result.safe_to_treat_as_clear
    (m,) = result.matches
    assert (m.rule_code, m.version_id, m.rule_status, m.kind) == ("synthetic_one", 2, "approved", "phrase")
    assert "ZZAlpha" not in str(result.provenance) and "mentioned here" not in str(result.provenance)


async def test_a_pattern_rule_fires_and_clean_text_is_clear(db, engine):
    w = await world(db)
    await add_rule(db, w, "synthetic_pat", patterns=[r"zz\d{2} +gamma"])
    assert (await evaluate(db, engine, w, None, "ZZ42    Gamma now")).outcome == "red_flag"
    clear = await evaluate(db, engine, w, None, "an ordinary sentence")
    assert clear.outcome == "clear" and clear.safe_to_treat_as_clear


async def test_a_draft_rule_is_usable_in_demo_but_ignored_in_clinical(db, engine):
    w = await world(db)
    await add_rule(db, w, "synthetic_draft", phrases=["zzalpha"], status="draft_unreviewed")
    demo = await evaluate(db, engine, w, None, profile="demo")
    assert demo.outcome == "red_flag" and demo.matches[0].rule_status == "draft_unreviewed"
    clinical = await evaluate(db, engine, w, None, profile="clinical")
    assert clinical.outcome == "unassessable" and clinical.drafts_excluded >= 1 and not clinical.safe_to_treat_as_clear  # the inert global stub is a draft too


async def test_clinical_with_no_approved_rule_is_unassessable_not_clear(db, engine):
    w = await world(db)  # only the inert global stub exists for this clinic
    for profile in ("clinical", "demo"):
        result = await evaluate(db, engine, w, None, "anything", profile=profile)
        assert result.outcome == "unassessable" and not result.safe_to_treat_as_clear, profile


async def test_a_newer_draft_does_not_replace_the_approved_version_in_clinical(db, engine):
    w = await world(db)
    await add_rule(db, w, "versioned", phrases=["zzalpha"], status="approved", version=1)
    await add_rule(db, w, "versioned", phrases=["zzother"], status="draft_unreviewed", version=2)
    clinical = await evaluate(db, engine, w, None, profile="clinical")
    assert clinical.outcome == "red_flag" and clinical.matches[0].version_id == 1
    demo = await evaluate(db, engine, w, None, profile="demo")
    assert demo.outcome == "clear" and demo.rules_evaluated >= 1  # demo follows the newest draft, which does not match


async def test_a_retired_rule_no_longer_fires(db, engine):
    w = await world(db)
    await add_rule(db, w, "retiring", phrases=["zzalpha"], status="approved", version=1)
    await add_rule(db, w, "retiring", phrases=["zzalpha"], status="retired", version=2)
    assert (await evaluate(db, engine, w, None)).outcome == "unassessable"


async def test_rules_are_scoped_to_their_clinic_origin_and_platform(db, engine):
    w = await world(db)
    await add_rule(db, w, "only_b", phrases=["zzalpha"], clinic="b")
    assert (await evaluate(db, engine, w, None)).outcome == "unassessable"  # B's rule never screens A's text
    await add_rule(db, w, "everywhere", phrases=["zzalpha"], clinic="global")
    assert (await evaluate(db, engine, w, None)).outcome == "red_flag"  # a platform-wide rule covers every clinic
    assert (await evaluate(db, engine, w, w.worker_b, clinic=w.b)).outcome == "red_flag"
    await add_rule(db, w, "eval_only", phrases=["zzdelta"], origin="eval")
    assert (await evaluate(db, engine, w, None, "zzdelta", origin="test")).outcome != "red_flag"  # another origin's rule never applies
    assert (await evaluate(db, engine, w, None, "zzdelta", origin="eval")).outcome == "red_flag"


async def test_a_caller_who_may_not_read_the_rules_gets_unassessable_never_clear(db, engine):
    w = await world(db)
    await add_rule(db, w, "synthetic_one", phrases=["zzalpha"])
    for blind in (w.auditor, w.patient):
        result = await act(db, blind, engine.evaluate_text, w.a, "test", TEXT)
        assert result.outcome == "unassessable", "RLS hid every rule; the engine must not read that as 'no red flag'"


async def test_demo_and_clinical_return_identical_results_for_approved_rules(db, engine):
    w = await world(db)
    await add_rule(db, w, "parity", phrases=["zzalpha", "zz beta"], patterns=[r"zz\d{2} gamma"])
    for sample in (TEXT, "zz   BETA", "ZZ77 gamma", "nothing relevant", ""):
        demo = await evaluate(db, engine, w, None, sample, profile="demo")
        clinical = await evaluate(db, engine, w, None, sample, profile="clinical")
        assert (demo.outcome, demo.matches) == (clinical.outcome, clinical.matches), sample


async def test_the_match_is_logged_with_provenance_and_without_the_text(db, engine, caplog):
    w = await world(db)
    await add_rule(db, w, "logged_rule", phrases=["zzalpha"])
    with caplog.at_level(logging.WARNING, logger="careflow.rules"):
        await evaluate(db, engine, w, None)
    assert any("red flag matched" in r.message and "logged_rule" in r.message for r in caplog.records)
    assert not any("mentioned here" in r.message for r in caplog.records)


async def test_no_model_and_no_network_are_needed_to_reach_a_safety_outcome(db, engine, monkeypatch):
    """Rules are loaded once; after that, deciding the outcome performs NO I/O of any kind."""
    w = await world(db)
    await add_rule(db, w, "offline", phrases=["zzalpha"])
    ruleset = await act(db, w.worker, engine.load_rules, w.a, "test", "clinical")

    def forbidden(*args, **kwargs):
        raise AssertionError("the safety decision tried to use the network")

    for name in ("connect", "send", "sendall", "sendto", "recv"):
        monkeypatch.setattr(socket.socket, name, forbidden)
    assert evaluate_with(ruleset, TEXT).outcome == "red_flag"
    assert evaluate_with(ruleset, "quiet").outcome == "clear"
    stream = rules.StreamEvaluator(ruleset)
    assert stream.feed("zz").outcome == "clear" and stream.feed("alpha").outcome == "red_flag"


# ---- voice: the same rules over a live transcript --------------------------------------------------


async def test_a_live_transcript_triggers_the_nurse_task_on_the_chunk_that_completes_the_phrase(db, engine):
    w = await world(db)
    await add_rule(db, w, "voice_rule", phrases=["zz beta gamma"])
    stream = await act(db, w.worker, engine.open_stream, w.a, "test", profile="clinical")
    for chunk in ("hello, ", "zz  be", "ta"):
        assert (await _feed(stream, chunk)).outcome != "red_flag"
    flagged = await _feed(stream, " gam")
    assert flagged.outcome != "red_flag"  # still incomplete
    flagged = await _feed(stream, "ma please")
    assert flagged.outcome == "red_flag" and stream.red_flag_seen
    task = await act(db, w.worker, engine.escalate_red_flag, w.a, "test", flagged, "voice-session-1:utt-7")
    assert not task.replayed
    assert await one(db, "select count(*) from agents.review_tasks where clinic_id = :c", c=w.a) == 1
    assert (await _feed(stream, " and more talk")).matches == ()  # not reported twice


async def _feed(stream, chunk):
    return stream.feed(chunk)


# ---- escalation: the immediate nurse task ----------------------------------------------------------


async def flagged_evaluation(db, engine, w):
    await add_rule(db, w, "esc_rule", phrases=["zzalpha"], version=4)
    return await evaluate(db, engine, w, None)


async def test_a_red_flag_creates_an_immediate_nurse_task_with_provenance_only(db, engine):
    w = await world(db)
    result = await flagged_evaluation(db, engine, w)
    task = await act(db, w.worker, engine.escalate_red_flag, w.a, "test", result, "msg-1")
    (row,) = await rows(db, "select * from agents.review_tasks where id = :i", i=task.task_id)
    m = row._mapping
    assert (m["kind"], m["priority"], m["assigned_role"], m["status"], m["origin"], m["clinic_id"], m["source_message_id"]) == (
        "red_flag", "immediate", "nurse", "open", "test", w.a, "msg-1")
    stored = m["provenance"]
    assert stored["matches"][0]["rule_code"] == "esc_rule" and stored["matches"][0]["version_id"] == 4
    assert "ZZAlpha" not in str(stored) and "mentioned here" not in str(stored)


async def test_escalation_is_idempotent_and_distinct_per_message(db, engine):
    w = await world(db)
    result = await flagged_evaluation(db, engine, w)
    first = await act(db, w.worker, engine.escalate_red_flag, w.a, "test", result, "msg-1")
    again = await act(db, w.worker, engine.escalate_red_flag, w.a, "test", result, "msg-1")
    other = await act(db, w.worker, engine.escalate_red_flag, w.a, "test", result, "msg-2")
    assert (again.replayed, again.task_id) == (True, first.task_id) and other.task_id != first.task_id
    assert await one(db, "select count(*) from agents.review_tasks") == 2


async def test_evaluate_and_escalate_records_a_screening_and_raises_a_task_only_for_a_red_flag(db, engine):
    """Since 0013 a STORED message is screened through msg.record_screening for every role: a red flag raises the immediate
    nurse task, a clear (the database agrees with the rule set) raises none."""
    w = await world(db)
    await add_rule(db, w, "e2e", phrases=["zzalpha"])
    flagged, quiet_message = await stored_message(db, w), await stored_message(db, w)
    evaluation, result = await act(db, w.worker, engine.evaluate_and_escalate, w.a, "test", TEXT, flagged, profile="clinical")
    assert evaluation.outcome == "red_flag" and result.outcome == "red_flag" and not result.replayed
    quiet, recorded = await act(db, w.worker, engine.evaluate_and_escalate, w.a, "test", "a quiet message", quiet_message, profile="clinical")
    assert quiet.outcome == "clear" and recorded.outcome == "clear"
    assert await one(db, "select count(*) from agents.review_tasks") == 1
    assert await one(db, "select kind from agents.review_tasks") == "red_flag"
    assert await one(db, "select count(*) from msg.screenings") == 2


async def test_evaluate_and_escalate_with_no_usable_rule_records_unassessable_and_raises_a_nurse_task(db, engine):
    """The migration-0007 state: no approved rule. Unknown is never clear, and never a quiet dead end."""
    w = await world(db)
    message = await stored_message(db, w)
    evaluation, result = await act(db, w.worker, engine.evaluate_and_escalate, w.a, "test", "anything", message, profile="clinical")
    assert evaluation.outcome == "unassessable" and result.outcome == "unassessable"
    (row,) = await rows(db, "select kind, priority, assigned_role, provenance from agents.review_tasks")
    assert tuple(row[:3]) == ("screening_unassessable", "standard", "nurse")
    assert row[3]["placeholder"] == rules.PLACEHOLDER and row[3]["priority_note"] == "pending clinical lead confirmation"


@pytest.mark.parametrize("who", ["worker", "nurse", "doctor", "fd"])
async def test_every_role_that_may_screen_a_message_can_record_its_screening(db, engine, who):
    """Front desk may screen an appointments message (not care); the others any topic."""
    w = await world(db)
    await add_rule(db, w, "e2e", phrases=["zzalpha"])
    message = await stored_message(db, w, topic="appointments")
    _evaluation, result = await act(db, getattr(w, who), engine.evaluate_and_escalate, w.a, "test", TEXT, message, profile="clinical")
    assert result.outcome == "red_flag"


async def test_front_desk_cannot_screen_a_care_message(db, engine):
    w = await world(db)
    await add_rule(db, w, "e2e", phrases=["zzalpha"])
    message = await stored_message(db, w, topic="care")
    with pytest.raises(rules.NotPermitted):
        await act(db, w.fd, engine.evaluate_and_escalate, w.a, "test", TEXT, message, profile="clinical")
    assert await one(db, "select count(*) from msg.screenings") == 0


@pytest.mark.parametrize("who", ["worker", "nurse", "doctor"])  # not front desk since 0013: it screens through msg.record_screening
async def test_staff_and_workers_of_the_clinic_can_raise_the_task(db, engine, who):
    w = await world(db)
    result = await flagged_evaluation(db, engine, w)
    task = await act(db, getattr(w, who), engine.escalate_red_flag, w.a, "test", result, f"msg-{who}")
    assert task.task_id is not None


@pytest.mark.parametrize("who", ["fd", "worker_b", "auditor", "patient", None])
async def test_everyone_else_is_refused(db, engine, who):
    w = await world(db)
    result = await flagged_evaluation(db, engine, w)
    with pytest.raises(rules.NotPermitted):
        await act(db, getattr(w, who) if who else None, engine.escalate_red_flag, w.a, "test", result, "msg-x")
    assert await one(db, "select count(*) from agents.review_tasks") == 0


async def test_the_database_makes_a_quiet_red_flag_task_impossible(db):
    w = await world(db)
    for priority, role in (("standard", "nurse"), ("immediate", "doctor"), ("immediate", "front_desk")):
        with pytest.raises(DBAPIError):
            async with db.begin_nested():
                await db.execute(text(
                    "insert into agents.review_tasks (clinic_id, origin, kind, priority, assigned_role, source_message_id, provenance, idempotency_key) "
                    "values (:c, 'test', 'red_flag', :p, :r, 'm', '{}', :k)"), {"c": w.a, "p": priority, "r": role, "k": f"k-{_u()}"})


async def _make_task(db, engine, w):
    result = await flagged_evaluation(db, engine, w)
    return (await act(db, w.worker, engine.escalate_red_flag, w.a, "test", result, "msg-ack")).task_id


async def test_only_the_clinics_own_staff_and_workers_see_the_task(db, engine):
    w = await world(db)
    await _make_task(db, engine, w)
    count = lambda: _count_tasks
    for who in (w.nurse, w.doctor, w.worker):
        assert await act(db, who, count()) == 1
    # Front desk lost message-sourced tasks in 0013 (the existence of a flag, and the rule code, can reveal a condition).
    for who in (w.fd, w.auditor, w.nurse_b, w.worker_b, w.patient, None):
        assert await act(db, who, count()) == 0, who


async def test_front_desk_still_sees_and_inserts_unmerge_review_tasks(db):
    w = await world(db)
    insert = (
        "insert into agents.review_tasks (clinic_id, origin, kind, priority, assigned_role, source_message_id, provenance, idempotency_key) "
        "values (:c, 'test', :k, 'immediate', 'nurse', 'merge:1', '{}', :ik)"
    )
    await db.execute(text(insert), {"c": w.a, "k": "unmerge_review", "ik": f"u-{_u()}"})
    await db.execute(text(insert), {"c": w.a, "k": "screening_unassessable", "ik": f"s-{_u()}"})
    assert await act(db, w.fd, _count_tasks) == 1  # only the unmerge_review one
    assert await act(db, w.nurse, _count_tasks) == 2

    async def front_desk_insert(conn, kind):
        await conn.execute(text(insert), {"c": w.a, "k": kind, "ik": f"x-{_u()}"})

    await act(db, w.fd, front_desk_insert, "unmerge_review")
    for kind in ("red_flag", "screening_unassessable", "anything_new"):
        with pytest.raises(DBAPIError, match="row-level security"):
            await act(db, w.fd, front_desk_insert, kind)


async def _count_tasks(conn):
    return (await conn.execute(text("select count(*) from agents.review_tasks"))).scalar_one()


async def test_a_nurse_acknowledges_as_themselves_and_nobody_else_can(db, engine):
    w = await world(db)
    task_id = await _make_task(db, engine, w)
    ack = "update agents.review_tasks set status = 'acknowledged', acknowledged_by = :by, acknowledged_at = now() where id = :i"
    for who in (w.fd, w.worker, w.nurse_b):
        assert await act(db, who, _run_update, ack, {"by": who, "i": task_id}) == 0, who  # not visible or not allowed: no row changes
    with pytest.raises(DBAPIError):  # a nurse cannot sign an acknowledgement as somebody else
        await act(db, w.nurse, _run_update, ack, {"by": w.doctor, "i": task_id})
    assert await act(db, w.nurse, _run_update, ack, {"by": w.nurse, "i": task_id}) == 1
    assert await one(db, "select status from agents.review_tasks where id = :i", i=task_id) == "acknowledged"


async def _run_update(conn, sql, params):
    return (await conn.execute(text(sql), params)).rowcount


async def test_the_application_role_cannot_rewrite_a_task_or_delete_one(db, engine):
    w = await world(db)
    task_id = await _make_task(db, engine, w)
    for column, value in (("priority", "'standard'"), ("assigned_role", "'doctor'"), ("provenance", "'{}'::jsonb"), ("clinic_id", f"'{w.b}'")):
        with pytest.raises(DBAPIError, match="permission denied"):
            await act(db, w.nurse, _exec, f"update agents.review_tasks set {column} = {value} where id = '{task_id}'")
    with pytest.raises(DBAPIError, match="permission denied"):
        await act(db, w.nurse, _exec, "delete from agents.review_tasks")


async def test_tasks_are_forced_rls_with_no_access_for_the_api_roles(db):
    flags = await rows(db, "select relrowsecurity, relforcerowsecurity from pg_class where oid = 'agents.review_tasks'::regclass")
    assert tuple(flags[0]) == (True, True)
    for role in ("anon", "authenticated"):
        for table in ("agents.review_tasks", "kb.red_flag_rules"):
            assert not await one(db, "select has_table_privilege(:r, :t, 'select')", r=role, t=table)
