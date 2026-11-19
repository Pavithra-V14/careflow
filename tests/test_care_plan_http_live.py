"""The care-plan flow driven over HTTP with real signed tokens, against the real database inside ONE NEVER-COMMITTED transaction (BUILD-SPEC.md 21 end-to-end tests; Appendix D Phase 6; owner decision 2026-11-18).

A doctor writes a plan -> it waits for a confirmation, nothing runs -> a nurse may not confirm, another clinic's doctor cannot see it -> the doctor confirms and the plan waits for a result -> a nurse records a
result over HTTP (audited, ids only) -> the plan list shows ids and status, never the plan text -> every human act is on the audit trail.

What this does NOT drive, on purpose: the WAKE-UP. Over HTTP a result only queues an outbox event; the worker (a different identity, in a different process) wakes the plan. That step, the step closing as met,
the referral proposal and the "a report with no value goes to a doctor, never closed as not met" path run against the same database in `tests/test_care_journey_db_live.py` with the graph wired to the SQL ports.
The harness follows tests/test_api_offers_live.py (one outer transaction rolled back at the end; the app's database dependency is replaced by the shared connection running as the restricted role).
"""

import base64
import os
import uuid
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from dotenv import dotenv_values
from fastapi import Depends
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from careflow import care_journey_sql as cj
from careflow.app import Settings, create_app, db_conn, verified_claims
from careflow.crypto import DemographicVault
from careflow.db import set_request_claims
from careflow.rules import RedFlagEngine
from tests.db_helpers import ensure_app_role_sa
from tests.test_api_role_matrix import PROJECT, bearer, fake_jwks_client, mint
from tests.test_care_journey_db_live import GOOD

ROOT = Path(__file__).resolve().parent.parent
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(ROOT / ".env").get("DATABASE_URL_MIGRATOR")
PLANS, RESULTS = "/v1/staff/care-plans", "/v1/staff/results"
PLAN_TEXT = "DEMO plan text: SYNTHETIC, not a clinical instruction"

pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured")]


class Clear:
    async def screen(self, conn, *, plan_id, clinic_id):
        return "clear"


async def _plan(_text: str) -> dict:
    return GOOD


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world():
    engine = create_async_engine(make_url(MIGRATOR or "").set(drivername="postgresql+asyncpg"), connect_args={"statement_cache_size": 0})
    conn = await engine.connect()
    outer = await conn.begin()
    previous = os.environ.get("FEATURE_CARE_JOURNEY")
    os.environ["FEATURE_CARE_JOURNEY"] = "1"
    try:
        await ensure_app_role_sa(conn)
        vault = DemographicVault.from_env({f"CAREFLOW_{k}_SECRET_V1": base64.b64encode(os.urandom(32)).decode() for k in ("ENC", "HMAC")})
        a, b = uuid.uuid4(), uuid.uuid4()
        who = {"doctor_a": (a, "doctor"), "nurse_a": (a, "nurse"), "fd_a": (a, "front_desk"), "doctor_b": (b, "doctor")}
        ids = {key: uuid.uuid4() for key in who}
        patient = uuid.uuid4()
        handle = (await conn.get_raw_connection()).driver_connection
        await handle.execute("insert into iam.clinics (id, name, origin) values ($1, 'CP A', 'test'), ($2, 'CP B', 'test')", a, b)
        await handle.execute("insert into iam.accounts (id, account_type, status, origin) values " + ", ".join(f"('{ids[k]}', 'staff', 'active', 'test')" for k in who))
        await handle.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values " + ", ".join(f"('{ids[k]}', '{c}', '{r}', 'test')" for k, (c, r) in who.items()))
        pct = "'\\x" + "01" * 40 + "'::bytea"
        await handle.execute(f"insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, origin, idempotency_key) values ('{patient}', '{a}', {pct}, 1, {pct}, 1, 'test', 'cp-p-{patient}')")
        components = cj.CareJourneyComponents(vault, RedFlagEngine(), _plan, "standard", "doctor", InMemorySaver(), screener=Clear())
        app = create_app(
            Settings(_env_file=None, supabase_url=PROJECT, database_url_app="postgresql://unused"), jwks=fake_jwks_client(), engine=object(), vault=vault, care_journey_components=components  # type: ignore[arg-type]
        )

        async def shared_db(claims=Depends(verified_claims)):
            async with conn.begin_nested():
                await conn.exec_driver_sql("set local role careflow_app")
                await set_request_claims(conn, claims)
                yield conn
                await conn.exec_driver_sql("reset role")
                await conn.execute(text("select set_config('request.jwt.claims', '', true)"))

        app.dependency_overrides[db_conn] = shared_db
        async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield type("World", (), {"conn": conn, "client": client, "ids": ids, "a": a, "b": b, "patient": patient})
    finally:
        if previous is None:
            os.environ.pop("FEATURE_CARE_JOURNEY", None)
        else:
            os.environ["FEATURE_CARE_JOURNEY"] = previous
        await outer.rollback()
        await conn.close()
        await engine.dispose()


def headers(w, who, key="new"):
    return {**bearer(mint(w.ids[who])), **({} if key is None else {"X-Idempotency-Key": f"k-{uuid.uuid4()}" if key == "new" else key})}


async def rows(w, sql, **params):
    return (await w.conn.execute(text(sql), params)).all()


async def test_a_plan_is_written_confirmed_and_a_result_recorded_over_http_with_every_act_on_record(world):
    w = world
    key = f"plan-{uuid.uuid4()}"
    body = {"clinic_id": str(w.a), "patient_id": str(w.patient), "plan_text": PLAN_TEXT}

    # 1. only a doctor writes a plan; the plan then WAITS for a doctor's confirmation and nothing runs
    assert (await w.client.post(PLANS, headers=headers(w, "nurse_a"), json=body)).status_code == 403
    assert (await w.client.post(PLANS, headers=headers(w, "doctor_b"), json=body)).status_code == 404  # another clinic's doctor does not see this clinic
    made = await w.client.post(PLANS, headers=headers(w, "doctor_a", key), json=body)
    assert made.status_code == 201 and made.json()["created"] is True and made.json()["status"] == "confirmation_requested" and made.json()["waiting_for"] == "confirmation"
    plan_id = made.json()["plan_id"]
    assert PLAN_TEXT not in made.text  # the answer carries ids and closed words, never the plan text
    assert (await rows(w, "select count(*) from ops.care_plan_waits where plan_id = :p", p=uuid.UUID(plan_id)))[0][0] == 0  # nothing runs before a doctor confirms
    again = await w.client.post(PLANS, headers=headers(w, "doctor_a", key), json=body)
    assert again.status_code == 201 and again.json()["plan_id"] == plan_id and again.json()["created"] is False  # idempotent on the key

    # 2. the list shows ids and status, never the text, and only to a nurse or doctor of the clinic
    listed = await w.client.get(f"{PLANS}?clinic_id={w.a}", headers=headers(w, "nurse_a", None))
    assert listed.status_code == 200 and [p["plan_id"] for p in listed.json()["plans"]] == [plan_id] and PLAN_TEXT not in listed.text
    assert (await w.client.get(f"{PLANS}?clinic_id={w.a}", headers=headers(w, "fd_a", None))).status_code == 403

    # 3. a nurse may not confirm; the doctor confirms and the plan waits for a result
    confirm = f"{PLANS}/{plan_id}/confirm"
    assert (await w.client.post(confirm, headers=headers(w, "nurse_a"), json={"action": "approve"})).status_code == 403
    assert (await w.client.post(confirm, headers=headers(w, "doctor_b"), json={"action": "approve"})).status_code in (403, 404)
    approved = await w.client.post(confirm, headers=headers(w, "doctor_a"), json={"action": "approve"})
    assert approved.status_code == 200 and approved.json()["status"] == "waiting" and approved.json()["waiting_for"] == "result"
    assert (await w.client.post(confirm, headers=headers(w, "doctor_a"), json={"action": "approve"})).status_code == 409  # no longer waiting for a confirmation

    # 4. a nurse records a result over HTTP (append-only, audited); a front desk person may not
    result = {"clinic_id": str(w.a), "patient_id": str(w.patient), "code": "DEMO-T1.a", "measures": {"demo_value": 5.0}, "verified": True, "valid_from": "2026-01-01T00:00:00Z"}
    assert (await w.client.post(RESULTS, headers=headers(w, "fd_a"), json=result)).status_code == 403
    rkey = f"result-{uuid.uuid4()}"
    recorded = await w.client.post(RESULTS, headers=headers(w, "nurse_a", rkey), json=result)
    assert recorded.status_code == 201 and recorded.json()["created"] is True
    replayed = await w.client.post(RESULTS, headers=headers(w, "nurse_a", rkey), json=result)
    assert replayed.json()["result_id"] == recorded.json()["result_id"] and replayed.json()["created"] is False

    # 5. every human act is on the audit trail, as ids and closed words only
    actions = [r[0] for r in await rows(w, "select action from audit.log where clinic_id = :c and action in ('care_plan.create', 'care_plan.confirm', 'result.record') order by id", c=w.a)]
    assert actions == ["care_plan.create", "care_plan.confirm", "result.record"]
    leaked = await rows(w, "select count(*) from audit.log where clinic_id = :c and details::text like :t", c=w.a, t=f"%{PLAN_TEXT[:20]}%")
    assert leaked[0][0] == 0
