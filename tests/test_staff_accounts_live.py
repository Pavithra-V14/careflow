"""Staff account management against the real database (migration 0034): the five iam.* functions, who may call them, the audit rows they write, and that the application role still cannot write iam directly.
Everything runs as the restricted `careflow_app` role with a verified account's claims inside ONE never-committed outer transaction (a rollback removes it all)."""

import json
import os
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db_helpers import aal2_claims, ensure_app_role_sa

ROOT = Path(__file__).resolve().parent.parent
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(ROOT / ".env").get("DATABASE_URL_MIGRATOR")
pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured")]
REFUSED = "staff change refused"


def _u() -> uuid.UUID:
    return uuid.uuid4()


async def raw(conn, sql):
    handle = await conn.get_raw_connection()
    await handle.driver_connection.execute(sql)


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


class World:
    pass


async def world(conn) -> World:
    """Clinic A (two admins, a nurse), clinic B (an admin), and one person who works for both clinics."""
    w = World()
    w.a, w.b = _u(), _u()
    roster = [("admin1", w.a, "admin"), ("admin2", w.a, "admin"), ("nurse", w.a, "nurse"), ("admin_b", w.b, "admin"), ("both", w.a, "nurse"), ("both", w.b, "nurse")]
    w.people = {k: _u() for k, _, _ in roster}
    await raw(
        conn,
        f"insert into iam.clinics (id, name, origin) values ('{w.a}', 'SA', 'test'), ('{w.b}', 'SB', 'test');\n"
        "insert into iam.accounts (id, account_type, status, origin) values " + ", ".join(f"('{i}', 'staff', 'active', 'test')" for i in w.people.values())
        + ";\ninsert into iam.staff_roles (account_id, clinic_id, role, origin) values " + ", ".join(f"('{w.people[k]}', '{c}', '{r}', 'test')" for k, c, r in roster) + ";",
    )
    return w


async def call(db, uid, sql, aal=None, **params):
    """Run one SELECT of a function as `uid` (claims set, optionally with a verified second factor). A refusal raises and rolls the savepoint back."""
    async with db.begin_nested():
        await db.exec_driver_sql("set local role careflow_app")
        claims = {"sub": str(uid), "role": "authenticated", "aal": aal} if aal == "aal1" else {"sub": str(uid), "role": "authenticated"} if aal == "none" else aal2_claims(uid)  # (aal1 and "none" are the weak claims a test passes; everything else carries a verified factor)
        await db.execute(text("select set_config('request.jwt.claims', :c, true)"), {"c": json.dumps(claims)})
        value = (await db.execute(text(sql), params)).scalar()
        await db.exec_driver_sql("reset role")
        return value


async def one(db, sql, **params):
    return (await db.execute(text(sql), params)).scalar()


INVITE = "select iam.invite_staff_account(:a, :c, :r, :k)"
GRANT = "select iam.grant_staff_role(:a, :c, :r, :k)"
REVOKE = "select iam.revoke_staff_role(:a, :c, :r, :k)"
STATUS = "select iam.set_staff_account_status(:a, :s, :k)"


async def test_an_admin_invites_a_new_account_with_one_role_and_an_audit_row_and_a_replay_writes_nothing_more(db):
    w = await world(db)
    new = _u()
    assert await call(db, w.people["admin1"], INVITE, a=new, c=w.a, r="nurse", k="inv-1") is True
    assert await one(db, "select status from iam.accounts where id = :a", a=new) == "invited"
    assert (await db.execute(text("select role, granted_by from iam.staff_roles where account_id = :a and revoked_at is null"), {"a": new})).all() == [("nurse", w.people["admin1"])]
    rows = (await db.execute(text("select actor_id, actor_role, details from audit.log where action = 'staff.invite' and resource_id = :a"), {"a": str(new)})).all()
    assert len(rows) == 1 and rows[0][0] == w.people["admin1"] and rows[0][1] == "admin" and set(rows[0][2]) == {"role", "role_scope"} and rows[0][2]["role"] == "nurse"
    assert await call(db, w.people["admin1"], INVITE, a=new, c=w.a, r="nurse", k="inv-1") is False  # the same key: a replay
    assert await one(db, "select count(*) from audit.log where action = 'staff.invite' and resource_id = :a", a=str(new)) == 1


@pytest.mark.parametrize(
    ("who", "role", "clinic"),
    [("nurse", "nurse", "a"), ("admin_b", "nurse", "a"), ("admin1", "queue_worker", "a"), ("admin1", "superuser", "a"), ("admin1", "nurse", "unknown")],
)
async def test_nobody_but_an_admin_of_the_clinic_may_invite_and_queue_worker_is_never_grantable(db, who, role, clinic):
    w = await world(db)
    new = _u()
    with pytest.raises(Exception, match=REFUSED):
        await call(db, w.people[who], INVITE, a=new, c=w.a if clinic == "a" else _u(), r=role, k=f"inv-{_u()}")
    assert await one(db, "select count(*) from iam.accounts where id = :a", a=new) == 0


async def test_an_existing_account_cannot_be_re_invited(db):
    w = await world(db)
    with pytest.raises(Exception, match=REFUSED):
        await call(db, w.people["admin1"], INVITE, a=w.people["nurse"], c=w.a, r="doctor", k="inv-x")


async def test_grant_and_revoke_a_role_are_idempotent_audited_and_admin_only(db):
    w = await world(db)
    nurse = w.people["nurse"]
    assert await call(db, w.people["admin1"], GRANT, a=nurse, c=w.a, r="doctor", k="g-1") is True
    assert await call(db, w.people["admin1"], GRANT, a=nurse, c=w.a, r="doctor", k="g-2") is False  # already held
    assert await one(db, "select count(*) from iam.staff_roles where account_id = :a and role = 'doctor' and revoked_at is null", a=nurse) == 1
    with pytest.raises(Exception, match=REFUSED):
        await call(db, nurse, GRANT, a=nurse, c=w.a, r="admin", k="g-self")  # nobody promotes themselves
    assert await call(db, w.people["admin1"], REVOKE, a=nurse, c=w.a, r="doctor", k="r-1") is True
    assert await one(db, "select count(*) from iam.staff_roles where account_id = :a and role = 'doctor' and revoked_at is null", a=nurse) == 0
    assert await one(db, "select count(*) from audit.log where action in ('staff.grant_role', 'staff.revoke_role') and resource_id = :a", a=str(nurse)) >= 2


async def test_a_clinic_always_keeps_an_active_administrator(db):
    w = await world(db)
    assert await call(db, w.people["admin1"], REVOKE, a=w.people["admin2"], c=w.a, r="admin", k="r-a2") is True  # one of two: fine
    with pytest.raises(Exception, match="active administrator"):
        await call(db, w.people["admin1"], REVOKE, a=w.people["admin1"], c=w.a, r="admin", k="r-a1")  # the last one: refused
    assert await one(db, "select count(*) from iam.staff_roles where clinic_id = :c and role = 'admin' and revoked_at is null", c=w.a) == 1


async def test_suspend_and_reinstate_need_admin_of_every_clinic_the_person_works_for_and_never_oneself(db):
    w = await world(db)
    nurse = w.people["nurse"]
    assert await call(db, w.people["admin1"], STATUS, a=nurse, s="suspended", k="s-1") is True
    assert await one(db, "select status from iam.accounts where id = :a", a=nurse) == "suspended"
    assert await call(db, w.people["admin1"], STATUS, a=nurse, s="suspended", k="s-2") is False  # already so
    assert await call(db, w.people["admin1"], STATUS, a=nurse, s="active", k="s-3") is True
    with pytest.raises(Exception, match=REFUSED):
        await call(db, w.people["admin1"], STATUS, a=w.people["both"], s="suspended", k="s-both")  # also works for clinic B, where this admin has no say
    with pytest.raises(Exception, match=REFUSED):
        await call(db, w.people["admin1"], STATUS, a=w.people["admin1"], s="suspended", k="s-self")
    with pytest.raises(Exception, match=REFUSED):
        await call(db, w.people["nurse"], STATUS, a=w.people["admin1"], s="suspended", k="s-nurse")


async def test_an_admin_cannot_activate_an_invited_account_only_the_person_with_a_second_factor_can(db):
    w = await world(db)
    new = _u()
    await call(db, w.people["admin1"], INVITE, a=new, c=w.a, r="nurse", k="inv-act")
    with pytest.raises(Exception, match=REFUSED):
        await call(db, w.people["admin1"], STATUS, a=new, s="active", k="s-act")  # invited is not reinstated by an admin
    with pytest.raises(Exception, match=REFUSED):
        await call(db, new, "select iam.activate_own_account()", aal="none")  # no second factor in the token
    with pytest.raises(Exception, match=REFUSED):
        await call(db, new, "select iam.activate_own_account()", aal="aal1")
    assert await call(db, new, "select iam.activate_own_account()", aal="aal2") is True
    assert await one(db, "select status from iam.accounts where id = :a", a=new) == "active"
    assert await call(db, new, "select iam.activate_own_account()", aal="aal2") is False  # a replay
    assert await one(db, "select count(*) from audit.log where action = 'staff.activate' and resource_id = :a", a=str(new)) == 1


async def test_the_application_role_still_cannot_write_iam_directly(db):
    w = await world(db)
    for statement in (
        f"insert into iam.accounts (id, account_type, status, origin) values ('{_u()}', 'staff', 'active', 'test')",
        f"update iam.accounts set status = 'active' where id = '{w.people['nurse']}'",
        f"insert into iam.staff_roles (account_id, clinic_id, role, origin) values ('{w.people['nurse']}', '{w.a}', 'admin', 'test')",
        f"update iam.staff_roles set revoked_at = now() where account_id = '{w.people['nurse']}'",
    ):
        with pytest.raises(Exception, match="permission denied"):
            async with db.begin_nested():
                await db.exec_driver_sql("set local role careflow_app")
                await db.execute(text("select set_config('request.jwt.claims', :c, true)"), {"c": json.dumps({"sub": str(w.people["admin1"]), "role": "authenticated"})})
                await db.exec_driver_sql(statement)
