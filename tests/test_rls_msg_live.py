"""RLS, constraints, append-only and the erased-patient guard of migration 0011 (msg.threads, msg.messages,
msg.message_rotation_batches), live.

BUILD-SPEC.md §9.4, §17.1, §17.5, §18.4, §21; CLAUDE.md rules 5, 8, 10. Same harness as the other live suites: ONE never
committed outer transaction on the migrator connection, each test in its own savepoint, rows tagged origin='test', and
everything rolled back at the end, so the project is left as it was. Migration 0011 is executed inside that outer
transaction when it is not applied yet, so the file is verified without being applied. Nothing here commits, so these
tests leave no committed message rows behind (msg.messages is append-only for every role and could not be cleaned up).

All role-matrix statements run as the restricted `careflow_app` role with that identity's claims (never as the migrator),
so row-level security is genuinely exercised. Tests that need the table owner (append-only triggers, CHECK constraints)
run as the migrator, with a real identity's claims set because the erased-patient guard trigger also fires for the owner.

What "denied" means here: an INSERT that fails a policy or the guard trigger is SQLSTATE 42501 ('err:42501'); a read
that no policy admits returns ZERO rows; there is no UPDATE, DELETE or TRUNCATE grant at all.
"""

import contextlib
import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import asyncpg
import pytest
import pytest_asyncio
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
_ENV = dotenv_values(ROOT / ".env")
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or _ENV.get("DATABASE_URL_MIGRATOR")
SQL_0011 = (ROOT / "db" / "migrations" / "0011_msg_threads_messages.sql").read_text(encoding="utf-8")

pytestmark = [
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured"),
]

DENIED = "err:42501"  # insufficient_privilege: RLS WITH CHECK, the guard trigger, or no grant
TOPICS = ("care", "appointments", "safety")
MSG_TABLES = ("msg.threads", "msg.messages", "msg.message_rotation_batches")
CT = b"\x01" * 40  # a plausible ciphertext length (29..40100); never decrypted by these tests


def _u() -> uuid.UUID:
    return uuid.uuid4()


def _dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def bx(data: bytes) -> str:
    return f"'\\x{data.hex()}'::bytea"


HELPERS = """
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


async def attempts(conn, uid, sqls):
    return await conn.fetchval("select pg_temp.try_seq($1::uuid, $2::text[])", uid, sqls)


async def message_of(conn, uid, sql):
    return await conn.fetchval("select pg_temp.try_msg($1::uuid, $2)", uid, sql)


async def rows_as(conn, uid, sql):
    return json.loads(await conn.fetchval("select pg_temp.rows_as($1::uuid, $2)", uid, sql))


async def fails(conn, sql, *args):
    """Run `sql` as the connection's own role (the owner) in a savepoint. Returns the SQLSTATE of the error, or None."""
    await conn.execute("savepoint attempt")
    try:
        await conn.execute(sql, *args)
    except asyncpg.PostgresError as exc:
        await conn.execute("rollback to savepoint attempt")
        return exc.sqlstate
    await conn.execute("release savepoint attempt")
    return None


@contextlib.asynccontextmanager
async def claims(conn, uid):
    """Set a real identity's claims for the owner's statements, so guard triggers and auth.uid() see a caller."""
    await conn.execute("select set_config('request.jwt.claims', $1, true)", json.dumps({"sub": str(uid), "role": "authenticated"}))
    try:
        yield
    finally:
        await conn.execute("select set_config('request.jwt.claims', '', true)")


# ---- shared connection, per-test savepoint -----------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _conn():
    conn = await asyncpg.connect(_dsn(MIGRATOR or ""), statement_cache_size=0, timeout=20)
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute("grant careflow_app to current_user with set true")
        if await conn.fetchval("select to_regclass('msg.threads') is null"):
            await conn.execute(SQL_0011)
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


# ---- seeding -----------------------------------------------------------------------------------------------------------


async def new_patient(conn, clinic, *, erased=False, origin="test"):
    pid = _u()
    if erased:
        await conn.execute(
            "insert into mpi.patients (id, clinic_id, erased_at, origin, idempotency_key) "
            f"values ('{pid}', '{clinic}', now(), '{origin}', 'seed-{pid}')"
        )
    else:
        await conn.execute(
            "insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, origin, idempotency_key) "
            f"values ('{pid}', '{clinic}', {bx(CT)}, 1, {bx(CT)}, 1, '{origin}', 'seed-{pid}')"
        )
    return pid


def thread_sql(clinic, patient, topic, by, *, key=None, origin="test"):
    return (
        "insert into msg.threads (clinic_id, patient_id, topic, created_by, origin, idempotency_key) "
        f"values ('{clinic}', '{patient}', '{topic}', '{by}', '{origin}', '{key or 'th-' + str(_u())}')"
    )


def message_sql(clinic, thread, topic, by, *, kind="staff", key=None, mid=None, origin="test", returning=False):
    return (
        "insert into msg.messages (id, clinic_id, origin, topic, thread_id, sender_kind, recorded_by, body_ct, body_key_version, idempotency_key) "
        f"values ('{mid or _u()}', '{clinic}', '{origin}', '{topic}', '{thread}', '{kind}', '{by}', {bx(CT)}, 1, '{key or 'ms-' + str(_u())}')"
        + (" returning id" if returning else "")
    )


async def seed(conn):
    """Clinic A: one account per role, plus a patient with one thread and one message per topic. Clinic B: a front desk
    and a nurse. A patient ACCOUNT with no staff role."""
    a, b = _u(), _u()
    staff = [
        ("fd_a", a, "front_desk"), ("nurse_a", a, "nurse"), ("doctor_a", a, "doctor"), ("admin_a", a, "admin"),
        ("auditor_a", a, "auditor"), ("owner_a", a, "business_owner"), ("worker_a", a, "queue_worker"),
        ("fd_b", b, "front_desk"), ("nurse_b", b, "nurse"),
    ]
    people = {key: _u() for key, _, _ in staff}
    people["patient_acct"] = _u()
    accounts = [f"('{people[k]}', 'staff', 'active', 'test')" for k, _, _ in staff]
    accounts.append(f"('{people['patient_acct']}', 'patient', 'active', 'test')")
    roles = [f"('{people[k]}', '{c}', '{r}', 'test')" for k, c, r in staff]
    await conn.execute(
        f"insert into iam.clinics (id, name, origin) values ('{a}', 'Clinic A', 'test'), ('{b}', 'Clinic B', 'test');\n"
        f"insert into iam.accounts (id, account_type, status, origin) values {', '.join(accounts)};\n"
        f"insert into iam.staff_roles (account_id, clinic_id, role, origin) values {', '.join(roles)};"
    )
    w = SimpleNamespace(a=a, b=b, people=people, threads={}, messages={})
    w.patient = await new_patient(conn, a)
    async with claims(conn, people["nurse_a"]):  # the erased-patient guard fires for the owner too
        for topic in TOPICS:
            w.threads[topic] = await conn.fetchval(thread_sql(a, w.patient, topic, people["nurse_a"]) + " returning id")
            w.messages[topic] = await conn.fetchval(
                message_sql(a, w.threads[topic], topic, people["nurse_a"], returning=True)
            )
    return w


# =====================================================================================================================
# structure and privileges
# =====================================================================================================================


async def test_forced_rls_on_every_msg_table(db):
    rows = await db.fetch(
        "select oid::regclass::text as t, relrowsecurity, relforcerowsecurity from pg_class "
        "where oid = any($1::regclass[])", list(MSG_TABLES)
    )
    assert {r["t"] for r in rows} == set(MSG_TABLES)
    assert all(r["relrowsecurity"] and r["relforcerowsecurity"] for r in rows)


async def test_app_role_has_no_update_delete_truncate_on_messages_or_threads(db):
    for table in ("msg.messages", "msg.threads", "msg.message_rotation_batches"):
        for priv in ("UPDATE", "DELETE", "TRUNCATE"):
            assert not await db.fetchval("select has_table_privilege('careflow_app', $1, $2)", table, priv), (table, priv)
    columns = await db.fetch("select attname from pg_attribute where attrelid = 'msg.messages'::regclass and attnum > 0 and not attisdropped")
    for c in columns:
        assert not await db.fetchval("select has_column_privilege('careflow_app', 'msg.messages', $1, 'UPDATE')", c["attname"])


async def test_client_cannot_insert_server_set_columns(db):
    w = await seed(db)
    patient = await new_patient(db, w.a)
    nurse = w.people["nurse_a"]
    for column, value in (("created_at", "now()"), ("status", "'closed'"), ("id", f"'{_u()}'")):
        sql = thread_sql(w.a, patient, "care", nurse).replace("(clinic_id,", f"({column}, clinic_id,").replace("values (", f"values ({value}, ", 1)
        assert (await attempts(db, nurse, [sql])) == [DENIED], column
    msg = message_sql(w.a, w.threads["care"], "care", nurse).replace("(id,", "(created_at, erased_at, id,").replace("values (", "values (now(), now(), ", 1)
    assert (await attempts(db, nurse, [msg])) == [DENIED]


async def test_msg_tables_and_functions_not_accessible_to_anon_authenticated_service_role(db):
    for role in ("anon", "authenticated", "service_role"):
        if not await db.fetchval("select exists (select 1 from pg_roles where rolname = $1)", role):
            continue
        for table in MSG_TABLES:
            for priv in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                assert not await db.fetchval("select has_table_privilege($1, $2, $3)", role, table, priv), (role, table, priv)
        assert not await db.fetchval("select has_schema_privilege($1, 'msg', 'USAGE')", role)
        for fn in await db.fetch("select oid::regprocedure::text as f from pg_proc where pronamespace = 'msg'::regnamespace"):
            assert not await db.fetchval("select has_function_privilege($1, $2, 'EXECUTE')", role, fn["f"]), (role, fn["f"])


async def test_erasure_guard_and_trigger_functions_are_not_executable_by_the_app_role(db):
    fns = await db.fetch("select oid::regprocedure::text as f from pg_proc where pronamespace = 'msg'::regnamespace")
    assert len(fns) >= 5
    for fn in fns:
        assert not await db.fetchval("select has_function_privilege('careflow_app', $1, 'EXECUTE')", fn["f"]), fn["f"]


async def test_owner_bypasses_forced_rls_for_the_erasure_guard(db):
    """The guard is SECURITY DEFINER and relies on the owner bypassing FORCE RLS, exactly as iam.has_clinic_role does."""
    assert await db.fetchval("select rolbypassrls from pg_roles where rolname = current_user")
    w = await seed(db)
    patient = await new_patient(db, w.a)
    assert (await attempts(db, w.people["nurse_a"], [thread_sql(w.a, patient, "care", w.people["nurse_a"])])) == ["ok:1"]


async def test_message_rotation_ledger_unreachable_by_the_app_role(db):
    w = await seed(db)
    sql_select = "select * from msg.message_rotation_batches"
    sql_insert = (
        "insert into msg.message_rotation_batches (clinic_id, origin, from_version, to_version, messages_rotated, first_message_id, "
        f"last_message_id, idempotency_key) values ('{w.a}', 'test', 1, 2, 1, '{_u()}', '{_u()}', 'x-{_u()}')"
    )
    for who in ("fd_a", "nurse_a", "doctor_a", "admin_a", "worker_a"):
        assert (await attempts(db, w.people[who], [sql_select, sql_insert])) == [DENIED, DENIED], who


# =====================================================================================================================
# reads
# =====================================================================================================================


async def _topics_seen(db, uid, table):
    return {r["topic"] for r in await rows_as(db, uid, f"select topic from {table}")}


async def test_nurse_and_doctor_read_every_topic(db):
    w = await seed(db)
    for who in ("nurse_a", "doctor_a"):
        for table in ("msg.threads", "msg.messages"):
            assert await _topics_seen(db, w.people[who], table) == set(TOPICS), (who, table)


async def test_front_desk_reads_only_appointments_and_safety(db):
    w = await seed(db)
    for table in ("msg.threads", "msg.messages"):
        assert await _topics_seen(db, w.people["fd_a"], table) == {"appointments", "safety"}, table


async def test_roles_without_msg_access_and_other_clinics_read_nothing(db):
    w = await seed(db)
    for who in ("admin_a", "auditor_a", "owner_a", "worker_a", "patient_acct", "fd_b", "nurse_b"):
        for table in ("msg.threads", "msg.messages", "msg.message_rotation_batches"):
            sql = f"select * from {table}"
            out = await attempts(db, w.people[who], [sql])
            assert out in (["ok:0"], [DENIED]), (who, table, out)
            if out == ["ok:0"]:
                assert await rows_as(db, w.people[who], sql) == []


async def test_no_identity_reads_or_writes_nothing(db):
    w = await seed(db)
    out = await attempts(
        db, None,
        ["select * from msg.threads", "select * from msg.messages",
         thread_sql(w.a, w.patient, "care", w.people["nurse_a"]), message_sql(w.a, w.threads["care"], "care", w.people["nurse_a"])],
    )
    assert out == ["ok:0", "ok:0", DENIED, DENIED]


# =====================================================================================================================
# writes: threads
# =====================================================================================================================

ALLOWED_THREAD_TOPICS = {
    "nurse_a": set(TOPICS), "doctor_a": set(TOPICS), "fd_a": {"appointments"},
    "admin_a": set(), "auditor_a": set(), "owner_a": set(), "worker_a": set(),
    "fd_b": set(), "nurse_b": set(), "patient_acct": set(),
}


async def test_thread_insert_matrix_roles_by_topics(db):
    w = await seed(db)
    for who, allowed in ALLOWED_THREAD_TOPICS.items():
        patient = await new_patient(db, w.a)  # one thread per (patient, topic): a fresh patient per role
        uid = w.people[who]
        out = await attempts(db, uid, [thread_sql(w.a, patient, t, uid) for t in TOPICS])
        assert out == [("ok:1" if t in allowed else DENIED) for t in TOPICS], who


async def test_nurse_and_doctor_can_create_threads_and_messages_despite_front_desk_only_patient_update_policy(db):
    """The erased-patient guard locks the patient row FOR SHARE; the only UPDATE policy on mpi.patients is front-desk-only,
    so a caller-run lock would fail for nurse and doctor. The guard is a definer function, so they succeed."""
    w = await seed(db)
    for who in ("nurse_a", "doctor_a"):
        patient = await new_patient(db, w.a)
        uid = w.people[who]
        assert (await attempts(db, uid, [thread_sql(w.a, patient, "care", uid)])) == ["ok:1"], who
        thread = await db.fetchval("select id from msg.threads where patient_id = $1 and topic = 'care'", patient)
        assert thread is not None
        assert (await attempts(db, uid, [message_sql(w.a, thread, "care", uid)])) == ["ok:1"], who


async def test_insert_returning_works_for_each_role_that_may_write(db):
    w = await seed(db)
    out = await attempts(db, w.people["fd_a"], [message_sql(w.a, w.threads["appointments"], "appointments", w.people["fd_a"], returning=True)])
    assert out == ["ok:1"]
    for who in ("nurse_a", "doctor_a"):
        uid = w.people[who]
        patient = await new_patient(db, w.a)
        assert (await attempts(db, uid, [thread_sql(w.a, patient, "safety", uid) + " returning id, created_at, status"])) == ["ok:1"], who


async def test_thread_created_by_must_be_the_caller(db):
    w = await seed(db)
    patient = await new_patient(db, w.a)
    out = await attempts(db, w.people["nurse_a"], [thread_sql(w.a, patient, "care", w.people["doctor_a"])])
    assert out == [DENIED]


async def test_one_thread_per_patient_and_topic(db):
    w = await seed(db)
    out = await attempts(db, w.people["nurse_a"], [thread_sql(w.a, w.patient, "care", w.people["nurse_a"])])
    assert out == ["err:23505"]


async def test_thread_refused_for_an_erased_patient(db):
    w = await seed(db)
    erased = await new_patient(db, w.a, erased=True)
    assert (await attempts(db, w.people["nurse_a"], [thread_sql(w.a, erased, "care", w.people["nurse_a"])])) == [DENIED]


async def test_thread_patient_must_share_clinic_and_origin(db):
    w = await seed(db)
    foreign = await new_patient(db, w.b)
    nurse = w.people["nurse_a"]
    out = await attempts(
        db, nurse,
        [thread_sql(w.a, foreign, "care", nurse),                       # patient of another clinic, claimed clinic A
         thread_sql(w.a, await new_patient(db, w.a), "care", nurse, origin="eval")],  # origin differs from the patient's
    )
    assert out == [DENIED, DENIED]


async def test_unknown_topic_is_refused(db):
    w = await seed(db)
    async with claims(db, w.people["nurse_a"]):
        patient = await new_patient(db, w.a)
        assert await fails(db, thread_sql(w.a, patient, "readiness", w.people["nurse_a"])) == "23514"
        assert await fails(db, message_sql(w.a, w.threads["care"], "readiness", w.people["nurse_a"])) == "23514"


# =====================================================================================================================
# writes: messages
# =====================================================================================================================


async def test_message_insert_matrix_roles_by_topics(db):
    w = await seed(db)
    for who, allowed in ALLOWED_THREAD_TOPICS.items():
        uid = w.people[who]
        out = await attempts(db, uid, [message_sql(w.a, w.threads[t], t, uid) for t in TOPICS])
        assert out == [("ok:1" if t in allowed else DENIED) for t in TOPICS], who


async def test_only_the_staff_sender_kind_is_accepted_in_0011(db):
    w = await seed(db)
    nurse = w.people["nurse_a"]
    out = await attempts(
        db, nurse,
        [message_sql(w.a, w.threads["care"], "care", nurse, kind="patient"),
         message_sql(w.a, w.threads["care"], "care", nurse, kind="system"),
         message_sql(w.a, w.threads["care"], "care", nurse, kind="staff")],
    )
    assert out == [DENIED, DENIED, "ok:1"]


async def test_message_recorded_by_must_be_the_caller(db):
    w = await seed(db)
    out = await attempts(db, w.people["nurse_a"], [message_sql(w.a, w.threads["care"], "care", w.people["doctor_a"])])
    assert out == [DENIED]


async def test_message_topic_must_match_its_thread(db):
    w = await seed(db)
    nurse = w.people["nurse_a"]
    out = await attempts(db, nurse, [message_sql(w.a, w.threads["safety"], "care", nurse)])  # thread is safety, row says care
    assert out == ["err:23503"]


async def test_message_clinic_and_origin_must_match_its_thread(db):
    w = await seed(db)
    nurse_b = w.people["nurse_b"]
    nurse = w.people["nurse_a"]
    assert (await attempts(db, nurse_b, [message_sql(w.b, w.threads["care"], "care", nurse_b)])) == [DENIED]   # thread belongs to clinic A
    assert (await attempts(db, nurse, [message_sql(w.a, w.threads["care"], "care", nurse, origin="eval")])) == [DENIED]


async def test_message_refused_in_the_thread_of_an_erased_patient(db):
    w = await seed(db)
    nurse = w.people["nurse_a"]
    patient = await new_patient(db, w.a)
    async with claims(db, nurse):
        thread = await db.fetchval(thread_sql(w.a, patient, "care", nurse) + " returning id")
    await db.execute(
        "update mpi.patients set erased_at = now(), name_ct = null, name_key_version = null, dob_ct = null, dob_key_version = null "
        f"where id = '{patient}'"
    )
    assert (await attempts(db, nurse, [message_sql(w.a, thread, "care", nurse)])) == [DENIED]


async def test_a_thread_replay_with_the_same_key_inserts_nothing(db):
    w = await seed(db)
    nurse = w.people["nurse_a"]
    patient = await new_patient(db, w.a)
    key = f"thread-replay-{_u()}"
    first = thread_sql(w.a, patient, "care", nurse, key=key)
    replay = first + " on conflict (idempotency_key) do nothing"
    assert (await attempts(db, nurse, [first, replay])) == ["ok:1", "ok:0"]
    assert await db.fetchval("select count(*) from msg.threads where patient_id = $1", patient) == 1


async def test_duplicate_idempotency_key_and_message_id_collisions_are_not_silent(db):
    """The key conflict is the replay signal (ON CONFLICT (idempotency_key) DO NOTHING inserts nothing); a message ID
    collision under a different key is an ERROR, never a silent no-op."""
    w = await seed(db)
    nurse = w.people["nurse_a"]
    key, mid = f"dup-{_u()}", _u()
    first = message_sql(w.a, w.threads["care"], "care", nurse, key=key, mid=mid)
    replay = message_sql(w.a, w.threads["care"], "care", nurse, key=key).replace("returning id", "") + " on conflict (idempotency_key) do nothing"
    collision = message_sql(w.a, w.threads["care"], "care", nurse, mid=mid) + " on conflict (idempotency_key) do nothing"
    out = await attempts(db, nurse, [first, replay, collision, first.replace(str(mid), str(_u()))])
    assert out == ["ok:1", "ok:0", "err:23505", "err:23505"]


# =====================================================================================================================
# the erased-patient guard gives no signal (same error as a real RLS denial)
# =====================================================================================================================


async def test_erasure_guard_error_matches_a_real_rls_denial_sqlstate_and_text(db):
    w = await seed(db)
    patient = await new_patient(db, w.a)
    real = await message_of(db, w.people["fd_a"], thread_sql(w.a, patient, "care", w.people["fd_a"]))   # policy WITH CHECK denial
    guard = await message_of(db, w.people["admin_a"], thread_sql(w.a, patient, "care", w.people["admin_a"]))  # guard trigger denial
    assert real == guard == '42501:new row violates row-level security policy for table "threads"'


async def test_erasure_guard_gives_no_cross_clinic_existence_signal(db):
    w = await seed(db)
    live_a, erased_a = await new_patient(db, w.a), await new_patient(db, w.a, erased=True)
    nurse_b, nurse_a, admin_a = w.people["nurse_b"], w.people["nurse_a"], w.people["admin_a"]
    seen = {
        "role in B, patient of A, claimed B": await message_of(db, nurse_b, thread_sql(w.b, live_a, "care", nurse_b)),
        "role in B, no such patient": await message_of(db, nurse_b, thread_sql(w.b, _u(), "care", nurse_b)),
        "role in A, erased patient": await message_of(db, nurse_a, thread_sql(w.a, erased_a, "care", nurse_a)),
        "wrong role, live patient": await message_of(db, admin_a, thread_sql(w.a, live_a, "care", admin_a)),
    }
    assert len(set(seen.values())) == 1, seen
    assert next(iter(seen.values())).startswith("42501:")


async def test_message_guard_resolves_patient_through_the_thread_in_the_claimed_clinic(db):
    w = await seed(db)
    nurse_b = w.people["nurse_b"]
    other_clinic_thread = await message_of(db, nurse_b, message_sql(w.b, w.threads["care"], "care", nurse_b))
    no_such_thread = await message_of(db, nurse_b, message_sql(w.b, _u(), "care", nurse_b))
    assert other_clinic_thread == no_such_thread and other_clinic_thread.startswith("42501:")


async def test_thread_insert_blocks_while_a_patient_erasure_holds_the_row_lock():
    """The guard's FOR SHARE (not only the foreign key's FOR KEY SHARE, which a non-key UPDATE does not conflict with) is
    what makes a concurrent erasure and a concurrent insert wait for each other. This needs real concurrency, so it is the
    ONE test here that commits: it creates a throwaway clinic, nurse and patient (origin 'test'), never commits a thread or
    a message, and deletes everything it created in `finally`. Connection `erasure` holds FOR NO KEY UPDATE on the patient,
    which is the lock an `update mpi.patients set erased_at = ...` takes; the insert must wait for it."""
    url = _dsn(MIGRATOR or "")
    setup = await asyncpg.connect(url, statement_cache_size=0, timeout=20)
    erasure = await asyncpg.connect(url, statement_cache_size=0, timeout=20)
    writer = await asyncpg.connect(url, statement_cache_size=0, timeout=20)
    clinic, nurse, patient = _u(), _u(), _u()
    try:
        await setup.execute(
            f"insert into iam.clinics (id, name, origin) values ('{clinic}', 'Lock test', 'test');\n"
            f"insert into iam.accounts (id, account_type, status, origin) values ('{nurse}', 'staff', 'active', 'test');\n"
            f"insert into iam.staff_roles (account_id, clinic_id, role, origin) values ('{nurse}', '{clinic}', 'nurse', 'test');\n"
            "insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, origin, idempotency_key) "
            f"values ('{patient}', '{clinic}', {bx(CT)}, 1, {bx(CT)}, 1, 'test', 'lock-{patient}')"
        )
        await erasure.execute("begin")
        await erasure.execute("select 1 from mpi.patients where id = $1 for no key update", patient)
        await writer.execute("begin")
        await writer.execute("select set_config('request.jwt.claims', $1, true)", json.dumps({"sub": str(nurse), "role": "authenticated"}))
        await writer.execute("set local lock_timeout = '1500ms'")
        insert = thread_sql(clinic, patient, "care", nurse)
        with pytest.raises(asyncpg.exceptions.LockNotAvailableError):  # SQLSTATE 55P03: the insert WAITED for the erasure
            await writer.execute(insert)
        await writer.execute("rollback")
        # Control: once the erasure transaction is gone the same insert goes through (and is rolled back, never committed).
        await erasure.execute("rollback")
        await writer.execute("begin")
        await writer.execute("select set_config('request.jwt.claims', $1, true)", json.dumps({"sub": str(nurse), "role": "authenticated"}))
        await writer.execute(insert)
        await writer.execute("rollback")
    finally:
        for conn in (writer, erasure):
            with contextlib.suppress(Exception):
                await conn.execute("rollback")
        await setup.execute(
            f"delete from mpi.patients where id = '{patient}';\n"
            f"delete from iam.staff_roles where clinic_id = '{clinic}';\n"
            f"delete from iam.accounts where id = '{nurse}';\n"
            f"delete from iam.clinics where id = '{clinic}';"
        )
        for conn in (setup, erasure, writer):
            await conn.close()


# =====================================================================================================================
# CHECK constraints, append-only, erasure and rotation transitions (as the table owner)
# =====================================================================================================================


async def test_body_and_erased_at_check_constraint(db):
    w = await seed(db)
    nurse = w.people["nurse_a"]
    async with claims(db, nurse):
        base = message_sql(w.a, w.threads["care"], "care", nurse)
        too_short = base.replace(bx(CT), bx(b"\x01" * 28))
        too_long = base.replace(bx(CT), bx(b"\x01" * 40101))
        no_body = base.replace(bx(CT), "null")
        erased_with_body = base.replace("body_key_version, idempotency_key)", "body_key_version, erased_at, idempotency_key)").replace(", 1, '", ", 1, now(), '", 1)
        null_version = base.replace(", 1, '", ", null, '", 1)  # a live body with NO key version would be stranded: refused
        for sql in (too_short, too_long, no_body, erased_with_body, null_version):
            assert await fails(db, sql) == "23514", sql[-80:]
        # The bounds are inclusive. Each valid row gets its own message id and key.
        smallest = message_sql(w.a, w.threads["care"], "care", nurse).replace(bx(CT), bx(b"\x01" * 29))
        largest = message_sql(w.a, w.threads["care"], "care", nurse).replace(bx(CT), bx(b"\x01" * 40100))
        assert await fails(db, smallest) is None
        assert await fails(db, largest) is None


async def test_append_only_trigger_blocks_edits_even_for_the_owner(db):
    w = await seed(db)
    mid = w.messages["care"]
    assert await fails(db, f"update msg.messages set topic = 'safety' where id = '{mid}'") == "23001"
    assert await fails(db, f"update msg.messages set recorded_by = '{w.people['doctor_a']}' where id = '{mid}'") == "23001"
    assert await fails(db, f"update msg.messages set created_at = now() where id = '{mid}'") == "23001"
    assert await fails(db, f"delete from msg.messages where id = '{mid}'") == "23001"
    assert await fails(db, "truncate msg.messages") == "23001"


async def test_erasure_transition_is_the_only_edit_and_changes_nothing_else(db):
    w = await seed(db)
    mid = w.messages["care"]
    erase = "body_ct = null, body_key_version = null, erased_at = now()"
    assert await fails(db, f"update msg.messages set {erase}, created_at = now() where id = '{mid}'") == "23001"   # changes something else
    assert await fails(db, f"update msg.messages set erased_at = now() where id = '{mid}'") == "23001"           # BEFORE triggers run before CHECKs: the body must go in the same step
    assert await fails(db, f"update msg.messages set {erase} where id = '{mid}'") is None
    row = await db.fetchrow("select body_ct, body_key_version, erased_at from msg.messages where id = $1", mid)
    assert row["body_ct"] is None and row["body_key_version"] is None and row["erased_at"] is not None


async def test_erasure_is_irreversible(db):
    w = await seed(db)
    mid = w.messages["care"]
    assert await fails(db, f"update msg.messages set body_ct = null, body_key_version = null, erased_at = now() where id = '{mid}'") is None
    assert await fails(db, f"update msg.messages set body_ct = {bx(CT)}, body_key_version = 1, erased_at = null where id = '{mid}'") == "23001"
    assert await fails(db, f"update msg.messages set body_ct = {bx(CT)}, body_key_version = 2 where id = '{mid}'") in ("23001", "23514")


async def test_rotation_transition_requires_a_new_key_version_and_changes_nothing_else(db):
    w = await seed(db)
    mid = w.messages["care"]
    other = b"\x02" * 41
    assert await fails(db, f"update msg.messages set body_ct = {bx(other)} where id = '{mid}'") == "23001"                       # same version
    assert await fails(db, f"update msg.messages set body_ct = {bx(other)}, body_key_version = 2, created_at = now() where id = '{mid}'") == "23001"
    assert await fails(db, f"update msg.messages set body_ct = {bx(other)}, body_key_version = 2 where id = '{mid}'") is None
    assert await db.fetchval("select body_key_version from msg.messages where id = $1", mid) == 2


async def test_append_only_trigger_covers_a_column_added_later(db):
    """'Nothing else changes' is judged on the whole row, so a column added later is immutable by default."""
    w = await seed(db)
    await db.execute("alter table msg.messages add column later_note text")
    mid = w.messages["care"]
    assert await fails(db, f"update msg.messages set later_note = 'x' where id = '{mid}'") == "23001"
    assert await fails(db, f"update msg.messages set later_note = 'x', body_ct = {bx(b'z' * 41)}, body_key_version = 2 where id = '{mid}'") == "23001"
