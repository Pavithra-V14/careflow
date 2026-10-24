"""msg.screenings, msg.record_screening, the worst-of view, the deferred screening constraint and the review-task policies of
migration 0013, live.

BUILD-SPEC.md §5.2, §11.6, §12.1, §20.2, §18.4; CLAUDE.md rules 1, 2, 3, 5, 8, 9. Same harness as the other live suites: ONE never
committed outer transaction on the migrator connection, each test in its own savepoint, rows tagged origin='test' (one test uses a
'live' clinic inside the same rolled-back transaction), everything rolled back at the end. Migration 0013 is executed inside that
transaction when it is not applied yet. Statements as the application role run as `careflow_app` with that identity's claims, so
row-level security and the function's own role check are genuinely exercised. Nothing here commits; audit.log and msg.messages are
append-only for every role, so nothing here may ever write to them outside a rolled-back transaction.

# CLINICAL-PLACEHOLDER: needs clinician sign-off
Every rule phrase here is a SYNTHETIC token (`zzalpha`); none is a clinical term (CLAUDE.md rule 1).
"""

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import asyncpg
import pytest
import pytest_asyncio
from dotenv import dotenv_values

from careflow import rules
from tests.db_helpers import ensure_app_role

ROOT = Path(__file__).resolve().parent.parent
_ENV = dotenv_values(ROOT / ".env")
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or _ENV.get("DATABASE_URL_MIGRATOR")
SQL_0013 = (ROOT / "db" / "migrations" / "0013_msg_screenings.sql").read_text(encoding="utf-8")

pytestmark = [
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured"),
]

DENIED = "42501:new row violates row-level security policy for table \"screenings\""
EMPTY = rules.EMPTY_RULE_SET_HASH
ZERO = "0" * 64
TOPICS = ("care", "appointments", "safety")
CT = "'\\x" + "01" * 40 + "'::bytea"


def _u() -> uuid.UUID:
    return uuid.uuid4()


def _dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def lit(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


HELPERS = """
create function pg_temp.try_msg(p_uid uuid, p_sql text) returns text language plpgsql as $f$
declare out text;
begin
    perform set_config('role', 'careflow_app', true);
    perform set_config('request.jwt.claims',
        case when p_uid is null then '' else json_build_object('sub', p_uid, 'role', 'authenticated')::text end, true);
    begin
        execute p_sql;
        out := 'ok';
    exception when others then
        out := sqlstate || ':' || sqlerrm;
    end;
    perform set_config('role', 'none', true);
    perform set_config('request.jwt.claims', '', true);
    return out;
end $f$;

create function pg_temp.try_seq(p_uid uuid, p_sqls text[]) returns text[] language plpgsql as $f$
declare
    out text[] := '{}';
    q text;
    n bigint;
begin
    perform set_config('role', 'careflow_app', true);
    perform set_config('request.jwt.claims',
        case when p_uid is null then '' else json_build_object('sub', p_uid, 'role', 'authenticated')::text end, true);
    foreach q in array p_sqls loop
        begin
            execute q;
            get diagnostics n = row_count;
            out := out || ('ok:' || n);
        exception when others then
            out := out || ('err:' || sqlstate);
        end;
    end loop;
    perform set_config('role', 'none', true);
    perform set_config('request.jwt.claims', '', true);
    return out;
end $f$;

create function pg_temp.rows_as(p_uid uuid, p_sql text) returns jsonb language plpgsql as $f$
declare j jsonb;
begin
    perform set_config('role', 'careflow_app', true);
    perform set_config('request.jwt.claims',
        case when p_uid is null then '' else json_build_object('sub', p_uid, 'role', 'authenticated')::text end, true);
    execute format('select coalesce(jsonb_agg(to_jsonb(t)), ''[]''::jsonb) from (%s) t', p_sql) into j;
    perform set_config('role', 'none', true);
    perform set_config('request.jwt.claims', '', true);
    return j;
end $f$;
"""


async def message_of(conn, uid, sql):
    return await conn.fetchval("select pg_temp.try_msg($1::uuid, $2)", uid, sql)


async def attempts(conn, uid, sqls):
    return await conn.fetchval("select pg_temp.try_seq($1::uuid, $2::text[])", uid, sqls)


async def rows_as(conn, uid, sql):
    return json.loads(await conn.fetchval("select pg_temp.rows_as($1::uuid, $2)", uid, sql))


async def outcome(conn, sql, *args):
    """Run `sql` as the connection's own role (the owner) in a savepoint: (None, None) on success, else (sqlstate, message)."""
    await conn.execute("savepoint attempt")
    try:
        await conn.execute(sql, *args)
    except asyncpg.PostgresError as exc:
        await conn.execute("rollback to savepoint attempt")
        return exc.sqlstate, str(exc)
    await conn.execute("release savepoint attempt")
    return None, None


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _conn():
    conn = await asyncpg.connect(_dsn(MIGRATOR or ""), statement_cache_size=0, timeout=20)
    tx = conn.transaction()
    await tx.start()
    try:
        n = await conn.fetchval(
            "select count(*) from kb.red_flag_rules where origin = 'test' and status = 'approved' and (cardinality(phrases) > 0 or cardinality(patterns) > 0)"
        )
        if n:  # read-only precondition; nothing is deleted
            pytest.fail(f"This suite assumes the database holds NO committed approved origin='test' red-flag rule with criteria (it inserts its own inside a rolled-back transaction); found {n}. Such a rule changes what screenings and rule-set hashes the tests expect. Nothing was deleted: an engineer with the migrator credential must retire or remove those rules deliberately, then re-run.", pytrace=False)
        await ensure_app_role(conn)
        if await conn.fetchval("select to_regclass('msg.screenings') is null"):
            await conn.execute(SQL_0013)
        await conn.execute(HELPERS)
        yield conn
    finally:
        await tx.rollback()
        await conn.close()


@pytest_asyncio.fixture(loop_scope="module")
async def db(_conn):
    await _conn.execute("savepoint per_test")
    try:
        yield _conn
    finally:
        await _conn.execute("rollback to savepoint per_test")
        # A successful `set constraints all immediate` inside a (released) inner savepoint is NOT undone by the rollback above:
        # restore the deferred mode explicitly, or every later patient-kind insert would be checked at once.
        await _conn.execute("set constraints all deferred")


# ---- seeding ---------------------------------------------------------------------------------------------------------------------


async def set_claims(conn, uid):
    await conn.execute("select set_config('request.jwt.claims', $1, true)", json.dumps({"sub": str(uid), "role": "authenticated"}) if uid else "")


async def add_message(conn, w, thread, topic, *, kind="staff", clinic=None, origin="test"):
    mid = _u()
    await set_claims(conn, w.people["nurse_a"] if clinic is None else w.people["nurse_b"])
    await conn.execute(
        "insert into msg.messages (id, clinic_id, origin, topic, thread_id, sender_kind, recorded_by, body_ct, body_key_version, idempotency_key) "
        f"values ('{mid}', '{clinic or w.a}', '{origin}', '{topic}', '{thread}', '{kind}', "
        f"'{w.people['nurse_a'] if clinic is None else w.people['nurse_b']}', {CT}, 1, 'm-{mid}')"
    )
    await set_claims(conn, None)
    return mid


async def seed(conn, origin="test"):
    """Clinic A (nurse, doctor, front desk, admin, worker, auditor) with a patient, a thread and a staff message per topic;
    clinic B (nurse, front desk) with one care message; a patient account."""
    a, b = _u(), _u()
    staff = [("nurse_a", a, "nurse"), ("doctor_a", a, "doctor"), ("fd_a", a, "front_desk"), ("admin_a", a, "admin"),
             ("worker_a", a, "queue_worker"), ("auditor_a", a, "auditor"), ("nurse_b", b, "nurse"), ("fd_b", b, "front_desk")]
    people = {k: _u() for k, _, _ in staff}
    people["patient_acct"] = _u()
    accounts = [f"('{people[k]}', 'staff', 'active', '{origin}')" for k, _, _ in staff] + [f"('{people['patient_acct']}', 'patient', 'active', '{origin}')"]
    await conn.execute(
        f"insert into iam.clinics (id, name, origin) values ('{a}', 'A', '{origin}'), ('{b}', 'B', '{origin}');"
        f"insert into iam.accounts (id, account_type, status, origin) values {', '.join(accounts)};"
        "insert into iam.staff_roles (account_id, clinic_id, role, origin) values "
        + ", ".join(f"('{people[k]}', '{c}', '{r}', '{origin}')" for k, c, r in staff) + ";"
    )
    w = SimpleNamespace(a=a, b=b, people=people, threads={}, messages={}, origin=origin)
    pa, pb = _u(), _u()
    for pid, clinic in ((pa, a), (pb, b)):
        await conn.execute(
            "insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, origin, idempotency_key) "
            f"values ('{pid}', '{clinic}', {CT}, 1, {CT}, 1, '{origin}', 'p-{pid}')"
        )
    await set_claims(conn, people["nurse_a"])
    for topic in TOPICS:
        w.threads[topic] = _u()
        await conn.execute(
            "insert into msg.threads (id, clinic_id, patient_id, topic, created_by, origin, idempotency_key) "
            f"values ('{w.threads[topic]}', '{a}', '{pa}', '{topic}', '{people['nurse_a']}', '{origin}', 't-{w.threads[topic]}')"
        )
    await set_claims(conn, people["nurse_b"])
    w.thread_b = _u()
    await conn.execute(
        "insert into msg.threads (id, clinic_id, patient_id, topic, created_by, origin, idempotency_key) "
        f"values ('{w.thread_b}', '{b}', '{pb}', 'care', '{people['nurse_b']}', '{origin}', 't-{w.thread_b}')"
    )
    await set_claims(conn, None)
    for topic in TOPICS:
        w.messages[topic] = await add_message(conn, w, w.threads[topic], topic, origin=origin)
    w.message_b = await add_message(conn, w, w.thread_b, "care", clinic=b, origin=origin)
    return w


async def add_rule(conn, w, code, *, phrases=(), patterns=(), status="approved", version=1, clinic="a", origin="test"):
    cid = {"a": w.a, "b": w.b, "global": None}[clinic]
    rid = _u()
    now = datetime.now(UTC)
    meta = json.dumps({"placeholder": rules.PLACEHOLDER} if status == "draft_unreviewed" else {"note": "synthetic test rule"})
    # The table demands two DIFFERENT named approvers for an approved rule, a retired_at for a retired one, the placeholder for a draft.
    approvers = (w.people["doctor_a"], w.people["nurse_a"]) if status == "approved" else (None, None)
    await conn.execute(
        "insert into kb.red_flag_rules (id, clinic_id, rule_code, version_id, status, phrases, patterns, governance_metadata, authored_by, "
        "approved_by_1, approved_by_2, approved_at, retired_at, origin, idempotency_key) "
        "values ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11, $12, $13, $14, $15)",
        rid, cid, code, version, status, list(phrases), list(patterns), meta, w.people["doctor_a"], approvers[0], approvers[1],
        now if status == "approved" else None, now if status == "retired" else None, origin, f"r-{rid}",
    )
    return rid


def call(msg, outcome_="unassessable", profile="clinical", n=0, h=None, prov=None, key="k"):
    p = "null" if prov is None else lit(json.dumps(prov)) + "::jsonb"
    return f"select * from msg.record_screening({lit(str(msg))}::uuid, {lit(outcome_)}, {lit(profile)}, {lit(n)}, {lit(h)}, {p}, {lit(key)})"


def match(rule_id, *, code="r", version=1, status="approved", kind="phrase", index=0, start=0, end=3):
    return {"rule_id": str(rule_id), "rule_code": code, "version_id": version, "rule_status": status, "kind": kind, "index": index, "start": start, "end": end}


async def record(db, w, who, msg, **kw):
    (row,) = await rows_as(db, w.people[who], call(msg, **kw))
    return row


async def screenings(db, msg):
    return [dict(r) for r in await db.fetch("select * from msg.screenings where message_id = $1 order by created_at, id", msg)]


async def tasks(db):
    out = []
    for r in await db.fetch(
        "select * from agents.review_tasks where clinic_id in (select id from iam.clinics where name in ('A', 'B')) order by created_at, id"
    ):
        row = dict(r)
        if isinstance(row.get("provenance"), str):  # asyncpg hands jsonb back as text
            row["provenance"] = json.loads(row["provenance"])
        out.append(row)
    return out


# =====================================================================================================================
# authorisation: who may call the function, and the one error
# =====================================================================================================================

ALLOWED = {
    "nurse_a": set(TOPICS), "doctor_a": set(TOPICS), "worker_a": set(TOPICS), "fd_a": {"appointments", "safety"},
    "admin_a": set(), "auditor_a": set(), "nurse_b": set(), "fd_b": set(), "patient_acct": set(),
}


async def test_record_screening_role_matrix_roles_by_topics_by_clinic(db):
    w = await seed(db)
    for who, topics in ALLOWED.items():
        for topic in TOPICS:
            got = await message_of(db, w.people[who], call(w.messages[topic], key=f"{who}-{topic}"))
            assert got == "ok" if topic in topics else got == DENIED, (who, topic, got)


async def test_the_error_is_identical_for_missing_wrong_clinic_wrong_role_wrong_topic_and_no_identity(db):
    w = await seed(db)
    seen = {
        "missing message": await message_of(db, w.people["nurse_a"], call(_u())),
        "other clinic": await message_of(db, w.people["nurse_b"], call(w.messages["care"])),
        "wrong role": await message_of(db, w.people["admin_a"], call(w.messages["care"])),
        "wrong topic": await message_of(db, w.people["fd_a"], call(w.messages["care"])),
        "no identity": await message_of(db, None, call(w.messages["care"])),
    }
    assert set(seen.values()) == {DENIED}, seen


async def test_app_role_cannot_write_screenings_directly(db):
    w = await seed(db)
    nurse = w.people["nurse_a"]
    out = await attempts(db, nurse, [
        (f"insert into msg.screenings (clinic_id, origin, topic, message_id, outcome, profile, rules_evaluated, rule_set_hash, key_sha256, "
        f"provenance_sha256, patient_reply, screened_by, idempotency_key) values ('{w.a}', 'test', 'care', '{w.messages['care']}', 'clear', "
        f"'clinical', 1, '{ZERO}', '{ZERO}', '{ZERO}', 'not_required', '{nurse}', 'x')"),
        "update msg.screenings set outcome = 'clear'", "delete from msg.screenings", "truncate msg.screenings"])
    assert out == ["err:42501"] * 4


async def test_only_record_screening_is_executable_by_the_app_role_and_no_api_role_can_run_anything(db):
    internal = ["kb.active_red_flag_rules(uuid,text,text)", "msg.deny_screening()", "msg.sanitise_matches(jsonb)", "msg.screenings_guard()",
                "msg.screenings_immutable()", "msg.require_screening()"]
    public = "msg.record_screening(uuid,text,text,integer,text,jsonb,text)"
    assert await db.fetchval("select has_function_privilege('careflow_app', $1, 'EXECUTE')", public)
    for fn in internal:
        assert not await db.fetchval("select has_function_privilege('careflow_app', $1, 'EXECUTE')", fn), fn
    for role in ("anon", "authenticated", "service_role"):
        if await db.fetchval("select exists (select 1 from pg_roles where rolname = $1)", role):
            for fn in [public, *internal]:
                assert not await db.fetchval("select has_function_privilege($1, $2, 'EXECUTE')", role, fn), (role, fn)
            assert not await db.fetchval("select has_table_privilege($1, 'msg.screenings', 'SELECT')", role)


async def test_the_definer_owner_bypasses_forced_rls_so_the_function_can_read_what_it_needs(db):
    w = await seed(db)
    assert await db.fetchval("select rolbypassrls from pg_roles where rolname = current_user")
    row = await record(db, w, "worker_a", w.messages["care"])  # a queue_worker cannot read msg.messages itself
    assert row["replayed"] is False


# =====================================================================================================================
# outcomes, tasks, provenance
# =====================================================================================================================


async def test_a_red_flag_creates_an_immediate_nurse_task_linked_to_the_message(db):
    w = await seed(db)
    rid = await add_rule(db, w, "esc", phrases=["zzalpha"])
    row = await record(db, w, "nurse_a", w.messages["care"], outcome_="red_flag", prov={"matches": [match(rid, code="esc")]})
    (task,) = await tasks(db)
    assert (task["kind"], task["priority"], task["assigned_role"], task["status"], task["source_message_id"]) == (
        "red_flag", "immediate", "nurse", "open", str(w.messages["care"]))
    (s,) = await screenings(db, w.messages["care"])
    assert s["id"] == uuid.UUID(row["screening_id"]) and s["review_task_id"] == task["id"]
    assert (s["patient_reply"], s["screened_by"], s["outcome"]) == ("pending_clinician_template", w.people["nurse_a"], "red_flag")
    assert task["provenance"]["matches"][0]["rule_code"] == "esc" and task["provenance"]["unverified_matches"] is False


async def test_unassessable_creates_a_standard_nurse_task_with_the_placeholder_marker_in_provenance(db):
    w = await seed(db)
    await record(db, w, "nurse_a", w.messages["care"])
    (task,) = await tasks(db)
    assert (task["kind"], task["priority"], task["assigned_role"]) == ("screening_unassessable", "standard", "nurse")
    assert task["provenance"]["placeholder"] == rules.PLACEHOLDER == "# CLINICAL-PLACEHOLDER: needs clinician sign-off"
    assert task["provenance"]["priority_note"] == "pending clinical lead confirmation"
    (s,) = await screenings(db, w.messages["care"])
    assert s["patient_reply"] == "not_required"


async def test_a_clear_has_no_task_and_needs_the_database_to_agree_with_the_rule_set(db):
    w = await seed(db)
    rid = await add_rule(db, w, "c1", phrases=["zzalpha"])
    good = rules.rule_set_hash([{"id": rid, "version_id": 1}])
    await record(db, w, "nurse_a", w.messages["care"], outcome_="clear", n=1, h=good, key="clear-1")
    assert await tasks(db) == []
    (s,) = await screenings(db, w.messages["care"])
    assert (s["outcome"], s["review_task_id"], s["rules_evaluated"], s["rule_set_hash"]) == ("clear", None, 1, good)


@pytest.mark.parametrize("claim", ["count", "hash", "null_count", "null_hash", "bad_hash", "null_key", "empty_key", "profile_null_with_a_wrong_hash"])
async def test_a_clear_is_refused_on_any_wrong_or_missing_claim(db, claim):
    """Every comparison is NULL-safe: a NULL or malformed claim refuses the clear instead of slipping through."""
    w = await seed(db)
    rid = await add_rule(db, w, "c1", phrases=["zzalpha"])
    good = rules.rule_set_hash([{"id": rid, "version_id": 1}])
    kw = {"n": 1, "h": good, "key": "k"}
    kw.update({"count": {"n": 2}, "hash": {"h": ZERO}, "null_count": {"n": None}, "null_hash": {"h": None}, "bad_hash": {"h": "not-hex"},
               "null_key": {"key": None}, "empty_key": {"key": "  "}, "profile_null_with_a_wrong_hash": {"profile": None, "h": ZERO}}[claim])
    got = await message_of(db, w.people["nurse_a"], call(w.messages["care"], outcome_="clear", **kw))
    assert got == "P0001:CLEAR_REFUSED", got
    assert await screenings(db, w.messages["care"]) == []


async def test_a_clear_is_refused_when_no_rule_is_in_force_and_the_stub_does_not_count(db):
    w = await seed(db)  # only the inert draft stub from 0007 exists; clinical uses approved rules only
    got = await message_of(db, w.people["nurse_a"], call(w.messages["care"], outcome_="clear", n=0, h=EMPTY))
    assert got == "P0001:CLEAR_REFUSED"


async def test_a_clear_is_refused_when_an_approved_rule_cannot_be_compiled_by_the_engine(db):
    """The database counts the rule (it has a pattern); Python's engine drops it, so its count is one lower: fail closed."""
    w = await seed(db)
    await add_rule(db, w, "broken", patterns=["("])
    await add_rule(db, w, "fine", phrases=["zzalpha"])
    rows_ = [dict(r) for r in await db.fetch("select id, clinic_id, rule_code, version_id, status, phrases, patterns from kb.red_flag_rules where origin = 'test'")]
    chosen, _ = rules.select_active_versions(rows_, "clinical")
    python_count = sum(1 for r in chosen if rules.compile_rule(r)[0] is not None and rules.compile_rule(r)[0].has_criteria)
    got = await message_of(db, w.people["nurse_a"], call(w.messages["care"], outcome_="clear", n=python_count, h=rules.rule_set_hash(chosen)))
    assert got == "P0001:CLEAR_REFUSED"


async def test_a_clear_after_a_red_flag_is_refused_and_the_view_stays_red_flag(db):
    w = await seed(db)
    rid = await add_rule(db, w, "c1", phrases=["zzalpha"])
    good = rules.rule_set_hash([{"id": rid, "version_id": 1}])
    await record(db, w, "nurse_a", w.messages["care"], outcome_="red_flag")
    assert await message_of(db, w.people["nurse_a"], call(w.messages["care"], outcome_="clear", n=1, h=good, key="k2")) == "P0001:CLEAR_REFUSED"
    assert await db.fetchval("select effective_outcome from msg.message_screening_status where message_id = $1", w.messages["care"]) == "red_flag"


async def test_a_red_flag_after_a_clinical_clear_with_the_same_rule_set_is_recordable(db):
    w = await seed(db)
    rid = await add_rule(db, w, "c1", phrases=["zzalpha"])
    good = rules.rule_set_hash([{"id": rid, "version_id": 1}])
    await record(db, w, "nurse_a", w.messages["care"], outcome_="clear", n=1, h=good, key="a")
    row = await record(db, w, "nurse_a", w.messages["care"], outcome_="red_flag", n=1, h=good, key="b")
    assert row["replayed"] is False
    assert await db.fetchval("select effective_outcome from msg.message_screening_status where message_id = $1", w.messages["care"]) == "red_flag"
    assert len(await tasks(db)) == 1


async def test_unassessable_after_a_red_flag_returns_the_red_flag_and_raises_no_second_task(db):
    w = await seed(db)
    first = await record(db, w, "nurse_a", w.messages["care"], outcome_="red_flag", key="a")
    again = await record(db, w, "nurse_a", w.messages["care"], outcome_="unassessable", key="b")
    assert again["screening_id"] == first["screening_id"] and again["replayed"] is True
    assert again["recorded_outcome"] == "red_flag" and first["recorded_outcome"] == "red_flag"  # the row's own outcome is reported
    assert len(await tasks(db)) == 1


async def test_a_repeat_of_the_same_logical_screening_under_a_new_key_is_a_no_op(db):
    w = await seed(db)
    first = await record(db, w, "nurse_a", w.messages["care"], key="one")
    second = await record(db, w, "nurse_a", w.messages["care"], key="two")
    assert (second["screening_id"], second["replayed"]) == (first["screening_id"], True)
    assert len(await tasks(db)) == 1 and len(await screenings(db, w.messages["care"])) == 1


async def test_a_replay_with_the_same_key_and_parameters_returns_the_same_row(db):
    w = await seed(db)
    first = await record(db, w, "nurse_a", w.messages["care"], key="same")
    again = await record(db, w, "nurse_a", w.messages["care"], key="same")
    assert (again["screening_id"], again["replayed"]) == (first["screening_id"], True)


async def test_the_replay_lookup_compares_every_row_for_the_key(db):
    """Two rows share one key hash (different profiles). Replaying the second must find the second, not stop at the first."""
    w = await seed(db)
    first = await record(db, w, "nurse_a", w.messages["care"], profile="clinical", key="shared")
    second = await record(db, w, "nurse_a", w.messages["care"], profile="demo", key="shared")
    assert second["screening_id"] != first["screening_id"] and second["replayed"] is False
    replay = await record(db, w, "nurse_a", w.messages["care"], profile="demo", key="shared")
    assert (replay["screening_id"], replay["replayed"]) == (second["screening_id"], True)


async def test_key_reuse_with_different_parameters_on_a_red_flag_is_recorded_not_refused(db):
    w = await seed(db)
    await record(db, w, "nurse_a", w.messages["care"], outcome_="unassessable", key="reused")
    row = await record(db, w, "nurse_a", w.messages["care"], outcome_="red_flag", key="reused")
    assert row["replayed"] is False and len(await screenings(db, w.messages["care"])) == 2


async def test_the_key_hash_column_is_not_unique_and_two_clinics_may_use_the_same_caller_key(db):
    w = await seed(db)
    uniques = await db.fetch(
        "select i.indexrelid::regclass::text as idx from pg_index i join pg_attribute a on a.attrelid = i.indrelid and a.attnum = any(i.indkey) "
        "where i.indrelid = 'msg.screenings'::regclass and a.attname = 'key_sha256' and i.indisunique")
    assert uniques == []
    one = await record(db, w, "nurse_a", w.messages["care"], key="same-key")
    other = await record(db, w, "nurse_b", w.message_b, key="same-key")
    assert one["screening_id"] != other["screening_id"] and other["replayed"] is False


async def test_a_unique_violation_on_the_logical_tuple_returns_the_existing_row_for_red_flag_and_unassessable(db):
    w = await seed(db)
    for outcome_ in ("red_flag", "unassessable"):
        msg = w.messages["appointments" if outcome_ == "red_flag" else "safety"]
        first = await record(db, w, "nurse_a", msg, outcome_=outcome_, key="x")
        # same (message, profile, hash, outcome) under a different key and different provenance: the existing row comes back
        again = await record(db, w, "nurse_a", msg, outcome_=outcome_, key="y", prov={"matches": []})
        assert (again["screening_id"], again["replayed"]) == (first["screening_id"], True)


# ---- NULL and unknown inputs never refuse a red flag or an unassessable ---------------------------------------------------------


async def test_null_key_null_provenance_and_unknown_profile_still_record_a_red_flag(db):
    w = await seed(db)
    row = await record(db, w, "nurse_a", w.messages["care"], outcome_="red_flag", profile="prod", n=None, h=None, prov=None, key=None)
    (s,) = await screenings(db, w.messages["care"])
    assert row["replayed"] is False and s["profile"] == "clinical" and s["claimed_values_invalid"] is True
    assert s["claimed_rules_evaluated"] is None and s["claimed_rule_set_hash"] is None
    assert len(await tasks(db)) == 1


@pytest.mark.parametrize("bad", [None, "", "red-flag", "CLEAR", "other"])
async def test_a_null_or_unknown_outcome_is_refused_loudly(db, bad):
    w = await seed(db)
    got = await message_of(db, w.people["nurse_a"], call(w.messages["care"], outcome_=bad))
    assert got.startswith("22023:invalid screening outcome"), got
    assert await screenings(db, w.messages["care"]) == [] and await tasks(db) == []


async def test_the_function_is_not_strict_so_a_null_message_is_a_normal_refusal(db):
    w = await seed(db)
    got = await message_of(db, w.people["nurse_a"], "select * from msg.record_screening(null, 'unassessable', 'clinical', 0, null, null, 'k')")
    assert got == DENIED
    assert not await db.fetchval("select proisstrict from pg_proc where oid = 'msg.record_screening(uuid,text,text,integer,text,jsonb,text)'::regprocedure")


async def test_claimed_values_are_stored_only_when_valid(db):
    w = await seed(db)
    await record(db, w, "nurse_a", w.messages["care"], outcome_="red_flag", n=3, h="a" * 64, key="v")
    (s,) = await screenings(db, w.messages["care"])
    assert (s["claimed_rules_evaluated"], s["claimed_rule_set_hash"], s["claimed_values_invalid"]) == (3, "a" * 64, False)
    assert (s["rules_evaluated"], s["rule_set_hash"]) == (0, EMPTY)  # what the DATABASE computed, not the claim
    w2 = await record(db, w, "nurse_a", w.messages["safety"], outcome_="red_flag", n=-5, h="NOTHEX", key="w")
    (t,) = await screenings(db, w.messages["safety"])
    assert (t["claimed_rules_evaluated"], t["claimed_rule_set_hash"], t["claimed_values_invalid"]) == (None, None, True) and w2["replayed"] is False


# ---- the empty rule set: the state migration 0007 ships in --------------------------------------------------------------------


async def test_unassessable_and_red_flag_are_recordable_with_an_empty_in_force_set(db):
    w = await seed(db)
    for outcome_, topic in (("unassessable", "care"), ("red_flag", "appointments")):
        row = await record(db, w, "nurse_a", w.messages[topic], outcome_=outcome_)
        assert row["replayed"] is False
        (s,) = await screenings(db, w.messages[topic])
        assert (s["rules_evaluated"], s["rule_set_hash"], s["rule_set_unverified"]) == (0, EMPTY, False)


async def test_the_empty_rule_set_hash_equals_sha256_of_nothing(db):
    assert EMPTY == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert await db.fetchval("select encode(sha256(convert_to(coalesce(string_agg('x', E'\\n'), ''), 'UTF8')), 'hex') from (select 1 where false) t") == EMPTY


async def test_a_failing_rule_set_computation_still_records_a_red_flag_and_flags_it(db):
    w = await seed(db)
    await db.execute(
        "create or replace function kb.active_red_flag_rules(p_clinic uuid, p_origin text, p_profile text) "
        "returns table (id uuid, rule_code text, version_id integer, status text, has_criteria boolean) language plpgsql security definer "
        "set search_path = '' as $$ begin raise exception 'boom'; end $$"
    )
    await record(db, w, "nurse_a", w.messages["care"], outcome_="red_flag")
    (s,) = await screenings(db, w.messages["care"])
    assert (s["rule_set_hash"], s["rules_evaluated"], s["rule_set_unverified"]) == (ZERO, 0, True)
    assert len(await tasks(db)) == 1
    got = await message_of(db, w.people["nurse_a"], call(w.messages["safety"], outcome_="clear", n=0, h=ZERO, key="z"))
    assert got == "P0001:CLEAR_REFUSED"


async def test_an_unverified_rule_set_row_can_never_be_a_clear(db):
    w = await seed(db)
    sql = (
        "insert into msg.screenings (clinic_id, origin, topic, message_id, outcome, profile, rules_evaluated, rule_set_hash, "
        f"rule_set_unverified, key_sha256, provenance_sha256, patient_reply, screened_by, idempotency_key) values ('{w.a}', 'test', 'care', "
        f"'{w.messages['care']}', 'clear', 'clinical', 1, '{ZERO}', true, '{ZERO}', '{ZERO}', 'not_required', '{w.people['nurse_a']}', 'u-{_u()}')"
    )
    state, _ = await outcome(db, sql)
    assert state == "23514"


# ---- provenance: the function builds what is stored, and never refuses over it ----------------------------------------------------


async def _stored(db, w, topic="care"):
    (task,) = await tasks(db)
    (s,) = await screenings(db, w.messages[topic])
    return task["provenance"], s


async def test_a_red_flag_with_more_than_fifty_matches_is_recorded_truncated(db):
    w = await seed(db)
    rid = await add_rule(db, w, "many", phrases=["zzalpha"])
    await record(db, w, "nurse_a", w.messages["care"], outcome_="red_flag", prov={"matches": [match(rid, code="many", index=0, start=i, end=i + 1) for i in range(60)]})
    prov, _ = await _stored(db, w)
    assert len(prov["matches"]) == 50 and prov["matches_truncated"] is True


async def test_a_red_flag_with_a_match_on_a_just_superseded_rule_is_recorded_and_flagged_with_the_rule_id_only(db):
    w = await seed(db)
    gone = _u()
    await record(db, w, "nurse_a", w.messages["care"], outcome_="red_flag", prov={"matches": [match(gone, code="old")]})
    prov, _ = await _stored(db, w)
    assert prov["unverified_matches"] is True and prov["matches"] == [{"rule_id": str(gone)}]


async def test_a_red_flag_with_malformed_provenance_is_recorded_with_dropped_items(db):
    w = await seed(db)
    rid = await add_rule(db, w, "ok", phrases=["zzalpha"])
    bad = [match(rid, code="ok"), {"rule_id": "not-a-uuid"}, {**match(rid), "extra": "patient words"}, "text", {**match(rid), "rule_code": "Has Spaces"},
           {**match(rid), "version_id": 10**12}, {**match(rid), "kind": "free text"}]
    await record(db, w, "nurse_a", w.messages["care"], outcome_="red_flag", prov={"matches": bad})
    prov, _ = await _stored(db, w)
    assert len(prov["matches"]) == 1 and prov["dropped_items"] == 6 and "patient words" not in json.dumps(prov)


@pytest.mark.parametrize("shape", [None, "just text", [1, 2], {"matches": "oops"}, {"matches": {"a": 1}}, {"matches": [None, 5, []]}, {}])
async def test_a_red_flag_with_odd_provenance_shapes_is_always_recorded(db, shape):
    w = await seed(db)
    await record(db, w, "nurse_a", w.messages["care"], outcome_="red_flag", prov=shape)
    assert len(await tasks(db)) == 1 and len(await screenings(db, w.messages["care"])) == 1


async def test_a_red_flag_with_empty_matches_is_recorded_as_a_model_escalation_would_be(db):
    w = await seed(db)
    await record(db, w, "nurse_a", w.messages["care"], outcome_="red_flag", prov={"matches": []})
    prov, _ = await _stored(db, w)
    assert prov["matches"] == [] and prov["unverified_matches"] is False


async def test_the_stored_outcome_and_profile_come_from_the_parameters_not_the_provenance(db):
    w = await seed(db)
    await record(db, w, "nurse_a", w.messages["care"], outcome_="red_flag", profile="demo", prov={"outcome": "clear", "profile": "clinical", "matches": []})
    prov, s = await _stored(db, w)
    assert (prov["outcome"], prov["profile"], s["profile"]) == ("red_flag", "demo", "demo")


async def test_a_verified_match_takes_its_rule_code_from_the_database_not_the_caller(db):
    w = await seed(db)
    rid = await add_rule(db, w, "real_code", phrases=["zzalpha"])
    await record(db, w, "nurse_a", w.messages["care"], outcome_="red_flag", prov={"matches": [match(rid, code="real_code")]})
    prov, _ = await _stored(db, w)
    assert prov["matches"][0]["rule_code"] == "real_code" and prov["matches"][0]["rule_status"] == "approved"


# ---- the linked-task trigger (as the owner, for any writer) -----------------------------------------------------------------------


def _screening_insert(w, task_id, outcome_="red_flag"):
    return (
        "insert into msg.screenings (clinic_id, origin, topic, message_id, outcome, profile, rules_evaluated, rule_set_hash, key_sha256, "
        "provenance_sha256, review_task_id, patient_reply, screened_by, idempotency_key) values "
        f"('{w.a}', 'test', 'care', '{w.messages['care']}', '{outcome_}', 'clinical', 0, '{EMPTY}', '{ZERO}', '{ZERO}', {lit(str(task_id))}, "
        f"'{'pending_clinician_template' if outcome_ == 'red_flag' else 'not_required'}', '{w.people['nurse_a']}', 'd-{_u()}')"
    )


async def _task(db, w, *, kind="red_flag", role="nurse", status="open", source=None, priority="immediate"):
    tid = _u()
    extra = ", acknowledged_by, acknowledged_at" if status != "open" else ""
    vals = f", '{w.people['nurse_a']}', now()" if status != "open" else ""
    await db.execute(
        "insert into agents.review_tasks (id, clinic_id, origin, kind, priority, assigned_role, status, source_message_id, provenance, idempotency_key"
        f"{extra}) values ('{tid}', '{w.a}', 'test', '{kind}', '{priority}', '{role}', '{status}', '{source or w.messages['care']}', '{{}}', 'tk-{tid}'{vals})"
    )
    return tid


async def test_the_screenings_trigger_accepts_a_correct_task_and_refuses_each_wrong_one(db):
    w = await seed(db)
    assert (await outcome(db, _screening_insert(w, await _task(db, w))))[0] is None
    w = await seed(db)
    for label, task, outcome_ in (
        ("wrong kind (an unassessable task offered for a red_flag)", await _task(db, w, kind="screening_unassessable", priority="standard"), "red_flag"),
        ("wrong role (kind and outcome match, the task is for a doctor)", await _task(db, w, kind="screening_unassessable", role="doctor", priority="standard"), "unassessable"),
        ("closed task", await _task(db, w, status="acknowledged"), "red_flag"),
        ("wrong source message", await _task(db, w, source=w.messages["safety"]), "red_flag"),
    ):
        state, message = await outcome(db, _screening_insert(w, task, outcome_=outcome_))
        assert state == "23514" and message is not None and "screening link refused" in message, label


async def test_a_screening_cannot_link_another_clinics_task(db):
    w = await seed(db)
    tid = _u()
    await db.execute(
        "insert into agents.review_tasks (id, clinic_id, origin, kind, priority, assigned_role, source_message_id, provenance, idempotency_key) "
        f"values ('{tid}', '{w.b}', 'test', 'red_flag', 'immediate', 'nurse', '{w.messages['care']}', '{{}}', 'x-{tid}')"
    )
    assert (await outcome(db, _screening_insert(w, tid)))[0] == "23503"  # the composite foreign key includes the clinic


async def test_the_task_iff_not_clear_and_reply_iff_red_flag_checks(db):
    w = await seed(db)
    state, _ = await outcome(db, _screening_insert(w, await _task(db, w), outcome_="clear"))
    assert state == "23514"  # a clear never has a task
    base = _screening_insert(w, await _task(db, w, kind="screening_unassessable", priority="standard"), outcome_="unassessable")
    assert (await outcome(db, base.replace("'not_required'", "'pending_clinician_template'")))[0] == "23514"


async def test_screenings_are_append_only_even_for_the_owner(db):
    w = await seed(db)
    await record(db, w, "nurse_a", w.messages["care"])
    for sql in ("update msg.screenings set outcome = 'clear'", "delete from msg.screenings", "truncate msg.screenings"):
        assert (await outcome(db, sql))[0] == "23001", sql


# =====================================================================================================================
# the lock: a row lock the application role cannot take, no advisory lock anywhere
# =====================================================================================================================


async def test_the_function_and_trigger_lock_the_thread_row_and_use_no_advisory_lock(db):
    for fn in ("msg.record_screening(uuid,text,text,integer,text,jsonb,text)", "msg.screenings_guard()"):
        source = (await db.fetchval("select pg_get_functiondef($1::regprocedure)", fn)).lower()
        assert "advisory" not in source, fn
        assert "from msg.threads" in source and "for no key update" in source, fn
        assert "from msg.messages m where m.id = p_message_id for" not in source and "for update" not in source.replace("for no key update", ""), fn


async def test_record_screening_leaves_no_advisory_lock_held(db):
    w = await seed(db)
    await record(db, w, "nurse_a", w.messages["care"])
    assert await db.fetchval("select count(*) from pg_locks where locktype = 'advisory' and pid = pg_backend_pid()") == 0


async def test_a_held_advisory_lock_does_not_affect_a_screening(db):
    """The old design serialised on pg_advisory_xact_lock(ns, hashtext(message_id)). A session holding that exact lock must now be
    irrelevant. (Across sessions this would need committed rows in append-only tables, so the structural tests above are the proof.)"""
    w = await seed(db)
    await db.fetchval("select pg_advisory_xact_lock(1, hashtext($1))", str(w.messages["care"]))
    row = await record(db, w, "nurse_a", w.messages["care"])
    assert row["replayed"] is False


@pytest.mark.parametrize("clause", ["for update", "for no key update", "for share", "for key share"])
@pytest.mark.parametrize("table", ["msg.threads", "msg.messages"])
async def test_the_app_role_cannot_take_a_row_lock_on_threads_or_messages(db, table, clause):
    w = await seed(db)
    refused = await message_of(db, w.people["nurse_a"], f"select 1 from {table} {clause}")
    assert refused.startswith("42501:"), refused


async def test_the_app_role_has_no_column_update_privilege_on_threads_or_messages(db):
    for table in ("msg.threads", "msg.messages"):
        assert not await db.fetchval("select has_any_column_privilege('careflow_app', $1, 'UPDATE')", table)


async def test_the_function_refuses_a_non_read_committed_transaction_for_every_outcome():
    conn = await asyncpg.connect(_dsn(MIGRATOR or ""), statement_cache_size=0, timeout=20)
    try:
        if await conn.fetchval("select to_regprocedure('msg.record_screening(uuid,text,text,integer,text,jsonb,text)') is null"):
            pytest.skip("needs a database where migration 0013 is really applied (this test cannot use the rolled-back transaction)")
        for outcome_ in ("red_flag", "unassessable", "clear"):
            await conn.execute("begin isolation level repeatable read")
            with pytest.raises(asyncpg.PostgresError) as exc:
                await conn.fetch(f"select * from msg.record_screening('{_u()}'::uuid, '{outcome_}', 'clinical', 0, null, null, 'k')")
            assert exc.value.sqlstate == "25000"
            await conn.execute("rollback")
    finally:
        await conn.close()


async def test_an_erased_message_is_refused_with_the_same_error(db):
    w = await seed(db)
    await db.execute(f"update msg.messages set body_ct = null, body_key_version = null, erased_at = now() where id = '{w.messages['care']}'")
    assert await message_of(db, w.people["nurse_a"], call(w.messages["care"], outcome_="red_flag")) == DENIED
    source = (await db.fetchval("select pg_get_functiondef('msg.record_screening(uuid,text,text,integer,text,jsonb,text)'::regprocedure)")).lower()
    assert source.index("for no key update") < source.index("erased_at", source.index("for no key update"))  # erasure is re-read AFTER the lock


# =====================================================================================================================
# the view, the audit trail, the deferred constraint
# =====================================================================================================================


async def _status(db, msg):
    return dict(await db.fetchrow("select * from msg.message_screening_status where message_id = $1", msg))


async def test_the_worst_of_view_ranks_red_flag_over_unassessable_over_clear_and_reports_unscreened(db):
    w = await seed(db)
    assert (await _status(db, w.messages["care"]))["effective_outcome"] == "unscreened"
    rid = await add_rule(db, w, "c1", phrases=["zzalpha"])
    good = rules.rule_set_hash([{"id": rid, "version_id": 1}])
    await record(db, w, "nurse_a", w.messages["care"], outcome_="clear", n=1, h=good, key="a")
    s = await _status(db, w.messages["care"])
    assert (s["effective_outcome"], s["clinical_clear"], s["clinical_clear_rule_set_hash"]) == ("clear", True, good)
    await record(db, w, "nurse_a", w.messages["care"], outcome_="unassessable", profile="demo", key="b")
    s = await _status(db, w.messages["care"])
    assert (s["effective_outcome"], s["clinical_clear"], s["clinical_clear_rule_set_hash"]) == ("unassessable", False, None)
    await record(db, w, "nurse_a", w.messages["care"], outcome_="red_flag", key="c")
    assert (await _status(db, w.messages["care"]))["effective_outcome"] == "red_flag"


async def test_a_demo_clear_is_recorded_for_a_test_origin_but_is_never_a_clinical_clear(db):
    w = await seed(db)
    rid = await add_rule(db, w, "draft", phrases=["zzalpha"], status="draft_unreviewed")
    demo_hash = rules.rule_set_hash([{"id": rid, "version_id": 1}, {"id": await db.fetchval("select id from kb.red_flag_rules where rule_code = 'unauthored_stub'"), "version_id": 1}])
    await record(db, w, "nurse_a", w.messages["care"], outcome_="clear", profile="demo", n=1, h=demo_hash, key="d")
    s = await _status(db, w.messages["care"])
    assert (s["effective_outcome"], s["clinical_clear"]) == ("demo_clear", False)


async def test_a_demo_clear_on_a_live_message_is_refused_and_cannot_be_stored(db):
    w = await seed(db, origin="live")
    got = await message_of(db, w.people["nurse_a"], call(w.messages["care"], outcome_="clear", profile="demo", n=1, h=ZERO))
    assert got == "P0001:CLEAR_REFUSED"
    sql = (
        "insert into msg.screenings (clinic_id, origin, topic, message_id, outcome, profile, rules_evaluated, rule_set_hash, key_sha256, "
        f"provenance_sha256, patient_reply, screened_by, idempotency_key) values ('{w.a}', 'live', 'care', '{w.messages['care']}', 'clear', 'demo', 1, "
        f"'{ZERO}', '{ZERO}', '{ZERO}', 'not_required', '{w.people['nurse_a']}', 'l-{_u()}')"
    )
    assert (await outcome(db, sql))[0] == "23514"


async def test_the_view_is_security_invoker_and_front_desk_sees_no_care_message(db):
    w = await seed(db)
    options = await db.fetchval("select reloptions from pg_class where oid = 'msg.message_screening_status'::regclass")
    assert options is not None and "security_invoker=true" in options
    seen = {r["topic"] for r in await rows_as(db, w.people["fd_a"], "select topic from msg.message_screening_status")}
    assert seen == {"appointments", "safety"}
    assert {r["topic"] for r in await rows_as(db, w.people["nurse_a"], "select topic from msg.message_screening_status")} == set(TOPICS)
    assert await rows_as(db, w.people["worker_a"], "select * from msg.message_screening_status") == []


async def test_screenings_select_policy_matches_the_messages_topics_and_excludes_queue_worker_and_admin(db):
    w = await seed(db)
    for topic in TOPICS:
        await record(db, w, "nurse_a", w.messages[topic], key=topic)
    expect = {"nurse_a": set(TOPICS), "doctor_a": set(TOPICS), "fd_a": {"appointments", "safety"}, "worker_a": set(), "admin_a": set(),
              "auditor_a": set(), "nurse_b": set(), "fd_b": set(), "patient_acct": set()}
    for who, topics in expect.items():
        assert {r["topic"] for r in await rows_as(db, w.people[who], "select topic from msg.screenings")} == topics, who


async def test_the_screened_by_is_the_caller_and_an_audit_row_is_written_with_ids_only(db):
    w = await seed(db)
    row = await record(db, w, "doctor_a", w.messages["care"], outcome_="red_flag")
    (s,) = await screenings(db, w.messages["care"])
    assert s["screened_by"] == w.people["doctor_a"]
    audit = await db.fetchrow("select actor_id, action, resource_type, resource_id, details, clinic_id from audit.log where resource_id = $1", row["screening_id"])
    assert (audit["actor_id"], audit["action"], audit["resource_type"], audit["clinic_id"]) == (w.people["doctor_a"], "msg.screening.recorded", "msg.screenings", w.a)
    assert set(json.loads(audit["details"])) == {"message_id", "outcome", "profile"}


async def test_a_patient_kind_message_cannot_commit_without_a_screening(db):
    w = await seed(db)
    nurse = w.people["nurse_a"]
    mid = _u()
    ins = (
        "insert into msg.messages (id, clinic_id, origin, topic, thread_id, sender_kind, recorded_by, body_ct, body_key_version, idempotency_key) "
        f"values ('{mid}', '{w.a}', 'test', 'care', '{w.threads['care']}', 'patient', '{nurse}', {CT}, 1, 'pk-{mid}')"
    )
    assert await attempts(db, nurse, [ins, "set constraints all immediate"]) == ["ok:1", "err:23514"]


async def test_a_demo_clear_satisfies_the_constraint_for_a_non_live_message(db):
    w = await seed(db)
    nurse = w.people["nurse_a"]
    rid = await add_rule(db, w, "draft", phrases=["zzalpha"], status="draft_unreviewed")
    stub = await db.fetchval("select id from kb.red_flag_rules where rule_code = 'unauthored_stub'")
    demo_hash = rules.rule_set_hash([{"id": rid, "version_id": 1}, {"id": stub, "version_id": 1}])
    mid = _u()
    ins = (
        "insert into msg.messages (id, clinic_id, origin, topic, thread_id, sender_kind, recorded_by, body_ct, body_key_version, idempotency_key) "
        f"values ('{mid}', '{w.a}', 'test', 'care', '{w.threads['care']}', 'patient', '{nurse}', {CT}, 1, 'pk-{mid}')"
    )
    out = await attempts(db, nurse, [ins, call(mid, outcome_="clear", profile="demo", n=1, h=demo_hash, key="d"), "set constraints all immediate"])
    assert out == ["ok:1", "ok:1", "ok:0"]


async def test_front_desk_can_relay_and_screen_a_patient_message_on_an_appointments_thread(db):
    w = await seed(db)
    fd = w.people["fd_a"]
    mid = _u()
    ins = (
        "insert into msg.messages (id, clinic_id, origin, topic, thread_id, sender_kind, recorded_by, body_ct, body_key_version, idempotency_key) "
        f"values ('{mid}', '{w.a}', 'test', 'appointments', '{w.threads['appointments']}', 'patient', '{fd}', {CT}, 1, 'pk-{mid}')"
    )
    assert await attempts(db, fd, [ins, call(mid, outcome_="unassessable"), "set constraints all immediate"]) == ["ok:1", "ok:1", "ok:0"]
    care = (
        "insert into msg.messages (id, clinic_id, origin, topic, thread_id, sender_kind, recorded_by, body_ct, body_key_version, idempotency_key) "
        f"values ('{_u()}', '{w.a}', 'test', 'care', '{w.threads['care']}', 'patient', '{fd}', {CT}, 1, 'pk-{_u()}')"
    )
    assert await attempts(db, fd, [care]) == ["err:42501"]  # front desk may not write a care message at all


async def test_a_patient_kind_message_with_a_screening_satisfies_the_constraint_and_set_immediate_cannot_skip_it(db):
    w = await seed(db)
    nurse = w.people["nurse_a"]
    mid = _u()
    ins = (
        "insert into msg.messages (id, clinic_id, origin, topic, thread_id, sender_kind, recorded_by, body_ct, body_key_version, idempotency_key) "
        f"values ('{mid}', '{w.a}', 'test', 'care', '{w.threads['care']}', 'patient', '{nurse}', {CT}, 1, 'pk-{mid}')"
    )
    out = await attempts(db, nurse, [ins, call(mid), "set constraints all immediate"])
    assert out == ["ok:1", "ok:1", "ok:0"]


async def test_a_rolled_back_savepoint_screening_still_fails_the_commit(db):
    w = await seed(db)
    nurse = w.people["nurse_a"]
    mid = _u()
    await set_claims(db, nurse)
    await db.execute(
        "insert into msg.messages (id, clinic_id, origin, topic, thread_id, sender_kind, recorded_by, body_ct, body_key_version, idempotency_key) "
        f"values ('{mid}', '{w.a}', 'test', 'care', '{w.threads['care']}', 'patient', '{nurse}', {CT}, 1, 'pk-{mid}')"
    )
    await db.execute("savepoint s")
    await db.fetch(call(mid))
    await db.execute("rollback to savepoint s")
    assert (await outcome(db, "set constraints all immediate"))[0] == "23514"
    await set_claims(db, None)


async def test_a_patient_kind_message_on_a_live_clinic_cannot_commit_without_a_screening(db):
    """A live message with no screening fails the deferred constraint. (A demo clear cannot be stored for a live message at all:
    see test_a_demo_clear_on_a_live_message_is_refused_and_cannot_be_stored.)"""
    w = await seed(db, origin="live")
    nurse = w.people["nurse_a"]
    mid = _u()
    await set_claims(db, nurse)
    await db.execute(
        "insert into msg.messages (id, clinic_id, origin, topic, thread_id, sender_kind, recorded_by, body_ct, body_key_version, idempotency_key) "
        f"values ('{mid}', '{w.a}', 'live', 'care', '{w.threads['care']}', 'patient', '{nurse}', {CT}, 1, 'pk-{mid}')"
    )
    await set_claims(db, None)
    assert (await outcome(db, "set constraints all immediate"))[0] == "23514"


# =====================================================================================================================
# review-task policies (M1, M3): front desk and admin keep only unmerge_review
# =====================================================================================================================


async def test_the_review_task_policy_set_is_exactly_the_expected_one(db):
    rows_ = await db.fetch("select policyname, cmd, qual, with_check from pg_policies where schemaname = 'agents' and tablename = 'review_tasks' order by policyname")
    assert [r["policyname"] for r in rows_] == ["review_tasks_insert", "review_tasks_select", "review_tasks_update"]
    for r in rows_:
        if r["policyname"] != "review_tasks_update":
            expr = (r["qual"] or r["with_check"]).lower()
            assert "unmerge_review" in expr and "queue_worker" in expr and "front_desk" in expr and "admin" in expr


async def test_front_desk_and_admin_cannot_read_message_sourced_tasks_but_clinical_staff_and_workers_can(db):
    w = await seed(db)
    await record(db, w, "nurse_a", w.messages["care"], outcome_="red_flag", key="r")
    await record(db, w, "nurse_a", w.messages["safety"], key="u")
    for who, count in (("nurse_a", 2), ("doctor_a", 2), ("worker_a", 2), ("fd_a", 0), ("admin_a", 0), ("auditor_a", 0), ("nurse_b", 0), ("patient_acct", 0)):
        assert len(await rows_as(db, w.people[who], "select kind from agents.review_tasks")) == count, who


async def test_front_desk_cannot_select_a_red_flag_task_through_a_screening_join(db):
    w = await seed(db)
    await record(db, w, "nurse_a", w.messages["appointments"], outcome_="red_flag")
    seen = await rows_as(db, w.people["fd_a"], "select s.review_task_id, t.kind from msg.screenings s left join agents.review_tasks t on t.id = s.review_task_id")
    assert len(seen) == 1 and seen[0]["review_task_id"] is not None and seen[0]["kind"] is None  # the id is visible, the task is not


def _task_insert(w, kind):
    return (
        "insert into agents.review_tasks (clinic_id, origin, kind, priority, assigned_role, source_message_id, provenance, idempotency_key) "
        f"values ('{w.a}', 'test', '{kind}', 'immediate', 'nurse', 'merge:1', '{{}}', 'ti-{_u()}')"
    )


async def test_front_desk_and_admin_can_insert_only_unmerge_review_and_clinical_staff_any_kind(db):
    w = await seed(db)
    for who in ("fd_a", "admin_a"):
        out = await attempts(db, w.people[who], [_task_insert(w, "unmerge_review"), _task_insert(w, "red_flag"), _task_insert(w, "screening_unassessable"), _task_insert(w, "anything_new")])
        assert out == ["ok:1", "err:42501", "err:42501", "err:42501"], who
    for who in ("nurse_a", "doctor_a", "worker_a"):
        assert await attempts(db, w.people[who], [_task_insert(w, "anything_new")]) == ["ok:1"], who
    assert await attempts(db, w.people["auditor_a"], [_task_insert(w, "unmerge_review")]) == ["err:42501"]


async def test_insert_returning_works_for_each_role_after_the_policy_swap(db):
    w = await seed(db)
    for who, kind in (("nurse_a", "red_flag"), ("doctor_a", "red_flag"), ("worker_a", "red_flag"), ("fd_a", "unmerge_review"), ("admin_a", "unmerge_review")):
        assert await attempts(db, w.people[who], [_task_insert(w, kind) + " returning id, kind"]) == ["ok:1"], who


# =====================================================================================================================
# SQL and Python agree on the rule set in force
# =====================================================================================================================


@pytest.mark.parametrize("profile", ["clinical", "demo"])
async def test_the_sql_rule_selector_and_hash_match_the_python_ones(db, profile):
    w = await seed(db)
    await add_rule(db, w, "g_approved", phrases=["zzalpha"], clinic="global")
    await add_rule(db, w, "dup", phrases=["zzbeta"], clinic="a")
    await add_rule(db, w, "dup", phrases=["zzgamma"], clinic="global")           # same code, platform-wide AND clinic: both in force
    await add_rule(db, w, "retired_rule", phrases=["zzdelta"], version=1)
    await add_rule(db, w, "retired_rule", phrases=["zzdelta"], status="retired", version=2)   # newest is retired: withdrawn
    await add_rule(db, w, "drafty", phrases=["zzeps"], version=1)
    await add_rule(db, w, "drafty", phrases=["zzeps"], status="draft_unreviewed", version=2)   # clinical keeps v1, demo uses v2
    await add_rule(db, w, "other_origin", phrases=["zzzeta"], origin="eval")
    await add_rule(db, w, "empty_rule")                                              # in force, no criteria
    await add_rule(db, w, "broken", patterns=["("])                                  # in force, the engine cannot compile it
    rows_ = [dict(r) for r in await db.fetch(
        "select id, clinic_id, rule_code, version_id, status, phrases, patterns from kb.red_flag_rules where origin = 'test' and (clinic_id = $1 or clinic_id is null)", w.a)]
    chosen, _ = rules.select_active_versions(rows_, profile)
    expected_hash = rules.rule_set_hash(chosen)
    expected_count = sum(1 for r in chosen if r["phrases"] or r["patterns"])
    msg = w.messages["care"]
    await record(db, w, "nurse_a", msg, profile=profile)
    (s,) = await screenings(db, msg)
    assert (s["rule_set_hash"], s["rules_evaluated"]) == (expected_hash, expected_count)
    assert expected_count >= 4 and len(chosen) >= 6
