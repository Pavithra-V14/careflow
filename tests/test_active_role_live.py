"""The ACTIVE ROLE at the database (migration 0028; docs/design/active-role.md sections 8.11 and 9), against the real database inside ONE NEVER-COMMITTED transaction per test.

It sets `request.jwt.claims` directly, exactly as the application does, and asks the two RLS helpers and the new functions what they say as the restricted role `careflow_app`. What it proves:
no key = every live grant counts (today); the key present with ANY value turns narrowing on and a malformed value grants nothing (null, string, array, wrong types, a clinic the caller lacks, a role the caller
lacks, another account's grant, a revoked grant, a suspended or invited account); narrowing can only remove (a grant never appears that was not there); a row-level-security policy that uses the helpers narrows
with no per-policy change (iam.clinics); the one-statement check `iam.active_grant_ok(set_config(...))`; and invalid JSON in the setting fails with an error, never open.
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
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(ROOT / ".env").get("DATABASE_URL_MIGRATOR")
pytestmark = [pytest.mark.asyncio(loop_scope="function"), pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured")]


class World:
    """Attributes are set by the fixture (a bag of ids and the connection)."""

    def __getattr__(self, name):  # lets the checker accept attributes the fixture assigns
        raise AttributeError(name)


@pytest_asyncio.fixture
async def w():
    conn = await asyncpg.connect((MIGRATOR or "").replace("+asyncpg", ""), statement_cache_size=0)
    tx = conn.transaction()
    await tx.start()
    try:
        await ensure_app_role(conn)
        world = World()
        world.conn = conn
        world.c1, world.c2, world.c3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        world.multi, world.other, world.invited, world.suspended, world.patient, world.peer = (uuid.uuid4() for _ in range(6))
        await conn.execute("insert into iam.clinics (id, name, origin) values ($1, 'AR One', 'test'), ($2, 'AR Two', 'test'), ($3, 'AR Three', 'test')", world.c1, world.c2, world.c3)
        for account, kind, status in (
            (world.multi, "staff", "active"), (world.other, "staff", "active"), (world.peer, "staff", "active"), (world.invited, "staff", "invited"), (world.suspended, "staff", "suspended"), (world.patient, "patient", "active"),
        ):
            await conn.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, $2, $3, 'test')", account, kind, status)
        grants = [
            (world.multi, world.c1, "nurse"), (world.multi, world.c1, "doctor"), (world.multi, world.c2, "admin"),
            (world.other, world.c1, "front_desk"), (world.peer, world.c2, "front_desk"), (world.invited, world.c1, "nurse"), (world.suspended, world.c1, "nurse"),
        ]
        for account, clinic, role in grants:
            await conn.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, $3, 'test')", account, clinic, role)
        await conn.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin, revoked_at) values ($1, $2, 'auditor', 'test', now())", world.multi, world.c1)  # a REVOKED grant
        yield world
    finally:
        await tx.rollback()
        await conn.close()


def claims(sub, **extra):
    return json.dumps({"sub": str(sub), "role": "authenticated", **extra})


def active(clinic, role):
    return {"careflow_active": {"clinic_id": str(clinic), "role": role}}


async def as_caller(conn, claim_json):
    """Set the claims the way the application does, then become the restricted role."""
    await conn.execute("reset role")
    await conn.execute("select set_config('request.jwt.claims', $1, true)", claim_json)
    await conn.execute("set local role careflow_app")


async def asks(w, claim_json, clinic, roles, any_clinic=False):
    await as_caller(w.conn, claim_json)
    if any_clinic:
        return await w.conn.fetchval("select iam.has_any_role($1::text[])", roles)
    return await w.conn.fetchval("select iam.has_clinic_role($1, $2::text[])", clinic, roles)


@pytest.mark.asyncio
async def test_without_the_key_every_live_grant_counts_exactly_as_before(w):
    sub = w.multi
    assert await asks(w, claims(sub), w.c1, ["nurse"]) and await asks(w, claims(sub), w.c1, ["doctor"]) and await asks(w, claims(sub), w.c2, ["admin"])
    assert await asks(w, claims(sub), None, ["admin"], any_clinic=True)
    assert not await asks(w, claims(sub), w.c1, ["auditor"])  # revoked: never counted
    assert not await asks(w, claims(sub), w.c3, ["nurse"])
    assert await w.conn.fetchval("select iam.effective_roles($1)", w.c1) == ["doctor", "nurse"]
    assert await w.conn.fetchval("select iam.role_scope()") == "all_grants"


@pytest.mark.asyncio
async def test_a_well_formed_active_claim_counts_only_that_one_grant_and_everything_else_stops_counting(w):
    claim = claims(w.multi, **active(w.c1, "nurse"))
    assert await asks(w, claim, w.c1, ["nurse"])
    assert not await asks(w, claim, w.c1, ["doctor"])  # held, but not the active one
    assert not await asks(w, claim, w.c2, ["admin"])  # held at another clinic
    assert await asks(w, claim, w.c1, ["nurse", "doctor", "admin", "front_desk", "auditor"])  # the active nurse grant is in the list: still true
    assert await asks(w, claim, None, ["nurse"], any_clinic=True) and not await asks(w, claim, None, ["admin"], any_clinic=True)
    assert await w.conn.fetchval("select iam.effective_roles($1)", w.c1) == ["nurse"]
    assert await w.conn.fetchval("select iam.effective_roles($1)", w.c2) == []
    assert await w.conn.fetchval("select iam.role_scope()") == "active"
    assert await w.conn.fetchval("select iam.active_grant_ok('ignored')") is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "shape",
    [
        None, "nurse", ["nurse"], 5, True, {}, {"clinic_id": "x"}, {"role": "nurse"}, {"clinic_id": 5, "role": "nurse"}, {"clinic_id": "{c1}", "role": 5}, {"clinic_id": "{c1}", "role": None},
        {"clinic_id": "{c3}", "role": "nurse"}, {"clinic_id": "{c1}", "role": "admin"}, {"clinic_id": "{c1}", "role": "NURSE"}, {"clinic_id": "{c1}".upper(), "role": "nurse"}, {"clinic_id": ["{c1}"], "role": "nurse"},
    ],
)
async def test_a_malformed_or_unheld_claim_grants_nothing_and_never_falls_back_to_no_narrowing(w, shape):
    def fill(value):
        if isinstance(value, str):
            return value.replace("{c1}", str(w.c1)).replace("{C1}", str(w.c1).upper()).replace("{c3}", str(w.c3))
        if isinstance(value, dict):
            return {k: fill(v) for k, v in value.items()}
        if isinstance(value, list):
            return [fill(v) for v in value]
        return value

    claim = claims(w.multi, careflow_active=fill(shape))
    for clinic, roles in ((w.c1, ["nurse"]), (w.c1, ["doctor"]), (w.c2, ["admin"]), (w.c1, ["nurse", "doctor", "admin"])):
        assert not await asks(w, claim, clinic, roles), (shape, roles)
    assert not await asks(w, claim, None, ["nurse", "doctor", "admin", "front_desk"], any_clinic=True)
    assert await w.conn.fetchval("select iam.effective_roles($1)", w.c1) == []
    assert await w.conn.fetchval("select iam.active_grant_ok('x')") is False
    assert await w.conn.fetchval("select iam.role_scope()") == "active"  # the KEY alone turned narrowing on


@pytest.mark.asyncio
async def test_narrowing_never_adds_a_grant_someone_else_or_a_dead_account_holds(w):
    # another account's grant named in my claim: nothing (the claim is checked against MY live grants)
    assert not await asks(w, claims(w.other, **active(w.c1, "nurse")), w.c1, ["nurse"])
    assert not await asks(w, claims(w.other, **active(w.c1, "nurse")), w.c1, ["front_desk"])  # and my own grant is not the one named, so it stops counting too
    assert await asks(w, claims(w.other, **active(w.c1, "front_desk")), w.c1, ["front_desk"])
    # a revoked grant, an invited or suspended account, a patient, an unknown account
    assert not await asks(w, claims(w.multi, **active(w.c1, "auditor")), w.c1, ["auditor"])
    for dead in (w.invited, w.suspended, w.patient, uuid.uuid4()):
        claim = claims(dead, **active(w.c1, "nurse"))
        assert not await asks(w, claim, w.c1, ["nurse"])
        assert await w.conn.fetchval("select iam.active_grant_ok('x')") is False
    # no identity at all
    assert not await asks(w, json.dumps({"role": "authenticated", **active(w.c1, "nurse")}), w.c1, ["nurse"])


@pytest.mark.asyncio
async def test_a_row_level_security_policy_narrows_with_no_per_policy_change(w):
    await as_caller(w.conn, claims(w.multi))
    seen = {r["id"] for r in await w.conn.fetch("select id from iam.clinics where id = any($1)", [w.c1, w.c2, w.c3])}
    assert seen == {w.c1, w.c2}  # no narrowing: every clinic where the caller holds a role
    await as_caller(w.conn, claims(w.multi, **active(w.c1, "nurse")))
    assert {r["id"] for r in await w.conn.fetch("select id from iam.clinics where id = any($1)", [w.c1, w.c2, w.c3])} == {w.c1}
    await as_caller(w.conn, claims(w.multi, **active(w.c2, "admin")))
    assert {r["id"] for r in await w.conn.fetch("select id from iam.clinics where id = any($1)", [w.c1, w.c2, w.c3])} == {w.c2}
    await as_caller(w.conn, claims(w.multi, careflow_active=None))
    assert await w.conn.fetch("select id from iam.clinics where id = any($1)", [w.c1, w.c2, w.c3]) == []  # malformed: nothing, not everything


@pytest.mark.asyncio
async def test_a_caller_always_reads_their_own_grants_whatever_the_active_claim(w):
    await as_caller(w.conn, claims(w.multi, **active(w.c1, "nurse")))
    rows = await w.conn.fetch("select clinic_id, role from iam.staff_roles where account_id = $1 and revoked_at is null order by clinic_id, role", w.multi)
    assert {(r["clinic_id"], r["role"]) for r in rows} == {(w.c1, "nurse"), (w.c1, "doctor"), (w.c2, "admin")}  # staff_roles_select_self is by account, not by role: the picker keeps working


@pytest.mark.asyncio
async def test_admin_reads_follow_the_active_role_so_an_admin_under_another_role_loses_admin_reads(w):
    admin_claims = claims(w.multi, **active(w.c2, "admin"))
    await as_caller(w.conn, admin_claims)
    assert await w.conn.fetchval("select count(*) from iam.staff_roles where clinic_id = $1 and account_id = $2", w.c2, w.peer) == 1  # as clinic two's admin: a colleague's grant is readable
    await as_caller(w.conn, claims(w.multi, **active(w.c1, "nurse")))
    assert await w.conn.fetchval("select count(*) from iam.staff_roles where clinic_id = $1 and account_id = $2", w.c2, w.peer) == 0  # as a clinic-one nurse: it is not


@pytest.mark.asyncio
async def test_invalid_json_in_the_setting_is_an_error_never_open(w):
    await w.conn.execute("reset role")
    await w.conn.execute("select set_config('request.jwt.claims', 'this is not json', true)")
    with pytest.raises(asyncpg.PostgresError):
        await w.conn.fetchval("select iam.narrowing_on()")


@pytest.mark.asyncio
async def test_the_new_functions_are_not_callable_by_public_and_only_three_are_granted_to_the_app_role(w):
    await w.conn.execute("reset role")
    granted = {
        r["proname"]
        for r in await w.conn.fetch(
            "select p.proname from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname = 'iam' and p.proname = any($1) and has_function_privilege('careflow_app', p.oid, 'execute')",
            ["narrowing_on", "grant_is_active", "effective_roles", "active_grant_ok", "role_scope"],
        )
    }
    assert granted == {"effective_roles", "active_grant_ok", "role_scope"}
    public = await w.conn.fetchval(
        "select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname = 'iam' and p.proname = any($1) and has_function_privilege('public', p.oid, 'execute')",
        ["narrowing_on", "grant_is_active", "effective_roles", "active_grant_ok", "role_scope"],
    )
    assert public == 0
