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
from careflow.messages import decrypt_body, encrypt_body
from careflow.mpi import PatientIngestionPipeline, derive_tokens
from careflow.rotation import (
    RotationError,
    message_version_usage,
    rotate_key_version,
    rotate_message_bodies,
    verify_message_bodies,
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
    "msg.threads": "0011_msg_threads_messages.sql",
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


# ---- message bodies (migration 0011): rotated under the migrator connection, never the app role -------------------------


async def _claims(conn, uid):
    """The erased-patient guard also fires for the table owner, so seeding as the owner sets a real caller's claims."""
    await set_request_claims(conn, {"sub": str(uid), "role": "authenticated"})


async def _clear_claims(conn):
    await conn.execute(text("select set_config('request.jwt.claims', '', true)"))


async def add_thread(conn, uid, clinic, patient, topic):
    await _claims(conn, uid)
    try:
        return (
            await conn.execute(
                text(
                    "insert into msg.threads (clinic_id, patient_id, topic, created_by, origin, idempotency_key) "
                    "values (:c, :p, :t, :u, 'test', :k) returning id"
                ),
                {"c": clinic, "p": patient, "t": topic, "u": uid, "k": f"th-{_u()}"},
            )
        ).scalar_one()
    finally:
        await _clear_claims(conn)


async def add_message(conn, vault, uid, clinic, thread, topic, body, *, version=1, message_id=None, body_ct=None):
    enc = encrypt_body(vault, body, clinic_id=clinic, thread_id=thread, key_version=version, message_id=message_id)
    await _claims(conn, uid)
    try:
        await conn.execute(
            text(
                "insert into msg.messages (id, clinic_id, origin, topic, thread_id, sender_kind, recorded_by, body_ct, body_key_version, idempotency_key) "
                "values (:i, :c, 'test', :t, :th, 'staff', :u, :ct, :v, :k)"
            ),
            {"i": enc.message_id if message_id is None else message_id, "c": clinic, "t": topic, "th": thread, "u": uid,
             "ct": body_ct or enc.body_ct, "v": version, "k": f"ms-{_u()}"},
        )
    finally:
        await _clear_claims(conn)
    return enc.message_id


async def seed_messages(conn, vault, texts):
    """Clinic A: a care thread and an appointments thread (nurse), clinic B: an appointments thread (front desk)."""
    a, b, p = await seed(conn)
    pa = (await register(conn, vault, p["fd_a"], a, people=[PEOPLE[0]]))[0]
    pb = (await register(conn, vault, p["fd_b"], b, people=[PEOPLE[1]]))[0]
    care = await add_thread(conn, p["nurse_a"], a, pa, "care")
    appt = await add_thread(conn, p["nurse_a"], a, pa, "appointments")
    appt_b = await add_thread(conn, p["fd_b"], b, pb, "appointments")
    ids = []
    for i, body in enumerate(texts):
        thread, clinic, topic, uid = [(care, a, "care", p["nurse_a"]), (appt, a, "appointments", p["nurse_a"]),
                                      (appt_b, b, "appointments", p["fd_b"])][i % 3]
        ids.append((await add_message(conn, vault, uid, clinic, thread, topic, body), clinic, thread))
    return a, b, p, ids


async def test_rotation_covers_message_bodies_and_they_decrypt_under_the_new_version(db, vault):
    texts = [f"message number {i} private words" for i in range(6)]
    _a, _b, _p, ids = await seed_messages(db, vault, texts)
    assert await message_version_usage(db) == {1: 6}
    rep = await rotate_message_bodies(db, vault, from_version=1, to_version=2, batch_size=2)
    assert (rep.messages_rotated, rep.batches) == (6, 3)  # clinic A: 4 messages in 2 batches; clinic B: 2 in 1
    assert await message_version_usage(db) == {2: 6}
    for (mid, clinic, thread), body in zip(ids, texts, strict=True):
        row = (await q(db, "select body_ct, body_key_version from msg.messages where id = :i", i=mid))[0]
        assert row.body_key_version == 2
        assert decrypt_body(vault, bytes(row.body_ct), clinic_id=clinic, thread_id=thread, message_id=mid, key_version=2) == body
    ledger = await q(db, "select sum(messages_rotated), count(*) from msg.message_rotation_batches where from_version = 1 and to_version = 2")
    assert ledger[0][0] == 6 and ledger[0][1] == len(rep.batch_keys)
    verified = await verify_message_bodies(db, vault, to_version=2)
    assert verified.ok and verified.messages_checked == 6


async def test_message_rotation_resumes_and_a_second_run_does_nothing(db, vault):
    await seed_messages(db, vault, ["one", "two", "three"])
    first = await rotate_message_bodies(db, vault, from_version=1, to_version=2, batch_size=1)
    assert first.messages_rotated == 3 and first.batches == 3
    again = await rotate_message_bodies(db, vault, from_version=1, to_version=2)
    assert (again.messages_rotated, again.batches) == (0, 0)


async def test_an_unreadable_body_stops_the_run_names_only_the_id_and_rolls_back_the_batch(db, vault):
    _a, _b, p, ids = await seed_messages(db, vault, ["good one", "good two"])
    _mid, clinic, thread = ids[0]
    bogus = encrypt_body(vault, "secret sentence SHOULD-NOT-APPEAR", clinic_id=clinic, thread_id=thread, key_version=1)  # bound to ANOTHER message id
    bad_id = await add_message(db, vault, p["nurse_a"], clinic, thread, "care", "placeholder", message_id=_u(), body_ct=bogus.body_ct)
    with pytest.raises(RotationError) as exc:
        await rotate_message_bodies(db, vault, from_version=1, to_version=2, clinic_id=clinic)
    assert str(bad_id) in str(exc.value) and "SHOULD-NOT-APPEAR" not in str(exc.value)
    assert await message_version_usage(db, clinic_id=clinic) == {1: 3}  # the whole failed batch rolled back: nothing moved
    report = await verify_message_bodies(db, vault, to_version=1, clinic_id=clinic)
    assert report.unreadable == [bad_id] and not report.ok


async def test_the_message_usage_count_is_unscoped_and_ignores_erased_bodies(db, vault):
    """The count sees every clinic and every topic (care included) because it runs as the migrator; an erased body is not counted."""
    _a, b, _p, ids = await seed_messages(db, vault, ["care text", "appointments text", "other clinic text"])
    assert await message_version_usage(db) == {1: 3}
    assert await message_version_usage(db, clinic_id=b) == {1: 1}
    await db.execute(text("update msg.messages set body_ct = null, body_key_version = null, erased_at = now() where id = :i"), {"i": ids[0][0]})
    assert await message_version_usage(db) == {1: 2}
    rep = await rotate_message_bodies(db, vault, from_version=1, to_version=2)
    assert rep.messages_rotated == 2  # the erased message is never rotated


async def test_message_rotation_refuses_to_run_as_the_app_role(db, vault):
    await seed_messages(db, vault, ["only one"])
    await db.exec_driver_sql("set local role careflow_app")
    try:
        with pytest.raises(RotationError, match="bypasses row-level security"):
            await rotate_message_bodies(db, vault, from_version=1, to_version=2)
        with pytest.raises(RotationError, match="bypasses row-level security"):
            await message_version_usage(db)
        with pytest.raises(RotationError, match="bypasses row-level security"):
            await verify_message_bodies(db, vault, to_version=1)
    finally:
        await db.exec_driver_sql("reset role")
    assert await message_version_usage(db) == {1: 1}  # nothing was changed


async def test_message_usage_is_nonzero_while_bodies_use_a_key(db, vault):
    """The retirement checklist reads this count; it must report bodies still on the old version."""
    await seed_messages(db, vault, ["still on version one"])
    assert (await message_version_usage(db)).get(1, 0) == 1
    await rotate_message_bodies(db, vault, from_version=1, to_version=2)
    assert (await message_version_usage(db)).get(1, 0) == 0
