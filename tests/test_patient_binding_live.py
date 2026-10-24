"""Migration 0012 (patient account binding), live. BUILD-SPEC.md §17.2, §18.4; CLAUDE.md rules 8 and 10.

0012 closes the hole that let any front-desk user bind ANY account to ANY patient record: the application role has no
INSERT or UPDATE privilege on mpi.patients.account_id, and a trigger guards the column for every role including the owner.

Same harness as the other live suites: ONE never-committed outer transaction on the migrator connection, each test in its
own savepoint, rows tagged origin='test', everything rolled back at the end. Migration 0012 is executed inside that outer
transaction when it is not applied yet, so the file is verified without being applied. Statements as the application role
run as `careflow_app` with that identity's claims (never as the migrator); the owner-only paths (a valid bind, the
trigger's refusals) run as the migrator. Nothing here commits.
"""

import json
import os
import uuid
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio
from dotenv import dotenv_values

from tests.db_helpers import ensure_app_role

ROOT = Path(__file__).resolve().parent.parent
_ENV = dotenv_values(ROOT / ".env")
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or _ENV.get("DATABASE_URL_MIGRATOR")
SQL_0012 = (ROOT / "db" / "migrations" / "0012_patient_account_binding.sql").read_text(encoding="utf-8")

pytestmark = [
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured"),
]

DENIED = "err:42501"
REFUSED = "patient account binding refused"
ALLOWED_UPDATE = ("name_ct", "name_key_version", "dob_ct", "dob_key_version", "phone_ct", "phone_key_version", "erased_at")
NEVER_UPDATE = ("account_id", "clinic_id", "origin", "idempotency_key", "id", "created_at")


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


async def rows_as(conn, uid, sql):
    return json.loads(await conn.fetchval("select pg_temp.rows_as($1::uuid, $2)", uid, sql))


async def outcome(conn, sql):
    """Run `sql` as the connection's own role (the owner) in a savepoint: (None, None) on success, else (sqlstate, message)."""
    await conn.execute("savepoint attempt")
    try:
        await conn.execute(sql)
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
        await ensure_app_role(conn)
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


async def seed(conn):
    """Clinic A with a front desk, and one account of every kind a bind could name."""
    a = _u()
    w = {"a": a, "fd": _u()}
    kinds = {
        "ok": ("patient", "active", "test"), "ok2": ("patient", "active", "test"),
        "invited": ("patient", "invited", "test"), "suspended": ("patient", "suspended", "test"),
        "closed": ("patient", "closed", "test"), "staff": ("staff", "active", "test"),
        "guardian": ("guardian", "active", "test"), "eval": ("patient", "active", "eval"),
    }
    for k in kinds:
        w[k] = _u()
    rows = [f"('{w['fd']}', 'staff', 'active', 'test')"] + [f"('{w[k]}', '{t}', '{s}', '{o}')" for k, (t, s, o) in kinds.items()]
    await conn.execute(
        f"insert into iam.clinics (id, name, origin) values ('{a}', 'Clinic A', 'test');\n"
        f"insert into iam.accounts (id, account_type, status, origin) values {', '.join(rows)};\n"
        f"insert into iam.staff_roles (account_id, clinic_id, role, origin) values ('{w['fd']}', '{a}', 'front_desk', 'test');"
    )
    w["patient"] = await new_patient(conn, a)
    return w


async def new_patient(conn, clinic, *, erased=False, account=None):
    pid = _u()
    acct = f"'{account}'" if account else "null"
    if erased:
        await conn.execute(
            "insert into mpi.patients (id, clinic_id, account_id, erased_at, origin, idempotency_key) "
            f"values ('{pid}', '{clinic}', {acct}, now(), 'test', 'seed-{pid}')"
        )
    else:
        await conn.execute(
            "insert into mpi.patients (id, clinic_id, account_id, name_ct, name_key_version, dob_ct, dob_key_version, origin, idempotency_key) "
            f"values ('{pid}', '{clinic}', {acct}, {bx(b'n' * 20)}, 1, {bx(b'd' * 20)}, 1, 'test', 'seed-{pid}')"
        )
    return pid


def insert_sql(clinic, *, with_account=None, key=None):
    cols = "clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, origin, idempotency_key"
    vals = f"'{clinic}', {bx(b'n' * 20)}, 1, {bx(b'd' * 20)}, 1, 'test', '{key or 'new-' + str(_u())}'"
    if with_account:
        cols, vals = "account_id, " + cols, f"'{with_account}', " + vals
    return f"insert into mpi.patients ({cols}) values ({vals})"


# ---- the application role cannot touch account_id ------------------------------------------------------------------------


async def test_front_desk_cannot_insert_a_patient_with_an_account_id(db):
    w = await seed(db)
    assert await attempts(db, w["fd"], [insert_sql(w["a"]), insert_sql(w["a"], with_account=w["ok"])]) == ["ok:1", DENIED]
    assert await db.fetchval("select count(*) from mpi.patients where account_id is not null") == 0


async def test_front_desk_cannot_update_account_id(db):
    w = await seed(db)
    out = await attempts(db, w["fd"], [f"update mpi.patients set account_id = '{w['ok']}' where id = '{w['patient']}'"])
    assert out == [DENIED]
    assert await db.fetchval("select account_id from mpi.patients where id = $1", w["patient"]) is None


async def test_on_conflict_do_update_cannot_set_account_id(db):
    w = await seed(db)
    key = f"seed-{w['patient']}"
    sql = insert_sql(w["a"], key=key) + " on conflict (idempotency_key) do update set account_id = excluded.account_id"
    # excluded.account_id is NULL here, but the privilege on the SET column is checked regardless of the value.
    assert await attempts(db, w["fd"], [sql]) == [DENIED]
    sql_bound = insert_sql(w["a"], key=key) + f" on conflict (idempotency_key) do update set account_id = '{w['ok']}'"
    assert await attempts(db, w["fd"], [sql_bound]) == [DENIED]


async def test_insert_select_with_an_account_id_is_denied(db):
    w = await seed(db)
    sql = (
        "insert into mpi.patients (account_id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, origin, idempotency_key) "
        f"select '{w['ok']}', clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, origin, 'copy-{_u()}' "
        f"from mpi.patients where id = '{w['patient']}'"
    )
    assert await attempts(db, w["fd"], [sql]) == [DENIED]


async def test_has_column_privilege_matrix(db):
    cols = [r["attname"] for r in await db.fetch(
        "select attname from pg_attribute where attrelid = 'mpi.patients'::regclass and attnum > 0 and not attisdropped")]
    assert "account_id" in cols
    for c in cols:
        can_insert = await db.fetchval("select has_column_privilege('careflow_app', 'mpi.patients', $1, 'INSERT')", c)
        can_update = await db.fetchval("select has_column_privilege('careflow_app', 'mpi.patients', $1, 'UPDATE')", c)
        assert can_insert == (c != "account_id"), ("INSERT", c)
        assert can_update == (c in ALLOWED_UPDATE), ("UPDATE", c)
    assert set(NEVER_UPDATE) <= set(cols)


async def test_immutable_patient_columns_cannot_be_updated_by_the_app_role(db):
    w = await seed(db)
    sets = {
        "clinic_id": f"'{_u()}'", "origin": "'live'", "idempotency_key": "'changed'", "id": f"'{_u()}'", "created_at": "now()",
    }
    out = await attempts(db, w["fd"], [f"update mpi.patients set {c} = {v} where id = '{w['patient']}'" for c, v in sets.items()])
    assert out == [DENIED] * len(sets)


async def test_the_clinic_check_still_denies_a_move_even_if_the_column_grant_were_present(db):
    """The column privilege is not the only guard: grant UPDATE(clinic_id) inside this rolled-back transaction and the
    row-level WITH CHECK of patients_update_front_desk must still refuse moving a patient to a clinic the caller is not in."""
    w = await seed(db)
    other = _u()
    await db.execute(f"insert into iam.clinics (id, name, origin) values ('{other}', 'Clinic B', 'test')")
    await db.execute("grant update (clinic_id) on mpi.patients to careflow_app")
    out = await attempts(db, w["fd"], [f"update mpi.patients set clinic_id = '{other}' where id = '{w['patient']}'"])
    assert out == [DENIED]


async def test_app_role_cannot_disable_the_trigger_or_set_session_replication_role(db):
    w = await seed(db)
    out = await attempts(db, w["fd"], ["alter table mpi.patients disable trigger patients_guard_account", "set session_replication_role = replica"])
    assert out == [DENIED, DENIED]


async def test_the_app_role_can_still_lock_patient_rows(db):
    """SELECT ... FOR UPDATE / FOR SHARE need UPDATE on at least one column; identity.py and key rotation rely on it. The
    seven-column UPDATE grant must therefore never be narrowed to nothing."""
    w = await seed(db)
    out = await attempts(
        db, w["fd"],
        [f"select 1 from mpi.patients where id = '{w['patient']}' for update",
         f"select 1 from mpi.patients where id = '{w['patient']}' for share"],
    )
    assert out == ["ok:1", "ok:1"]


async def test_the_guard_function_is_not_executable_by_the_app_or_api_roles(db):
    for role in ("careflow_app", "anon", "authenticated", "service_role"):
        if not await db.fetchval("select exists (select 1 from pg_roles where rolname = $1)", role):
            continue
        assert not await db.fetchval("select has_function_privilege($1, 'mpi.guard_patient_account()', 'EXECUTE')", role), role


async def test_the_trigger_is_before_insert_or_update_of_account_id(db):
    definition = await db.fetchval("select pg_get_triggerdef(oid) from pg_trigger where tgname = 'patients_guard_account'")
    assert "BEFORE INSERT OR UPDATE OF account_id ON mpi.patients" in definition and "FOR EACH ROW" in definition


# ---- the trigger, for the table owner ---------------------------------------------------------------------------------


async def test_null_to_value_is_allowed_for_the_owner_when_the_account_is_valid(db):
    w = await seed(db)
    assert await outcome(db, f"update mpi.patients set account_id = '{w['ok']}' where id = '{w['patient']}'") == (None, None)
    assert await db.fetchval("select account_id from mpi.patients where id = $1", w["patient"]) == w["ok"]
    assert await outcome(db, insert_sql(w["a"], with_account=w["ok2"])) == (None, None)  # an INSERT that names a valid account


async def test_an_existing_binding_cannot_be_changed_or_cleared_even_by_the_owner(db):
    w = await seed(db)
    bound = await new_patient(db, w["a"], account=w["ok"])
    for sql in (
        f"update mpi.patients set account_id = '{w['ok2']}' where id = '{bound}'",
        f"update mpi.patients set account_id = null where id = '{bound}'",
    ):
        state, message = await outcome(db, sql)
        assert state == "23514" and REFUSED in message, sql
    assert await db.fetchval("select account_id from mpi.patients where id = $1", bound) == w["ok"]


async def test_a_noop_account_id_set_is_allowed(db):
    w = await seed(db)
    bound = await new_patient(db, w["a"], account=w["ok"])
    assert await outcome(db, f"update mpi.patients set account_id = account_id where id = '{bound}'") == (None, None)


async def test_binding_target_must_be_an_active_patient_account_of_the_same_origin(db):
    w = await seed(db)
    bad_targets = {
        "nonexistent": str(_u()), "invited": str(w["invited"]), "suspended": str(w["suspended"]), "closed": str(w["closed"]),
        "staff": str(w["staff"]), "guardian": str(w["guardian"]), "other origin": str(w["eval"]),
    }
    for label, account in bad_targets.items():
        state, message = await outcome(db, f"update mpi.patients set account_id = '{account}' where id = '{w['patient']}'")
        assert state == "23514" and REFUSED in message, label
    assert await db.fetchval("select account_id from mpi.patients where id = $1", w["patient"]) is None


async def test_every_rejection_gives_the_same_error_text(db):
    """One generic message for every reason, so the trigger is no oracle on whether an arbitrary uuid is a patient account."""
    w = await seed(db)
    bound = await new_patient(db, w["a"], account=w["ok"])
    erased = await new_patient(db, w["a"], erased=True)
    attempts_ = [
        f"update mpi.patients set account_id = '{_u()}' where id = '{w['patient']}'",          # no such account
        f"update mpi.patients set account_id = '{w['staff']}' where id = '{w['patient']}'",    # wrong type
        f"update mpi.patients set account_id = '{w['ok2']}' where id = '{bound}'",             # already bound
        f"update mpi.patients set account_id = '{w['ok']}' where id = '{erased}'",             # erased patient
    ]
    messages = {(await outcome(db, sql))[1] for sql in attempts_}
    assert len(messages) == 1 and REFUSED in next(iter(messages)), messages


async def test_binding_an_erased_patient_is_refused(db):
    w = await seed(db)
    erased = await new_patient(db, w["a"], erased=True)
    state, message = await outcome(db, f"update mpi.patients set account_id = '{w['ok']}' where id = '{erased}'")
    assert state == "23514" and REFUSED in message
    state, message = await outcome(
        db, f"insert into mpi.patients (account_id, clinic_id, erased_at, origin, idempotency_key) values ('{w['ok']}', '{w['a']}', now(), 'test', 'e-{_u()}')"
    )
    assert state == "23514" and REFUSED in message


async def test_a_second_patient_with_the_same_account_is_refused(db):
    w = await seed(db)
    await new_patient(db, w["a"], account=w["ok"])
    state, _ = await outcome(db, f"update mpi.patients set account_id = '{w['ok']}' where id = '{w['patient']}'")
    assert state == "23505"  # patients_one_per_account: the trigger allows it, the unique index does not


# ---- what must keep working ----------------------------------------------------------------------------------------------


async def test_a_bound_patient_still_accepts_rotation_and_erasure_updates(db):
    w = await seed(db)
    bound = await new_patient(db, w["a"], account=w["ok"])
    out = await attempts(
        db, w["fd"],
        [f"update mpi.patients set name_ct = {bx(b'N' * 20)}, name_key_version = 2 where id = '{bound}'",       # rotation
         (
             "update mpi.patients set erased_at = now(), name_ct = null, name_key_version = null, dob_ct = null, "
             f"dob_key_version = null, phone_ct = null, phone_key_version = null where id = '{bound}'"
         )],  # erasure
    )
    assert out == ["ok:1", "ok:1"]
    assert await db.fetchval("select account_id from mpi.patients where id = $1", bound) == w["ok"]


async def test_an_owner_bound_account_passes_patients_select_own(db):
    w = await seed(db)
    bound = await new_patient(db, w["a"], account=w["ok"])
    seen = await rows_as(db, w["ok"], "select id from mpi.patients")
    assert [r["id"] for r in seen] == [str(bound)]
    assert await rows_as(db, w["ok2"], "select id from mpi.patients") == []
