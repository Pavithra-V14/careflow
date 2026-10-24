"""RLS, constraints and the no-double-booking guarantee of migration 0002 (mpi + sched), live.

BUILD-SPEC.md §17.1, §17.2, §18.4, §21; CLAUDE.md rule 8. All role-matrix queries run as the
restricted `careflow_app` role (never as the migrator), so RLS is genuinely exercised.

Two kinds of test, both against the real hosted dev/CI project:

* Rolled-back tests share ONE connection and ONE outer transaction (that is never committed); each test
  runs inside its own savepoint, which is rolled back afterwards. If 0002 has not been applied yet it
  is executed in that outer transaction first, so the migration is verified without being applied.
* Concurrency tests need several real sessions (a session cannot see another's uncommitted DDL), so
  they run only once 0002 is applied. They commit a small fixture tagged origin='test' and delete it
  again in teardown.

Latency: the project is hosted, so every round trip is expensive. The suite therefore (1) connects once
per module, (2) seeds with one multi-statement round trip, and (3) runs a whole role matrix server-side
in ONE query through two temporary functions: `pg_temp.try_seq(uid, sqls)` runs statements as
careflow_app with that identity's claims, each in its own sub-transaction, and returns each outcome as
'ok:<rowcount>' or 'err:<sqlstate>'; `pg_temp.rows_as(uid, sql)` returns the rows a query yields for
that identity as JSON. The assertions are the same ones a one-statement-per-round-trip loop would make.

What "denied" means here, stated precisely: the app role HAS select/insert/update on these tables (on mpi.patients,
since migration 0012, INSERT on every column but account_id and UPDATE on seven named columns only), and
row-level security decides the rows. So a role with no policy reads ZERO rows (not an error), a write
it is not allowed fails with "new row violates row-level security policy" (SQLSTATE 42501), and an
update it is not allowed matches zero rows ('ok:0'). DELETE has no grant at all and fails with 42501.
"""

import asyncio
import contextlib
import json
import os
import uuid
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio
from dotenv import dotenv_values

from tests.bounds import bounded
from tests.db_helpers import ensure_app_role

ROOT = Path(__file__).resolve().parent.parent
_ENV = dotenv_values(ROOT / ".env")
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or _ENV.get("DATABASE_URL_MIGRATOR")
APP = os.environ.get("DATABASE_URL_APP") or _ENV.get("DATABASE_URL_APP")
SQL_0002 = (ROOT / "db" / "migrations" / "0002_mpi_and_scheduling.sql").read_text(encoding="utf-8")
SQL_0012 = (ROOT / "db" / "migrations" / "0012_patient_account_binding.sql").read_text(encoding="utf-8")

pytestmark = [
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured"),
]

CLINICAL_READERS = ("front_desk_a", "nurse_a", "doctor_a")
NO_CASE_ACCESS = ("admin_a", "auditor_a", "owner_a")
T0 = "2031-03-03 10:00+00"
RLS_DENIED = "err:42501"  # insufficient_privilege (RLS WITH CHECK failed, or no grant at all)
EXCLUSION = "err:23P01"  # exclusion_violation


def _u() -> uuid.UUID:
    return uuid.uuid4()


def _dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def bx(data: bytes) -> str:
    return f"'\\x{data.hex()}'::bytea"


# ---- server-side helpers ---------------------------------------------------------------------------

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
    """Run `sqls` in order as careflow_app with `uid`'s claims; each statement is isolated, so one
    failure does not affect the next. Returns ['ok:<rows>' | 'err:<sqlstate>', ...]."""
    return await conn.fetchval("select pg_temp.try_seq($1::uuid, $2::text[])", uid, sqls)


async def try_each(conn, uids, sql):
    """The same statement attempted by each identity, all in one round trip. Returns {uid: outcome}."""
    rows = await conn.fetch(
        "select (pg_temp.try_seq(u, array[$2]::text[]))[1] as r from unnest($1::uuid[]) with ordinality as t(u, n) order by n",
        list(uids), sql,
    )
    return {uid: r["r"] for uid, r in zip(uids, rows, strict=True)}


async def rows_each(conn, uids, sql):
    """The rows `sql` returns for each identity, all in one round trip. Returns {uid: [row dicts]}."""
    rows = await conn.fetch(
        "select pg_temp.rows_as(u, $2) as r from unnest($1::uuid[]) with ordinality as t(u, n) order by n",
        list(uids), sql,
    )
    return {uid: json.loads(r["r"]) for uid, r in zip(uids, rows, strict=True)}


# ---- shared connection, per-test savepoint ---------------------------------------------------------


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _conn():
    conn = await asyncpg.connect(_dsn(MIGRATOR or ""), statement_cache_size=0, timeout=20)
    tx = conn.transaction()
    await tx.start()
    try:
        # The migrator may not SET ROLE to careflow_app by default; grant that inside this
        # transaction only (it is never committed).
        await ensure_app_role(conn)
        if await conn.fetchval("select to_regclass('mpi.patients') is null"):
            await conn.execute(SQL_0002)
        # The column privileges these tests describe are 0012's: load it inside this never-committed transaction if needed.
        if await conn.fetchval("select to_regprocedure('mpi.guard_patient_account()') is null"):
            await conn.execute(SQL_0012)
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


# ---- seeding (one round trip each) -----------------------------------------------------------------


async def seed(conn):
    """Clinics A and B. Clinic A: one account per role. Clinic B: a front desk. Two patient accounts."""
    a, b = _u(), _u()
    staff = [
        ("front_desk_a", a, "front_desk"),
        ("nurse_a", a, "nurse"),
        ("doctor_a", a, "doctor"),
        ("admin_a", a, "admin"),
        ("auditor_a", a, "auditor"),
        ("owner_a", a, "business_owner"),
        ("front_desk_b", b, "front_desk"),
    ]
    people = {key: _u() for key, _, _ in staff}
    people["patient_1"], people["patient_2"] = _u(), _u()
    accounts = [f"('{people[k]}', 'staff', 'active', 'test')" for k, _, _ in staff]
    accounts += [f"('{people[k]}', 'patient', 'active', 'test')" for k in ("patient_1", "patient_2")]
    roles = [f"('{people[k]}', '{c}', '{r}', 'test')" for k, c, r in staff]
    await conn.execute(
        f"insert into iam.clinics (id, name, origin) values ('{a}', 'Clinic A', 'test'), ('{b}', 'Clinic B', 'test');\n"
        f"insert into iam.accounts (id, account_type, status, origin) values {', '.join(accounts)};\n"
        f"insert into iam.staff_roles (account_id, clinic_id, role, origin) values {', '.join(roles)};"
    )
    return a, b, people


CT = {"a1": b"\x01cipher-name-a1", "a2": b"\x01cipher-name-a2", "b1": b"\x01cipher-name-b"}
TOKEN = bx(b"\xaa" * 32)
TOKEN_B = bx(b"\xbb" * 32)


async def seed_patients(conn, a, b, people):
    spec = [("a1", a, people["patient_1"]), ("a2", a, people["patient_2"]), ("b1", b, None)]
    ids = {key: _u() for key, _, _ in spec}
    patients, tokens = [], []
    for key, clinic, account in spec:
        acct = f"'{account}'" if account else "null"
        patients.append(
            f"('{ids[key]}', '{clinic}', {acct}, {bx(CT[key])}, 1, {bx(CT[key])}, 1, {bx(CT[key])}, 1, 'test', 'seed-{key}-{_u()}')"
        )
        tokens.append(f"('{ids[key]}', '{clinic}', 'phone', {TOKEN}, 1, 'test')")
    await conn.execute(
        "insert into mpi.patients (id, clinic_id, account_id, name_ct, name_key_version, dob_ct, dob_key_version, "
        f"phone_ct, phone_key_version, origin, idempotency_key) values {', '.join(patients)};\n"
        "insert into mpi.identity_tokens (patient_id, clinic_id, kind, token, key_version, origin) "
        f"values {', '.join(tokens)};"
    )
    return ids


def new_patient_sql(clinic):
    return (
        "insert into mpi.patients (clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, origin, idempotency_key) "
        f"values ('{clinic}', {bx(b'n')}, 1, {bx(b'd')}, 1, 'test', 'new-{_u()}')"
    )


# ---- 1. who can read patient ciphertext ------------------------------------------------------------


async def test_clinical_staff_read_their_clinics_ciphertext_only(db):
    a, b, p = await seed(db)
    ids = await seed_patients(db, a, b, p)
    uids = [p[w] for w in CLINICAL_READERS]
    patients = await rows_each(db, uids, "select id, name_ct, name_key_version from mpi.patients")
    tokens = await rows_each(db, uids, "select patient_id from mpi.identity_tokens")
    for uid in uids:
        rows = patients[uid]
        assert {r["id"] for r in rows} == {str(ids["a1"]), str(ids["a2"])}  # clinic B's patient is invisible
        assert {r["name_ct"] for r in rows} == {f"\\x{CT['a1'].hex()}", f"\\x{CT['a2'].hex()}"}
        assert all(r["name_key_version"] == 1 for r in rows)
        assert {r["patient_id"] for r in tokens[uid]} == {str(ids["a1"]), str(ids["a2"])}


async def test_admin_auditor_and_business_owner_read_no_patient_rows(db):
    """RLS boundary, not an error: the role may query, and gets nothing back."""
    a, b, p = await seed(db)
    await seed_patients(db, a, b, p)
    uids = [p[w] for w in NO_CASE_ACCESS]
    for table in ("mpi.patients", "mpi.identity_tokens"):
        res = await rows_each(db, uids, f"select * from {table}")
        assert all(rows == [] for rows in res.values()), table


async def test_no_identity_and_unknown_identity_read_nothing(db):
    a, b, p = await seed(db)
    await seed_patients(db, a, b, p)
    res = await rows_each(db, [None, _u()], "select * from mpi.patients")
    assert all(rows == [] for rows in res.values())


async def test_patient_reads_only_their_own_record_and_no_tokens(db):
    a, b, p = await seed(db)
    ids = await seed_patients(db, a, b, p)
    own = await rows_each(db, [p["patient_1"]], "select id from mpi.patients")
    assert [r["id"] for r in own[p["patient_1"]]] == [str(ids["a1"])]
    assert (await rows_each(db, [p["patient_1"]], "select * from mpi.identity_tokens"))[p["patient_1"]] == []


async def test_front_desk_of_another_clinic_reads_nothing_of_clinic_a(db):
    a, b, p = await seed(db)
    ids = await seed_patients(db, a, b, p)
    res = await rows_each(db, [p["front_desk_b"]], "select id from mpi.patients")
    assert [r["id"] for r in res[p["front_desk_b"]]] == [str(ids["b1"])]


# ---- 2. who can write ------------------------------------------------------------------------------

OTHERS = ["nurse_a", "doctor_a", "admin_a", "auditor_a", "owner_a", "front_desk_b", "patient_1"]


async def test_front_desk_can_insert_a_patient_into_their_own_clinic(db):
    a, _, p = await seed(db)
    assert await attempts(db, p["front_desk_a"], [new_patient_sql(a)]) == ["ok:1"]
    assert await db.fetchval("select count(*) from mpi.patients where clinic_id = $1", a) == 1


async def test_every_other_identity_cannot_insert_a_patient(db):
    a, _, p = await seed(db)
    res = await try_each(db, [p[w] for w in OTHERS] + [None], new_patient_sql(a))  # None = unauthenticated
    assert set(res.values()) == {RLS_DENIED}, res  # new row violates row-level security policy
    assert await db.fetchval("select count(*) from mpi.patients where clinic_id = $1", a) == 0


async def test_only_front_desk_of_the_clinic_can_update_a_patient(db):
    a, b, p = await seed(db)
    ids = await seed_patients(db, a, b, p)
    upd = "update mpi.patients set phone_ct = {ct}, phone_key_version = {v} where id = '{id}'"
    assert await attempts(db, p["front_desk_a"], [upd.format(ct=bx(b"\x02new"), v=2, id=ids["a1"])]) == ["ok:1"]
    # An UPDATE the policy does not allow matches no rows; it does not error and changes nothing.
    res = await try_each(db, [p[w] for w in OTHERS], upd.format(ct=bx(b"\x03evil"), v=3, id=ids["a1"]))
    assert set(res.values()) == {"ok:0"}, res
    row = await db.fetchrow("select phone_ct, phone_key_version from mpi.patients where id = $1", ids["a1"])
    assert (bytes(row["phone_ct"]), row["phone_key_version"]) == (b"\x02new", 2)


async def test_front_desk_cannot_move_a_patient_into_another_clinic(db):
    a, b, p = await seed(db)
    ids = await seed_patients(db, a, b, p)
    # Since 0012 the denial comes from the COLUMN privilege (the app role may not update clinic_id at all). The row-level
    # WITH CHECK behind it is tested separately in tests/test_patient_binding_live.py, which grants the column inside a
    # rolled-back transaction, so this test no longer pretends to prove the policy.
    assert not await db.fetchval("select has_column_privilege('careflow_app', 'mpi.patients', 'clinic_id', 'UPDATE')")
    res = await attempts(db, p["front_desk_a"], [f"update mpi.patients set clinic_id = '{b}' where id = '{ids['a1']}'"])
    assert res == [RLS_DENIED]


async def test_nobody_can_delete_patient_rows(db):
    a, b, p = await seed(db)
    await seed_patients(db, a, b, p)
    uids = [p["front_desk_a"], p["admin_a"]]
    for table in ("mpi.patients", "mpi.identity_tokens"):
        assert set((await try_each(db, uids, f"delete from {table}")).values()) == {RLS_DENIED}, table


async def test_only_front_desk_writes_identity_tokens(db):
    a, b, p = await seed(db)
    ids = await seed_patients(db, a, b, p)
    ins = (
        "insert into mpi.identity_tokens (patient_id, clinic_id, kind, token, key_version, origin) "
        f"values ('{{pid}}', '{a}', 'dob', {TOKEN_B}, 1, 'test')"
    )
    assert await attempts(db, p["front_desk_a"], [ins.format(pid=ids["a1"])]) == ["ok:1"]
    assert await attempts(db, p["nurse_a"], [ins.format(pid=ids["a2"])]) == [RLS_DENIED]


# ---- table constraints (as the migrator: constraints apply to every writer) ------------------------


async def test_origin_has_no_default_and_must_be_stated_and_valid(db):
    a, _, _ = await seed(db)
    default = await db.fetchval(
        "select column_default from information_schema.columns where table_schema='mpi' and table_name='patients' and column_name='origin'"
    )
    assert default is None
    await db.execute("savepoint c")
    with pytest.raises(asyncpg.NotNullViolationError):
        await db.execute(
            "insert into mpi.patients (clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, idempotency_key) "
            "values ($1, $2, 1, $2, 1, 'no-origin')", a, b"\x01",
        )
    await db.execute("rollback to savepoint c")
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute(
            "insert into mpi.patients (clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, origin, idempotency_key) "
            "values ($1, $2, 1, $2, 1, 'production', 'bad-origin')", a, b"\x01",
        )


@pytest.mark.parametrize(
    "cols, vals",
    [
        ("name_ct, dob_ct, dob_key_version", "'\\x01'::bytea, '\\x01'::bytea, 1"),  # ciphertext without its key_version
        ("name_ct, name_key_version, dob_ct, dob_key_version", "'\\x01'::bytea, 0, '\\x01'::bytea, 1"),  # version must be > 0
        ("name_ct, name_key_version", "'\\x01'::bytea, 1"),  # live record without a date of birth
        ("name_key_version, dob_ct, dob_key_version", "1, '\\x01'::bytea, 1"),  # key_version without ciphertext
    ],
)
async def test_ciphertext_and_key_version_must_travel_together(db, cols, vals):
    a, _, _ = await seed(db)
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute(
            f"insert into mpi.patients (clinic_id, {cols}, origin, idempotency_key) values ($1, {vals}, 'test', 'k-{_u()}')", a
        )


async def test_erased_record_must_carry_no_identifiable_field_and_idempotency_key_is_unique(db):
    a, _, _ = await seed(db)
    await db.execute(
        "insert into mpi.patients (clinic_id, erased_at, origin, idempotency_key) values ($1, now(), 'test', 'erased-1')", a
    )
    await db.execute("savepoint e")
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute(
            "insert into mpi.patients (clinic_id, erased_at, name_ct, name_key_version, origin, idempotency_key) "
            "values ($1, now(), '\\x01', 1, 'test', 'erased-2')", a,
        )
    await db.execute("rollback to savepoint e")
    with pytest.raises(asyncpg.UniqueViolationError):
        await db.execute(
            "insert into mpi.patients (clinic_id, erased_at, origin, idempotency_key) values ($1, now(), 'test', 'erased-1')", a
        )


async def test_token_key_versions_coexist_but_not_duplicate(db):
    a, b, p = await seed(db)
    ids = await seed_patients(db, a, b, p)
    ins = "insert into mpi.identity_tokens (patient_id, clinic_id, kind, token, key_version, origin) values ($1, $2, 'phone', $3, $4, 'test')"
    await db.execute(ins, ids["a1"], a, b"\xcc" * 32, 2)  # rotation: a second version alongside version 1
    with pytest.raises(asyncpg.UniqueViolationError):
        await db.execute(ins, ids["a1"], a, b"\xdd" * 32, 2)


async def test_token_must_belong_to_the_patients_own_clinic(db):
    a, b, p = await seed(db)
    ids = await seed_patients(db, a, b, p)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await db.execute(
            "insert into mpi.identity_tokens (patient_id, clinic_id, kind, token, key_version, origin) "
            "values ($1, $2, 'dob', $3, 1, 'test')", ids["a1"], b, b"\xee" * 32,
        )


# ---- sched: RLS, tenancy and the exclusion constraint on one connection ----------------------------


async def seed_sched(conn, clinic, k):
    prov, loc, vt = _u(), _u(), _u()
    await conn.execute(
        "insert into sched.providers (id, clinic_id, display_name, origin, idempotency_key) "
        f"values ('{prov}', '{clinic}', 'Dr Test', 'test', 'prov-{k}-{_u()}');\n"
        "insert into sched.locations (id, clinic_id, name, origin, idempotency_key) "
        f"values ('{loc}', '{clinic}', 'Room 1', 'test', 'loc-{k}-{_u()}');\n"
        "insert into sched.visit_types (id, clinic_id, name, duration_minutes, origin, idempotency_key) "
        f"values ('{vt}', '{clinic}', 'Review', 30, 'test', 'vt-{k}-{_u()}');"
    )
    return prov, loc, vt


SLOT = (
    "insert into sched.slots (clinic_id, provider_id, location_id, visit_type_id, starts_at, ends_at, origin, idempotency_key) "
    "values ($1, $2, $3, $4, $5::text::timestamptz, $6::text::timestamptz, 'test', $7)"
)


def slot_sql(clinic, ids, start, end):
    prov, loc, vt = ids
    return (
        "insert into sched.slots (clinic_id, provider_id, location_id, visit_type_id, starts_at, ends_at, origin, idempotency_key) "
        f"values ('{clinic}', '{prov}', '{loc}', '{vt}', '{start}', '{end}', 'test', 'slot-{_u()}')"
    )


async def test_clinical_staff_read_scheduling_but_admin_and_front_desk_manage_it(db):
    a, _, p = await seed(db)
    ids = await seed_sched(db, a, "r")
    first = slot_sql(a, ids, "2031-03-03 10:00+00", "2031-03-03 10:30+00")
    assert await attempts(db, p["front_desk_a"], [first]) == ["ok:1"]
    readers = [p[w] for w in ("front_desk_a", "nurse_a", "doctor_a", "admin_a")]
    blind = [p[w] for w in ("auditor_a", "owner_a", "front_desk_b", "patient_1")]
    for table in ("sched.slots", "sched.providers"):
        for uid, rows in (await rows_each(db, readers, f"select id from {table}")).items():
            assert len(rows) == 1, (table, uid)
        for uid, rows in (await rows_each(db, blind, f"select id from {table}")).items():
            assert rows == [], (table, uid)
    # admin manages too
    second = slot_sql(a, ids, "2031-03-03 11:00+00", "2031-03-03 11:30+00")
    assert await attempts(db, p["admin_a"], [second]) == ["ok:1"]
    refused = [p[w] for w in ("nurse_a", "doctor_a", "auditor_a", "owner_a", "front_desk_b", "patient_1")]
    res = await try_each(db, refused, slot_sql(a, ids, "2031-03-03 12:00+00", "2031-03-03 12:30+00"))
    assert set(res.values()) == {RLS_DENIED}, res


async def test_only_managers_write_providers_locations_and_visit_types(db):
    a, _, p = await seed(db)
    stmts = [
        "insert into sched.providers (clinic_id, display_name, origin, idempotency_key) values ('{c}', 'X', 'test', 'k-{k}')",
        "insert into sched.locations (clinic_id, name, origin, idempotency_key) values ('{c}', 'X', 'test', 'k-{k}')",
        "insert into sched.visit_types (clinic_id, name, duration_minutes, origin, idempotency_key) values ('{c}', 'X', 20, 'test', 'k-{k}')",
    ]
    others = [p[w] for w in ("nurse_a", "doctor_a", "auditor_a", "owner_a", "front_desk_b")]
    for sql in stmts:
        assert await attempts(db, p["front_desk_a"], [sql.format(c=a, k=_u())]) == ["ok:1"]
        assert await attempts(db, p["admin_a"], [sql.format(c=a, k=_u())]) == ["ok:1"]
        assert set((await try_each(db, others, sql.format(c=a, k=_u()))).values()) == {RLS_DENIED}
    assert await attempts(db, p["admin_a"], ["delete from sched.slots"]) == [RLS_DENIED]


async def test_slot_must_use_a_provider_from_its_own_clinic(db):
    a, b, _ = await seed(db)
    ids_b = await seed_sched(db, b, "b")
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await db.execute(slot_sql(a, ids_b, "2031-03-03 10:00+00", "2031-03-03 10:30+00"))


async def test_slot_must_end_after_it_starts(db):
    a, _, _ = await seed(db)
    ids = await seed_sched(db, a, "t")
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute(slot_sql(a, ids, "2031-03-03 10:30+00", "2031-03-03 10:30+00"))


async def test_overlapping_slots_for_one_provider_are_blocked_in_one_session(db):
    a, _, p = await seed(db)
    ids = await seed_sched(db, a, "o")
    other = await seed_sched(db, a, "o2")
    windows = [
        (ids, "10:00", "10:30"),  # the original
        (ids, "10:00", "10:30"),  # identical
        (ids, "10:15", "10:45"),  # partial overlap
        (ids, "09:45", "10:05"),  # overlaps the start
        (ids, "09:00", "12:00"),  # contains it
        (ids, "10:30", "11:00"),  # back-to-back is fine (half-open ranges)
        (other, "10:00", "10:30"),  # a different provider may use the same time
    ]
    sqls = [slot_sql(a, i, f"2031-03-03 {s}+00", f"2031-03-03 {e}+00") for i, s, e in windows]
    expected = ["ok:1", EXCLUSION, EXCLUSION, EXCLUSION, EXCLUSION, "ok:1", "ok:1"]
    assert await attempts(db, p["front_desk_a"], sqls) == expected


async def test_cancelled_slot_releases_its_time(db):
    a, _, p = await seed(db)
    ids = await seed_sched(db, a, "c")
    sqls = [
        slot_sql(a, ids, "2031-03-03 10:00+00", "2031-03-03 10:30+00"),
        "update sched.slots set status = 'cancelled'",
        slot_sql(a, ids, "2031-03-03 10:00+00", "2031-03-03 10:30+00"),  # now allowed
        slot_sql(a, ids, "2031-03-03 10:10+00", "2031-03-03 10:20+00"),  # the replacement is live: blocked again
    ]
    assert await attempts(db, p["front_desk_a"], sqls) == ["ok:1", "ok:1", "ok:1", EXCLUSION]


# ---- 3. concurrency: several real sessions, committed fixture --------------------------------------


@pytest_asyncio.fixture(loop_scope="module")
async def committed():
    """A committed fixture (clinic, front-desk account, provider, location, visit type) plus teardown.
    Requires 0002 to be applied; skips otherwise. Everything is tagged origin='test' and removed."""
    if not APP:
        pytest.skip("DATABASE_URL_APP not configured")
    admin = await asyncpg.connect(_dsn(MIGRATOR or ""), statement_cache_size=0, timeout=20)
    if await admin.fetchval("select to_regclass('sched.slots') is null"):
        await admin.close()
        pytest.skip("migration 0002 is not applied yet; the concurrency test needs committed tables")
    clinic, acct = _u(), _u()
    try:
        await admin.execute("insert into iam.clinics (id, name, origin) values ($1, 'Concurrency Clinic', 'test')", clinic)
        await admin.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test')", acct)
        await admin.execute(
            "insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, 'front_desk', 'test')", acct, clinic
        )
        ids = await seed_sched(admin, clinic, "cc")
        yield clinic, acct, ids
    finally:
        for table in ("sched.slots", "sched.visit_types", "sched.locations", "sched.providers"):
            await admin.execute(f"delete from {table} where clinic_id = $1", clinic)
        await admin.execute("delete from iam.staff_roles where clinic_id = $1", clinic)
        await admin.execute("delete from iam.accounts where id = $1", acct)
        await admin.execute("delete from iam.clinics where id = $1", clinic)
        await admin.close()


async def _app_session(acct):
    """A real careflow_app session through the transaction-mode pooler, with claims set per transaction."""
    conn = await asyncpg.connect(_dsn(APP or ""), statement_cache_size=0, timeout=20)
    tx = conn.transaction()
    await tx.start()
    claims = json.dumps({"sub": str(acct), "role": "authenticated"})
    await conn.execute("select set_config('request.jwt.claims', $1, true)", claims)
    return conn, tx


async def _slot_count(clinic):
    admin = await asyncpg.connect(_dsn(MIGRATOR or ""), statement_cache_size=0)
    try:
        return await admin.fetchval("select count(*) from sched.slots where clinic_id = $1", clinic)
    finally:
        await admin.close()


async def test_second_concurrent_write_of_an_overlapping_slot_is_blocked_by_the_database(committed):
    clinic, acct, (prov, loc, vt) = committed
    first, tx1 = await _app_session(acct)
    second, tx2 = await _app_session(acct)
    try:
        await first.execute(SLOT, clinic, prov, loc, vt, T0, "2031-03-03 10:30+00", f"cc1-{_u()}")  # uncommitted
        blocked = asyncio.create_task(
            second.execute(SLOT, clinic, prov, loc, vt, "2031-03-03 10:15+00", "2031-03-03 10:45+00", f"cc2-{_u()}")
        )
        await asyncio.sleep(2)
        # The engine makes the second writer WAIT on the first's uncommitted row; it cannot slip past it.
        assert not blocked.done(), "second overlapping write did not wait for the first transaction"
        await tx1.commit()
        with pytest.raises(asyncpg.ExclusionViolationError) as exc:  # raised by the database
            await blocked
        assert exc.value.sqlstate == "23P01"
        await tx2.rollback()
    finally:
        for tx in (tx1, tx2):
            with contextlib.suppress(asyncpg.PostgresError, asyncpg.InterfaceError):  # already finished
                await tx.rollback()
        await first.close()
        await second.close()
    assert await _slot_count(clinic) == 1


async def test_if_the_first_writer_rolls_back_the_waiting_writer_succeeds(committed):
    clinic, acct, (prov, loc, vt) = committed
    first, tx1 = await _app_session(acct)
    second, tx2 = await _app_session(acct)
    try:
        await first.execute(SLOT, clinic, prov, loc, vt, T0, "2031-03-03 10:30+00", f"rb1-{_u()}")
        waiting = asyncio.create_task(second.execute(SLOT, clinic, prov, loc, vt, T0, "2031-03-03 10:30+00", f"rb2-{_u()}"))
        await asyncio.sleep(2)
        assert not waiting.done()
        await tx1.rollback()
        await waiting  # no error: the time was never actually taken
        await tx2.commit()
    finally:
        for tx in (tx1, tx2):
            with contextlib.suppress(asyncpg.PostgresError, asyncpg.InterfaceError):
                await tx.rollback()
        await first.close()
        await second.close()
    assert await _slot_count(clinic) == 1


async def test_many_simultaneous_writers_for_one_window_produce_exactly_one_slot(committed):
    clinic, acct, (prov, loc, vt) = committed

    async def attempt(i):
        conn = await asyncpg.connect(_dsn(APP or ""), statement_cache_size=0, timeout=30)
        try:
            async with conn.transaction():
                claims = json.dumps({"sub": str(acct), "role": "authenticated"})
                await conn.execute("select set_config('request.jwt.claims', $1, true)", claims)
                await conn.execute(SLOT, clinic, prov, loc, vt, T0, "2031-03-03 10:30+00", f"many-{i}-{_u()}")
            return "won"
        except asyncpg.ExclusionViolationError:
            return "blocked"
        finally:
            await conn.close()

    results = await bounded(asyncio.gather(*(attempt(i) for i in range(8))), "the concurrent overlapping slot writes")
    assert results.count("won") == 1
    assert results.count("blocked") == 7
    assert await _slot_count(clinic) == 1
