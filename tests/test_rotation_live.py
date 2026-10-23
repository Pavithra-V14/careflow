"""Key rotation against the real hosted dev project (careflow/rotation.py, scripts/rotate_keys.py;
BUILD-SPEC.md §17.2; CLAUDE.md rules 5, 8, 10).

Same harness as the other live suites: one never-committed outer transaction on the migrator connection, every test in
its own savepoint, rows tagged origin='test', everything rolled back at the end, so the dev database is left exactly as
it was. Migration 0010 (the batch ledger) is executed inside that outer transaction when it is not applied yet, so the
file is verified without being applied.

Rotation runs here as the restricted `careflow_app` role with a front-desk identity, i.e. under the real RLS policies,
not as a superuser. (The maintenance script itself uses the migrator credential; that path is the CLI test below and
the runbook, and shares the same engine.)
"""

import base64
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from careflow.crypto import CryptoError, DemographicVault, UnknownKeyVersionError
from careflow.db import set_request_claims
from careflow.mpi import PatientIngestionPipeline, derive_tokens
from careflow.rotation import (
    RotationError,
    rotate_key_version,
    verify_rotation,
    version_usage,
)

ROOT = Path(__file__).resolve().parent.parent
_ENV = dotenv_values(ROOT / ".env")
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or _ENV.get("DATABASE_URL_MIGRATOR")
SQL = {n: (ROOT / "db" / "migrations" / f).read_text(encoding="utf-8") for n, f in {
    "mpi.match_candidates": "0003_mpi_match_candidates.sql",
    "mpi.merges": "0008_mpi_merge_ledger.sql",
    "ops.key_rotation_batches": "0010_key_rotation_ledger.sql",
}.items()}

pytestmark = [
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured"),
]

PEOPLE = [
    ("Asha Venkatesh", "1987-04-23", "+91 98765 43210"),
    ("Ravi  Kumar", "1990-01-15", "9123456780"),  # inner double space: stored collapsed, token over the collapsed form
    ("Meera Nair", "2001-12-02", None),  # no phone: no phone ciphertext, no phone token
]


def _b64() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def _u() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture(scope="module")
def keys():
    return {f"CAREFLOW_{k}_SECRET_V{v}": _b64() for v in (1, 2) for k in ("ENC", "HMAC")}


@pytest.fixture(scope="module")
def vault(keys):
    return DemographicVault.from_env(keys)


async def raw(conn, sql):
    handle = await conn.get_raw_connection()
    await handle.driver_connection.execute(sql)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _conn():
    engine = create_async_engine(make_url(MIGRATOR or "").set(drivername="postgresql+asyncpg"), connect_args={"statement_cache_size": 0})
    conn = await engine.connect()
    outer = await conn.begin()
    try:
        await conn.exec_driver_sql("grant careflow_app to current_user with set true")
        for table, script in SQL.items():
            if (await conn.execute(text("select to_regclass(:t) is null"), {"t": table})).scalar_one():
                await raw(conn, script)
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


async def q(conn, sql, **params):
    return (await conn.execute(text(sql), params)).all()


async def seed(conn):
    """Clinic A (front desk, nurse, auditor) and clinic B (front desk)."""
    a, b = _u(), _u()
    who = {"fd_a": (a, "front_desk"), "nurse_a": (a, "nurse"), "auditor_a": (a, "auditor"), "fd_b": (b, "front_desk")}
    ids = {k: _u() for k in who}
    await raw(
        conn,
        f"insert into iam.clinics (id, name, origin) values ('{a}', 'Clinic A', 'test'), ('{b}', 'Clinic B', 'test');\n"
        "insert into iam.accounts (id, account_type, status, origin) values "
        + ", ".join(f"('{ids[k]}', 'staff', 'active', 'test')" for k in who)
        + ";\ninsert into iam.staff_roles (account_id, clinic_id, role, origin) values "
        + ", ".join(f"('{ids[k]}', '{c}', '{r}', 'test')" for k, (c, r) in who.items())
        + ";",
    )
    return a, b, ids


async def as_user(conn, uid, fn):
    """Run `fn(conn)` as the restricted app role with this identity's claims, in a savepoint, as a request would."""
    async with conn.begin_nested():
        await conn.exec_driver_sql("set local role careflow_app")
        await set_request_claims(conn, {"sub": str(uid), "role": "authenticated"})
        result = await fn(conn)
        # No `finally`: on an error the savepoint rollback restores the role, and a statement after a failed
        # statement would only raise "transaction is aborted" over the real error.
        await conn.exec_driver_sql("reset role")
        return result


async def register(conn, vault, uid, clinic, people=PEOPLE, *, version=1):
    pipeline = PatientIngestionPipeline(vault, key_version=version)
    out = []
    for name, dob, phone in people:
        async def ingest(c, name=name, dob=dob, phone=phone):
            return await pipeline.ingest(c, clinic_id=clinic, origin="test", name=name, dob=dob, phone=phone, idempotency_key=f"idem-{_u()}")
        out.append((await as_user(conn, uid, ingest)).patient_id)
    return out


async def snapshot(conn, clinic):
    rows = await q(conn, "select id, name_ct, name_key_version, dob_ct, dob_key_version, phone_ct, phone_key_version "
                         "from mpi.patients where clinic_id = :c order by id", c=clinic)
    tokens = await q(conn, "select patient_id, kind, key_version, token from mpi.identity_tokens where clinic_id = :c order by 1, 2, 3", c=clinic)
    return [tuple(r) for r in rows], [tuple(t) for t in tokens]


async def rotate_as(conn, vault, uid, **kwargs):
    kwargs.setdefault("from_version", 1)
    kwargs.setdefault("to_version", 2)
    # Not wrapped in `as_user`'s savepoint: that would roll back batches the engine had already finished, and the
    # engine's own per-batch savepoints are exactly what the failure tests exercise.
    await conn.exec_driver_sql("set local role careflow_app")
    await set_request_claims(conn, {"sub": str(uid), "role": "authenticated"})
    try:
        return await rotate_key_version(conn, vault, **kwargs)
    finally:
        await conn.exec_driver_sql("reset role")


# ---- the happy path, under real RLS ------------------------------------------------------------------


async def test_rotation_keeps_every_demographic_readable_under_the_new_version(db, vault):
    a, _, p = await seed(db)
    ids = await register(db, vault, p["fd_a"], a)

    report = await rotate_as(db, vault, p["fd_a"])
    assert (report.patients_rotated, report.batches) == (3, 1)
    assert report.tokens_written == 5  # name_dob x3 + phone x2 (one person has no phone)

    rows = {r.id: r for r in await q(db, "select * from mpi.patients where clinic_id = :c", c=a)}
    for pid, (name, dob, phone) in zip(ids, PEOPLE):
        r = rows[pid]
        assert (r.name_key_version, r.dob_key_version) == (2, 2)
        assert vault.decrypt(bytes(r.name_ct), field="name", key_version=2) == " ".join(name.split())
        assert vault.decrypt(bytes(r.dob_ct), field="dob", key_version=2) == dob
        if phone is None:
            assert (r.phone_ct, r.phone_key_version) == (None, None)
        else:
            assert r.phone_key_version == 2
            assert vault.decrypt(bytes(r.phone_ct), field="phone", key_version=2).startswith("+91")
        with pytest.raises(CryptoError):  # the old key no longer opens it
            vault.decrypt(bytes(r.name_ct), field="name", key_version=1)


async def test_identity_lookup_matches_through_the_new_tokens_and_old_ones_coexist(db, vault):
    a, _, p = await seed(db)
    ids = await register(db, vault, p["fd_a"], a)
    await rotate_as(db, vault, p["fd_a"])

    both = await q(db, "select patient_id, kind, key_version from mpi.identity_tokens where patient_id = :p order by kind, key_version", p=ids[0])
    assert [(k, v) for _, k, v in both] == [("name_dob", 1), ("name_dob", 2), ("phone", 1), ("phone", 2)]

    name, dob, phone = PEOPLE[0]
    for version in (2, 1):  # a v2 ingest matches the rotated person; a v1 ingest still does until the old key retires
        pipe = PatientIngestionPipeline(vault, key_version=version)
        async def again(c, pipe=pipe):
            return await pipe.ingest(c, clinic_id=a, origin="test", name=name, dob=dob, phone=phone, idempotency_key=f"idem-{_u()}")
        res = await as_user(db, p["fd_a"], again)
        assert (res.outcome, res.patient_id) == ("matched", ids[0])


async def test_new_tokens_are_exactly_what_a_fresh_ingest_would_write(db, vault):
    a, _, p = await seed(db)
    (pid, *_) = await register(db, vault, p["fd_a"], a)
    await rotate_as(db, vault, p["fd_a"])
    expected = derive_tokens(vault, name="Asha Venkatesh", dob_iso="1987-04-23", phone_canonical="+919876543210", key_version=2)
    stored = {k: bytes(t) for k, t in await q(db, "select kind, token from mpi.identity_tokens where patient_id = :p and key_version = 2", p=pid)}
    assert stored == expected


async def test_verify_passes_and_usage_shows_the_old_key_is_unreferenced_by_ciphertexts(db, vault):
    a, _, p = await seed(db)
    await register(db, vault, p["fd_a"], a)
    await rotate_as(db, vault, p["fd_a"])
    rep = await as_user(db, p["fd_a"], lambda c: verify_rotation(c, vault, to_version=2))
    assert rep.ok and rep.patients_checked == 3
    usage = await as_user(db, p["fd_a"], lambda c: version_usage(c, clinic_id=a))
    assert usage[1]["ciphertexts"] == 0  # safe to retire v1 ciphertext-wise
    assert usage[1]["tokens"] == 5 and usage[2] == {"ciphertexts": 8, "tokens": 5}


async def test_verify_catches_a_missing_new_token(db, vault):
    a, _, p = await seed(db)
    (pid, *_) = await register(db, vault, p["fd_a"], a)
    await rotate_as(db, vault, p["fd_a"])
    await db.execute(text("delete from mpi.identity_tokens where patient_id = :p and kind = 'phone' and key_version = 2"), {"p": pid})
    rep = await as_user(db, p["fd_a"], lambda c: verify_rotation(c, vault, to_version=2))
    assert not rep.ok and rep.token_problems == [pid]


# ---- idempotency and batching (rule 5) ----------------------------------------------------------------


async def test_rerun_is_a_no_op(db, vault):
    a, _, p = await seed(db)
    await register(db, vault, p["fd_a"], a)
    await rotate_as(db, vault, p["fd_a"])
    before = await snapshot(db, a)
    again = await rotate_as(db, vault, p["fd_a"])
    assert (again.patients_rotated, again.batches, again.tokens_written) == (0, 0, 0)
    assert await snapshot(db, a) == before


async def test_batches_carry_unique_idempotency_keys_and_ledger_rows(db, vault):
    a, _, p = await seed(db)
    await register(db, vault, p["fd_a"], a)
    report = await rotate_as(db, vault, p["fd_a"], batch_size=1)
    assert report.batches == 3 and len(set(report.batch_keys)) == 3
    ledger = await q(db, "select idempotency_key, patients_rotated, from_version, to_version, origin "
                         "from ops.key_rotation_batches where clinic_id = :c", c=a)
    assert sorted(r[0] for r in ledger) == sorted(report.batch_keys)
    assert all(r[1:] == (1, 1, 2, "test") for r in ledger)


# ---- refusal and failure leave data untouched ----------------------------------------------------------


@pytest.mark.parametrize("kwargs, error", [
    ({"to_version": 99}, UnknownKeyVersionError),   # unconfigured target
    ({"from_version": 99}, UnknownKeyVersionError),  # unconfigured source
    ({"to_version": 1}, RotationError),              # same version
    ({"to_version": 0}, RotationError),              # not a valid version
    ({"batch_size": 0}, RotationError),
    ({"batch_size": 100000}, RotationError),
])
async def test_invalid_request_aborts_before_touching_anything(db, vault, kwargs, error):
    a, _, p = await seed(db)
    await register(db, vault, p["fd_a"], a)
    before = await snapshot(db, a)
    ledger_before = await q(db, "select count(*) from ops.key_rotation_batches")
    with pytest.raises(error):
        await rotate_as(db, vault, p["fd_a"], **kwargs)
    assert await snapshot(db, a) == before
    assert await q(db, "select count(*) from ops.key_rotation_batches") == ledger_before


async def test_a_corrupt_ciphertext_rolls_the_whole_batch_back_and_stops(db, vault):
    a, _, p = await seed(db)
    ids = await register(db, vault, p["fd_a"], a)
    await db.execute(
        text("update mpi.patients set name_ct = set_byte(name_ct, 20, get_byte(name_ct, 20) # 255) where id = :i"), {"i": ids[1]}
    )
    before = await snapshot(db, a)
    with pytest.raises(RotationError) as err:
        await rotate_as(db, vault, p["fd_a"], batch_size=10)
    assert str(ids[1]) in str(err.value) and "Asha" not in str(err.value)  # an id, never a demographic
    assert await snapshot(db, a) == before  # all three untouched, no v2 token, no ledger row
    assert await q(db, "select count(*) from ops.key_rotation_batches where clinic_id = :c", c=a) == [(0,)]


async def test_a_failure_in_a_later_batch_keeps_earlier_batches_valid(db, vault):
    a, _, p = await seed(db)
    ids = sorted(await register(db, vault, p["fd_a"], a))
    await db.execute(
        text("update mpi.patients set dob_ct = set_byte(dob_ct, 20, get_byte(dob_ct, 20) # 255) where id = :i"), {"i": ids[2]}
    )
    with pytest.raises(RotationError):
        await rotate_as(db, vault, p["fd_a"], batch_size=1)
    versions = {r.id: r.name_key_version for r in await q(db, "select id, name_key_version from mpi.patients where clinic_id = :c", c=a)}
    assert versions == {ids[0]: 2, ids[1]: 2, ids[2]: 1}  # resumable: the two finished stay finished, the bad one is as it was
    rep = await as_user(db, p["fd_a"], lambda c: verify_rotation(c, vault, to_version=2))
    assert rep.ok and rep.patients_checked == 2


# ---- who can rotate (rule 8): RLS, not code ------------------------------------------------------------


async def test_only_front_desk_of_the_clinic_rotates_and_only_that_clinic(db, vault):
    a, b, p = await seed(db)
    await register(db, vault, p["fd_a"], a)
    await register(db, vault, p["fd_b"], b, PEOPLE[:1])
    before_a, before_b = await snapshot(db, a), await snapshot(db, b)

    for who in ("nurse_a", "auditor_a"):  # may read demographics (or not), may never rewrite them
        report = await rotate_as(db, vault, p[who])
        assert report.patients_rotated == 0
    assert (await snapshot(db, a), await snapshot(db, b)) == (before_a, before_b)

    report = await rotate_as(db, vault, p["fd_b"])  # front desk of B sees and rotates only B
    assert report.patients_rotated == 1
    assert await snapshot(db, a) == before_a
    assert {r[0] for r in await q(db, "select name_key_version from mpi.patients where clinic_id = :c", c=b)} == {2}


async def test_the_ledger_is_front_desk_only_and_never_edited(db):
    a, _b, p = await seed(db)
    row = {"c": a, "p": _u(), "k": f"rotate-test-{_u()}"}
    insert = ("insert into ops.key_rotation_batches (clinic_id, origin, from_version, to_version, patients_rotated, tokens_written, "
              "first_patient_id, last_patient_id, idempotency_key) values (:c, 'test', 1, 2, 1, 1, :p, :p, :k)")

    async def write(c):
        await c.execute(text(insert), row)

    for who in ("nurse_a", "auditor_a", "fd_b"):
        with pytest.raises(Exception, match="row-level security|permission denied"):
            await as_user(db, p[who], write)
    await as_user(db, p["fd_a"], write)
    for sql in ("update ops.key_rotation_batches set patients_rotated = 2", "delete from ops.key_rotation_batches"):
        async def edit(c, sql=sql):
            await c.execute(text(sql))
        with pytest.raises(Exception, match="permission denied"):
            await as_user(db, p["fd_a"], edit)
    seen_b = await as_user(db, p["fd_b"], lambda c: q(c, "select count(*) from ops.key_rotation_batches where clinic_id = :c", c=a))
    assert seen_b == [(0,)]


# ---- the script's own refusal path ---------------------------------------------------------------------


async def test_cli_refuses_an_unconfigured_target_before_any_connection(keys):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CAREFLOW_", "DATABASE_URL"))}
    env.update({k: v for k, v in keys.items() if k.endswith("_V1")})  # only v1 is configured
    run = subprocess.run(  # noqa: ASYNC221  (a short blocking call in a test is fine)
        [sys.executable, str(ROOT / "scripts" / "rotate_keys.py"), "--env-file", str(ROOT / "nonexistent.env"), "rotate", "--from", "1", "--to", "2"],
        capture_output=True, text=True, env=env, timeout=60, check=False,
    )
    assert run.returncode == 2
    assert "key_version 2" in run.stderr
    assert not any(secret in run.stdout + run.stderr for secret in keys.values())  # no key material ever printed
