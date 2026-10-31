"""The configured paths of the offer routes (GET /v1/staff/scheduling/offers/draft, POST .../offers/send), against the real database, inside ONE NEVER-COMMITTED transaction.

WHY A SEPARATE HARNESS. A send writes an ENCRYPTED MESSAGE (and a thread and an audit row) and msg.messages, msg.threads and audit.log are append-only for every role, so a committed fixture
could never be cleaned up; worse, a committed message on key version 1 breaks the key-rotation tests' precondition for the whole shared database (they assert there is NO committed message on
version 1). So, like tests/test_api_screen_message_live.py, the app is driven over HTTP with real signed tokens and the real auth dependency, its database dependency is replaced by one shared
connection inside an outer transaction (each request in a savepoint with `set local role careflow_app` and the verified claims), and everything is rolled back at the end.

The routes' ROLE MATRIX (who may call them, the refusals, the unconfigured 503) is in tests/test_api_role_matrix.py (MATRIX); this file covers only what needs a configured send.
What it does not cover (same documented exception as the screen harness, docs/design/role-matrix-exceptions.md): the real pooler engine and commit-before-response.
"""

import base64
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from dotenv import dotenv_values
from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from careflow.app import (
    Settings,
    create_app,
    db_conn,
    scheduling_settings,
    verified_claims,
)
from careflow.crypto import DemographicVault
from careflow.db import set_request_claims
from careflow.scheduling_agent import SchedulingSettings
from tests.db_helpers import ensure_app_role_sa
from tests.test_api_role_matrix import PROJECT, bearer, fake_jwks_client, mint

ROOT = Path(__file__).resolve().parent.parent
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(ROOT / ".env").get("DATABASE_URL_MIGRATOR")
DRAFT = "/v1/staff/scheduling/offers/draft"
SEND = "/v1/staff/scheduling/offers/send"
TEMPLATE = "TEST-TEMPLATE-NOT-A-CLINICAL-TEXT"

pytestmark = [
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured"),
]


def _u() -> uuid.UUID:
    return uuid.uuid4()


async def raw(conn, sql):
    handle = await conn.get_raw_connection()
    await handle.driver_connection.execute(sql)


def configured() -> SchedulingSettings:
    # test inputs only: stand-ins for the clinician-owned values, not recommendations
    return SchedulingSettings(
        _env_file=None, offer_expiry_seconds=3600, hold_lifetime_seconds=900, backfill_candidates=2, accept_confidence_threshold=0.5, offer_message_template=TEMPLATE,
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world():
    engine = create_async_engine(make_url(MIGRATOR or "").set(drivername="postgresql+asyncpg"), connect_args={"statement_cache_size": 0})
    conn = await engine.connect()
    outer = await conn.begin()
    try:
        await ensure_app_role_sa(conn)
        k = lambda: base64.b64encode(os.urandom(32)).decode()
        vault = DemographicVault.from_env({"CAREFLOW_ENC_SECRET_V1": k(), "CAREFLOW_HMAC_SECRET_V1": k()})
        a, b = _u(), _u()
        who = {"fd_a": (a, "front_desk"), "nurse_a": (a, "nurse"), "doctor_a": (a, "doctor"), "fd_b": (b, "front_desk")}
        ids = {key: _u() for key in who}
        await raw(
            conn,
            f"insert into iam.clinics (id, name, origin) values ('{a}', 'Offer A', 'test'), ('{b}', 'Offer B', 'test'); "
            "insert into iam.accounts (id, account_type, status, origin) values " + ", ".join(f"('{ids[key]}', 'staff', 'active', 'test')" for key in who) + "; "
            "insert into iam.staff_roles (account_id, clinic_id, role, origin) values " + ", ".join(f"('{ids[key]}', '{c}', '{r}', 'test')" for key, (c, r) in who.items()) + ";",
        )
        pct = "'\\x" + "01" * 40 + "'::bytea"
        provider, location, vt, vt_b, slot_ok, slot_2, slot_3, slot_booked = (_u() for _ in range(8))
        top, second, third, booker, booking = _u(), _u(), _u(), _u(), _u()
        now = datetime.now(UTC)
        start = (now + timedelta(days=30)).replace(minute=0, second=0, microsecond=0)
        await raw(
            conn,
            f"insert into sched.providers (id, clinic_id, display_name, origin, idempotency_key) values ('{provider}', '{a}', 'P', 'test', 'o-prov-{provider}'); "
            f"insert into sched.locations (id, clinic_id, name, origin, idempotency_key) values ('{location}', '{a}', 'L', 'test', 'o-loc-{location}'); "
            f"insert into sched.visit_types (id, clinic_id, name, duration_minutes, origin, idempotency_key) values ('{vt}', '{a}', 'V', 30, 'test', 'o-vt-{vt}'), ('{vt_b}', '{b}', 'V', 30, 'test', 'o-vtb-{vt_b}'); "
            f"insert into sched.priority_levels (clinic_id, code, label, rank, approved_by, origin, idempotency_key) values "
            f"('{a}', 'level_one', 'Test one', 1, '{ids['doctor_a']}', 'test', 'o-pl1-{a}'), ('{a}', 'level_two', 'Test two', 2, '{ids['doctor_a']}', 'test', 'o-pl2-{a}'); "
            + " ".join(
                f"insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, origin, idempotency_key) values ('{p}', '{a}', {pct}, 1, {pct}, 1, 'test', 'o-p-{p}'); "
                for p in (top, second, third, booker)
            )
            + f"insert into sched.waitlist (clinic_id, patient_id, visit_type_id, priority_code, joined_at, origin, idempotency_key) values "
            f"('{a}', '{second}', '{vt}', 'level_two', '{(now - timedelta(days=3)).isoformat()}', 'test', 'o-w2-{second}'), "
            f"('{a}', '{top}', '{vt}', 'level_one', '{(now - timedelta(days=1)).isoformat()}', 'test', 'o-w1-{top}'), "
            f"('{a}', '{third}', '{vt}', 'level_two', '{(now - timedelta(days=2)).isoformat()}', 'test', 'o-w3-{third}'); "
            + " ".join(
                f"insert into sched.slots (id, clinic_id, provider_id, location_id, visit_type_id, starts_at, ends_at, status, origin, idempotency_key) values "
                f"('{s}', '{a}', '{provider}', '{location}', '{vt}', '{(start + timedelta(hours=i)).isoformat()}', '{(start + timedelta(hours=i, minutes=30)).isoformat()}', 'open', 'test', 'o-s-{s}'); "
                for i, s in enumerate((slot_ok, slot_2, slot_3))
            )
            + f"insert into sched.slots (id, clinic_id, provider_id, location_id, visit_type_id, starts_at, ends_at, status, origin, idempotency_key) values "
            f"('{slot_booked}', '{a}', '{provider}', '{location}', '{vt}', '{(start + timedelta(hours=9)).isoformat()}', '{(start + timedelta(hours=9, minutes=30)).isoformat()}', 'booked', 'test', 'o-s-{slot_booked}'); "
            f"insert into sched.bookings (id, clinic_id, slot_id, patient_id, origin, idempotency_key) values ('{booking}', '{a}', '{slot_booked}', '{booker}', 'test', 'o-b-{booking}'); ",
        )
        app = create_app(Settings(supabase_url=PROJECT, database_url_app="postgresql://unused"), jwks=fake_jwks_client(), engine=object(), vault=vault)  # type: ignore[arg-type]

        async def shared_db(claims=Depends(verified_claims)):
            async with conn.begin_nested():
                await conn.exec_driver_sql("set local role careflow_app")
                await set_request_claims(conn, claims)
                yield conn
                await conn.exec_driver_sql("reset role")
                await conn.execute(text("select set_config('request.jwt.claims', '', true)"))

        app.dependency_overrides[db_conn] = shared_db
        app.dependency_overrides[scheduling_settings] = configured
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                yield type("World", (), {
                    "conn": conn, "client": client, "ids": ids, "a": a, "b": b, "vt": vt, "vt_b": vt_b, "slots": [slot_ok, slot_2, slot_3], "top": top, "second": second, "third": third, "booking": booking, "slot_booked": slot_booked,
                })
    finally:
        await outer.rollback()
        await conn.close()
        await engine.dispose()


@pytest_asyncio.fixture(loop_scope="module")
async def w(world):
    sp = await world.conn.begin_nested()
    try:
        yield world
    finally:
        await sp.rollback()


def headers(w, who, key="new"):
    return {**bearer(mint(w.ids[who])), **({} if key is None else {"X-Idempotency-Key": f"k-{_u()}" if key == "new" else key})}


async def rows(w, sql, **params):
    return (await w.conn.execute(text(sql), params)).all()


async def draft(w, who="fd_a"):
    return await w.client.get(f"{DRAFT}?clinic_id={w.a}&visit_type_id={w.vt}", headers=headers(w, who, None))


def send_body(w, slot, waitlist_id, **over):
    return {"clinic_id": str(w.a), "visit_type_id": str(w.vt), "slot_id": str(slot), "waitlist_id": str(waitlist_id), **over}


async def waitlist_id(w, patient):
    return (await rows(w, "select id from sched.waitlist where patient_id = :p", p=patient))[0][0]


async def test_the_draft_is_computed_in_clinical_priority_order_then_wait_and_stores_nothing(w):
    out = await draft(w)
    assert out.status_code == 200 and out.json()["status"] == "ok"
    candidates = out.json()["data"]["candidates"]
    assert [c["patient_id"] for c in candidates] == [str(w.top), str(w.second)]  # level one first although it joined later; then the longer wait; backfill_candidates = 2
    assert out.json()["data"]["draft"]["template"] == TEMPLATE and out.json()["data"]["draft"]["candidate_patient_id"] == str(w.top)
    assert (await rows(w, "select count(*) from sched.holds where clinic_id = :c", c=w.a))[0][0] == 0 and (await rows(w, "select count(*) from msg.messages where clinic_id = :c", c=w.a))[0][0] == 0  # a draft stores nothing


async def test_a_send_holds_the_slot_for_the_candidate_and_records_the_template_encrypted_once_as_the_person(w):
    top = await waitlist_id(w, w.top)
    key = f"k-{_u()}"
    ok = await w.client.post(SEND, headers=headers(w, "fd_a", key), json=send_body(w, w.slots[0], top))
    assert ok.status_code == 200 and ok.json()["replayed"] is False and ok.json()["patient_id"] == str(w.top) and ok.json()["slot_id"] == str(w.slots[0])
    again = await w.client.post(SEND, headers=headers(w, "fd_a", key), json=send_body(w, w.slots[0], top))
    assert again.status_code == 200 and again.json()["replayed"] is True and again.json()["message_id"] == ok.json()["message_id"] and again.json()["hold_id"] == ok.json()["hold_id"]
    assert [r[0] for r in await rows(w, "select patient_id from sched.holds where slot_id = :s", s=w.slots[0])] == [w.top]
    (msg,) = await rows(w, "select topic, sender_kind, recorded_by, body_ct from msg.messages where id = :i", i=uuid.UUID(ok.json()["message_id"]))
    assert (msg.topic, msg.sender_kind, msg.recorded_by) == ("appointments", "staff", w.ids["fd_a"]) and TEMPLATE.encode() not in bytes(msg.body_ct)
    (audit,) = await rows(w, "select actor_id, actor_role, details, clinic_id from audit.log where resource_type = 'offer' and resource_id = :r and action = 'offer.send'", r=ok.json()["hold_id"])
    assert (audit.actor_id, audit.actor_role, audit.clinic_id) == (w.ids["fd_a"], "front_desk", w.a)
    details = json.loads(audit.details) if isinstance(audit.details, str) else audit.details
    assert details == {"slot_id": str(w.slots[0]), "patient_id": str(w.top), "replayed": False}
    assert len(await rows(w, "select 1 from audit.log where resource_id = :r and action = 'offer.send'", r=ok.json()["hold_id"])) == 1  # the replay wrote no second row


async def test_a_held_slot_an_unranked_candidate_and_a_foreign_slot_are_all_refused_and_nothing_is_written(w):
    top, third = await waitlist_id(w, w.top), await waitlist_id(w, w.third)
    assert (await w.client.post(SEND, headers=headers(w, "fd_a"), json=send_body(w, w.slots[1], top))).status_code == 200
    held_again = await w.client.post(SEND, headers=headers(w, "fd_a"), json=send_body(w, w.slots[1], top))
    assert held_again.status_code == 409  # the slot is held now
    before = (await rows(w, "select count(*) from msg.messages where clinic_id = :c", c=w.a))[0][0]
    stranger = await w.client.post(SEND, headers=headers(w, "fd_a"), json=send_body(w, w.slots[2], uuid.uuid4()))
    assert stranger.status_code == 409 and stranger.json() == {"detail": "the slot or the candidate cannot be offered"}
    outside_top_two = await w.client.post(SEND, headers=headers(w, "fd_a"), json=send_body(w, w.slots[2], third))
    assert outside_top_two.status_code == 409  # the third ranked candidate is beyond backfill_candidates = 2
    other_type = await w.client.post(SEND, headers=headers(w, "fd_a"), json=send_body(w, w.slots[2], top, visit_type_id=str(w.vt_b)))
    assert other_type.status_code == 409
    assert (await rows(w, "select count(*) from msg.messages where clinic_id = :c", c=w.a))[0][0] == before and (await rows(w, "select count(*) from sched.holds where slot_id = :s", s=w.slots[2]))[0][0] == 0


async def test_only_front_desk_of_the_clinic_may_draft_or_send_even_when_configured(w):
    top = await waitlist_id(w, w.top)
    for who, status in (("nurse_a", 403), ("doctor_a", 403), ("fd_b", 404)):
        assert (await draft(w, who)).status_code == status, who
        assert (await w.client.post(SEND, headers=headers(w, who), json=send_body(w, w.slots[2], top))).status_code == status, who
    assert (await rows(w, "select count(*) from sched.holds where slot_id = :s", s=w.slots[2]))[0][0] == 0


async def test_end_to_end_a_freed_slot_goes_to_the_higher_priority_patient_ahead_of_a_longer_waiting_lower_priority_one_and_booking_it_leaves_the_others_waiting(w):
    """Phase 3 acceptance (BUILD-SPEC Appendix D): cancel -> ranked list -> draft -> send -> accept (book). Level one joined LATER than two level-two patients and must still be offered first."""
    cancel = await w.client.post(f"/v1/staff/scheduling/bookings/{w.booking}/cancel", headers=headers(w, "fd_a"))
    assert cancel.status_code == 200 and cancel.json()["slot_id"] == str(w.slot_booked)
    assert [c["patient_id"] for c in cancel.json()["candidates"]] == [str(w.top), str(w.second), str(w.third)]  # rank first, then who joined first
    assert [c["priority_code"] for c in cancel.json()["candidates"]] == ["level_one", "level_two", "level_two"]
    assert (await rows(w, "select count(*) from sched.holds where clinic_id = :c", c=w.a))[0][0] == 0  # the cancel offered nobody
    out = await draft(w)
    assert out.json()["data"]["draft"]["candidate_patient_id"] == str(w.top)
    top = await waitlist_id(w, w.top)
    sent = await w.client.post(SEND, headers=headers(w, "fd_a"), json=send_body(w, w.slot_booked, top))
    assert sent.status_code == 200 and sent.json()["patient_id"] == str(w.top)
    longer_waiting = await w.client.post(f"/v1/staff/scheduling/slots/{w.slot_booked}/book", headers=headers(w, "fd_a"), json={"patient_id": str(w.second)})
    assert longer_waiting.status_code == 409  # the slot is held for the higher-priority patient: the longer-waiting one cannot take it
    accepted = await w.client.post(f"/v1/staff/scheduling/slots/{w.slot_booked}/book", headers=headers(w, "fd_a"), json={"patient_id": str(w.top)})
    assert accepted.status_code == 200 and accepted.json()["patient_id"] == str(w.top) and accepted.json()["status"] == "confirmed"
    states = {r[0]: r[1] for r in await rows(w, "select patient_id, status from sched.waitlist where clinic_id = :c", c=w.a)}
    assert states[w.top] == "fulfilled" and states[w.second] == "waiting" and states[w.third] == "waiting"
