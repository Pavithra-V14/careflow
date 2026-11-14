"""The API role matrix (BUILD-SPEC.md §18.4, §21; CLAUDE.md rule 8).

Drives the real FastAPI app over HTTP with Starlette's TestClient. Tokens are real ES256 JWTs signed by a
key generated for this run; the app verifies them against a fake JWKS endpoint, so there is no committed
key and no dependency on a live Auth service for the matrix itself (one separate test, in
test_api_real_token_live.py, uses the project's real Auth). The database is real: the app connects as the
restricted `careflow_app` role through the transaction-mode pooler, so row-level security is genuinely in
the path. Accounts are committed under origin='test' and removed in teardown.

How the harness works. A route's expectations are listed once in MATRIX, as (label, how to authenticate,
expected status, expected body). `check_route_matrix` runs them and reports every mismatch. To prove the
harness really catches a violation, a test points it at a deliberately unprotected route and requires it to
fail. `test_every_route_is_in_the_matrix` fails if the app has a route the matrix does not cover, so adding
a route without adding its matrix case breaks the build.
"""

import asyncio
import base64
import json
import os
import random
import re
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace

import asyncpg
import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from dotenv import dotenv_values
from fastapi import FastAPI
from starlette.testclient import TestClient

from careflow.adapters.fake import FakeUpload
from careflow.app import Settings, create_app, key_scope
from careflow.auth import JWKSClient
from careflow.auth_admin import FakeAuthAdmin
from careflow.crypto import DemographicVault
from careflow.db import make_app_engine
from careflow.documents import ALLOWED_TYPES as ALLOWED_DOC_TYPES
from careflow.documents import DocumentIntake, document_id_for
from careflow.front_door_runner import TASK_TYPE as FRONT_DOOR_TASK
from careflow.front_door_runner import FrontDoorComponents
from tests.bounds import bounded
from tests.db_helpers import without_scope

ENV = dotenv_values(Path(__file__).resolve().parent.parent / ".env")
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or ENV.get("DATABASE_URL_MIGRATOR")
APP_URL = os.environ.get("DATABASE_URL_APP") or ENV.get("DATABASE_URL_APP")
PROJECT = "https://example-project.supabase.co"
ISSUER = PROJECT + "/auth/v1"

pytestmark = pytest.mark.skipif(not (MIGRATOR and APP_URL), reason="database URLs not configured")

STAFF_ROLES = ("front_desk", "nurse", "doctor", "admin", "business_owner", "auditor")


# ---- signing and a fake key endpoint ---------------------------------------------------------------


def _key(kid):
    private = ec.generate_private_key(ec.SECP256R1())
    jwk = jwt.algorithms.ECAlgorithm.to_jwk(private.public_key(), as_dict=True)
    jwk.update({"kid": kid, "alg": "ES256", "use": "sig"})
    return private, jwk


SIGNING, SIGNING_JWK = _key("matrix-key")
ATTACKER, _ = _key("matrix-key")  # same kid, different key material


def mint(sub, *, key=SIGNING, kid="matrix-key", alg="ES256", **over):
    # A staff token carries a verified second factor by default (every staff route demands one: careflow.auth.mfa_failure); a test that wants a password-only session says aal=None, amr=None.
    claims = {"sub": str(sub), "iss": ISSUER, "aud": "authenticated", "role": "authenticated", "exp": int(time.time()) + 3000,
              "aal": "aal2", "amr": [{"method": "totp", "timestamp": int(time.time()) - 5}], **over}
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm=alg, headers={"kid": kid})


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def fake_jwks_client() -> JWKSClient:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"keys": [SIGNING_JWK]}))
    return JWKSClient(ISSUER + "/.well-known/jwks.json", http=httpx.AsyncClient(transport=transport))


# ---- committed fixture: real rows the route reads under RLS ----------------------------------------


class Identities:
    pass


def _run(coro):
    return asyncio.run(coro)


async def _seed() -> Identities:
    ids = Identities()
    ids.clinic_a, ids.clinic_b = uuid.uuid4(), uuid.uuid4()
    ids.people = {}  # key -> (account_id, account_type, status, [(clinic, role)])
    spec = [(f"{r}_a", "staff", "active", [(ids.clinic_a, r)]) for r in STAFF_ROLES]
    spec += [
        ("front_desk_b", "staff", "active", [(ids.clinic_b, "front_desk")]),
        ("multi", "staff", "active", [(ids.clinic_a, "nurse"), (ids.clinic_b, "doctor")]),
        ("patient", "patient", "active", []),
        ("guardian", "guardian", "active", []),
        ("suspended", "staff", "suspended", [(ids.clinic_a, "nurse")]),
        ("invited", "staff", "invited", [(ids.clinic_a, "nurse")]),
        ("closed", "patient", "closed", []),
        ("front_desk_suspended", "staff", "suspended", [(ids.clinic_a, "front_desk")]),
    ]
    for key, kind, status, grants in spec:
        ids.people[key] = (uuid.uuid4(), kind, status, grants)
    ids.no_account = uuid.uuid4()  # a valid Auth user with no iam.accounts row
    ids.fd2 = uuid.uuid4()  # a SECOND active front desk person of clinic A, for the two-person merge rule (not in ids.people on purpose)
    conn = await asyncpg.connect((MIGRATOR or "").replace("+asyncpg", ""), statement_cache_size=0)
    try:
        await conn.execute("insert into iam.clinics (id, name, origin) values ($1, 'Matrix A', 'test'), ($2, 'Matrix B', 'test')", ids.clinic_a, ids.clinic_b)
        await conn.executemany(
            "insert into iam.accounts (id, account_type, status, origin) values ($1, $2, $3, 'test')",
            [(a, k, s) for a, k, s, _ in ids.people.values()],
        )
        await conn.executemany(
            "insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, $3, 'test')",
            [(a, c, r) for a, _, _, grants in ids.people.values() for c, r in grants],
        )
        await conn.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test')", ids.fd2)
        await conn.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, 'front_desk', 'test')", ids.fd2, ids.clinic_a)
        await _seed_scheduling(ids, conn)
        ids.claimant = uuid.uuid4()  # an ACTIVE patient account for the portal claim cases: its claim rows are append-only and are left behind with it
        await conn.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'patient', 'active', 'test')", ids.claimant)
        rule, episode, ids.episode_rule = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        await conn.execute("insert into ops.care_recurrence_rules (id, clinic_id, origin, rule_code, interval_days, overdue_after_days, idempotency_key) values ($1, $2, 'test', 'matrix_seed_rule', 30, 5, $3)", rule, ids.clinic_a, f"mx-r-{rule}")
        await conn.execute("insert into ops.care_episodes (id, clinic_id, origin, patient_id, episode_code, started_on, idempotency_key) values ($1, $2, 'test', $3, 'matrix_seed_episode', current_date - 60, $4)", episode, ids.clinic_a, ids.patients_a[42], f"mx-e-{episode}")
        await conn.execute("insert into ops.care_episode_rules (id, clinic_id, origin, episode_id, rule_id, idempotency_key) values ($1, $2, 'test', $3, $4, $5)", ids.episode_rule, ids.clinic_a, episode, rule, f"mx-er-{ids.episode_rule}")
        await conn.execute("insert into iam.break_glass_reasons (clinic_id, code) values ($1, 'demo_reason')", ids.clinic_a)
        await conn.execute("insert into iam.break_glass_settings (clinic_id, max_minutes, max_reopens) values ($1, 240, 5)", ids.clinic_a)  # owner-managed limits (migration 0066)
        ids.bg_grants = []
        for n in range(2):  # grants held by nurse_a: one the read cases use, one the review cases use (append-only: they stay behind with their clinic)
            ids.bg_grants.append(
                await conn.fetchval(
                    "insert into iam.break_glass_grants (clinic_id, origin, patient_id, opened_by, reason_code, expires_at, idempotency_key) values ($1, 'test', $2, $3, 'demo_reason', now() + interval '3 hours', $4) returning id",
                    ids.clinic_a, ids.patients_a[45], ids.people["nurse_a"][0], f"mx-bg-{n}-{uuid.uuid4()}",
                )
            )
    except Exception:
        await _cleanup(ids, conn)
        await conn.close()
        raise
    await conn.close()
    return ids


POOL = {"hold": 90, "book": 90, "extra": 110, "cancel": 70}  # slots / bookings handed out one per test case
N_PATIENTS = 49


async def _seed_scheduling(ids, conn) -> None:
    """Synthetic scheduling rows for clinic A (and just enough for clinic B), all origin='test'. Patients hold dummy
    ciphertext: these routes never decrypt. The two priority levels are test fixtures with neutral names, not
    clinical content."""
    from datetime import datetime, timedelta

    tag = uuid.uuid4().hex[:8]
    k = lambda name: f"matrix-{tag}-{name}"
    a, b = ids.clinic_a, ids.clinic_b
    doctor = ids.people["doctor_a"][0]
    provider = await conn.fetchval("insert into sched.providers (clinic_id, display_name, origin, idempotency_key) values ($1, 'Test provider', 'test', $2) returning id", a, k("prov"))
    location = await conn.fetchval("insert into sched.locations (clinic_id, name, origin, idempotency_key) values ($1, 'Test room', 'test', $2) returning id", a, k("loc"))
    ids.vt_a = await conn.fetchval("insert into sched.visit_types (clinic_id, name, duration_minutes, origin, idempotency_key) values ($1, 'Test visit', 30, 'test', $2) returning id", a, k("vta"))
    ids.vt_b = await conn.fetchval("insert into sched.visit_types (clinic_id, name, duration_minutes, origin, idempotency_key) values ($1, 'Test visit', 30, 'test', $2) returning id", b, k("vtb"))
    await conn.executemany(
        "insert into sched.priority_levels (clinic_id, code, label, rank, approved_by, origin, idempotency_key) values ($1, $2, $3, $4, $5, 'test', $6)",
        [(a, "level_one", "Test level one", 1, doctor, k("pl1")), (a, "level_two", "Test level two", 2, doctor, k("pl2"))],
    )
    patients = [(uuid.uuid4(), a) for _ in range(N_PATIENTS)] + [(uuid.uuid4(), b)]
    await conn.executemany(
        "insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, origin, idempotency_key) "
        "values ($1, $2, $3, 1, $3, 1, 'test', $4)",
        [(pid, c, b"x" * 40, k(f"pt{i}")) for i, (pid, c) in enumerate(patients)],
    )
    ids.patients_a = [p for p, c in patients if c == a]
    ids.patient_b = patients[-1][0]
    ids.provider_a, ids.location_a = provider, location
    # A waiting queue to read: the LOWER-priority patient joined EARLIER, so order by rank must beat order by time.
    ids.wait_low, ids.wait_high = ids.patients_a[43], ids.patients_a[44]
    now = datetime.now(UTC)
    await conn.executemany(
        "insert into sched.waitlist (clinic_id, patient_id, visit_type_id, priority_code, joined_at, origin, idempotency_key) "
        "values ($1, $2, $3, $4, $5, 'test', $6)",
        [(a, ids.wait_low, ids.vt_a, "level_two", now - timedelta(days=2), k("wl-low")),
         (a, ids.wait_high, ids.vt_a, "level_one", now - timedelta(days=1), k("wl-high"))],
    )

    start = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) + timedelta(days=60)
    slots, pools, n = [], {}, 0
    for pool, size in POOL.items():
        pools[pool] = []
        for _ in range(size):
            sid = uuid.uuid4()
            booked = pool == "cancel"
            slots.append((sid, a, provider, location, ids.vt_a, start + timedelta(hours=n), start + timedelta(hours=n, minutes=30),
                          "booked" if booked else "open", k(f"slot{n}")))
            pools[pool].append(sid)
            n += 1
    await conn.executemany(
        "insert into sched.slots (id, clinic_id, provider_id, location_id, visit_type_id, starts_at, ends_at, status, origin, idempotency_key) "
        "values ($1, $2, $3, $4, $5, $6, $7, $8, 'test', $9)",
        slots,
    )
    # patients 40 to 44 are reserved (the cancel-candidates test asserts they have no booking, and 43/44 are the waiting pair): the seeded bookings cycle over the first 40 only
    bookings = [(uuid.uuid4(), sid, ids.patients_a[i % 40], k(f"bk{i}")) for i, sid in enumerate(pools["cancel"])]
    await conn.executemany(
        "insert into sched.bookings (id, clinic_id, slot_id, patient_id, origin, idempotency_key) values ($1, $2, $3, $4, 'test', $5)",
        [(bid, a, sid, pid, key) for bid, sid, pid, key in bookings],
    )
    ids.pools = {**{p: list(v) for p, v in pools.items() if p != "cancel"}, "cancel": [bid for bid, *_ in bookings]}


LEFT_BEHIND_WHERE = {
    "sched.holds": "not exists (select 1 from sched.offer_deliveries d where d.hold_id = t.id)",
    "sched.slots": "not exists (select 1 from sched.holds h where h.slot_id = t.id)",
    "sched.visit_types": "not exists (select 1 from sched.slots s where s.visit_type_id = t.id)",
    "sched.providers": "not exists (select 1 from sched.slots s where s.provider_id = t.id)",
    "sched.locations": "not exists (select 1 from sched.slots s where s.location_id = t.id)",
}


async def _cleanup(ids, conn=None):
    own = conn is None
    conn = conn or await asyncpg.connect((MIGRATOR or "").replace("+asyncpg", ""), statement_cache_size=0)
    try:
        accounts = [a for a, *_ in ids.people.values()] + ([ids.fd2] if hasattr(ids, "fd2") else []) + list(getattr(ids, "extra_accounts", []))
        clinics = [ids.clinic_a, ids.clinic_b]
        for table in ("sched.bookings", "sched.holds", "sched.waitlist", "sched.slots", "sched.priority_levels",
                      "mpi.merges", "agents.review_tasks", "mpi.match_candidates", "mpi.identity_tokens", "mpi.patients", "sched.visit_types", "sched.providers",
                      "sched.locations"):
            if table == "mpi.patients":
                # A confirmed patient cannot be deleted: mpi.identity_confirmations is append-only (migration 0023, a trigger blocks the owner too) and references it. Such patients, and
                # whatever references them (the confirming accounts, the clinics), stay behind, tagged origin='test'; nothing reads them and every other suite is scoped to its own clinic.
                await conn.execute(
                    "delete from mpi.patients p where p.clinic_id = any($1) and not exists (select 1 from mpi.identity_confirmations c where c.patient_id = p.id) "
                    "and not exists (select 1 from agents.triage_proposals pr where pr.patient_id = p.id) "
                    "and not exists (select 1 from mpi.erasure_requests e where e.patient_id = p.id) "
                    "and not exists (select 1 from ops.care_barriers b where b.patient_id = p.id) "
                    "and not exists (select 1 from ops.care_episodes ce where ce.patient_id = p.id) "
                    "and not exists (select 1 from clinical.results cr where cr.patient_id = p.id) "
                    "and not exists (select 1 from clinical.medications md where md.patient_id = p.id) "
                    "and not exists (select 1 from ops.care_plans cp where cp.patient_id = p.id) "
                    "and not exists (select 1 from clinical.notes n where n.patient_id = p.id) "
                    "and not exists (select 1 from iam.break_glass_grants bg where bg.patient_id = p.id) "
                    "and not exists (select 1 from msg.threads th where th.patient_id = p.id) "
                    "and not exists (select 1 from sched.holds h where h.patient_id = p.id)", clinics  # (a hold left behind for an append-only delivery row keeps its patient)
                )
                continue
            if table == "agents.review_tasks":
                # A task that an append-only triage proposal points at cannot be deleted (the referral-queue cases seed one); it stays behind with its clinic, tagged origin='test'.
                await conn.execute("delete from agents.review_tasks t where t.clinic_id = any($1) and not exists (select 1 from agents.triage_proposals p where p.review_task_id = t.id)", clinics)
                continue
            if table in LEFT_BEHIND_WHERE:
                # Rows that an append-only delivery record (migration 0031) points at, directly or through the hold, the slot and the reference rows they use, cannot be deleted: they stay behind with
                # their clinic, tagged origin='test'; nothing reads them and every other suite is scoped to its own clinic.
                await conn.execute(f"delete from {table} t where t.clinic_id = any($1) and {LEFT_BEHIND_WHERE[table]}", clinics)
                continue
            await conn.execute(f"delete from {table} where clinic_id = any($1)", clinics)
        await conn.execute("delete from iam.break_glass_reasons where clinic_id = any($1)", clinics)
        await conn.execute("delete from iam.break_glass_settings where clinic_id = any($1)", clinics)
        await conn.execute("delete from msg.voice_preferences where account_id = any($1)", accounts)
        await conn.execute("delete from iam.patient_profiles where account_id = any($1)", accounts)
        await conn.execute("delete from iam.staff_roles where clinic_id = any($1)", [ids.clinic_a, ids.clinic_b])
        for statement, args in (("delete from iam.accounts where id = any($1)", accounts), ("delete from iam.clinics where id = any($1)", [ids.clinic_a, ids.clinic_b])):
            try:
                await conn.execute(statement, args)
            except asyncpg.ForeignKeyViolationError:
                pass  # still referenced by an append-only confirmation (see above): left behind on purpose
    finally:
        if own:
            await conn.close()


@pytest.fixture(scope="module")
def ids():
    seeded = _run(_seed())
    yield seeded
    leaked = _run(_assert_no_committed_messages(seeded))
    _run(_cleanup(seeded))
    assert not leaked, leaked


async def _assert_no_committed_messages(ids) -> str | None:
    """This module COMMITS its fixtures. msg.messages is append-only for every role, so a message committed here can never be deleted, and the key-rotation tests assert that NO committed message
    exists on key version 1: one stray row fails all of them in every later run until a person erases it by hand (PR #9, 2026-10-30). If a case reaches a route that writes a message, move it into a
    never-committed harness (tests/test_api_offers_live.py). Returns the explanation, or None."""
    rows = await _fetch("select count(*) as n from msg.messages where clinic_id = any($1)", [ids.clinic_a, ids.clinic_b])
    if rows[0]["n"]:
        return (f"{rows[0]['n']} message(s) were COMMITTED into this module's clinics. msg.messages is append-only, so they cannot be deleted and the key-rotation tests will fail until they are "
                "erased by hand. Move the case that writes a message into a never-committed harness (see tests/test_no_committed_message_writes.py).")
    return None


# Per-run keys: nothing secret is committed. The patient-registration route encrypts demographics with them (version 1, the only one loaded).
TEST_VAULT = DemographicVault.from_env({f"CAREFLOW_{k}_SECRET_V1": base64.b64encode(os.urandom(32)).decode() for k in ("ENC", "HMAC")})


# seconds; the merge routes require a second factor verified within this window. Generous on purpose: tokens are minted when a route's cases are built and
# the cases then run one after another against a shared hosted database; at 300 s the last cases of the approve route (278 s in CI) came back 401.
STEP_UP_WINDOW = 1800


UPLOADS = FakeUpload()  # the matrix app's storage: the document routes' cases play the browser against it


def _enable_patient_view(built: FastAPI) -> FastAPI:
    """The matrix app has the patient-view feature ON with stand-in settings (test inputs): the disabled and unconfigured behaviours are tested by overriding this."""
    from careflow.app import patient_view_config
    from careflow.patient_view import (
        OpenRateLimiter,
        PatientViewConfig,
        PatientViewSettings,
    )

    config = PatientViewConfig(True, PatientViewSettings(_env_file=None, max_opens_per_minute=10_000, max_label_rows_per_minute=10_000), OpenRateLimiter(10_000), OpenRateLimiter(10_000))
    built.dependency_overrides[patient_view_config] = lambda: config
    return built


def _matrix_interpreter():
    """A FAKE decision service (phi_approved, free) so the reply-interpretation route gets past its 503 and the committed half can show 401/404/422: no case here reaches it (random message ids)."""
    from decimal import Decimal as _D

    from careflow.decision import DecisionService, InMemoryDecisionRecorder
    from careflow.gateway import Gateway, InMemorySpendLedger, ModelRow, SpendLimits
    from careflow.ports import AdapterResult, DecisionResult, Usage

    async def engine(request):
        return AdapterResult(DecisionResult("unclear", {o: 1 / len(request.options) for o in request.options}, 0.2, "fake", False, "fake"), Usage(1, 1))

    zero = _D(0)
    row = ModelRow("decision", "fake", "fake-decision-1", "phi_approved", 1, zero, zero, zero, zero)
    return DecisionService(Gateway([row], {("fake", "fake-decision-1"): engine}, InMemorySpendLedger(), SpendLimits(clinic_daily_usd=_D(1)), origin="test"), InMemoryDecisionRecorder())


@pytest.fixture(scope="module")
def app() -> FastAPI:
    settings = Settings(_env_file=None, supabase_url=PROJECT, database_url_app=APP_URL or "", step_up_max_age_seconds=STEP_UP_WINDOW, erase_reasons="entered_in_error")
    # Stand-in components (all None): the referral routes need SOME configured front door to get past their 503; the paths tested here (404, 422, 401) never touch a component.
    built = create_app(
        settings, jwks=fake_jwks_client(), vault=TEST_VAULT, front_door=FrontDoorComponents(None, None, None, None, None, None, None, None, None),
        document_intake=DocumentIntake(UPLOADS, profile="demo"),  # a fake storage in the demo profile with no scanner: registration reports scan=not_checked
        reply_interpreter=_matrix_interpreter(), auth_admin=FakeAuthAdmin(),
    )
    return _enable_patient_view(built)


@pytest.fixture(scope="module")
def client(app, ids):
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _expected_time_zone():
    """The clinic time zone /v1/iam/me should carry: the required setting, or the labelled demo placeholder under the demo pack (the same environment the app is built from)."""
    from careflow import demo_phase4
    from careflow.app import clinic_time_zone
    from careflow.rules import resolve_profile

    return clinic_time_zone(demo_phase4.effective_environ(os.environ, resolve_profile()).get("CAREFLOW_CLINIC_TIME_ZONE"))


def expected_me(ids, key):
    account_id, kind, status, grants = ids.people[key]
    return {
        "account_id": str(account_id),
        "account_type": kind,
        "status": status,
        # the deployment's profile (the live job sets CAREFLOW_PROFILE=demo); unset or unrecognised is null
        "clinic_time_zone": _expected_time_zone(),
        "operating_profile": (os.environ.get("CAREFLOW_PROFILE") or "").strip().lower() if (os.environ.get("CAREFLOW_PROFILE") or "").strip().lower() in ("demo", "clinical") else None,
        # each grant also names its clinic and that clinic's origin (the frontend shows them): the fixture's two clinics are "Matrix A" and "Matrix B", both origin 'test'
        "roles": sorted(
            ({"clinic_id": str(c), "role": r, "clinic_name": "Matrix A" if c == ids.clinic_a else "Matrix B", "clinic_origin": "test"} for c, r in grants),
            key=lambda g: (g["clinic_id"], g["role"]),
        ),
    }


# ---- the harness -----------------------------------------------------------------------------------


def check_route_matrix(client, method, path, cases):
    """Run every (label, headers, status, body|None) case and raise ONE AssertionError listing all failures."""
    failures = []
    for label, headers, status, body, *rest in cases:
        extra = rest[0] if rest else {}  # optional: {"path": concrete path, "json": request body, "headers": more headers}
        response = client.request(
            method, extra.get("path", path), headers={**headers, **extra.get("headers", {})}, json=extra.get("json")
        )
        if response.status_code != status:
            failures.append(f"{label}: expected {status}, got {response.status_code}")
        elif callable(body):  # a predicate over the parsed JSON, for bodies that carry generated ids and times
            if not body(response.json()):
                failures.append(f"{label}: body check failed: {str(response.json())[:300]}")
        elif body is not None and response.json() != body:
            failures.append(f"{label}: unexpected body {response.json()}")
    assert not failures, f"{method} {path}:\n  " + "\n  ".join(failures)


def me_cases(ids) -> list[tuple]:
    allowed = [
        (f"{key} may read their own profile", bearer(mint(ids.people[key][0])), 200, expected_me(ids, key))
        for key, (_, _, status, _) in ids.people.items()
        if status == "active"
    ]
    refused_account = [
        (f"{key} ({ids.people[key][2]} account) is refused", bearer(mint(ids.people[key][0])), 403, {"detail": "no active account"})
        for key in ("suspended", "invited", "closed")
    ]
    refused_account.append(("valid user with no iam account is refused", bearer(mint(ids.no_account)), 403, {"detail": "no active account"}))
    sub = ids.people["nurse_a"][0]
    rejected_token = [
        ("no Authorization header", {}, 401, None),
        ("empty bearer", {"Authorization": "Bearer "}, 401, None),
        ("basic scheme", {"Authorization": "Basic YTpi"}, 401, None),
        ("garbage token", bearer("not.a.jwt"), 401, None),
        ("expired token", bearer(mint(sub, exp=int(time.time()) - 3600)), 401, None),
        ("signed by another key under the right kid", bearer(mint(sub, key=ATTACKER)), 401, None),
        ("unknown kid", bearer(mint(sub, kid="somebody-elses")), 401, None),
        ("HS256 downgrade", bearer(jwt.encode({"sub": str(sub), "iss": ISSUER, "aud": "authenticated", "role": "authenticated",
                                               "exp": int(time.time()) + 600}, "x" * 64, algorithm="HS256", headers={"kid": "matrix-key"})), 401, None),
        ("alg none", bearer(jwt.encode({"sub": str(sub), "iss": ISSUER, "aud": "authenticated", "role": "authenticated",
                                        "exp": int(time.time()) + 600}, None, algorithm="none", headers={"kid": "matrix-key"})), 401, None),
        ("wrong audience", bearer(mint(sub, aud="service_role")), 401, None),
        ("wrong issuer", bearer(mint(sub, iss="https://evil.example/auth/v1")), 401, None),
        ("anonymous role", bearer(mint(sub, role="anon")), 401, None),
        ("service role claim", bearer(mint(sub, role="service_role")), 401, None),
        ("no subject", bearer(mint_without(sub, "sub")), 401, None),
        ("no expiry", bearer(mint_without(sub, "exp")), 401, None),
    ]
    return allowed + refused_account + rejected_token


def mint_without(sub, claim):
    claims = {"sub": str(sub), "iss": ISSUER, "aud": "authenticated", "role": "authenticated", "exp": int(time.time()) + 600}
    claims.pop(claim)
    return jwt.encode(claims, SIGNING, algorithm="ES256", headers={"kid": "matrix-key"})


# ---- staff scheduling routes (rule 8: these cases ship in the same change as the routes) ---------------------------

PREFIX = "/v1/staff/scheduling"
HOLD, BOOK = PREFIX + "/slots/{slot_id}/hold", PREFIX + "/slots/{slot_id}/book"
CANCEL, JOIN = PREFIX + "/bookings/{booking_id}/cancel", PREFIX + "/waitlist/join"


def _who(ids, who):
    """A person key from `ids.people`, or a ready-made header dict (for the unauthenticated cases)."""
    return who if isinstance(who, dict) else bearer(mint(ids.people[who][0]))


def _new_key() -> str:
    return f"matrix-{uuid.uuid4()}"


def _case(ids, label, who, status, path, *, json=None, key="new", body=None):
    """One request. key='new' sends a fresh X-Idempotency-Key, key=None omits the header, any other string is sent as is."""
    headers = {} if key is None else {"X-Idempotency-Key": _new_key() if key == "new" else key}
    return (label, _who(ids, who), status, body, {"path": path, "json": json, "headers": headers})


def _unauthenticated(ids, path, json):
    sub = ids.people["front_desk_a"][0]
    return [
        _case(ids, "no Authorization header", {}, 401, path, json=json),
        _case(ids, "garbage token", bearer("not.a.jwt"), 401, path, json=json),
        _case(ids, "expired token", bearer(mint(sub, exp=int(time.time()) - 3600)), 401, path, json=json),
        _case(ids, "signed by another key under the right kid", bearer(mint(sub, key=ATTACKER)), 401, path, json=json),
        _case(ids, "no header and no key still 401 (authentication comes first)", {}, 401, path, json=json, key=None),
    ]


def _everyone_else(ids, status_by_person, make_path, make_json, default):
    """front_desk_a is tested by the caller; every other seeded identity must get `status_by_person.get(p, default)`."""
    cases = [
        _case(ids, f"{p} ({ids.people[p][2]}) is refused", p, status_by_person.get(p, default), make_path(), json=make_json())
        for p in ids.people
        if p != "front_desk_a"
    ]
    cases.append(_case(ids, "valid user with no iam account is refused", bearer(mint(ids.no_account)), default, make_path(), json=make_json()))
    return cases


def slot_write_cases(ids, op) -> list[tuple]:
    """hold / book. Only front desk of the slot's clinic may write. Admin can lock a slot (its own UPDATE policy) but the
    hold/booking INSERT is front-desk-only, so admin gets 403; every role that cannot lock the slot sees it as not found."""
    template = HOLD if op == "hold" else BOOK
    path = lambda slot=None: template.format(slot_id=slot or ids.pools[op].pop())
    body = (lambda **o: {"duration_minutes": 15, **o}) if op == "hold" else (lambda **o: {"patient_id": str(ids.patients_a[0]), **o})
    fd, key, slot = "front_desk_a", _new_key(), ids.pools[op].pop()
    taken = "slot is held" if op == "hold" else "slot is booked"
    cases = [
        _case(ids, "front desk of the clinic succeeds", fd, 200, path(slot), json=body(), key=key),
        _case(ids, "replaying the same key and request succeeds again (idempotent)", fd, 200, path(slot), json=body(), key=key),
        _case(ids, f"a second request with a new key finds the {taken.split()[-1]} slot taken", fd, 409, path(slot), json=body()),
        _case(ids, "the same key on a different slot is a conflict", fd, 409, path(), json=body(), key=key),
        _case(ids, "a patient of another clinic is not found", fd, 404, path(), json=body(patient_id=str(ids.patient_b))),
        _case(ids, "an unknown slot is not found", fd, 404, template.format(slot_id=uuid.uuid4()), json=body()),
        _case(ids, "missing X-Idempotency-Key is rejected", fd, 422, path(), json=body(), key=None),
        _case(ids, "blank X-Idempotency-Key is rejected", fd, 422, path(), json=body(), key="   "),
        _case(ids, "slot id that is not a uuid is rejected", fd, 422, template.format(slot_id="nope"), json=body()),
        _case(ids, "unknown body field is rejected", fd, 422, path(), json=body(unexpected=1)),
        _case(ids, "empty body is rejected", fd, 422, path(), json={}),
    ]
    if op == "hold":
        cases += [
            _case(ids, "zero minutes is rejected", fd, 422, path(), json=body(duration_minutes=0)),
            _case(ids, "negative minutes is rejected", fd, 422, path(), json=body(duration_minutes=-5)),
            _case(ids, "minutes as text is rejected", fd, 422, path(), json=body(duration_minutes="soon")),
        ]
    cases += _everyone_else(ids, {"admin_a": 403}, path, body, 404)
    return cases + _unauthenticated(ids, template.format(slot_id=ids.pools["extra"][0]), body())


def cancel_cases(ids) -> list[tuple]:
    """Cancelling rewrites the booking and its slot: front desk only. Others either cannot see the booking or cannot lock its slot."""
    path = lambda booking=None: CANCEL.format(booking_id=booking or ids.pools["cancel"].pop())
    fd, key, booking = "front_desk_a", _new_key(), ids.pools["cancel"].pop()
    cases = [
        _case(ids, "front desk of the clinic succeeds", fd, 200, path(booking), key=key),
        _case(ids, "replaying the same key succeeds again (idempotent)", fd, 200, path(booking), key=key),
        _case(ids, "cancelling it again with a new key is refused", fd, 409, path(booking)),
        _case(ids, "the same key on a different booking is a conflict", fd, 409, path(), key=key),
        _case(ids, "an unknown booking is not found", fd, 404, CANCEL.format(booking_id=uuid.uuid4())),
        _case(ids, "missing X-Idempotency-Key is rejected", fd, 422, path(), key=None),
        _case(ids, "blank X-Idempotency-Key is rejected", fd, 422, path(), key="   "),
        _case(ids, "booking id that is not a uuid is rejected", fd, 422, CANCEL.format(booking_id="nope")),
    ]
    cases += _everyone_else(ids, {}, path, lambda: None, 404)
    return cases + _unauthenticated(ids, CANCEL.format(booking_id=ids.pools["extra"][0]), None)


def join_cases(ids) -> list[tuple]:
    """Joining the waitlist is a front-desk write. Staff who can read the visit type but not write get 403; anyone who
    cannot see the visit type at all gets 404."""
    patients = iter(ids.patients_a[1:])
    body = lambda **o: {"patient_id": str(next(patients)), "visit_type_id": str(ids.vt_a), "clinical_priority": "level_one", **o}
    fd, key = "front_desk_a", _new_key()
    same = body()
    other_patient = {**same, "patient_id": str(next(patients))}
    cases = [
        _case(ids, "front desk of the clinic succeeds", fd, 200, JOIN, json=same, key=key),
        _case(ids, "replaying the same key succeeds again (idempotent)", fd, 200, JOIN, json=same, key=key),
        _case(ids, "the same patient already waiting is refused", fd, 409, JOIN, json=same),
        _case(ids, "the same key for a different patient is a conflict", fd, 409, JOIN, json=other_patient, key=key),
        _case(ids, "a priority level the clinic never defined is not found", fd, 404, JOIN, json=body(clinical_priority="level_nine")),
        _case(ids, "a patient of another clinic is not found", fd, 404, JOIN, json=body(patient_id=str(ids.patient_b))),
        _case(ids, "another clinic's visit type is not found", fd, 404, JOIN, json=body(visit_type_id=str(ids.vt_b))),
        _case(ids, "missing X-Idempotency-Key is rejected", fd, 422, JOIN, json=body(), key=None),
        _case(ids, "blank X-Idempotency-Key is rejected", fd, 422, JOIN, json=body(), key="   "),
        _case(ids, "empty priority is rejected", fd, 422, JOIN, json=body(clinical_priority="")),
        _case(ids, "missing priority is rejected", fd, 422, JOIN, json={k: v for k, v in body().items() if k != "clinical_priority"}),
        _case(ids, "unknown body field is rejected", fd, 422, JOIN, json=body(unexpected=1)),
    ]
    cases += _everyone_else(ids, {r: 403 for r in ("nurse_a", "doctor_a", "admin_a", "multi")}, lambda: JOIN, body, 404)
    return cases + _unauthenticated(ids, JOIN, body())


def _page(ids, *, clinic_only=None, check=lambda items: True, non_empty=False):
    """Predicate for a list response: the paging envelope is well formed, nothing from another clinic is in it, and
    `check(items)` holds. `non_empty` also requires at least one item."""

    def predicate(body):
        items = body.get("items")
        if set(body) != {"items", "limit", "has_more"} or not isinstance(items, list):
            return False
        if non_empty and not items:
            return False
        if clinic_only and any(i["clinic_id"] != str(clinic_only) for i in items):
            return False
        return check(items)

    return predicate


def read_cases(ids, route) -> list[tuple]:
    """GET slots / bookings / waitlist. Readers: front desk, nurse, doctor (of the clinic whose rows they see). Everyone else
    gets 403 before any query runs: admin, auditor, business owner, patients, guardians, suspended or invited accounts, and a
    user with no account. A reader of another clinic is allowed, and simply sees none of this clinic's rows."""
    path = PREFIX + "/" + route
    A = ids.clinic_a
    in_order = lambda items, key: all(key(i) <= key(j) for i, j in pairwise(items))
    wait_pair = (str(ids.wait_low), str(ids.wait_high))
    shape = {
        "slots": {"check": lambda items: all(i["status"] in ("open", "held") for i in items) and in_order(items, lambda i: i["starts_at"])},
        "bookings": {"check": lambda items: in_order(items, lambda i: i["starts_at"])},
        "waitlist": {"check": lambda items: in_order(items, lambda i: (i["rank"], i["joined_at"]))
                         and [i["patient_id"] for i in items if i["patient_id"] in wait_pair] == [wait_pair[1], wait_pair[0]]},
    }[route]  # waitlist: the higher-priority patient is listed first although they joined later
    reads = lambda who, label: _case(ids, label, who, 200, path, key=None, body=_page(ids, clinic_only=A, non_empty=True, **shape))
    cases = [reads(r, f"{r} of the clinic reads") for r in ("front_desk_a", "nurse_a", "doctor_a")]
    cases += [
        _case(ids, "a reader of another clinic is allowed and sees none of this clinic's rows", "front_desk_b", 200, path, key=None,
              body=_page(ids, check=lambda items: items == [])),
        reads("multi", "a nurse here who is a doctor elsewhere reads, and only this clinic has rows"),
        _case(ids, "limit=1 returns one row and says there are more", "front_desk_a", 200, path + "?limit=1", key=None,
              body=lambda b: len(b["items"]) == 1 and b["limit"] == 1 and b["has_more"] is True),
        _case(ids, "limit=0 is rejected", "front_desk_a", 422, path + "?limit=0", key=None),
        _case(ids, "limit=501 is rejected", "front_desk_a", 422, path + "?limit=501", key=None),
    ]
    filters = {
        "slots": [("provider_id", ids.provider_a), ("location_id", ids.location_a)],
        "waitlist": [("visit_type_id", ids.vt_a)],
        "bookings": [],
    }[route]
    for name, known in filters:
        cases += [
            _case(ids, f"filter {name}=known returns rows", "front_desk_a", 200, f"{path}?{name}={known}", key=None,
                  body=_page(ids, clinic_only=A, non_empty=True)),
            _case(ids, f"filter {name}=unknown returns nothing", "front_desk_a", 200, f"{path}?{name}={uuid.uuid4()}", key=None,
                  body=_page(ids, check=lambda items: items == [])),
            _case(ids, f"filter {name} that is not a uuid is rejected", "front_desk_a", 422, f"{path}?{name}=nope", key=None),
        ]
    if route == "waitlist":
        cases.append(_case(ids, "another clinic's visit type returns nothing", "front_desk_a", 200, f"{path}?visit_type_id={ids.vt_b}",
                           key=None, body=_page(ids, check=lambda items: items == [])))
    cases += [
        _case(ids, "clinic_id of the reader's own clinic returns that clinic's rows", "front_desk_a", 200, f"{path}?clinic_id={A}", key=None, body=_page(ids, clinic_only=A, non_empty=True, **shape)),
        _case(ids, "clinic_id of a clinic the reader cannot read is the one not-found", "front_desk_a", 404, f"{path}?clinic_id={ids.clinic_b}", key=None, body=_fixed(404)),
        _case(ids, "clinic_id of a clinic that does not exist is the same not-found", "front_desk_a", 404, f"{path}?clinic_id={uuid.uuid4()}", key=None, body=_fixed(404)),
        _case(ids, "a reader of the other clinic who names this clinic gets the same not-found", "front_desk_b", 404, f"{path}?clinic_id={A}", key=None, body=_fixed(404)),
        _case(ids, "a clinic_id that is not a uuid is rejected", "front_desk_a", 422, f"{path}?clinic_id=nope", key=None, body=_fixed(422)),
        _case(ids, "a non-reader who names a clinic is still refused first", "admin_a", 403, f"{path}?clinic_id={A}", key=None),
    ]
    denied = [p for p in ids.people if p not in ("front_desk_a", "front_desk_b", "nurse_a", "doctor_a", "multi")]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused", p, 403, path, key=None) for p in denied]
    cases.append(_case(ids, "valid user with no iam account is refused", bearer(mint(ids.no_account)), 403, path, key=None))
    return cases + _unauthenticated(ids, path, None)


# ---- POST /v1/staff/patients: patient registration / identity resolution (D2) ---------------------------------------------------------------
#
# Front desk of the clinic registers; every other identity is refused. A clinic you cannot see is 404, a clinic you can see without being its
# front desk is 403. EVERY response, success or error, must be free of the person's name, date of birth and phone: ids and an outcome only.

INGEST = "/v1/staff/patients"


def _person() -> dict:
    n = uuid.uuid4().hex
    return {"name": f"Zz Sentinel {n}", "dob": "1990-01-01", "phone": "9" + str(random.randrange(10**8, 10**9))}


def _clean_of(*values):
    """Predicate: the parsed JSON contains none of these values anywhere."""
    return lambda b: not any(v in json.dumps(b) for v in values)


def ingest_cases(ids) -> list[tuple]:
    fd, person, key = "front_desk_a", _person(), _new_key()
    base = lambda **o: {"clinic_id": str(ids.clinic_a), **person, **o}
    fresh = lambda **o: {"clinic_id": str(ids.clinic_a), **_person(), **o}
    secrets = (person["name"], person["phone"], person["dob"])

    def ids_only(outcome, candidates=None):
        def check(b):
            return (set(b) == {"patient_id", "outcome", "candidate_ids"} and b["outcome"] == outcome
                    and (candidates is None or len(b["candidate_ids"]) == candidates) and _clean_of(*secrets)(b))
        return check

    def refused_without_echo(*values):
        return lambda b: isinstance(b.get("detail"), (str, list)) and _clean_of(*values)(b)

    cases = [
        _case(ids, "front desk of the clinic registers a new person", fd, 200, INGEST, json=base(), key=key, body=ids_only("created", 0)),
        _case(ids, "replaying the same key and the same person is a replay", fd, 200, INGEST, json=base(), key=key, body=ids_only("replayed", 0)),
        _case(ids, "the same key for a different person is a conflict", fd, 409, INGEST, json=fresh(), key=key),
        _case(ids, "the same person under a new key matches the existing record", fd, 200, INGEST, json=base(), body=ids_only("matched", 0)),
        _case(ids, "the same name and birth date with another phone is a near-match queued for review", fd, 200, INGEST,
              json=base(phone="9" + str(random.randrange(10**8, 10**9))), body=ids_only("review", 1)),
        _case(ids, "a person with no phone is registered", fd, 200, INGEST, json={k: v for k, v in fresh().items() if k != "phone"},
              body=lambda b: b["outcome"] == "created"),
        _case(ids, "an unknown clinic is not found", fd, 404, INGEST, json=fresh(clinic_id=str(uuid.uuid4()))),
        _case(ids, "missing X-Idempotency-Key is rejected", fd, 422, INGEST, json=fresh(), key=None),
        _case(ids, "blank X-Idempotency-Key is rejected", fd, 422, INGEST, json=fresh(), key="   "),
        _case(ids, "no clinic is rejected", fd, 422, INGEST, json={k: v for k, v in fresh().items() if k != "clinic_id"}),
        _case(ids, "a clinic id that is not a uuid is rejected", fd, 422, INGEST, json=fresh(clinic_id="nope")),
        _case(ids, "an empty body is rejected", fd, 422, INGEST, json={}),
        _case(ids, "a client-supplied origin is an unknown field", fd, 422, INGEST, json=fresh(origin="live")),
        _case(ids, "an unknown field carrying the person is rejected without echoing it", fd, 422, INGEST,
              json=fresh(note=person["name"]), body=refused_without_echo(person["name"])),
    ]
    for label, bad in (
        ("a month that does not exist", {"dob": "1990-13-45"}), ("an age instead of a birth date", {"dob": "58"}),
        ("a birth date in the future", {"dob": "2999-01-01"}), ("a phone that is not a mobile number", {"phone": "12345"}),
        ("a landline", {"phone": "0442345678"}), ("a blank name", {"name": "   "}),
    ):
        value = next(iter(bad.values()))
        cases.append(_case(ids, f"{label} is rejected and never echoed", fd, 422, INGEST, json=fresh(**bad), body=refused_without_echo(value)))
    visible_but_not_front_desk = {r: 403 for r in ("nurse_a", "doctor_a", "admin_a", "business_owner_a", "auditor_a", "multi")}
    cases += _everyone_else(ids, visible_but_not_front_desk, lambda: INGEST, fresh, 404)
    return cases + _unauthenticated(ids, INGEST, fresh())


# ---- POST /v1/staff/merges, .../{id}/approve, .../{id}/unmerge: merge propose / approve / unmerge (D3) -------------------------------------
#
# STEP-UP IMPLEMENTED, CLAIM SHAPE PENDING LIVE VERIFICATION: the tokens below carry the DOCUMENTED shape (aal2 plus an amr list of
# {"method": "totp", "timestamp": unix-seconds}); a real Supabase token has not been inspected yet (docs/design/step-up-spike.md).
#
# Every identity is tried twice: with a stepped-up token (the answer its role earns) and with a token that is not stepped up (always 401,
# before any role or database check). Front desk proposes, a DIFFERENT front desk person approves, one front desk person unmerges.
# Responses carry ids and statuses only; every refusal is a fixed body.

MERGES = "/v1/staff/merges"
APPROVE, UNMERGE = MERGES + "/{merge_id}/approve", MERGES + "/{merge_id}/unmerge"
MERGE_KEYS = {"merge_id", "status", "master_patient_id", "subordinate_patient_id", "replayed"}


def _totp(seconds_ago: int = 30) -> list:
    return [{"method": "totp", "timestamp": int(time.time()) - seconds_ago}]


def stepped(sub, **over):
    """Headers for a staff token that has just verified a second factor (the documented, unverified claim shape)."""
    claims = {"aal": "aal2", "amr": _totp(), **over}
    return bearer(mint(sub, **claims))


def not_stepped_variants(sub) -> list[tuple[str, dict]]:
    return [
        ("a password-only session (no aal, no amr)", bearer(mint(sub, aal=None, amr=None))),
        ("aal1 even with a totp entry", bearer(mint(sub, aal="aal1", amr=_totp()))),
        ("aal2 with a totp entry older than the window", bearer(mint(sub, aal="aal2", amr=_totp(STEP_UP_WINDOW + 600)))),
        ("aal2 with a totp entry in the future", bearer(mint(sub, aal="aal2", amr=_totp(-3600)))),
        ("aal2 with only a password entry", bearer(mint(sub, aal="aal2", amr=[{"method": "password", "timestamp": int(time.time())}]))),
        ("aal2 with a legacy string amr", bearer(mint(sub, aal="aal2", amr=["totp"]))),
    ]


async def _owner(fn):
    conn = await asyncpg.connect((MIGRATOR or "").replace("+asyncpg", ""), statement_cache_size=0)
    try:
        return await fn(conn)
    finally:
        await conn.close()


async def _new_pair(conn, clinic, *, candidate=True):
    """Two fresh synthetic patients and (unless candidate=False) the pending possible-duplicate row a merge starts from."""
    tag = uuid.uuid4().hex
    master, subordinate = uuid.uuid4(), uuid.uuid4()
    await conn.executemany(
        "insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, origin, idempotency_key) "
        "values ($1, $2, $3, 1, $3, 1, 'test', $4)",
        [(master, clinic, b"x" * 40, f"matrix-m-{tag}-a"), (subordinate, clinic, b"x" * 40, f"matrix-m-{tag}-b")],
    )
    cand = None
    if candidate:
        cand = await conn.fetchval(
            "insert into mpi.match_candidates (clinic_id, patient_id, candidate_patient_id, matched_kinds, reason, origin, idempotency_key) "
            "values ($1, $2, $3, array['phone'], 'near_match', 'test', $4) returning id", clinic, subordinate, master, f"matrix-m-{tag}-c",
        )
    return master, subordinate, cand


def new_pair(ids, *, clinic=None, candidate=True):
    return _run(_owner(lambda conn: _new_pair(conn, clinic or ids.clinic_a, candidate=candidate)))


def new_merge(ids, state="awaiting"):
    """A merge proposed by front_desk_a (and, if state='active', confirmed by the second front desk person), written by the owner."""

    async def make(conn):
        master, subordinate, cand = await _new_pair(conn, ids.clinic_a)
        key = f"matrix-merge-{uuid.uuid4().hex}"
        if state == "active":
            await conn.execute("update mpi.match_candidates set status = 'resolved', resolved_at = now(), resolved_by = $2 where id = $1", cand, ids.fd2)
            row = await conn.fetchrow(
                "insert into mpi.merges (clinic_id, master_patient_id, subordinate_patient_id, candidate_id, approved_by, second_approved_by, "
                "status, activated_at, origin, idempotency_key) values ($1, $2, $3, $4, $5, $6, 'active', now(), 'test', $7) returning id",
                ids.clinic_a, master, subordinate, cand, ids.people["front_desk_a"][0], ids.fd2, key,
            )
        else:
            row = await conn.fetchrow(
                "insert into mpi.merges (clinic_id, master_patient_id, subordinate_patient_id, candidate_id, approved_by, origin, idempotency_key) "
                "values ($1, $2, $3, $4, $5, 'test', $6) returning id", ids.clinic_a, master, subordinate, cand, ids.people["front_desk_a"][0], key,
            )
        return row["id"], master, subordinate

    return _run(_owner(make))


FIXED_BODY = {
    401: {"detail": "step-up required"}, 503: {"detail": "voice is not available"}, 403: {"detail": "your role may not do this"}, 404: {"detail": "not found"},
}


def _rejected(b):
    """A refusal the ROUTE itself raises (a value the schema allows but the rules do not): the fixed string body, not the validation handler's list."""
    return b == {"detail": "request rejected"} or _fixed(422)(b)  # (the validation handler's list when the schema refuses it first)


def _fixed(status):
    """The exact fixed refusal body for a status; a 422 is the app-wide handler's list of {type, loc, msg} with no echoed input."""
    if status == 422:
        return lambda b: (isinstance(b.get("detail"), list) and set(b) == {"detail"}
                          and all(set(e) == {"type", "loc", "msg"} for e in b["detail"]))
    return lambda b: b == FIXED_BODY[status]


def _merge_body(merge_id=None, status=None, **expect):
    def check(b):
        return (set(b) == MERGE_KEYS and (merge_id is None or b["merge_id"] == str(merge_id)) and (status is None or b["status"] == status)
                and all(b[k] == v for k, v in expect.items()))
    return check


def _everyone_at_the_gate(ids, make_path, make_json, status_for, default):
    """Every identity but front_desk_a, stepped up (the answer its role earns) and not stepped up (always 401). Nothing here may succeed."""
    people = [*ids.people, "no account"]
    cases = []
    for p in people:
        sub = ids.no_account if p == "no account" else ids.people[p][0]
        if p != "front_desk_a":
            status = status_for.get(p, default)
            cases.append(_case(ids, f"{p} with a second factor is refused", stepped(sub), status, make_path(), json=make_json(), body=_fixed(status)))
        for label, headers in not_stepped_variants(sub):
            cases.append(_case(ids, f"{p} with {label} is told to step up", headers, 401, make_path(), json=make_json(), body=_fixed(401)))
    return cases


def _not_stepped_cases(ids, make_path, make_json, who="front_desk_a"):
    sub = ids.people[who][0]
    return [_case(ids, f"{who} with {label} is told to step up", headers, 401, make_path(), json=make_json(), body=_fixed(401))
            for label, headers in not_stepped_variants(sub)]


def merge_propose_cases(ids) -> list[tuple]:
    fd, fd2 = stepped(ids.people["front_desk_a"][0]), stepped(ids.fd2)
    m1, s1, _ = new_pair(ids)
    m2, s2, _ = new_pair(ids)
    m3, s3, _ = new_pair(ids)
    m4, s4, _ = new_pair(ids)
    m5, s5, _ = new_pair(ids, candidate=False)
    mr, sr, _ = new_pair(ids)  # a valid pair every refused identity aims at; it is never proposed
    key = _new_key()
    body = lambda m, s_, **o: {"clinic_id": str(ids.clinic_a), "master_patient_id": str(m), "subordinate_patient_id": str(s_), **o}
    visible_but_not_front_desk = {r: 403 for r in ("nurse_a", "doctor_a", "admin_a", "business_owner_a", "auditor_a", "multi")}
    cases = [
        _case(ids, "front desk proposes a merge for a pending possible-duplicate", fd, 200, MERGES, json=body(m1, s1), key=key,
              body=_merge_body(status="awaiting_second_approval", replayed=False, master_patient_id=str(m1), subordinate_patient_id=str(s1))),
        _case(ids, "replaying the same key and the same pair is a replay", fd, 200, MERGES, json=body(m1, s1), key=key,
              body=_merge_body(status="awaiting_second_approval", replayed=True)),
        _case(ids, "the same key for a different pair is a conflict", fd, 409, MERGES, json=body(m2, s2), key=key,
              body=lambda b: b == {"detail": "this idempotency key was already used by a different request"}),
        _case(ids, "a second front desk person can also propose", fd2, 200, MERGES, json=body(m3, s3), body=_merge_body(status="awaiting_second_approval")),
        _case(ids, "a pair that is not a pending possible-duplicate is refused with the one generic body", fd, 409, MERGES, json=body(m5, s5),
              body=lambda b: b == {"detail": "this merge cannot be carried out"}),
        _case(ids, "a record cannot be merged into itself", fd, 409, MERGES, json=body(m4, m4), body=lambda b: b == {"detail": "this merge cannot be carried out"}),
        _case(ids, "a patient of another clinic is simply not found", fd, 404, MERGES, json=body(m4, ids.patient_b), body=lambda b: b == {"detail": "not found"}),
        _case(ids, "a patient that does not exist gives the identical answer", fd, 404, MERGES, json=body(m4, uuid.uuid4()), body=lambda b: b == {"detail": "not found"}),
        _case(ids, "an unknown clinic is not found", fd, 404, MERGES, json=body(m4, s4, clinic_id=str(uuid.uuid4())), body=_fixed(404)),
        _case(ids, "front desk of ANOTHER clinic cannot propose here", stepped(ids.people["front_desk_b"][0]), 404, MERGES, json=body(m4, s4),
              body=_fixed(404)),
        _case(ids, "missing X-Idempotency-Key is rejected", fd, 422, MERGES, json=body(m4, s4), key=None, body=_fixed(422)),
        _case(ids, "blank X-Idempotency-Key is rejected", fd, 422, MERGES, json=body(m4, s4), key="   ", body=_fixed(422)),
        _case(ids, "a patient id that is not a uuid is rejected", fd, 422, MERGES, json=body(m4, "nope"), body=_fixed(422)),
        _case(ids, "a client-supplied origin is an unknown field", fd, 422, MERGES, json=body(m4, s4, origin="live"), body=_fixed(422)),
        _case(ids, "an empty body is rejected", fd, 422, MERGES, json={}, body=_fixed(422)),
    ]
    cases += _everyone_at_the_gate(ids, lambda: MERGES, lambda: body(mr, sr), visible_but_not_front_desk, 404)
    cases += _not_stepped_cases(ids, lambda: MERGES, lambda: body(mr, sr))
    return cases + _unauthenticated(ids, MERGES, body(mr, sr))


def merge_approve_cases(ids) -> list[tuple]:
    fd, fd2 = stepped(ids.people["front_desk_a"][0]), stepped(ids.fd2)
    ok, ok_m, ok_s = new_merge(ids)
    own, _, _ = new_merge(ids)
    refused, _, _ = new_merge(ids)
    path = lambda mid: APPROVE.format(merge_id=mid)
    key = _new_key()
    not_found = lambda b: b == {"detail": "not found"}
    visible_but_not_front_desk = {r: 403 for r in ("nurse_a", "doctor_a", "multi")}  # front desk, nurse and doctor can SEE merges; the rest cannot
    cases = [
        _case(ids, "a DIFFERENT front desk person approves", fd2, 200, path(ok), key=key,
              body=_merge_body(ok, "active", replayed=False, master_patient_id=str(ok_m), subordinate_patient_id=str(ok_s))),
        _case(ids, "replaying the same key by the same approver is a replay", fd2, 200, path(ok), key=key, body=_merge_body(ok, "active", replayed=True)),
        _case(ids, "approving an already active merge under a new key is refused", fd2, 409, path(ok),
              body=lambda b: b == {"detail": "this merge is not in a state that allows that"}),
        _case(ids, "the SAME person who proposed cannot approve: one generic refusal", fd, 409, path(own),
              body=lambda b: b == {"detail": "this merge cannot be carried out"}),
        _case(ids, "an unknown merge is not found", fd2, 404, path(uuid.uuid4()), body=not_found),
        _case(ids, "front desk of ANOTHER clinic cannot even see the merge", stepped(ids.people["front_desk_b"][0]), 404, path(own), body=not_found),
        _case(ids, "missing X-Idempotency-Key is rejected", fd2, 422, path(own), key=None, body=_fixed(422)),
        _case(ids, "blank X-Idempotency-Key is rejected", fd2, 422, path(own), key="   ", body=_fixed(422)),
        _case(ids, "a merge id that is not a uuid is rejected", fd2, 422, path("nope"), body=_fixed(422)),
    ]
    cases += _everyone_at_the_gate(ids, lambda: path(refused), lambda: None, visible_but_not_front_desk, 404)
    cases += _not_stepped_cases(ids, lambda: path(refused), lambda: None)
    return cases + _unauthenticated(ids, path(refused), None)


def merge_unmerge_cases(ids) -> list[tuple]:
    fd, fd2 = stepped(ids.people["front_desk_a"][0]), stepped(ids.fd2)
    active, _, _ = new_merge(ids, "active")
    awaiting, _, _ = new_merge(ids)
    refused, _, _ = new_merge(ids, "active")
    path = lambda mid: UNMERGE.format(merge_id=mid)
    key = _new_key()
    task_ids = []
    visible_but_not_front_desk = {r: 403 for r in ("nurse_a", "doctor_a", "multi")}

    def unmerged(replayed, with_task):
        def check(b):
            if set(b) != {"merge_id", "status", "review_task_id", "decisions_flagged", "replayed"} or b["status"] != "unmerged" or b["replayed"] is not replayed:
                return False
            if not with_task:
                return b["review_task_id"] is None
            if b["review_task_id"] is None:
                return False
            task_ids.append(b["review_task_id"])
            return len(set(task_ids)) == 1  # the replay returns the SAME task
        return check

    cases = [
        _case(ids, "front desk unmerges an active link: the nurse review task is raised", fd, 200, path(active), key=key, body=unmerged(False, True)),
        _case(ids, "replaying the same key returns the same task", fd, 200, path(active), key=key, body=unmerged(True, True)),
        _case(ids, "unmerging again under a new key is refused", fd, 409, path(active), body=lambda b: b == {"detail": "this merge is not in a state that allows that"}),
        _case(ids, "cancelling a never-confirmed proposal raises no task", fd2, 200, path(awaiting), body=unmerged(False, False)),
        _case(ids, "an unknown merge is not found", fd, 404, path(uuid.uuid4()), body=lambda b: b == {"detail": "not found"}),
        _case(ids, "front desk of ANOTHER clinic cannot even see the merge", stepped(ids.people["front_desk_b"][0]), 404, path(refused),
              body=_fixed(404)),
        _case(ids, "missing X-Idempotency-Key is rejected", fd, 422, path(refused), key=None, body=_fixed(422)),
        _case(ids, "blank X-Idempotency-Key is rejected", fd, 422, path(refused), key="   ", body=_fixed(422)),
        _case(ids, "a merge id that is not a uuid is rejected", fd, 422, path("nope"), body=_fixed(422)),
    ]
    cases += _everyone_at_the_gate(ids, lambda: path(refused), lambda: None, visible_but_not_front_desk, 404)
    cases += _not_stepped_cases(ids, lambda: path(refused), lambda: None)
    return cases + _unauthenticated(ids, path(refused), None)


# ---- GET /v1/staff/messages/{message_id}: staff message read (D4), the COMMITTED half of a split matrix ----------------------------------
#
# Reading needs stored messages, and msg.messages / msg.threads are append-only for every role, so a committed fixture could never be cleaned
# up. This route is therefore tested in TWO halves (SPLIT_HARNESS below): THIS half runs on the real pooler role with real commits and covers
# everything that needs no stored message: every identity asking for a message that does not exist, authentication failures, a malformed id,
# and that a refused read writes NO audit row. The topic-dependent paths (who may read which topic, the audit row of each 200, an erased,
# tampered or unreadable body) are in tests/test_api_read_message_live.py. Documented in docs/design/role-matrix-exceptions.md.

READ = "/v1/staff/messages/{message_id}"


def message_read_cases(ids) -> list[tuple]:
    path = lambda: READ.format(message_id=uuid.uuid4())
    cases = []
    for p in [*ids.people, "no account"]:
        sub_ = ids.no_account if p == "no account" else ids.people[p][0]
        cases.append(_case(ids, f"{p} asking for a message that does not exist gets the one fixed 404", bearer(mint(sub_)), 404, path(), key=None,
                           body=_fixed(404)))
    cases.append(_case(ids, "a message id that is not a uuid is rejected", "front_desk_a", 422, READ.format(message_id="nope"), key=None, body=_fixed(422)))
    return cases + _unauthenticated(ids, path(), None)


# ---- GET /v1/staff/messages/threads and /threads/{thread_id}/messages: the COMMITTED half of a split matrix --------------------------------------------------------
#
# These list threads and message HEADERS (never a body). Who sees which topic needs stored messages (append-only), so that half is in tests/test_api_read_message_live.py. This half runs on
# the real pooler role with nothing stored: the readers (front desk, nurse, doctor) get an empty list or the one fixed 404 for an unknown thread, every other identity the one fixed 403, and the
# authentication and validation failures are pinned.

THREADS = "/v1/staff/messages/threads"
THREAD_HEADERS = "/v1/staff/messages/threads/{thread_id}/messages"
THREAD_READERS = ("front_desk_a", "nurse_a", "doctor_a", "multi", "front_desk_b")


def thread_list_cases(ids) -> list[tuple]:
    fixed403 = lambda b: b == {"detail": "your role may not read this"}
    cases = [_case(ids, f"{who} lists threads: none exist, an empty page", who, 200, THREADS, key=None, body=_page(ids, check=lambda items: items == [])) for who in THREAD_READERS]
    denied = [p for p in ids.people if p not in THREAD_READERS]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused", p, 403, THREADS, key=None, body=fixed403) for p in denied]
    cases.append(_case(ids, "valid user with no iam account is refused", bearer(mint(ids.no_account)), 403, THREADS, key=None, body=fixed403))
    cases += [
        _case(ids, "an unknown topic is rejected", "front_desk_a", 422, THREADS + "?topic=bogus", key=None, body=_fixed(422)),
        _case(ids, "limit=0 is rejected", "front_desk_a", 422, THREADS + "?limit=0", key=None, body=_fixed(422)),
        _case(ids, "a patient id that is not a uuid is rejected", "front_desk_a", 422, THREADS + "?patient_id=nope", key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, THREADS, None)


def thread_headers_cases(ids) -> list[tuple]:
    path = lambda: THREAD_HEADERS.format(thread_id=uuid.uuid4())
    fixed403 = lambda b: b == {"detail": "your role may not read this"}
    cases = [_case(ids, f"{who} asking for an unknown thread gets the one fixed 404", who, 404, path(), key=None, body=_fixed(404)) for who in THREAD_READERS]
    denied = [p for p in ids.people if p not in THREAD_READERS]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused", p, 403, path(), key=None, body=fixed403) for p in denied]
    cases.append(_case(ids, "valid user with no iam account is refused", bearer(mint(ids.no_account)), 403, path(), key=None, body=fixed403))
    cases.append(_case(ids, "a thread id that is not a uuid is rejected", "front_desk_a", 422, THREAD_HEADERS.format(thread_id="nope"), key=None, body=_fixed(422)))
    return cases + _unauthenticated(ids, path(), None)


# ---- POST /v1/staff/referrals/{document_id}/run and /review: the COMMITTED half of a split matrix --------------------------------------------------------
#
# A run needs a registered referral document, and docs.documents (like the checkpoint and decision tables a run writes) is append-only for every role, so a committed fixture could
# never be cleaned up. This half therefore runs every identity against RANDOM document ids (nothing exists, nothing to clean up) on the real pooler role and pins the fixed bodies
# of 401, 404 and 422. It CANNOT produce a 403 or a 200: both need an existing document. Those, the full run, the worker handler and replay are in the dedicated never-committed
# module tests/test_api_referrals_live.py. Documented in docs/design/role-matrix-exceptions.md.

RUN_REFERRAL = "/v1/staff/referrals/{document_id}/run"
REVIEW_REFERRAL = "/v1/staff/referrals/{document_id}/review"


def _referral_cases(ids, template: str, body) -> list[tuple]:
    path = lambda: template.format(document_id=uuid.uuid4())
    cases = [
        _case(ids, f"{p} asking about a document that does not exist gets the one fixed 404",
              bearer(mint(ids.no_account if p == "no account" else ids.people[p][0])), 404, path(), json=body, body=_fixed(404))
        for p in [*ids.people, "no account"]
    ]
    fd = "front_desk_a"
    cases += [
        _case(ids, "a missing X-Idempotency-Key is rejected", fd, 422, path(), json=body, key=None, body=_fixed(422)),
        _case(ids, "a blank X-Idempotency-Key is rejected", fd, 422, path(), json=body, key="   ", body=_fixed(422)),
        _case(ids, "a document id that is not a uuid is rejected", fd, 422, template.format(document_id="nope"), json=body, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, path(), body)


def referral_run_cases(ids) -> list[tuple]:
    return _referral_cases(ids, RUN_REFERRAL, None)


INTERPRET = "/v1/staff/scheduling/offers/interpret-reply"
RECORD_REPLY = "/v1/staff/scheduling/offers/replies"


def record_reply_cases(ids) -> list[tuple]:
    """The COMMITTED half of POST .../offers/replies: a random patient id, so front desk of the clinic gets the one fixed 404 BEFORE anything is written (a stored reply is an append-only encrypted message, so a
    success cannot be committed here); a role that sees the clinic but is not front desk gets 403; everyone else the one 404; plus 401 and 422. The 201 on a real patient, the red-flag order, the replay and
    the audit row are in tests/test_api_screen_message_live.py (SPLIT_HARNESS)."""
    body = lambda **o: {"clinic_id": str(ids.clinic_a), "patient_id": str(uuid.uuid4()), "text": "DEMO reply text", **o}
    refused = ("nurse_a", "doctor_a", "admin_a", "business_owner_a", "auditor_a", "multi")
    cases = []
    for p in [*ids.people, "no account"]:
        who = bearer(mint(ids.no_account)) if p == "no account" else p
        status, expect = (403, NOT_YOUR_ROLE) if p in refused else (404, _fixed(404))
        cases.append(_case(ids, f"{p} " + ("sees the clinic but is not front desk" if p in refused else "gets the one fixed not-found"), who, status, RECORD_REPLY, json=body(), body=expect))
    fd = "front_desk_a"
    cases += [
        _case(ids, "an unknown clinic is not found", fd, 404, RECORD_REPLY, json=body(clinic_id=str(uuid.uuid4())), body=_fixed(404)),
        _case(ids, "a blank text is rejected", fd, 422, RECORD_REPLY, json=body(text="   "), body=_fixed(422)),
        _case(ids, "an empty text is rejected", fd, 422, RECORD_REPLY, json=body(text=""), body=_fixed(422)),
        _case(ids, "an extra field is rejected", fd, 422, RECORD_REPLY, json=body(actor_id=str(uuid.uuid4())), body=_fixed(422)),
        _case(ids, "a patient id that is not a uuid is rejected", fd, 422, RECORD_REPLY, json=body(patient_id="nope"), body=_fixed(422)),
        _case(ids, "missing X-Idempotency-Key is rejected", fd, 422, RECORD_REPLY, json=body(), key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, RECORD_REPLY, body())


def interpret_reply_cases(ids) -> list[tuple]:
    """The COMMITTED half of POST .../offers/interpret-reply: random message ids, so every identity gets the one fixed 404 (nothing exists, nothing is written or needs cleaning up) plus 401 and 422. The 200 / 403 /
    404 split on a real, encrypted, append-only message is in tests/test_api_screen_message_live.py (SPLIT_HARNESS), where the red-flag order, the audit row and the fake model are exercised too."""
    body = lambda: {"message_id": str(uuid.uuid4())}
    cases = [
        _case(ids, f"{p} asking about a message that does not exist gets the one fixed 404", bearer(mint(ids.no_account if p == "no account" else ids.people[p][0])), 404, INTERPRET, json=body(), body=_fixed(404))
        for p in [*ids.people, "no account"]
    ]
    fd = "front_desk_a"
    cases += [
        _case(ids, "a missing X-Idempotency-Key is rejected", fd, 422, INTERPRET, json=body(), key=None, body=_fixed(422)),
        _case(ids, "a blank X-Idempotency-Key is rejected", fd, 422, INTERPRET, json=body(), key="   ", body=_fixed(422)),
        _case(ids, "a message id that is not a uuid is rejected", fd, 422, INTERPRET, json={"message_id": "nope"}, body=_fixed(422)),
        _case(ids, "an empty body is rejected", fd, 422, INTERPRET, json={}, body=_fixed(422)),
        _case(ids, "a body that names the actor is rejected (the actor is the signed-in account)", fd, 422, INTERPRET, json={**body(), "actor_id": str(uuid.uuid4())}, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, INTERPRET, body())


REVIEW_VIEW = "/v1/staff/referrals/{document_id}/review-view"


def referral_review_view_cases(ids) -> list[tuple]:
    """A document that does not exist is the one fixed 404 for everyone; the real view (and that a canary looks exactly like a case) is built by tests/test_phase4_modules.py. A read carries no idempotency key."""
    cases = [(label, who, status, body, {**req, "headers": {}}) for label, who, status, body, req in _referral_cases(ids, REVIEW_VIEW, None)]
    return [c for c in cases if "X-Idempotency-Key" not in c[0]]


def referral_review_cases(ids) -> list[tuple]:
    cases = _referral_cases(ids, REVIEW_REFERRAL, {"decision": "approve"})
    path = lambda: REVIEW_REFERRAL.format(document_id=uuid.uuid4())
    for label, bad in (
        ("an empty body", {}), ("a decision that is not one of the three", {"decision": "approve-all"}),
        ("a body that names the reviewer (the reviewer is the signed-in account, never the body)", {"decision": "approve", "reviewer_id": str(uuid.uuid4())}),
    ):
        cases.append(_case(ids, f"{label} is rejected", "nurse_a", 422, path(), json=bad, body=_fixed(422)))
    return cases


# ---- POST /v1/staff/patients/{patient_id}/confirm-identity: front desk confirms a worker-created record (pre-launch blocker 10, option A) -----------------
#
# Front desk of the clinic confirms; every other identity is refused with a fixed body: those who can SEE the patient under row-level security (nurse, doctor, and `multi`, who is a
# nurse here) get 403, those who cannot (admin, owner, auditor, patient, guardian, closed, suspended and invited accounts, front desk of ANOTHER clinic, an account-less token) get 404.
# No step-up (unlike merges): a confirmation only makes a record match normally; it links, merges and erases nothing. The queue worker has no identity in this matrix; the worker's
# refusal (it can read patients but has no INSERT policy on mpi.identity_confirmations) is in tests/test_rls_mpi_worker_live.py.

CONFIRM = "/v1/staff/patients/{patient_id}/confirm-identity"


async def _new_worker_patient(conn, clinic):
    """A synthetic patient created the way the unattended worker creates one (registered_via = 'referral_worker'), written by the owner."""
    pid = uuid.uuid4()
    await conn.execute(
        "insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, origin, idempotency_key, registered_via) "
        "values ($1, $2, $3, 1, $3, 1, 'test', $4, 'referral_worker')",
        pid, clinic, b"x" * 40, f"matrix-w-{uuid.uuid4().hex}",
    )
    return pid


def new_worker_patient(ids, *, clinic=None):
    return _run(_owner(lambda conn: _new_worker_patient(conn, clinic or ids.clinic_a)))


def _confirm_body(patient, replayed):
    return lambda b: b == {"patient_id": str(patient), "confirmed": True, "replayed": replayed}


def patient_confirm_cases(ids) -> list[tuple]:
    fd = "front_desk_a"
    ok, other, refused = new_worker_patient(ids), new_worker_patient(ids), new_worker_patient(ids)
    path = lambda pid: CONFIRM.format(patient_id=pid)
    key = _new_key()
    cases = [
        _case(ids, "front desk of the clinic confirms a worker-created patient", fd, 200, path(ok), key=key, body=_confirm_body(ok, False)),
        _case(ids, "replaying the same key is a replay", fd, 200, path(ok), key=key, body=_confirm_body(ok, True)),
        _case(ids, "an already confirmed patient under a new key is a replay in effect", fd, 200, path(ok), body=_confirm_body(ok, True)),
        _case(ids, "a key already used for a different patient is refused", fd, 409, path(other), key=key,
              body=lambda b: b == {"detail": "this idempotency key was already used by a different request"}),
        _case(ids, "an unknown patient is not found", fd, 404, path(uuid.uuid4()), body=_fixed(404)),
        _case(ids, "missing X-Idempotency-Key is rejected", fd, 422, path(refused), key=None, body=_fixed(422)),
        _case(ids, "blank X-Idempotency-Key is rejected", fd, 422, path(refused), key="   ", body=_fixed(422)),
        _case(ids, "a patient id that is not a uuid is rejected", fd, 422, path("nope"), body=_fixed(422)),
    ]
    visible_but_not_front_desk = {"nurse_a", "doctor_a", "multi"}
    for person in [*ids.people, "no account"]:
        if person == fd:
            continue
        status = 403 if person in visible_but_not_front_desk else 404
        who = bearer(mint(ids.no_account)) if person == "no account" else person
        cases.append(_case(ids, f"{person} is refused", who, status, path(refused), body=_fixed(status)))
    cases += _unauthenticated(ids, path(refused), None)
    return cases


# ---- POST /v1/staff/patients/{patient_id}/revoke-identity: front desk revokes a mistaken confirmation (migration 0025) ---------------------------------------
#
# Same refusals as confirm-identity (front desk of the clinic only: nurse, doctor and `multi` can see the patient and get 403; everyone else 404), one person, no step-up. The body is one
# closed reason word. In the matrix the clinician-owned task settings are UNSET, so a real revoke answers `review_task: not_configured`; the `raised` path is tested below with the settings
# overridden.

REVOKE = "/v1/staff/patients/{patient_id}/revoke-identity"
REASON = {"reason": "wrong_person"}


async def _new_confirmed_patient(conn, clinic, front_desk):
    """A synthetic worker-created patient that front desk has CONFIRMED (written by the owner), so there is something to revoke."""
    pid = await _new_worker_patient(conn, clinic)
    await conn.execute(
        "insert into mpi.identity_confirmations (clinic_id, patient_id, confirmed_by, origin, idempotency_key) values ($1, $2, $3, 'test', $4)",
        clinic, pid, front_desk, f"matrix-c-{uuid.uuid4().hex}",
    )
    return pid


def new_confirmed_patient(ids, *, clinic=None):
    return _run(_owner(lambda conn: _new_confirmed_patient(conn, clinic or ids.clinic_a, ids.people["front_desk_a"][0])))


def _revoke_body(patient, replayed, review_task):
    return lambda b: b == {"patient_id": str(patient), "confirmed": False, "replayed": replayed, "review_task": review_task, "admin_alert": "on_record" if review_task == "on_record" else "not_configured"}


def patient_revoke_cases(ids) -> list[tuple]:
    fd = "front_desk_a"
    ok, other, refused = new_confirmed_patient(ids), new_confirmed_patient(ids), new_confirmed_patient(ids)
    path = lambda pid: REVOKE.format(patient_id=pid)
    key = _new_key()
    cases = [
        _case(ids, "front desk of the clinic revokes a confirmed patient", fd, 200, path(ok), json=REASON, key=key, body=_revoke_body(ok, False, "not_configured")),
        _case(ids, "replaying the same key is a replay", fd, 200, path(ok), json=REASON, key=key, body=_revoke_body(ok, True, "on_record")),
        _case(ids, "an already unverified patient under a new key is a replay in effect", fd, 200, path(ok), json=REASON, body=_revoke_body(ok, True, "on_record")),
        _case(ids, "a key already used for a different patient is refused", fd, 409, path(other), json=REASON, key=key,
              body=lambda b: b == {"detail": "this idempotency key was already used by a different request"}),
        _case(ids, "an unknown patient is not found", fd, 404, path(uuid.uuid4()), json=REASON, body=_fixed(404)),
        _case(ids, "missing X-Idempotency-Key is rejected", fd, 422, path(refused), json=REASON, key=None, body=_fixed(422)),
        _case(ids, "blank X-Idempotency-Key is rejected", fd, 422, path(refused), json=REASON, key="   ", body=_fixed(422)),
        _case(ids, "a patient id that is not a uuid is rejected", fd, 422, path("nope"), json=REASON, body=_fixed(422)),
        _case(ids, "a reason outside the closed list is rejected", fd, 422, path(refused), json={"reason": "because I said so"}, body=_fixed(422)),
        _case(ids, "a missing body is rejected", fd, 422, path(refused), json=None, body=_fixed(422)),
        _case(ids, "an extra field is rejected", fd, 422, path(refused), json={**REASON, "note": "free text"}, body=_fixed(422)),
    ]
    visible_but_not_front_desk = {"nurse_a", "doctor_a", "multi"}
    for person in [*ids.people, "no account"]:
        if person == fd:
            continue
        status = 403 if person in visible_but_not_front_desk else 404
        who = bearer(mint(ids.no_account)) if person == "no account" else person
        cases.append(_case(ids, f"{person} is refused", who, status, path(refused), json=REASON, body=_fixed(status)))
    cases += _unauthenticated(ids, path(refused), REASON)
    return cases


# ---- /v1/staff/review-tasks: list, open, acknowledge, resolve (careflow/review_tasks.py) --------------------------------------------------------------------
#
# Row-level security decides what a person sees and may change (0007, 0013, 0020). Task readers are front desk, nurse, doctor and admin of SOME clinic (everyone else, including a suspended
# account, an auditor, the business owner, patients and an account-less token, is refused with 403 before any query). What a reader then sees: nurse and doctor every kind of their clinic,
# front desk `unmerge_review` and its own `referral_hold`s, admin `unmerge_review` only; a reader of ANOTHER clinic sees none of this clinic's tasks (404 for one task). A task a role can see
# but not change is a 403; a task another person holds, or one of a kind that closes elsewhere (`red_flag`), is a 409.

TASKS = "/v1/staff/review-tasks"
TASK = TASKS + "/{task_id}"
TASK_ACK = TASK + "/acknowledge"
TASK_RESOLVE = TASK + "/resolve"
TASK_READER_KEYS = ("front_desk_a", "nurse_a", "doctor_a", "admin_a", "multi", "front_desk_b")
READ_403 = lambda b: b == {"detail": "your role may not read this"}


async def _new_task(conn, clinic, kind, role, *, priority="standard", status="open", acknowledged_by=None):
    tid = uuid.uuid4()
    await conn.execute(
        "insert into agents.review_tasks (id, clinic_id, origin, kind, priority, assigned_role, status, acknowledged_by, acknowledged_at, source_message_id, provenance, idempotency_key) "
        "values ($1, $2, 'test', $3, $4, $5, $6, $7, case when $7::uuid is null then null else now() end, $8, '{}'::jsonb, $9)",
        tid, clinic, kind, priority, role, status, acknowledged_by, f"matrix-task:{tid}", f"matrix-task-{tid}",
    )
    return tid


def new_task(ids, kind, role="nurse", **kw):
    return _run(_owner(lambda conn: _new_task(conn, ids.clinic_a, kind, role, **kw)))


def _tasks_page(ids, must_have=(), must_lack=(), kinds=None):
    """A list response that contains every id in `must_have`, none in `must_lack`, only kinds in `kinds` (if given), and only this clinic's rows."""
    return _page(
        ids, clinic_only=ids.clinic_a,
        check=lambda items: {str(i) for i in must_have} <= {i["task_id"] for i in items} and not ({str(i) for i in must_lack} & {i["task_id"] for i in items})
        and (kinds is None or {i["kind"] for i in items} <= set(kinds)),
    )


def review_task_list_cases(ids) -> list[tuple]:
    hold, unmerge, red = new_task(ids, "referral_hold", "front_desk"), new_task(ids, "unmerge_review", "nurse", priority="immediate"), new_task(ids, "red_flag", "nurse", priority="immediate")
    seen_by = {
        "nurse_a": (hold, unmerge, red), "doctor_a": (hold, unmerge, red), "multi": (hold, unmerge, red),
        "front_desk_a": (hold, unmerge), "admin_a": (unmerge,),
    }
    hidden_from = {"front_desk_a": (red,), "admin_a": (hold, red)}
    cases = [
        _case(ids, f"{who} lists exactly what row-level security shows it", who, 200, TASKS, key=None, body=_tasks_page(ids, must_have=seen, must_lack=hidden_from.get(who, ())))
        for who, seen in seen_by.items()
    ]
    cases += [
        _case(ids, "a reader of another clinic sees none of this clinic's tasks", "front_desk_b", 200, TASKS, key=None, body=_page(ids, check=lambda items: items == [])),
        _case(ids, "an immediate task is listed before a standard one", "nurse_a", 200, TASKS, key=None,
              body=_page(ids, check=lambda items: [i["priority"] for i in items] == sorted((i["priority"] for i in items), key=lambda p: p != "immediate"))),
        _case(ids, "filter by kind", "nurse_a", 200, TASKS + "?kind=red_flag", key=None, body=_tasks_page(ids, must_have=(red,), must_lack=(hold, unmerge), kinds=("red_flag",))),
        _case(ids, "filter by status", "nurse_a", 200, TASKS + "?status=resolved", key=None, body=_tasks_page(ids, must_lack=(hold, unmerge, red))),
        _case(ids, "filter by assigned role", "nurse_a", 200, TASKS + "?assigned_role=front_desk", key=None, body=_tasks_page(ids, must_have=(hold,), must_lack=(unmerge, red))),
        _case(ids, "limit=1 returns one row and says there are more", "nurse_a", 200, TASKS + "?limit=1", key=None,
              body=lambda b: len(b["items"]) == 1 and b["limit"] == 1 and b["has_more"] is True),
        _case(ids, "limit=0 is rejected", "nurse_a", 422, TASKS + "?limit=0", key=None),
        _case(ids, "limit=501 is rejected", "nurse_a", 422, TASKS + "?limit=501", key=None),
        _case(ids, "an unknown status is rejected", "nurse_a", 422, TASKS + "?status=bogus", key=None),
        _case(ids, "a malformed kind is rejected", "nurse_a", 422, TASKS + "?kind=BAD%20KIND", key=None),
        _case(ids, "an unknown assigned role is rejected", "nurse_a", 422, TASKS + "?assigned_role=owner", key=None),
    ]
    denied = [p for p in ids.people if p not in TASK_READER_KEYS]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused", p, 403, TASKS, key=None) for p in denied]
    cases.append(_case(ids, "valid user with no iam account is refused", bearer(mint(ids.no_account)), 403, TASKS, key=None))
    return cases + _unauthenticated(ids, TASKS, None)


def _task_detail(task, kind):
    return lambda b: b["task_id"] == str(task) and b["kind"] == kind and set(b) >= {"provenance", "source_message_id"} and isinstance(b["provenance"], dict)


def review_task_open_cases(ids) -> list[tuple]:
    hold, unmerge, red = new_task(ids, "referral_hold", "front_desk"), new_task(ids, "unmerge_review", "nurse"), new_task(ids, "red_flag", "nurse", priority="immediate")
    path = lambda t: TASK.format(task_id=t)
    cases = [_case(ids, f"{who} opens the hold", who, 200, path(hold), key=None, body=_task_detail(hold, "referral_hold")) for who in ("front_desk_a", "nurse_a", "doctor_a", "multi")]
    cases += [
        _case(ids, "front desk opens the unmerge task", "front_desk_a", 200, path(unmerge), key=None, body=_task_detail(unmerge, "unmerge_review")),
        _case(ids, "admin opens the unmerge task", "admin_a", 200, path(unmerge), key=None, body=_task_detail(unmerge, "unmerge_review")),
        _case(ids, "nurse opens the red-flag task", "nurse_a", 200, path(red), key=None, body=_task_detail(red, "red_flag")),
        _case(ids, "front desk cannot see the red-flag task: the same 404 as a missing one", "front_desk_a", 404, path(red), key=None, body=_fixed(404)),
        _case(ids, "admin cannot see a hold", "admin_a", 404, path(hold), key=None, body=_fixed(404)),
        _case(ids, "a reader of another clinic gets the same 404", "front_desk_b", 404, path(hold), key=None, body=_fixed(404)),
        _case(ids, "an unknown task is not found", "nurse_a", 404, path(uuid.uuid4()), key=None, body=_fixed(404)),
        _case(ids, "a task id that is not a uuid is rejected", "nurse_a", 422, path("nope"), key=None, body=_fixed(422)),
    ]
    denied = [p for p in ids.people if p not in TASK_READER_KEYS]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused", p, 403, path(hold), key=None, body=READ_403) for p in denied]
    cases.append(_case(ids, "valid user with no iam account is refused", bearer(mint(ids.no_account)), 403, path(hold), key=None, body=READ_403))
    return cases + _unauthenticated(ids, path(hold), None)


def _transition_body(task, status, replayed):
    return lambda b: b == {"task_id": str(task), "status": status, "replayed": replayed}


def _task_write_denials(ids, path, cases):
    denied = [p for p in ids.people if p not in TASK_READER_KEYS]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused", p, 403, path, body=READ_403) for p in denied]
    cases.append(_case(ids, "valid user with no iam account is refused", bearer(mint(ids.no_account)), 403, path, body=READ_403))
    return cases


def review_task_acknowledge_cases(ids) -> list[tuple]:
    hold, held, unmerge, red = new_task(ids, "referral_hold", "front_desk"), new_task(ids, "referral_hold", "front_desk"), new_task(ids, "unmerge_review", "nurse"), new_task(ids, "red_flag", "nurse", priority="immediate")
    path = lambda t: TASK_ACK.format(task_id=t)
    key = _new_key()
    cases = [
        _case(ids, "front desk acknowledges its own hold", "front_desk_a", 200, path(hold), key=key, body=_transition_body(hold, "acknowledged", False)),
        _case(ids, "replaying the same key is a replay", "front_desk_a", 200, path(hold), key=key, body=_transition_body(hold, "acknowledged", True)),
        _case(ids, "the same person again under a new key is a replay in effect", "front_desk_a", 200, path(hold), body=_transition_body(hold, "acknowledged", True)),
        _case(ids, "a doctor cannot take what front desk holds", "doctor_a", 409, path(hold), body=lambda b: b == {"detail": "this task is already taken or closed"}),
        _case(ids, "a nurse takes a hold assigned to front desk", "nurse_a", 200, path(held), body=_transition_body(held, "acknowledged", False)),
        _case(ids, "a nurse takes the red-flag task", "nurse_a", 200, path(red), body=_transition_body(red, "acknowledged", False)),
        _case(ids, "front desk cannot see the red-flag task", "front_desk_a", 404, path(red), body=_fixed(404)),
        _case(ids, "front desk can see but not change an unmerge task", "front_desk_a", 403, path(unmerge), body=_fixed(403)),
        _case(ids, "admin can see but not change an unmerge task", "admin_a", 403, path(unmerge), body=_fixed(403)),
        _case(ids, "a reader of another clinic gets the same 404", "front_desk_b", 404, path(unmerge), body=_fixed(404)),
        _case(ids, "an unknown task is not found", "nurse_a", 404, path(uuid.uuid4()), body=_fixed(404)),
        _case(ids, "missing X-Idempotency-Key is rejected", "nurse_a", 422, path(unmerge), key=None, body=_fixed(422)),
        _case(ids, "blank X-Idempotency-Key is rejected", "nurse_a", 422, path(unmerge), key="   ", body=_fixed(422)),
        _case(ids, "a task id that is not a uuid is rejected", "nurse_a", 422, path("nope"), body=_fixed(422)),
    ]
    cases = _task_write_denials(ids, path(unmerge), cases)
    return cases + _unauthenticated(ids, path(unmerge), None)


def review_task_resolve_cases(ids) -> list[tuple]:
    hold, hold2, unmerge, red, shot = (new_task(ids, "referral_hold", "front_desk"), new_task(ids, "referral_hold", "front_desk"), new_task(ids, "unmerge_review", "nurse"),
                                       new_task(ids, "red_flag", "nurse", priority="immediate"), new_task(ids, "triage_proposal", "nurse"))
    taken = new_task(ids, "referral_hold", "front_desk", status="acknowledged", acknowledged_by=ids.people["doctor_a"][0])
    path = lambda t: TASK_RESOLVE.format(task_id=t)
    key = _new_key()
    cases = [
        _case(ids, "front desk releases its own hold", "front_desk_a", 200, path(hold), key=key, body=_transition_body(hold, "resolved", False)),
        _case(ids, "replaying the same key is a replay", "front_desk_a", 200, path(hold), key=key, body=_transition_body(hold, "resolved", True)),
        _case(ids, "a resolved task under a new key is a replay in effect", "front_desk_a", 200, path(hold), body=_transition_body(hold, "resolved", True)),
        _case(ids, "a nurse resolves a hold assigned to front desk", "nurse_a", 200, path(hold2), body=_transition_body(hold2, "resolved", False)),
        _case(ids, "a task another person acknowledged is a conflict", "front_desk_a", 409, path(taken), body=lambda b: b == {"detail": "this task is already taken or closed"}),
        _case(ids, "a red-flag task is not closed here", "nurse_a", 409, path(red), body=lambda b: b == {"detail": "this kind of task is closed through its own route"}),
        _case(ids, "a triage proposal task is not closed here", "nurse_a", 409, path(shot), body=lambda b: b == {"detail": "this kind of task is closed through its own route"}),
        _case(ids, "front desk can see but not change an unmerge task", "front_desk_a", 403, path(unmerge), body=_fixed(403)),
        _case(ids, "admin can see but not change an unmerge task", "admin_a", 403, path(unmerge), body=_fixed(403)),
        _case(ids, "a reader of another clinic gets the same 404", "front_desk_b", 404, path(unmerge), body=_fixed(404)),
        _case(ids, "front desk cannot see the red-flag task", "front_desk_a", 404, path(red), body=_fixed(404)),
        _case(ids, "an unknown task is not found", "nurse_a", 404, path(uuid.uuid4()), body=_fixed(404)),
        _case(ids, "missing X-Idempotency-Key is rejected", "nurse_a", 422, path(unmerge), key=None, body=_fixed(422)),
        _case(ids, "blank X-Idempotency-Key is rejected", "nurse_a", 422, path(unmerge), key="   ", body=_fixed(422)),
        _case(ids, "a task id that is not a uuid is rejected", "nurse_a", 422, path("nope"), body=_fixed(422)),
    ]
    cases = _task_write_denials(ids, path(unmerge), cases)
    return cases + _unauthenticated(ids, path(unmerge), None)


# ---- /v1/staff/scheduling/offers/draft and /offers/send: Phase 3 slice 2 (careflow/offers.py) ----------------------------------------------------------------
#
# Front desk of the clinic only. Who can SEE the clinic row but is not front desk gets 403 (nurse, doctor, admin, business owner, auditor, `multi`); everyone else, including front desk of another
# clinic, gets the same 404 as an unknown clinic. The matrix runs with the clinician-owned settings UNSET (so the draft is `unavailable` and a send is a 503 naming the missing settings); the
# configured paths (a real draft, a real send, replays, conflicts, the audit row) are the live tests after the matrix, with the settings overridden.

OFFER_DRAFT = PREFIX + "/offers/draft"
OFFER_SEND = PREFIX + "/offers/send"
CLINIC_VISIBLE_NOT_FRONT_DESK = {r: 403 for r in ("nurse_a", "doctor_a", "admin_a", "business_owner_a", "auditor_a", "multi")}


def _offer_gate_cases(ids, make_request, expected_for_front_desk_b=404):
    cases = []
    for p in [*ids.people, "no account"]:
        if p == "front_desk_a":
            continue
        status = CLINIC_VISIBLE_NOT_FRONT_DESK.get(p, 404)
        who = bearer(mint(ids.no_account)) if p == "no account" else p
        cases.append(make_request(f"{p} is refused", who, status))
    return cases


def offer_draft_cases(ids) -> list[tuple]:
    q = lambda **o: f"{OFFER_DRAFT}?" + "&".join(f"{k}={v}" for k, v in {"clinic_id": ids.clinic_a, "visit_type_id": ids.vt_a, **o}.items())
    cases = [
        _case(ids, "front desk reads the draft: unavailable, naming the missing settings, nothing guessed", "front_desk_a", 200, q(), key=None,
              body=lambda b: b["status"] == "unavailable" and b["reason"] == "not_configured" and isinstance(b["missing_settings"], list) and b["missing_settings"] and b["data"] is None and b["data"] is None),
        _case(ids, "an unknown clinic is not found", "front_desk_a", 404, q(clinic_id=uuid.uuid4()), key=None, body=_fixed(404)),
        _case(ids, "a missing visit type is rejected", "front_desk_a", 422, f"{OFFER_DRAFT}?clinic_id={ids.clinic_a}", key=None, body=_fixed(422)),
        _case(ids, "a clinic id that is not a uuid is rejected", "front_desk_a", 422, q(clinic_id="nope"), key=None, body=_fixed(422)),
    ]
    cases += _offer_gate_cases(ids, lambda label, who, status: _case(ids, label, who, status, q(), key=None, body=_fixed(status)))
    return cases + _unauthenticated(ids, q(), None)


def offer_send_cases(ids) -> list[tuple]:
    body = lambda **o: {"clinic_id": str(ids.clinic_a), "visit_type_id": str(ids.vt_a), "slot_id": str(ids.pools["extra"][0]), "waitlist_id": str(uuid.uuid4()), **o}
    unavailable = lambda b: b["detail"].startswith("offers are unavailable: not configured: ") and "offer_message_template" in b["detail"]
    cases = [
        _case(ids, "front desk with the settings unset is told offers are unavailable", "front_desk_a", 503, OFFER_SEND, json=body(), body=unavailable),
        _case(ids, "an unknown clinic is not found", "front_desk_a", 404, OFFER_SEND, json=body(clinic_id=str(uuid.uuid4())), body=_fixed(404)),
        _case(ids, "missing X-Idempotency-Key is rejected", "front_desk_a", 422, OFFER_SEND, json=body(), key=None, body=_fixed(422)),
        _case(ids, "blank X-Idempotency-Key is rejected", "front_desk_a", 422, OFFER_SEND, json=body(), key="   ", body=_fixed(422)),
        _case(ids, "a slot id that is not a uuid is rejected", "front_desk_a", 422, OFFER_SEND, json=body(slot_id="nope"), body=_fixed(422)),
        _case(ids, "a client-supplied patient or origin is an unknown field", "front_desk_a", 422, OFFER_SEND, json=body(patient_id=str(uuid.uuid4())), body=_fixed(422)),
        _case(ids, "an empty body is rejected", "front_desk_a", 422, OFFER_SEND, json={}, body=_fixed(422)),
    ]
    cases += _offer_gate_cases(ids, lambda label, who, status: _case(ids, label, who, status, OFFER_SEND, json=body(), body=_fixed(status)))
    return cases + _unauthenticated(ids, OFFER_SEND, body())


# ---- /v1/staff/documents (begin, register) and /v1/staff/referrals (list, open) ---------------------------------------------------------------------------
#
# Uploads: front desk or a nurse of the clinic (the same two roles that may insert `docs.documents`). A caller who SEES the clinic row but is not one of those (doctor, admin, business owner,
# auditor) gets 403; everyone else, including front desk of ANOTHER clinic, gets the same 404 as an unknown clinic. The matrix app is built with a fake storage (module-level `UPLOADS`) in the
# `demo` profile with no scanner, so a registration reports `scan: not_checked`.
# Referral queue: readers are front desk, nurse and doctor of SOME clinic (everyone else 403 before any query). Front desk cannot read triage proposals, so for front desk `proposal_visible` is
# false and the proposal fields are null; a reader of another clinic sees none of this clinic's documents (list: empty of them; open: the same 404 as a missing one).

DOC_BEGIN = "/v1/staff/documents/uploads"
DOC_REGISTER = "/v1/staff/documents/register"
REFERRALS = "/v1/staff/referrals"
REFERRAL = REFERRALS + "/{document_id}"
PDF_BYTES = b"%PDF-1.7\n" + b"x" * 300
UPLOAD_ALLOWED = ("front_desk_a", "nurse_a", "multi")
UPLOAD_VISIBLE_NOT_ALLOWED = {r: 403 for r in ("doctor_a", "admin_a", "business_owner_a", "auditor_a")}


def scoped_key(ids, who, key):
    """The key as the app stores it: behind the caller's own account prefix (careflow/app.py key_scope)."""
    return f"{key_scope(str(ids.people[who][0]))}:{key}"


def _put_object(ids, key, content=PDF_BYTES, content_type="application/pdf", *, clinic=None, who="front_desk_a"):
    """The browser's half of an upload: take a ticket from the real intake for (clinic, the caller's scoped key) and PUT the bytes at the server-chosen path."""
    clinic = clinic or ids.clinic_a
    ticket = _run(DocumentIntake(UPLOADS, profile="demo").begin(clinic, "referral", content_type if content_type in ALLOWED_DOC_TYPES else "application/pdf", scoped_key(ids, who, key)))
    UPLOADS.client_put(ticket.path, content, content_type)
    return ticket.document_id


def _upload_refusal(status):
    """The upload gate has its own 403 words; a 404 is the app-wide fixed body."""
    return (lambda b: b == {"detail": "your role may not upload documents"}) if status == 403 else _fixed(status)


def _gate_cases(ids, make_request):
    cases = []
    for p in [*ids.people, "no account"]:
        if p in UPLOAD_ALLOWED:
            continue
        status = UPLOAD_VISIBLE_NOT_ALLOWED.get(p, 404)
        who = bearer(mint(ids.no_account)) if p == "no account" else p
        cases.append(make_request(f"{p} is refused", who, status))
    return cases


def document_begin_cases(ids) -> list[tuple]:
    body = lambda **o: {"clinic_id": str(ids.clinic_a), "kind": "referral", "content_type": "application/pdf", **o}
    key = _new_key()
    ticket = lambda b: set(b) == {"document_id", "upload_url", "expires_at", "replayed_ticket"} and b["upload_url"].startswith("fake://upload/clinic/")
    cases = [
        _case(ids, "front desk begins an upload", "front_desk_a", 200, DOC_BEGIN, json=body(), key=key, body=ticket),
        _case(ids, "the same key gets the same document id back", "front_desk_a", 200, DOC_BEGIN, json=body(), key=key,
              body=lambda b: b["document_id"] == str(document_id_for(ids.clinic_a, scoped_key(ids, "front_desk_a", key)))),
        _case(ids, "a nurse begins an upload", "nurse_a", 200, DOC_BEGIN, json=body(kind="report", content_type="image/png"), body=ticket),
        _case(ids, "a size hint above the bound is refused with a fixed word", "front_desk_a", 422, DOC_BEGIN, json=body(size_hint=21 * 1024 * 1024), body=lambda b: b == {"detail": "too_large"}),
        _case(ids, "a content type that is not allowed is rejected", "front_desk_a", 422, DOC_BEGIN, json=body(content_type="text/html"), body=_fixed(422)),
        _case(ids, "an unknown kind is rejected", "front_desk_a", 422, DOC_BEGIN, json=body(kind="selfie"), body=_fixed(422)),
        _case(ids, "a client-supplied path is an unknown field", "front_desk_a", 422, DOC_BEGIN, json=body(path="clinic/x/doc/y"), body=_fixed(422)),
        _case(ids, "an unknown clinic is not found", "front_desk_a", 404, DOC_BEGIN, json=body(clinic_id=str(uuid.uuid4())), body=_fixed(404)),
        _case(ids, "missing X-Idempotency-Key is rejected", "front_desk_a", 422, DOC_BEGIN, json=body(), key=None, body=_fixed(422)),
        _case(ids, "blank X-Idempotency-Key is rejected", "front_desk_a", 422, DOC_BEGIN, json=body(), key="   ", body=_fixed(422)),
        _case(ids, "an empty body is rejected", "front_desk_a", 422, DOC_BEGIN, json={}, body=_fixed(422)),
    ]
    cases += _gate_cases(ids, lambda label, who, status: _case(ids, label, who, status, DOC_BEGIN, json=body(), body=_upload_refusal(status)))
    return cases + _unauthenticated(ids, DOC_BEGIN, body())


def document_register_cases(ids) -> list[tuple]:
    body = lambda **o: {"clinic_id": str(ids.clinic_a), "kind": "referral", **o}
    k_ok, k_nurse, k_mismatch, k_empty = _new_key(), _new_key(), _new_key(), _new_key()
    doc_ok = _put_object(ids, k_ok)
    doc_nurse = _put_object(ids, k_nurse, who="nurse_a")
    _put_object(ids, k_mismatch, b"this is not a pdf at all", "application/pdf")
    _put_object(ids, k_empty, b"", "application/pdf")
    registered = lambda doc, replayed: (lambda b: b == {
        "document_id": str(doc), "kind": "referral", "content_type": "application/pdf", "size_bytes": len(PDF_BYTES), "replayed": replayed, "scan": "not_checked"})
    cases = [
        _case(ids, "front desk registers an uploaded file", "front_desk_a", 200, DOC_REGISTER, json=body(), key=k_ok, body=registered(doc_ok, False)),
        _case(ids, "replaying the same key is a replay of the uploaded file", "front_desk_a", 200, DOC_REGISTER, json=body(), key=k_ok, body=registered(doc_ok, True)),
        _case(ids, "the same key for a different kind is a conflict (uploaded file)", "front_desk_a", 409, DOC_REGISTER, json=body(kind="report"), key=k_ok,
              body=lambda b: b == {"detail": "this idempotency key was already used by a different request"}),
        _case(ids, "a nurse registers an uploaded file", "nurse_a", 200, DOC_REGISTER, json=body(), key=k_nurse, body=registered(doc_nurse, False)),
        _case(ids, "a key with nothing uploaded is refused with a fixed word", "front_desk_a", 422, DOC_REGISTER, json=body(), body=lambda b: b == {"detail": "not_found"}),
        _case(ids, "an object that is not what its type says is refused (uploaded file)", "front_desk_a", 422, DOC_REGISTER, json=body(), key=k_mismatch, body=lambda b: b == {"detail": "content_mismatch"}),
        _case(ids, "an empty object is refused (uploaded file)", "front_desk_a", 422, DOC_REGISTER, json=body(), key=k_empty, body=lambda b: b == {"detail": "empty"}),
        _case(ids, "an unknown clinic is not found", "front_desk_a", 404, DOC_REGISTER, json=body(clinic_id=str(uuid.uuid4())), body=_fixed(404)),
        _case(ids, "missing X-Idempotency-Key is rejected", "front_desk_a", 422, DOC_REGISTER, json=body(), key=None, body=_fixed(422)),
        _case(ids, "blank X-Idempotency-Key is rejected", "front_desk_a", 422, DOC_REGISTER, json=body(), key="   ", body=_fixed(422)),
        _case(ids, "an unknown field is rejected", "front_desk_a", 422, DOC_REGISTER, json=body(sha256="0" * 64), body=_fixed(422)),
        _case(ids, "an empty body is rejected", "front_desk_a", 422, DOC_REGISTER, json={}, body=_fixed(422)),
    ]
    cases += _gate_cases(ids, lambda label, who, status: _case(ids, label, who, status, DOC_REGISTER, json=body(), body=_upload_refusal(status)))
    return cases + _unauthenticated(ids, DOC_REGISTER, body())


async def _seed_referrals(conn, ids):
    """Owner-written synthetic referral documents in clinic A with a queued run, holds (front desk's and a nurse's) and a triage proposal."""
    fd = ids.people["front_desk_a"][0]

    async def doc():
        did = uuid.uuid4()
        await conn.execute(
            "insert into docs.documents (id, clinic_id, origin, kind, storage_path, content_type, size_bytes, sha256, registered_by, idempotency_key) "
            "values ($1, $2, 'test', 'referral', $3, 'application/pdf', 300, $4, $5, $6)",
            did, ids.clinic_a, f"clinic/{ids.clinic_a}/doc/{did}", "ab" * 32, fd, f"matrix-doc-{did}",
        )
        return did

    plain, queued, held_fd, held_nurse, proposed = await doc(), await doc(), await doc(), await doc(), await doc()
    await conn.execute(
        "insert into ops.queue (clinic_id, origin, task_type, payload, idempotency_key) values ($1, 'test', $2, $3::jsonb, $4)",
        ids.clinic_a, FRONT_DOOR_TASK, json.dumps({"document_id": str(queued)}), f"matrix-run-{queued}",
    )
    for did, role in ((held_fd, "front_desk"), (held_nurse, "nurse")):
        await _new_task_for_document(conn, ids, did, role)
    patient = await _new_worker_patient(conn, ids.clinic_a)
    task = await _new_task(conn, ids.clinic_a, "triage_proposal", "nurse")
    pid = uuid.uuid4()
    await conn.execute(
        "insert into agents.triage_proposals (id, clinic_id, origin, document_id, patient_id, candidates, abstained, mode, review_task_id, idempotency_key) "
        "values ($1, $2, 'test', $3, $4, $5::jsonb, false, 'assisted', $6, $7)",
        pid, ids.clinic_a, proposed, patient, json.dumps([{"card_id": "card-x", "version": 3}]), task, f"matrix-prop-{pid}",
    )
    return SimpleNamespace(plain=plain, queued=queued, held_fd=held_fd, held_nurse=held_nurse, proposed=proposed, proposal=pid, task=task)


async def _new_task_for_document(conn, ids, did, role):
    tid = uuid.uuid4()
    await conn.execute(
        "insert into agents.review_tasks (id, clinic_id, origin, kind, priority, assigned_role, source_message_id, provenance, idempotency_key) "
        "values ($1, $2, 'test', 'referral_hold', 'standard', $3, $4, '{}'::jsonb, $5)", tid, ids.clinic_a, role, f"document:{did}", f"matrix-hold-{tid}",
    )
    return tid


def referral_list_cases(ids) -> list[tuple]:
    s = _run(_owner(lambda conn: _seed_referrals(conn, ids)))
    row = lambda items, doc: next((i for i in items if i["document_id"] == str(doc)), None)

    def check(who, wants):
        def pred(items):
            for doc, expect in wants.items():
                r = row(items, doc)
                if r is None or any(r[k] != v for k, v in expect.items()):
                    return False
            return True

        return _page(ids, clinic_only=ids.clinic_a, check=pred)

    fd_view = {s.plain: {"run_state": None, "open_holds": 0}, s.queued: {"run_state": "unclaimed"}, s.held_fd: {"open_holds": 1}, s.held_nurse: {"open_holds": 0},
               s.proposed: {"proposal_visible": False, "proposal_id": None, "decided": None}}
    nurse_view = {s.plain: {"run_state": None, "open_holds": 0}, s.queued: {"run_state": "unclaimed"}, s.held_fd: {"open_holds": 1}, s.held_nurse: {"open_holds": 1},
                  s.proposed: {"proposal_visible": True, "proposal_id": str(s.proposal), "decided": False}}
    cases = [
        _case(ids, "front desk lists: its own holds only, and it cannot see proposals", "front_desk_a", 200, REFERRALS, key=None, body=check("fd", fd_view)),
        _case(ids, "a nurse lists: every hold and the proposal", "nurse_a", 200, REFERRALS, key=None, body=check("nurse", nurse_view)),
        _case(ids, "a doctor lists like a nurse", "doctor_a", 200, REFERRALS, key=None, body=check("doctor", nurse_view)),
        _case(ids, "a nurse here who is a doctor elsewhere lists like a nurse", "multi", 200, REFERRALS, key=None, body=check("multi", nurse_view)),
        _case(ids, "a reader of another clinic sees none of this clinic's documents", "front_desk_b", 200, REFERRALS, key=None, body=_page(ids, check=lambda items: items == [])),
        _case(ids, "limit=1 returns one row and says there are more", "nurse_a", 200, REFERRALS + "?limit=1", key=None, body=lambda b: len(b["items"]) == 1 and b["limit"] == 1 and b["has_more"] is True),
        _case(ids, "limit=0 is rejected", "nurse_a", 422, REFERRALS + "?limit=0", key=None, body=_fixed(422)),
        _case(ids, "limit=501 is rejected", "nurse_a", 422, REFERRALS + "?limit=501", key=None, body=_fixed(422)),
    ]
    readers = ("front_desk_a", "nurse_a", "doctor_a", "multi", "front_desk_b")
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused", p, 403, REFERRALS, key=None, body=READ_403) for p in ids.people if p not in readers]
    cases.append(_case(ids, "valid user with no iam account is refused", bearer(mint(ids.no_account)), 403, REFERRALS, key=None, body=READ_403))
    return cases + _unauthenticated(ids, REFERRALS, None)


def referral_open_cases(ids) -> list[tuple]:
    s = _run(_owner(lambda conn: _seed_referrals(conn, ids)))
    path = lambda d: REFERRAL.format(document_id=d)
    base = lambda d: (lambda b: b["document_id"] == str(d) and b["clinic_id"] == str(ids.clinic_a))
    cases = [
        _case(ids, "front desk opens a document with its own hold: one hold, no proposal visible", "front_desk_a", 200, path(s.held_fd), key=None,
              body=lambda b: base(s.held_fd)(b) and len(b["holds"]) == 1 and b["holds"][0]["assigned_role"] == "front_desk" and b["proposal"] is None and b["proposal_visible"] is False),
        _case(ids, "front desk cannot see a nurse's hold on a document", "front_desk_a", 200, path(s.held_nurse), key=None, body=lambda b: base(s.held_nurse)(b) and b["holds"] == []),
        _case(ids, "front desk opens a document with a proposal: the proposal is hidden, not absent", "front_desk_a", 200, path(s.proposed), key=None,
              body=lambda b: b["proposal_visible"] is False and b["proposal"] is None and b["proposal_id"] is None),
        _case(ids, "a nurse opens the proposal: card ids and versions only", "nurse_a", 200, path(s.proposed), key=None,
              body=lambda b: b["proposal_visible"] is True and b["proposal"]["proposal_id"] == str(s.proposal) and b["proposal"]["candidates"] == [{"card_id": "card-x", "version": 3}]
              and b["proposal"]["mode"] == "assisted" and b["proposal"]["abstained"] is False),
        _case(ids, "a doctor opens the proposal too", "doctor_a", 200, path(s.proposed), key=None, body=lambda b: b["proposal"] is not None),
        _case(ids, "a nurse sees both holds on their document", "nurse_a", 200, path(s.held_nurse), key=None, body=lambda b: len(b["holds"]) == 1 and b["holds"][0]["assigned_role"] == "nurse"),
        _case(ids, "a queued run is shown", "front_desk_a", 200, path(s.queued), key=None, body=lambda b: b["run_state"] == "unclaimed"),
        _case(ids, "a reader of another clinic gets the same 404 as a missing document", "front_desk_b", 404, path(s.plain), key=None, body=_fixed(404)),
        _case(ids, "an unknown document is not found", "nurse_a", 404, path(uuid.uuid4()), key=None, body=_fixed(404)),
        _case(ids, "a document id that is not a uuid is rejected", "nurse_a", 422, path("nope"), key=None, body=_fixed(422)),
    ]
    readers = ("front_desk_a", "nurse_a", "doctor_a", "multi", "front_desk_b")
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused", p, 403, path(s.plain), key=None, body=READ_403) for p in ids.people if p not in readers]
    cases.append(_case(ids, "valid user with no iam account is refused", bearer(mint(ids.no_account)), 403, path(s.plain), key=None, body=READ_403))
    return cases + _unauthenticated(ids, path(s.plain), None)


# ---- /v1/staff/patients/search and /v1/staff/patients/{id} (lookup and verified status) -----------------------------------------------------------------------
#
# Search: front desk of the clinic only (CLINIC_VISIBLE_NOT_FRONT_DESK get 403, everyone else the same 404 as an unknown clinic); read-only, exact, returns ids and flags but no name, date of birth
# or phone. Status: readers are front desk, nurse and doctor of SOME clinic (everyone else 403); a record of another clinic is the same 404 as a missing one.

PATIENT_SEARCH = "/v1/staff/patients/search"
PATIENT_STATUS = "/v1/staff/patients/{patient_id}"


async def _new_front_desk_patient(conn, clinic):
    pid = uuid.uuid4()
    await conn.execute(
        "insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, origin, idempotency_key) values ($1, $2, $3, 1, $3, 1, 'test', $4)",
        pid, clinic, b"x" * 40, f"matrix-fd-{uuid.uuid4().hex}",
    )
    return pid


async def _revoke_event(conn, clinic, patient, by):
    await conn.execute(
        "insert into mpi.identity_confirmations (clinic_id, patient_id, confirmed_by, origin, idempotency_key, kind, reason) values ($1, $2, $3, 'test', $4, 'revoke', 'wrong_person')",
        clinic, patient, by, f"matrix-rv-{uuid.uuid4().hex}",
    )


def patient_search_cases(ids) -> list[tuple]:
    body = lambda **o: {"clinic_id": str(ids.clinic_a), "name": "Nobody Atall", "dob": "1990-01-01", "phone": "9876500001", **o}
    cases = [
        _case(ids, "front desk searches for a person nobody registered: an empty list, not an error", "front_desk_a", 200, PATIENT_SEARCH, json=body(), key=None, body=lambda b: b == {"items": [], "truncated": False, "labels_withheld": False}),
        _case(ids, "the phone is optional", "front_desk_a", 200, PATIENT_SEARCH, json={k: v for k, v in body().items() if k != "phone"}, key=None, body=lambda b: b == {"items": [], "truncated": False, "labels_withheld": False}),
        _case(ids, "a date of birth in the future is refused with a named message", "front_desk_a", 422, PATIENT_SEARCH, json=body(dob="2999-01-01"), key=None,
              body=lambda b: b == {"detail": "date of birth must be a real past date in YYYY-MM-DD form"}),
        _case(ids, "a phone that is not an Indian mobile is refused with a named message", "front_desk_a", 422, PATIENT_SEARCH, json=body(phone="123"), key=None,
              body=lambda b: b == {"detail": "phone must be a valid Indian mobile number"}),
        _case(ids, "a blank name is rejected", "front_desk_a", 422, PATIENT_SEARCH, json=body(name=""), key=None, body=_fixed(422)),
        _case(ids, "an unknown field is rejected", "front_desk_a", 422, PATIENT_SEARCH, json=body(origin="live"), key=None, body=_fixed(422)),
        _case(ids, "a missing date of birth is rejected", "front_desk_a", 422, PATIENT_SEARCH, json={"clinic_id": str(ids.clinic_a), "name": "A B"}, key=None, body=_fixed(422)),
        _case(ids, "an unknown clinic is not found", "front_desk_a", 404, PATIENT_SEARCH, json=body(clinic_id=str(uuid.uuid4())), key=None, body=_fixed(404)),
    ]
    cases += _offer_gate_cases(ids, lambda label, who, status: _case(ids, label, who, status, PATIENT_SEARCH, json=body(), key=None, body=_fixed(status)))
    return cases + _unauthenticated(ids, PATIENT_SEARCH, body())


def patient_status_cases(ids) -> list[tuple]:
    fd = ids.people["front_desk_a"][0]
    worker, confirmed, desk, revoked = new_worker_patient(ids), new_confirmed_patient(ids), _run(_owner(lambda c: _new_front_desk_patient(c, ids.clinic_a))), new_confirmed_patient(ids)
    _run(_owner(lambda c: _revoke_event(c, ids.clinic_a, revoked, fd)))
    path = lambda p: PATIENT_STATUS.format(patient_id=p)

    def shape(patient, via, verified, event):
        return lambda b: (
            b["patient_id"] == str(patient) and b["clinic_id"] == str(ids.clinic_a) and b["registered_via"] == via and b["verified"] is verified
            and b["latest_identity_event"] == event and (b["latest_identity_event_at"] is None) == (event is None) and b["merged_into"] is None
        )

    cases = [_case(ids, f"{who} reads a worker-created record: not verified", who, 200, path(worker), key=None, body=shape(worker, "referral_worker", False, None)) for who in ("front_desk_a", "nurse_a", "doctor_a", "multi")]
    cases += [
        _case(ids, "a confirmed worker-created record is verified", "front_desk_a", 200, path(confirmed), key=None, body=shape(confirmed, "referral_worker", True, "confirm")),
        _case(ids, "a front-desk-created record with no event is verified", "nurse_a", 200, path(desk), key=None, body=shape(desk, "front_desk", True, None)),
        _case(ids, "a confirmation that was revoked is not verified: the latest event wins", "front_desk_a", 200, path(revoked), key=None, body=shape(revoked, "referral_worker", False, "revoke")),
        _case(ids, "a reader of another clinic gets the same 404 as a missing record", "front_desk_b", 404, path(worker), key=None, body=_fixed(404)),
        _case(ids, "an unknown record is not found", "nurse_a", 404, path(uuid.uuid4()), key=None, body=_fixed(404)),
        _case(ids, "a patient id that is not a uuid is rejected", "nurse_a", 422, path("nope"), key=None, body=_fixed(422)),
    ]
    readers = ("front_desk_a", "nurse_a", "doctor_a", "multi", "front_desk_b")
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused", p, 403, path(worker), key=None, body=READ_403) for p in ids.people if p not in readers]
    cases.append(_case(ids, "valid user with no iam account is refused", bearer(mint(ids.no_account)), 403, path(worker), key=None, body=READ_403))
    return cases + _unauthenticated(ids, path(worker), None)


# ---- scheduling reference lists (providers, locations, visit types, priority levels) ---------------------------------------------------------------------------
#
# Same readers as the other scheduling reads: front desk, nurse and doctor of the clinic (and `multi`, a nurse here); a reader of ANOTHER clinic is allowed and sees none of this clinic's rows;
# everyone else is refused with 403 before any query. The matrix clinic has one provider, one location, one visit type and two priority levels (level_one rank 1, level_two rank 2).

REF_ROUTES = {
    "providers": PREFIX + "/providers", "locations": PREFIX + "/locations", "visit_types": PREFIX + "/visit-types", "priority_levels": PREFIX + "/priority-levels",
}


def reference_cases(ids, route) -> list[tuple]:
    path = REF_ROUTES[route]
    seeded = {
        "providers": lambda items: str(ids.provider_a) in {i["provider_id"] for i in items} and all(set(i) == {"provider_id", "clinic_id", "display_name", "active"} for i in items),
        "locations": lambda items: str(ids.location_a) in {i["location_id"] for i in items} and all(set(i) == {"location_id", "clinic_id", "name", "active"} for i in items),
        "visit_types": lambda items: str(ids.vt_a) in {i["visit_type_id"] for i in items} and all(set(i) == {"visit_type_id", "clinic_id", "name", "duration_minutes", "active"} for i in items),
        "priority_levels": lambda items: [(i["code"], i["rank"]) for i in items] == [("level_one", 1), ("level_two", 2)] and all(set(i) == {"clinic_id", "code", "label", "rank", "active"} for i in items),
    }[route]
    readers = ("front_desk_a", "nurse_a", "doctor_a", "multi")
    # `multi` is a nurse in clinic A and a doctor in clinic B, and clinic B has its own visit type: it sees both clinics' visit types (and only clinic A's other lists)
    cases = [
        _case(ids, f"{who} reads the {route.replace('_', ' ')}", who, 200, path, key=None,
              body=_page(ids, clinic_only=None if (who == "multi" and route == "visit_types") else ids.clinic_a, non_empty=True, check=seeded))
        for who in readers
    ]
    cases += [
        # clinic B has its own visit type, so "none of this clinic's rows" means no row of clinic A (not an empty list)
        _case(ids, "a reader of another clinic is allowed and sees none of this clinic's rows", "front_desk_b", 200, path, key=None, body=_page(ids, check=lambda items: all(i["clinic_id"] != str(ids.clinic_a) for i in items))),
        _case(ids, "include_inactive is accepted", "front_desk_a", 200, path + "?include_inactive=true", key=None, body=_page(ids, clinic_only=ids.clinic_a, non_empty=True, check=seeded)),
        _case(ids, "limit=1 returns one row", "front_desk_a", 200, path + "?limit=1", key=None, body=lambda b: len(b["items"]) == 1 and b["limit"] == 1 and (b["has_more"] is True or route != "priority_levels")),
        _case(ids, "limit=0 is rejected", "front_desk_a", 422, path + "?limit=0", key=None, body=_fixed(422)),
        _case(ids, "limit=501 is rejected", "front_desk_a", 422, path + "?limit=501", key=None, body=_fixed(422)),
        _case(ids, "include_inactive that is not a boolean is rejected", "front_desk_a", 422, path + "?include_inactive=maybe", key=None, body=_fixed(422)),
        _case(ids, "clinic_id of the reader's own clinic returns that clinic's rows", "front_desk_a", 200, f"{path}?clinic_id={ids.clinic_a}", key=None, body=_page(ids, clinic_only=ids.clinic_a, non_empty=True, check=seeded)),
        _case(ids, "clinic_id of a clinic the reader cannot read is the one not-found", "front_desk_a", 404, f"{path}?clinic_id={ids.clinic_b}", key=None, body=_fixed(404)),
        _case(ids, "clinic_id of a clinic that does not exist is the same not-found", "front_desk_a", 404, f"{path}?clinic_id={uuid.uuid4()}", key=None, body=_fixed(404)),
        _case(ids, "a reader of the other clinic who names this clinic gets the same not-found", "front_desk_b", 404, f"{path}?clinic_id={ids.clinic_a}", key=None, body=_fixed(404)),
        _case(ids, "a clinic_id that is not a uuid is rejected", "front_desk_a", 422, f"{path}?clinic_id=nope", key=None, body=_fixed(422)),
        _case(ids, "a non-reader who names a clinic is still refused first", "admin_a", 403, f"{path}?clinic_id={ids.clinic_a}", key=None, body=lambda b: b == {"detail": "your role may not read this"}),
    ]
    denied = [p for p in ids.people if p not in (*readers, "front_desk_b")]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused", p, 403, path, key=None, body=lambda b: b == {"detail": "your role may not read this"}) for p in denied]
    cases.append(_case(ids, "valid user with no iam account is refused", bearer(mint(ids.no_account)), 403, path, key=None, body=lambda b: b == {"detail": "your role may not read this"}))
    return cases + _unauthenticated(ids, path, None)


# ---- /v1/staff/identity/possible-duplicates, /merges, /merges/{id} (reads only; outside the step-up router on purpose) --------------------------------------------
#
# Possible duplicates: front desk only (the existing select policy). Merges: front desk, nurse and doctor. A reader of ANOTHER clinic gets an empty list / the same 404 as a missing record;
# everyone else is refused with 403 before any query. No step-up (it protects the three write steps, not reading a list).

DUPLICATES = "/v1/staff/identity/possible-duplicates"
MERGE_LIST = "/v1/staff/identity/merges"
MERGE_OPEN = "/v1/staff/identity/merges/{merge_id}"
READ_403_BODY = lambda b: b == {"detail": "your role may not read this"}


def duplicate_list_cases(ids) -> list[tuple]:
    _, _, pending = new_pair(ids)
    _, _, other = new_pair(ids)
    _run(_owner(lambda c: c.execute("update mpi.match_candidates set status = 'resolved', resolved_at = now(), resolved_by = $2 where id = $1", other, ids.fd2)))
    has = lambda cid, want: (lambda items: (str(cid) in {i["candidate_id"] for i in items}) is want)
    cases = [
        _case(ids, "front desk lists the pending duplicates by default", "front_desk_a", 200, DUPLICATES, key=None, body=_page(ids, clinic_only=ids.clinic_a, non_empty=True, check=lambda items: has(pending, True)(items) and has(other, False)(items) and all(i["status"] == "pending" for i in items))),
        _case(ids, "status=resolved lists the resolved ones", "front_desk_a", 200, DUPLICATES + "?status=resolved", key=None, body=_page(ids, clinic_only=ids.clinic_a, non_empty=True, check=lambda items: has(other, True)(items) and has(pending, False)(items))),
        _case(ids, "an item carries ids, kinds and closed words only", "front_desk_a", 200, DUPLICATES, key=None,
              body=_page(ids, check=lambda items: all(set(i) == {"candidate_id", "clinic_id", "patient_id", "candidate_patient_id", "matched_kinds", "reason", "status", "created_at", "resolved_at"} for i in items))),
        _case(ids, "a reader of another clinic sees none of this clinic's", "front_desk_b", 200, DUPLICATES, key=None, body=_page(ids, check=lambda items: items == [])),
        _case(ids, "limit=1 returns one row and says there are more", "front_desk_a", 200, DUPLICATES + "?limit=1", key=None, body=lambda b: len(b["items"]) == 1 and b["has_more"] is True),
        _case(ids, "an unknown status is rejected", "front_desk_a", 422, DUPLICATES + "?status=bogus", key=None, body=_fixed(422)),
        _case(ids, "limit=0 is rejected", "front_desk_a", 422, DUPLICATES + "?limit=0", key=None, body=_fixed(422)),
    ]
    denied = [p for p in ids.people if p not in ("front_desk_a", "front_desk_b")]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused", p, 403, DUPLICATES, key=None, body=READ_403_BODY) for p in denied]
    cases.append(_case(ids, "valid user with no iam account is refused", bearer(mint(ids.no_account)), 403, DUPLICATES, key=None, body=READ_403_BODY))
    return cases + _unauthenticated(ids, DUPLICATES, None)


def merge_list_cases(ids) -> list[tuple]:
    awaiting, _, _ = new_merge(ids)
    active, _, _ = new_merge(ids, "active")
    has = lambda mid, want: (lambda items: (str(mid) in {i["merge_id"] for i in items}) is want)
    readers = ("front_desk_a", "nurse_a", "doctor_a", "multi")
    cases = [
        _case(ids, f"{who} lists both merges", who, 200, MERGE_LIST, key=None, body=_page(ids, clinic_only=ids.clinic_a, non_empty=True, check=lambda items: has(awaiting, True)(items) and has(active, True)(items)))
        for who in readers
    ]
    cases += [
        _case(ids, "status=active lists only the active ones", "front_desk_a", 200, MERGE_LIST + "?status=active", key=None,
              body=_page(ids, clinic_only=ids.clinic_a, non_empty=True, check=lambda items: has(active, True)(items) and has(awaiting, False)(items) and all(i["status"] == "active" for i in items))),
        _case(ids, "a merge carries ids, statuses and times only", "nurse_a", 200, MERGE_LIST, key=None,
              body=_page(ids, check=lambda items: all(set(i) == {"merge_id", "clinic_id", "master_patient_id", "subordinate_patient_id", "candidate_id", "status", "proposed_by", "second_approved_by", "activated_at", "unmerged_at", "unmerged_by", "created_at"} for i in items))),
        _case(ids, "a reader of another clinic sees none of this clinic's", "front_desk_b", 200, MERGE_LIST, key=None, body=_page(ids, check=lambda items: items == [])),
        _case(ids, "an unknown status is rejected", "front_desk_a", 422, MERGE_LIST + "?status=bogus", key=None, body=_fixed(422)),
        _case(ids, "limit=501 is rejected", "front_desk_a", 422, MERGE_LIST + "?limit=501", key=None, body=_fixed(422)),
    ]
    denied = [p for p in ids.people if p not in (*readers, "front_desk_b")]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused", p, 403, MERGE_LIST, key=None, body=READ_403_BODY) for p in denied]
    cases.append(_case(ids, "valid user with no iam account is refused", bearer(mint(ids.no_account)), 403, MERGE_LIST, key=None, body=READ_403_BODY))
    return cases + _unauthenticated(ids, MERGE_LIST, None)


def merge_open_cases(ids) -> list[tuple]:
    mid, master, sub = new_merge(ids, "active")
    path = lambda m: MERGE_OPEN.format(merge_id=m)
    shape = lambda b: b["merge_id"] == str(mid) and b["master_patient_id"] == str(master) and b["subordinate_patient_id"] == str(sub) and b["status"] == "active" and b["second_approved_by"] == str(ids.fd2)
    readers = ("front_desk_a", "nurse_a", "doctor_a", "multi")
    cases = [_case(ids, f"{who} opens the merge", who, 200, path(mid), key=None, body=shape) for who in readers]
    cases += [
        _case(ids, "a reader of another clinic gets the same 404 as a missing merge", "front_desk_b", 404, path(mid), key=None, body=_fixed(404)),
        _case(ids, "an unknown merge is not found", "nurse_a", 404, path(uuid.uuid4()), key=None, body=_fixed(404)),
        _case(ids, "a merge id that is not a uuid is rejected", "nurse_a", 422, path("nope"), key=None, body=_fixed(422)),
    ]
    denied = [p for p in ids.people if p not in (*readers, "front_desk_b")]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused", p, 403, path(mid), key=None, body=READ_403_BODY) for p in denied]
    cases.append(_case(ids, "valid user with no iam account is refused", bearer(mint(ids.no_account)), 403, path(mid), key=None, body=READ_403_BODY))
    return cases + _unauthenticated(ids, path(mid), None)


# ---- /v1/staff/scheduling/holds (list) and /holds/{id}/release ------------------------------------------------------------------------------------------------
#
# List: the same readers as the other scheduling reads (front desk, nurse, doctor), a reader of another clinic sees none of this clinic's. Release: front desk of the clinic only; nurse, doctor and
# admin can SEE a hold and are refused with 403; everyone else gets the same 404 as a missing hold. A hold that was booked or has expired is a 409; an already released one is a replay.

HOLDS = PREFIX + "/holds"
HOLD_RELEASE = PREFIX + "/holds/{hold_id}/release"
HOLD_VISIBLE_NOT_FRONT_DESK = {"nurse_a": 403, "doctor_a": 403, "admin_a": 403, "multi": 403}


def new_hold(ids, status="active", patient=None):
    """A synthetic hold on a fresh matrix slot, written by the owner (the slot is 'held' while the hold is active)."""

    async def make(conn):
        slot = ids.pools["extra"].pop()
        hid = uuid.uuid4()
        await conn.execute(
            "insert into sched.holds (id, clinic_id, slot_id, patient_id, expires_at, origin, idempotency_key, status) values ($1, $2, $3, $4, now() + interval '1 hour', 'test', $5, $6)",
            hid, ids.clinic_a, slot, patient, f"matrix-hold-{hid}", status,
        )
        if status == "active":
            await conn.execute("update sched.slots set status = 'held' where id = $1", slot)
        return hid, slot

    return _run(_owner(make))


def hold_list_cases(ids) -> list[tuple]:
    active, _ = new_hold(ids)
    released, _ = new_hold(ids, "released")
    has = lambda hid, want: (lambda items: (str(hid) in {i["hold_id"] for i in items}) is want)
    readers = ("front_desk_a", "nurse_a", "doctor_a", "multi")
    cases = [
        _case(ids, f"{who} lists the active holds by default", who, 200, HOLDS, key=None,
              body=_page(ids, clinic_only=ids.clinic_a, non_empty=True, check=lambda items: has(active, True)(items) and has(released, False)(items) and all(i["status"] == "active" for i in items)))
        for who in readers
    ]
    cases += [
        _case(ids, "status=released lists the released ones", "front_desk_a", 200, HOLDS + "?status=released", key=None,
              body=_page(ids, clinic_only=ids.clinic_a, non_empty=True, check=lambda items: has(released, True)(items) and has(active, False)(items))),
        _case(ids, "a hold carries ids, a status and times only", "nurse_a", 200, HOLDS, key=None,
              body=_page(ids, check=lambda items: all(set(i) == {"hold_id", "clinic_id", "slot_id", "patient_id", "status", "created_at", "expires_at"} for i in items))),
        _case(ids, "a reader of another clinic sees none of this clinic's holds", "front_desk_b", 200, HOLDS, key=None, body=_page(ids, check=lambda items: items == [])),
        _case(ids, "an unknown status is rejected", "front_desk_a", 422, HOLDS + "?status=bogus", key=None, body=_fixed(422)),
        _case(ids, "clinic_id of the reader's own clinic lists that clinic's holds", "front_desk_a", 200, f"{HOLDS}?clinic_id={ids.clinic_a}", key=None, body=_page(ids, clinic_only=ids.clinic_a, non_empty=True, check=has(active, True))),
        _case(ids, "clinic_id of a clinic the reader cannot read is the one not-found", "front_desk_a", 404, f"{HOLDS}?clinic_id={ids.clinic_b}", key=None, body=_fixed(404)),
        _case(ids, "a clinic_id that does not exist is the same not-found", "front_desk_a", 404, f"{HOLDS}?clinic_id={uuid.uuid4()}", key=None, body=_fixed(404)),
        _case(ids, "a clinic_id that is not a uuid is rejected", "front_desk_a", 422, f"{HOLDS}?clinic_id=nope", key=None, body=_fixed(422)),
        _case(ids, "limit=0 is rejected", "front_desk_a", 422, HOLDS + "?limit=0", key=None, body=_fixed(422)),
    ]
    denied = [p for p in ids.people if p not in (*readers, "front_desk_b")]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused", p, 403, HOLDS, key=None, body=lambda b: b == {"detail": "your role may not read this"}) for p in denied]
    cases.append(_case(ids, "valid user with no iam account is refused", bearer(mint(ids.no_account)), 403, HOLDS, key=None, body=lambda b: b == {"detail": "your role may not read this"}))
    return cases + _unauthenticated(ids, HOLDS, None)


def hold_release_cases(ids) -> list[tuple]:
    ok, _ = new_hold(ids)
    booked, _ = new_hold(ids, "converted")
    expired, _ = new_hold(ids, "expired")
    refused, _ = new_hold(ids)
    path = lambda h: HOLD_RELEASE.format(hold_id=h)
    key = _new_key()
    released = lambda hid, replayed: (lambda b: b["hold_id"] == str(hid) and b["status"] == "released" and b["replayed"] is replayed)
    cases = [
        _case(ids, "front desk releases an active hold", "front_desk_a", 200, path(ok), key=key, body=released(ok, False)),
        _case(ids, "replaying the same key is a replay", "front_desk_a", 200, path(ok), key=key, body=released(ok, True)),
        _case(ids, "a released hold under a new key is a replay in effect", "front_desk_a", 200, path(ok), body=released(ok, True)),
        _case(ids, "a hold that was booked is a conflict, never reopened", "front_desk_a", 409, path(booked), body=lambda b: b == {"detail": "hold is converted"}),
        _case(ids, "a hold that has expired is a conflict", "front_desk_a", 409, path(expired), body=lambda b: b == {"detail": "hold is expired"}),
        _case(ids, "an unknown hold is not found", "front_desk_a", 404, path(uuid.uuid4()), body=_fixed(404)),
        _case(ids, "front desk of another clinic gets the same 404", "front_desk_b", 404, path(refused), body=_fixed(404)),
        _case(ids, "missing X-Idempotency-Key is rejected", "front_desk_a", 422, path(refused), key=None, body=_fixed(422)),
        _case(ids, "blank X-Idempotency-Key is rejected", "front_desk_a", 422, path(refused), key="   ", body=_fixed(422)),
        _case(ids, "a hold id that is not a uuid is rejected", "front_desk_a", 422, path("nope"), body=_fixed(422)),
    ]
    for p in [*ids.people, "no account"]:
        if p in ("front_desk_a", "front_desk_b"):
            continue
        status = HOLD_VISIBLE_NOT_FRONT_DESK.get(p, 404)
        who = bearer(mint(ids.no_account)) if p == "no account" else p
        cases.append(_case(ids, f"{p} is refused", who, status, path(refused), body=_fixed(status)))
    return cases + _unauthenticated(ids, path(refused), None)


# ---- /v1/staff/scheduling/offers (list) and /offers/{hold_id}/mark-delivered (a person delivered the offer by hand; migration 0031) -----------------------------------------------------------
#
# List: front desk, nurse, doctor of the clinic (the scheduling readers); a reader of another clinic sees none of this clinic's offers. Mark delivered: front desk of the clinic only; nurse, doctor and admin
# can SEE the offer and get 403; everyone else the same 404 as a missing hold. A hold that is not an offer, or an offer whose hold is no longer live, is a 409; a second press is a replay.

OFFERS = PREFIX + "/offers"
OFFER_DELIVERED = PREFIX + "/offers/{hold_id}/mark-delivered"


def new_offer_hold(ids, status="active"):
    """A synthetic OFFER hold (the key prefix `offer-hold:` is what marks one) on a fresh matrix slot, written by the owner."""

    async def make(conn):
        slot = ids.pools["extra"].pop()
        hid = uuid.uuid4()
        expires = "now() + interval '1 hour'" if status != "expired" else "now() + interval '1 second'"
        await conn.execute(
            f"insert into sched.holds (id, clinic_id, slot_id, patient_id, expires_at, origin, idempotency_key, status) values ($1, $2, $3, $4, {expires}, 'test', $5, $6)",
            hid, ids.clinic_a, slot, ids.patients_a[7], f"offer-hold:matrix-{hid}", "active" if status == "expired" else status,
        )
        if status == "active":
            await conn.execute("update sched.slots set status = 'held' where id = $1", slot)
        if status == "expired":
            await asyncio.sleep(1.5)
        return hid

    return _run(_owner(make))


def offer_list_cases(ids) -> list[tuple]:
    waiting, expired = new_offer_hold(ids), new_offer_hold(ids, "expired")
    plain, _slot = new_hold(ids)  # not an offer: never listed
    state_of = lambda hid, items: next((i["state"] for i in items if i["hold_id"] == str(hid)), None)
    readers = ("front_desk_a", "nurse_a", "doctor_a", "multi")
    cases = [
        _case(ids, f"{who} lists the offers", who, 200, OFFERS, key=None,
              body=_page(ids, clinic_only=ids.clinic_a, non_empty=True, check=lambda items: state_of(waiting, items) == "awaiting_delivery" and state_of(expired, items) == "expired" and state_of(plain, items) is None))
        for who in readers
    ]
    cases += [
        _case(ids, "state=expired filters", "front_desk_a", 200, OFFERS + "?state=expired", key=None,
              body=_page(ids, clinic_only=ids.clinic_a, non_empty=True, check=lambda items: all(i["state"] == "expired" for i in items) and state_of(waiting, items) is None)),
        _case(ids, "an offer row carries ids, times and closed words only", "nurse_a", 200, OFFERS, key=None,
              body=_page(ids, check=lambda items: all(set(i) == {"hold_id", "clinic_id", "slot_id", "patient_id", "state", "created_at", "expires_at", "delivered_at", "channel", "message_id"} for i in items))),
        _case(ids, "a reader of another clinic sees none of this clinic's offers", "front_desk_b", 200, OFFERS, key=None, body=_page(ids, check=lambda items: items == [])),
        _case(ids, "an unknown state is rejected", "front_desk_a", 422, OFFERS + "?state=sent", key=None, body=_fixed(422)),
        _case(ids, "clinic_id of the reader's own clinic lists that clinic's offers", "front_desk_a", 200, f"{OFFERS}?clinic_id={ids.clinic_a}", key=None,
              body=_page(ids, clinic_only=ids.clinic_a, non_empty=True, check=lambda items: state_of(waiting, items) == "awaiting_delivery")),
        _case(ids, "clinic_id of a clinic the reader cannot read is the one not-found", "front_desk_a", 404, f"{OFFERS}?clinic_id={ids.clinic_b}", key=None, body=_fixed(404)),
        _case(ids, "a clinic_id that does not exist is the same not-found", "front_desk_a", 404, f"{OFFERS}?clinic_id={uuid.uuid4()}", key=None, body=_fixed(404)),
        _case(ids, "a clinic_id that is not a uuid is rejected", "front_desk_a", 422, f"{OFFERS}?clinic_id=nope", key=None, body=_fixed(422)),
        _case(ids, "limit=0 is rejected", "front_desk_a", 422, OFFERS + "?limit=0", key=None, body=_fixed(422)),
    ]
    denied = [p for p in ids.people if p not in (*readers, "front_desk_b")]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused", p, 403, OFFERS, key=None, body=lambda b: b == {"detail": "your role may not read this"}) for p in denied]
    cases.append(_case(ids, "valid user with no iam account is refused", bearer(mint(ids.no_account)), 403, OFFERS, key=None, body=lambda b: b == {"detail": "your role may not read this"}))
    return cases + _unauthenticated(ids, OFFERS, None)


def offer_delivered_cases(ids) -> list[tuple]:
    ok, refused, dead, released = new_offer_hold(ids), new_offer_hold(ids), new_offer_hold(ids, "expired"), new_offer_hold(ids, "released")
    plain, _slot = new_hold(ids)
    path = lambda h: OFFER_DELIVERED.format(hold_id=h)
    delivered = lambda hid, replayed: (lambda b: b["hold_id"] == str(hid) and b["channel"] == "manual" and b["replayed"] is replayed and b["delivered_at"])
    cases = [
        _case(ids, "front desk marks an offer delivered", "front_desk_a", 200, path(ok), body=delivered(ok, False)),
        _case(ids, "a second press is a replay and writes nothing more", "front_desk_a", 200, path(ok), body=delivered(ok, True)),
        _case(ids, "a hold that is not an offer is a conflict", "front_desk_a", 409, path(plain), body=lambda b: b == {"detail": "this hold is not an offer"}),
        _case(ids, "an expired offer cannot be marked delivered", "front_desk_a", 409, path(dead), body=lambda b: b == {"detail": "this offer is no longer live"}),
        _case(ids, "a released offer cannot be marked delivered", "front_desk_a", 409, path(released), body=lambda b: b == {"detail": "this offer is no longer live"}),
        _case(ids, "an unknown hold is not found", "front_desk_a", 404, path(uuid.uuid4()), body=_fixed(404)),
        _case(ids, "front desk of another clinic gets the same 404", "front_desk_b", 404, path(refused), body=_fixed(404)),
        _case(ids, "missing X-Idempotency-Key is rejected", "front_desk_a", 422, path(refused), key=None, body=_fixed(422)),
        _case(ids, "a hold id that is not a uuid is rejected", "front_desk_a", 422, path("nope"), body=_fixed(422)),
    ]
    for p in [*ids.people, "no account"]:
        if p in ("front_desk_a", "front_desk_b"):
            continue
        status = HOLD_VISIBLE_NOT_FRONT_DESK.get(p, 404)
        who = bearer(mint(ids.no_account)) if p == "no account" else p
        cases.append(_case(ids, f"{p} is refused", who, status, path(refused), body=_fixed(status) if status == 404 else (lambda b: b == {"detail": "your role may not do this"})))
    return cases + _unauthenticated(ids, path(refused), None)


def test_a_delivery_record_cannot_be_changed_or_deleted_even_by_the_owner(client, ids):
    hold = new_offer_hold(ids)
    done = client.post(OFFER_DELIVERED.format(hold_id=hold), headers={**bearer(mint(ids.people["front_desk_a"][0])), "X-Idempotency-Key": _new_key()})
    assert done.status_code == 200
    for statement in ("update sched.offer_deliveries set channel = 'manual' where hold_id = $1", "delete from sched.offer_deliveries where hold_id = $1"):
        with pytest.raises(asyncpg.PostgresError, match="append-only"):
            _run(_fetch(statement, hold))
    rows = _run(_fetch("select delivered_by, channel from sched.offer_deliveries where hold_id = $1", hold))
    assert [(r["delivered_by"], r["channel"]) for r in rows] == [(ids.people["front_desk_a"][0], "manual")]
    audit = _run(_fetch("select actor_role, details from audit.log where action = 'offer.mark_delivered' and resource_id = $1", str(hold)))
    assert len(audit) == 1 and audit[0]["actor_role"] == "front_desk"


# ---- two-person erasure (migration 0032; BUILD-SPEC 12.5): POST /patients/{id}/erasure-requests, GET /erasure-requests, POST /erasure-requests/{id}/approve and /cancel ------------------------------------
#
# The matrix app is configured with the TEST reason `entered_in_error` (a stand-in input: the real list is the clinic's decision). Front desk of the clinic requests and cancels; a DIFFERENT front-desk
# person approves. Nurse, doctor and the other staff who can SEE the patient get 403; everyone else the same 404. Every write needs a recent second factor.

ERASE_REQUEST = "/v1/staff/patients/{patient_id}/erasure-requests"
ERASURES = "/v1/staff/erasure-requests"
ERASE_REASON = "entered_in_error"
NO_MESSAGE = {"detail": "your role may not do this"}


def new_erasure_request(ids, *, with_live_merge=False, status="requested"):
    """An erasure request written by the owner (requested by front_desk_a), for a fresh patient. Returns (request_id, patient_id)."""

    async def make(conn):
        master, subordinate, cand = await _new_pair(conn, ids.clinic_a, candidate=with_live_merge)
        if with_live_merge:
            await conn.execute(
                "insert into mpi.merges (clinic_id, master_patient_id, subordinate_patient_id, candidate_id, approved_by, origin, idempotency_key) values ($1, $2, $3, $4, $5, 'test', $6)",
                ids.clinic_a, master, subordinate, cand, ids.people["front_desk_a"][0], f"matrix-erase-merge-{uuid.uuid4().hex}",
            )
        request_id = await conn.fetchval(
            "insert into mpi.erasure_requests (clinic_id, patient_id, reason, requested_by, origin, idempotency_key) values ($1, $2, $3, $4, 'test', $5) returning id",
            ids.clinic_a, master, ERASE_REASON, ids.people["front_desk_a"][0], f"matrix-erase-{uuid.uuid4().hex}",
        )
        if status == "cancelled":
            await conn.execute("update mpi.erasure_requests set status = 'cancelled', decided_at = now() where id = $1", request_id)
        return request_id, master

    return _run(_owner(make))


def erase_request_cases(ids) -> list[tuple]:
    fd = stepped(ids.people["front_desk_a"][0])
    ok, _, _ = new_pair(ids, candidate=False)
    second, _, _ = new_pair(ids, candidate=False)
    refused, _, _ = new_pair(ids, candidate=False)
    path = lambda p: ERASE_REQUEST.format(patient_id=p)
    body = lambda **o: {"reason": ERASE_REASON, **o}
    key = _new_key()
    asked = lambda patient: (lambda b: set(b) == {"request_id", "patient_id", "status"} and b["patient_id"] == str(patient) and b["status"] == "requested")
    cases = [
        _case(ids, "front desk asks for an erasure", fd, 200, path(ok), json=body(), key=key, body=asked(ok)),
        _case(ids, "the same request and key answers the same request", fd, 200, path(ok), json=body(), key=key, body=asked(ok)),
        _case(ids, "a second open request for the same patient is a conflict", fd, 409, path(ok), json=body(), body=lambda b: b == {"detail": "an erasure request is already open for this patient"}),
        _case(ids, "a reason that is not on the clinic's list is rejected", fd, 422, path(second), json=body(reason="because"), body=_rejected),
        _case(ids, "an unknown field is rejected", fd, 422, path(second), json=body(surprise=1), body=_fixed(422)),
        _case(ids, "missing X-Idempotency-Key is rejected", fd, 422, path(second), json=body(), key=None, body=_fixed(422)),
        _case(ids, "a patient that does not exist is not found", fd, 404, path(uuid.uuid4()), json=body(), body=_fixed(404)),
        _case(ids, "front desk of ANOTHER clinic gets the same 404", stepped(ids.people["front_desk_b"][0]), 404, path(second), json=body(), body=_fixed(404)),
        _case(ids, "a patient id that is not a uuid is rejected", fd, 422, path("nope"), json=body(), body=_fixed(422)),
    ]
    visible_but_not_front_desk = {r: 403 for r in ("nurse_a", "doctor_a", "multi")}
    cases += _everyone_at_the_gate(ids, lambda: path(refused), body, visible_but_not_front_desk, 404)
    cases += _not_stepped_cases(ids, lambda: path(refused), body)
    return cases + _unauthenticated(ids, path(refused), body())


def erasure_reasons_cases(ids) -> list[tuple]:
    """GET .../erasure-requests/reasons: front desk of any clinic reads the closed list (the matrix app is configured with the test reason `entered_in_error`); everyone else is refused with 403."""
    path = ERASURES + "/reasons"
    cases = [_case(ids, f"{p} reads the list", p, 200, path, key=None, body=lambda b: b == {"reasons": ["entered_in_error"]}) for p in ("front_desk_a", "front_desk_b")]
    denied = [p for p in ids.people if p not in ("front_desk_a", "front_desk_b")]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused", p, 403, path, key=None, body=lambda b: b == {"detail": "your role may not read this"}) for p in denied]
    cases.append(_case(ids, "valid user with no iam account is refused", bearer(mint(ids.no_account)), 403, path, key=None, body=lambda b: b == {"detail": "your role may not read this"}))
    return cases + _unauthenticated(ids, path, None)


def erasure_list_cases(ids) -> list[tuple]:
    new_erasure_request(ids)
    readers = ("front_desk_a", "multi")  # multi is a nurse in A and a doctor in B: NOT front desk anywhere, so refused below
    cases = [
        _case(ids, "front desk lists the open requests", "front_desk_a", 200, ERASURES, key=None,
              body=_page(ids, non_empty=True, check=lambda items: all(set(i) == {"request_id", "patient_id", "reason", "status", "requested_by", "approved_by", "created_at", "decided_at"} and i["status"] == "requested" for i in items))),
        _case(ids, "an unknown status is rejected", "front_desk_a", 422, ERASURES + "?status=done", key=None, body=_fixed(422)),
        _case(ids, "limit=0 is rejected", "front_desk_a", 422, ERASURES + "?limit=0", key=None, body=_fixed(422)),
        _case(ids, "front desk of another clinic sees none of this clinic's requests", "front_desk_b", 200, ERASURES, key=None, body=_page(ids, check=lambda items: items == [])),
    ]
    denied = [p for p in ids.people if p not in (readers[0], "front_desk_b")]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused", p, 403, ERASURES, key=None, body=lambda b: b == {"detail": "your role may not read this"}) for p in denied]
    cases.append(_case(ids, "valid user with no iam account is refused", bearer(mint(ids.no_account)), 403, ERASURES, key=None, body=lambda b: b == {"detail": "your role may not read this"}))
    return cases + _unauthenticated(ids, ERASURES, None)


def erase_approve_cases(ids) -> list[tuple]:
    fd2 = stepped(ids.fd2)
    ok, _ok_patient = new_erasure_request(ids)
    own, _ = new_erasure_request(ids)
    linked, _ = new_erasure_request(ids, with_live_merge=True)
    other, _ = new_erasure_request(ids)
    path = lambda r: f"{ERASURES}/{r}/approve"
    key = _new_key()
    done = lambda flag: (lambda b: b == {"request_id": str(ok), "erased": flag})
    cases = [
        _case(ids, "the requester cannot approve their own request", stepped(ids.people["front_desk_a"][0]), 403, path(own), body=lambda b: b == NO_MESSAGE),
        _case(ids, "a DIFFERENT front desk person approves: the identity is erased", fd2, 200, path(ok), key=key, body=done(True)),
        _case(ids, "the same approval again is a replay", fd2, 200, path(ok), key=key, body=done(False)),
        _case(ids, "a patient in a live merge link cannot be erased", fd2, 409, path(linked), body=lambda b: b == {"detail": "this patient is in a live merge link"}),
        _case(ids, "an unknown request is refused with the one generic answer", fd2, 403, path(uuid.uuid4()), body=lambda b: b == NO_MESSAGE),
        _case(ids, "missing X-Idempotency-Key is rejected", fd2, 422, path(other), key=None, body=_fixed(422)),
        _case(ids, "a request id that is not a uuid is rejected", fd2, 422, path("nope"), body=_fixed(422)),
    ]
    for p in [*ids.people, "no account"]:
        if p == "front_desk_a":
            continue  # front_desk_a is the requester of these rows: covered above
        sub = ids.no_account if p == "no account" else ids.people[p][0]
        cases.append(_case(ids, f"{p} with a second factor is refused", stepped(sub), 403, path(other), body=lambda b: b == NO_MESSAGE))
    cases += [_case(ids, f"front_desk_a with {label} is told to step up", headers, 401, path(other), body=_fixed(401)) for label, headers in not_stepped_variants(ids.fd2)]
    return cases + _unauthenticated(ids, path(other), None)


def erase_cancel_cases(ids) -> list[tuple]:
    ok, _ = new_erasure_request(ids)
    done, _ = new_erasure_request(ids, status="cancelled")
    other, _ = new_erasure_request(ids)
    path = lambda r: f"{ERASURES}/{r}/cancel"
    fd = stepped(ids.people["front_desk_a"][0])
    cases = [
        _case(ids, "front desk cancels an open request", fd, 200, path(ok), body=lambda b: b == {"request_id": str(ok), "cancelled": True}),
        _case(ids, "cancelling again changes nothing", fd, 200, path(ok), body=lambda b: b == {"request_id": str(ok), "cancelled": False}),
        _case(ids, "an already cancelled request is not cancelled twice", fd, 200, path(done), body=lambda b: b["cancelled"] is False),
        _case(ids, "an unknown request is refused with the one generic answer", fd, 403, path(uuid.uuid4()), body=lambda b: b == NO_MESSAGE),
    ]
    for p in [*ids.people, "no account"]:
        if p == "front_desk_a":
            continue
        sub = ids.no_account if p == "no account" else ids.people[p][0]
        cases.append(_case(ids, f"{p} with a second factor is refused", stepped(sub), 403, path(other), body=lambda b: b == NO_MESSAGE))
    cases += [_case(ids, f"front_desk_a with {label} is told to step up", headers, 401, path(other), body=_fixed(401)) for label, headers in not_stepped_variants(ids.people["front_desk_a"][0])]
    return cases + _unauthenticated(ids, path(other), None)


def test_a_completed_erasure_clears_the_identity_columns_and_leaves_exactly_the_three_audit_rows_and_the_app_role_cannot_update_by_hand(client, ids):
    patient, _, _ = new_pair(ids, candidate=False)
    fd1, fd2 = ids.people["front_desk_a"][0], ids.fd2
    doctor = ids.people["doctor_a"][0]

    async def seed_result(conn):
        return await conn.fetchval(
            "insert into clinical.results (clinic_id, origin, patient_id, code, measures, verified, valid_from, recorded_by, idempotency_key) values ($1, 'test', $2, 'DEMO-T1.a', '{\"demo_value\": 5.5}'::jsonb, true, now(), $3, $4) returning id",
            ids.clinic_a, patient, doctor, f"er-{uuid.uuid4()}",
        )

    result_id = _run(_owner(seed_result))

    async def seed_plan(conn):
        return await conn.fetchval(
            "insert into ops.care_plans (clinic_id, origin, patient_id, plan_ct, plan_key_version, created_by, idempotency_key) values ($1, 'test', $2, decode(repeat('03', 32), 'hex'), 1, $3, $4) returning id",
            ids.clinic_a, patient, doctor, f"er-p-{uuid.uuid4()}",
        )

    plan_id = _run(_owner(seed_plan))
    assert str(plan_id) in {p["plan_id"] for p in client.get(f"{CARE_PLANS}?clinic_id={ids.clinic_a}&limit=200", headers=stepped(doctor)).json()["plans"]}
    asked = client.post(ERASE_REQUEST.format(patient_id=patient), headers={**stepped(fd1), "X-Idempotency-Key": _new_key()}, json={"reason": ERASE_REASON})
    assert asked.status_code == 200
    request_id = asked.json()["request_id"]
    done = client.post(f"{ERASURES}/{request_id}/approve", headers={**stepped(fd2), "X-Idempotency-Key": _new_key()})
    assert done.status_code == 200 and done.json()["erased"] is True
    row = _run(_fetch("select name_ct, dob_ct, phone_ct, name_key_version, dob_key_version, phone_key_version, erased_at from mpi.patients where id = $1", patient))[0]
    assert row["erased_at"] is not None and all(row[c] is None for c in ("name_ct", "dob_ct", "phone_ct", "name_key_version", "dob_key_version", "phone_key_version"))
    audit = _run(_fetch("select actor_id, action, details from audit.log where resource_id = $1 and action like 'patient.erase%' order by id", str(patient)))
    assert [(a["action"], a["actor_id"]) for a in audit] == [("patient.erase_request", fd1), ("patient.erase", fd2)]
    assert all(without_scope(a["details"]) == {"reason": ERASE_REASON} for a in audit)
    result = _run(_fetch("select measures::text as m, erased_at from clinical.results where id = $1", result_id))[0]
    assert result["m"] == "{}" and result["erased_at"] is not None  # the clinical numbers went with the identity (migration 0056)
    assert _run(_fetch("select status from ops.care_plans where id = $1", plan_id))[0]["status"] == "erased"
    assert str(plan_id) not in {p["plan_id"] for p in client.get(f"{CARE_PLANS}?clinic_id={ids.clinic_a}&limit=200", headers=stepped(doctor)).json()["plans"]}  # hidden from the staff list
    care = _run(_fetch("select details::text as d from audit.log where action = 'care_record.erased' and idempotency_key = $1", f"sys:erase-care-record:{request_id}"))
    assert len(care) == 1 and json.loads(care[0]["d"]) == {"care_plans": 1, "results": 1, "messages": 0, "voice_chunks": 0, "grievances": 0, "notes": 0, "visit_readiness": 0}  # counts only, in a row of its own
    request = _run(_fetch("select status, requested_by, approved_by from mpi.erasure_requests where id = $1::uuid", request_id))[0]
    assert (request["status"], request["requested_by"], request["approved_by"]) == ("erased", fd1, fd2)


# ---- staff accounts: GET /accounts, POST /accounts/invite, /{id}/roles, /{id}/roles/revoke, /{id}/suspend, /{id}/reinstate, /activate-me (migration 0034) ------------------------------------------------------
#
# Admin of the clinic only (every staff role of the clinic can SEE the clinic row, so every other one gets 403; front desk of another clinic gets 404), with a recent second factor on every write. The accounts
# these cases create or change are fresh owner-made staff accounts; they are removed with the clinics where the database allows (audit rows stay).

ACCOUNTS = "/v1/staff/accounts"
STAFF_NOT_ADMIN_VISIBLE = {r: 403 for r in ("front_desk_a", "nurse_a", "doctor_a", "business_owner_a", "auditor_a", "multi")}


def new_staff_account(ids, status="active", roles=("nurse",), clinic=None):
    """A fresh staff account (written by the owner) with live grants at clinic A (or `clinic`)."""

    async def make(conn):
        account = uuid.uuid4()
        await conn.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', $2, 'test')", account, status)
        for role in roles:
            await conn.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, $3, 'test')", account, clinic or ids.clinic_a, role)
        return account

    account = _run(_owner(make))
    ids.__dict__.setdefault("extra_accounts", []).append(account)
    return account


def _admin_stepped(ids):
    return stepped(ids.people["admin_a"][0])


def staff_list_cases(ids) -> list[tuple]:
    path = f"{ACCOUNTS}?clinic_id={ids.clinic_a}"
    cases = [
        _case(ids, "an admin lists the clinic's staff accounts with their live roles", "admin_a", 200, path, key=None,
              body=_page(ids, non_empty=True, check=lambda items: any(i["account_id"] == str(ids.people["admin_a"][0]) and i["roles"] == ["admin"] for i in items) and all(set(i) == {"account_id", "status", "roles"} for i in items))),
        _case(ids, "an unknown clinic is not found", "admin_a", 404, f"{ACCOUNTS}?clinic_id={uuid.uuid4()}", key=None, body=_fixed(404)),
        _case(ids, "a clinic id that is not a uuid is rejected", "admin_a", 422, f"{ACCOUNTS}?clinic_id=nope", key=None, body=_fixed(422)),
        _case(ids, "the clinic id is required", "admin_a", 422, ACCOUNTS, key=None, body=_fixed(422)),
        _case(ids, "limit=0 is rejected", "admin_a", 422, path + "&limit=0", key=None, body=_fixed(422)),
    ]
    for p in [*ids.people, "no account"]:
        if p == "admin_a":
            continue
        status = STAFF_NOT_ADMIN_VISIBLE.get(p, 404)
        who = bearer(mint(ids.no_account)) if p == "no account" else p
        cases.append(_case(ids, f"{p} is refused", who, status, path, key=None, body=_fixed(status) if status == 404 else (lambda b: b == {"detail": "your role may not do this"})))
    return cases + _unauthenticated(ids, path, None)


def _staff_write_cases(ids, make_path, make_json, ok_cases, *, default_status=404):
    """The shared gate cases of a step-up admin write: everyone else, not stepped up, unauthenticated."""
    cases = list(ok_cases)
    cases += _everyone_at_the_gate_admin(ids, make_path, make_json, default_status)
    cases += [_case(ids, f"admin_a with {label} is told to step up", headers, 401, make_path(), json=make_json(), body=_fixed(401)) for label, headers in not_stepped_variants(ids.people["admin_a"][0])]
    return cases + _unauthenticated(ids, make_path(), make_json())


def _everyone_at_the_gate_admin(ids, make_path, make_json, default_status):
    cases = []
    for p in [*ids.people, "no account"]:
        if p == "admin_a":
            continue
        sub = ids.no_account if p == "no account" else ids.people[p][0]
        status = STAFF_NOT_ADMIN_VISIBLE.get(p, default_status) if default_status == 404 else default_status
        cases.append(_case(ids, f"{p} with a second factor is refused", stepped(sub), status, make_path(), json=make_json(), body=_fixed(status) if status == 404 else (lambda b: b == {"detail": "your role may not do this"})))
    return cases


def staff_invite_cases(ids) -> list[tuple]:
    body = lambda **o: {"clinic_id": str(ids.clinic_a), "email": f"new-{uuid.uuid4().hex[:8]}@example.test", "role": "nurse", **o}
    key = _new_key()
    same = body()
    admin = _admin_stepped(ids)
    invited = lambda replayed: (lambda b: set(b) == {"account_id", "clinic_id", "role", "replayed"} and b["role"] == "nurse" and b["replayed"] is replayed)
    ok = [
        _case(ids, "an admin invites a new staff member", admin, 200, f"{ACCOUNTS}/invite", json=same, key=key, body=invited(False)),
        _case(ids, "the same request again is a replay", admin, 200, f"{ACCOUNTS}/invite", json=same, key=key, body=invited(True)),
        _case(ids, "queue_worker can never be granted by invitation", admin, 422, f"{ACCOUNTS}/invite", json=body(role="queue_worker"), body=_fixed(422)),
        _case(ids, "an address that is not an address is rejected", admin, 422, f"{ACCOUNTS}/invite", json=body(email="nope"), body=_fixed(422)),
        _case(ids, "missing X-Idempotency-Key is rejected", admin, 422, f"{ACCOUNTS}/invite", json=body(), key=None, body=_fixed(422)),
        _case(ids, "an unknown clinic is not found", admin, 404, f"{ACCOUNTS}/invite", json=body(clinic_id=str(uuid.uuid4())), body=_fixed(404)),
    ]
    return _staff_write_cases(ids, lambda: f"{ACCOUNTS}/invite", body, ok)


def staff_grant_cases(ids) -> list[tuple]:
    target, other = new_staff_account(ids, roles=()), new_staff_account(ids, roles=())
    path = lambda a: f"{ACCOUNTS}/{a}/roles"
    body = lambda role="nurse", **o: {"clinic_id": str(ids.clinic_a), "role": role, **o}
    admin, key = _admin_stepped(ids), _new_key()
    changed = lambda flag: (lambda b: b["changed"] is flag and b["role"] in {"nurse", "doctor"})
    ok = [
        _case(ids, "an admin grants a role", admin, 200, path(target), json=body(), key=key, body=changed(True)),
        _case(ids, "the same request again changes nothing", admin, 200, path(target), json=body(), key=key, body=changed(False)),
        _case(ids, "a role the account already holds changes nothing", admin, 200, path(target), json=body(), body=changed(False)),
        _case(ids, "queue_worker cannot be granted", admin, 422, path(other), json=body("queue_worker"), body=_fixed(422)),
        _case(ids, "an account that does not exist is refused", admin, 403, path(uuid.uuid4()), json=body(), body=lambda b: b == {"detail": "your role may not do this"}),
        _case(ids, "an unknown clinic is not found", admin, 404, path(other), json=body(clinic_id=str(uuid.uuid4())), body=_fixed(404)),
        _case(ids, "missing X-Idempotency-Key is rejected", admin, 422, path(other), json=body(), key=None, body=_fixed(422)),
    ]
    return _staff_write_cases(ids, lambda: path(other), body, ok)


def staff_revoke_cases(ids) -> list[tuple]:
    target, other, lone = new_staff_account(ids, roles=("nurse",)), new_staff_account(ids, roles=("nurse",)), ids.people["admin_a"][0]
    path = lambda a: f"{ACCOUNTS}/{a}/roles/revoke"
    body = lambda role="nurse", **o: {"clinic_id": str(ids.clinic_a), "role": role, **o}
    admin, key = _admin_stepped(ids), _new_key()
    ok = [
        _case(ids, "an admin revokes a live role", admin, 200, path(target), json=body(), key=key, body=lambda b: b["changed"] is True),
        _case(ids, "the same request again changes nothing", admin, 200, path(target), json=body(), key=key, body=lambda b: b["changed"] is False),
        _case(ids, "the last administrator cannot be revoked", admin, 409, path(lone), json=body("admin"), body=lambda b: b == {"detail": "a clinic must keep an active administrator"}),
        _case(ids, "an unknown clinic is not found", admin, 404, path(other), json=body(clinic_id=str(uuid.uuid4())), body=_fixed(404)),
        _case(ids, "missing X-Idempotency-Key is rejected", admin, 422, path(other), json=body(), key=None, body=_fixed(422)),
    ]
    return _staff_write_cases(ids, lambda: path(other), body, ok)


def staff_suspend_cases(ids) -> list[tuple]:
    target, other = new_staff_account(ids, roles=("nurse",)), new_staff_account(ids, roles=("nurse",))
    path = lambda a: f"{ACCOUNTS}/{a}/suspend"
    admin, key = _admin_stepped(ids), _new_key()
    ok = [
        _case(ids, "an admin suspends an active account", admin, 200, path(target), key=key, body=lambda b: b["status"] == "suspended" and b["changed"] is True),
        _case(ids, "the same request again changes nothing", admin, 200, path(target), key=key, body=lambda b: b["changed"] is False),
        _case(ids, "an admin cannot suspend themselves", admin, 403, path(ids.people["admin_a"][0]), body=lambda b: b == {"detail": "your role may not do this"}),
        _case(ids, "an account that does not exist is refused", admin, 403, path(uuid.uuid4()), body=lambda b: b == {"detail": "your role may not do this"}),
        _case(ids, "missing X-Idempotency-Key is rejected", admin, 422, path(other), key=None, body=_fixed(422)),
    ]
    return _staff_write_cases(ids, lambda: path(other), lambda: None, ok, default_status=403)


def staff_reinstate_cases(ids) -> list[tuple]:
    target, invited, other = new_staff_account(ids, "suspended", ("nurse",)), new_staff_account(ids, "invited", ("nurse",)), new_staff_account(ids, "suspended", ("nurse",))
    path = lambda a: f"{ACCOUNTS}/{a}/reinstate"
    admin, key = _admin_stepped(ids), _new_key()
    ok = [
        _case(ids, "an admin reinstates a suspended account", admin, 200, path(target), key=key, body=lambda b: b["status"] == "active" and b["changed"] is True),
        _case(ids, "the same request again changes nothing", admin, 200, path(target), key=key, body=lambda b: b["changed"] is False),
        _case(ids, "an invited account is not reinstated by an admin: the person activates it", admin, 403, path(invited), body=lambda b: b == {"detail": "your role may not do this"}),
        _case(ids, "missing X-Idempotency-Key is rejected", admin, 422, path(other), key=None, body=_fixed(422)),
    ]
    return _staff_write_cases(ids, lambda: path(other), lambda: None, ok, default_status=403)


def staff_activate_cases(ids) -> list[tuple]:
    mine, no_role, active = new_staff_account(ids, "invited", ("nurse",)), new_staff_account(ids, "invited", ()), ids.people["nurse_a"][0]
    path = f"{ACCOUNTS}/activate-me"
    password_only = [bearer(mint(mine, aal=None, amr=None))]
    return [
        _case(ids, "an invited person with a second factor activates their own account", bearer(mint(mine)), 200, path, body=lambda b: b == {"activated": True}),
        _case(ids, "doing it again is a replay", bearer(mint(mine)), 200, path, body=lambda b: b == {"activated": False}),
        _case(ids, "an invited account with no live role cannot activate", bearer(mint(no_role)), 403, path, body=lambda b: b == {"detail": "your role may not do this"}),
        _case(ids, "an already active account is a no-op", bearer(mint(active)), 200, path, body=lambda b: b == {"activated": False}),
        _case(ids, "a password-only session is refused before anything runs", password_only[0], 401, path, body=lambda b: b == {"detail": "multi-factor sign-in required"}),
        _case(ids, "a valid user with no account is refused", bearer(mint(ids.no_account)), 403, path, body=lambda b: b == {"detail": "your role may not do this"}),
    ] + _unauthenticated(ids, path, None)


def test_a_staff_role_change_is_audited_in_the_same_transaction_and_the_application_role_cannot_write_iam_directly(client, ids):
    target = new_staff_account(ids, roles=())
    admin = ids.people["admin_a"][0]
    done = client.post(f"{ACCOUNTS}/{target}/roles", headers={**stepped(admin), "X-Idempotency-Key": _new_key()}, json={"clinic_id": str(ids.clinic_a), "role": "doctor"})
    assert done.status_code == 200 and done.json()["changed"] is True
    rows = _run(_fetch("select actor_id, actor_role, details from audit.log where action = 'staff.grant_role' and resource_id = $1", str(target)))
    assert len(rows) == 1 and (rows[0]["actor_id"], rows[0]["actor_role"]) == (admin, "admin") and without_scope(rows[0]["details"]) == {"role": "doctor"}
    grants = _run(_fetch("select role, granted_by from iam.staff_roles where account_id = $1 and revoked_at is null", target))
    assert [(g["role"], g["granted_by"]) for g in grants] == [("doctor", admin)]


# ---- backfill: POST /backfills, GET /backfills/{slot_id}, POST /backfills/{slot_id}/decide and /reply (the Scheduling graph behind staff routes) -----------------------------------------------------------
#
# The matrix app has NO scheduling components (all the graph's ports are fakes in this package), so the committed half runs on the real pooler role up to the point where a permitted caller meets the visible
# 503: front desk gets it, every other identity is told apart BEFORE it (403 where the clinic or slot is visible to them, otherwise the one 404). The pause-by-pause flow, audit rows and the 409s are in
# tests/test_backfill_routes.py (fakes) and tests/test_scheduling_graph.py. Writes need an idempotency key; nothing here can reach a patient.

BACKFILLS = PREFIX + "/backfills"
UNCONFIGURED = {"detail": "backfill runs are unavailable: not configured"}
CLINIC_VISIBLE = {r: 403 for r in ("nurse_a", "doctor_a", "admin_a", "business_owner_a", "auditor_a", "multi")}  # every staff role of the clinic sees the clinic row
SLOT_VISIBLE_NOT_FRONT_DESK = {r: 403 for r in ("nurse_a", "doctor_a", "admin_a", "multi")}  # front desk, nurse, doctor and admin see slots (sched.slots select policy)


def _backfill_gate_cases(ids, make_path, make_json, *, allowed, status_for_visible, reader_status=503):
    cases = []
    for p in [*ids.people, "no account"]:
        sub = ids.no_account if p == "no account" else ids.people[p][0]
        who = bearer(mint(sub))
        if p in allowed:
            cases.append(_case(ids, f"{p} is allowed: the run is not configured, so the answer is the visible 503", who, reader_status, make_path(), json=make_json(), body=lambda b: b == UNCONFIGURED))
            continue
        status = status_for_visible.get(p, 404)
        cases.append(_case(ids, f"{p} is refused", who, status, make_path(), json=make_json(), body=_fixed(status) if status == 404 else (lambda b: b["detail"].startswith("your role may not"))))
    return cases


def backfill_start_cases(ids) -> list[tuple]:
    slot = ids.pools["extra"].pop()
    body = lambda **o: {"clinic_id": str(ids.clinic_a), "slot_id": str(slot), "visit_type_id": str(ids.vt_a), **o}
    cases = _backfill_gate_cases(ids, lambda: BACKFILLS, body, allowed={"front_desk_a"}, status_for_visible=CLINIC_VISIBLE)
    cases += [
        _case(ids, "missing X-Idempotency-Key is rejected", "front_desk_a", 422, BACKFILLS, json=body(), key=None, body=_fixed(422)),
        _case(ids, "an unknown field is rejected", "front_desk_a", 422, BACKFILLS, json=body(surprise=1), body=_fixed(422)),
        _case(ids, "a slot id that is not a uuid is rejected", "front_desk_a", 422, BACKFILLS, json=body(slot_id="nope"), body=_fixed(422)),
        _case(ids, "an unknown clinic is not found", "front_desk_a", 404, BACKFILLS, json=body(clinic_id=str(uuid.uuid4())), body=_fixed(404)),
    ]
    return cases + _unauthenticated(ids, BACKFILLS, body())


def backfill_get_cases(ids) -> list[tuple]:
    slot = ids.pools["extra"].pop()
    path = f"{BACKFILLS}/{slot}"
    readers = ("front_desk_a", "nurse_a", "doctor_a", "multi")
    cases = _backfill_gate_cases(ids, lambda: path, lambda: None, allowed=set(readers), status_for_visible={"admin_a": 403})
    cases += [
        _case(ids, "an unknown slot is not found", "front_desk_a", 404, f"{BACKFILLS}/{uuid.uuid4()}", key=None, body=_fixed(404)),
        _case(ids, "a slot id that is not a uuid is rejected", "front_desk_a", 422, f"{BACKFILLS}/nope", key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, path, None)


def backfill_decide_cases(ids) -> list[tuple]:
    slot = ids.pools["extra"].pop()
    path = f"{BACKFILLS}/{slot}/decide"
    body = lambda **o: {"action": "send", **o}
    cases = _backfill_gate_cases(ids, lambda: path, body, allowed={"front_desk_a"}, status_for_visible=SLOT_VISIBLE_NOT_FRONT_DESK)
    cases += [
        _case(ids, "an action that is not listed is rejected", "front_desk_a", 422, path, json=body(action="book it"), body=_fixed(422)),
        _case(ids, "missing X-Idempotency-Key is rejected", "front_desk_a", 422, path, json=body(), key=None, body=_fixed(422)),
        _case(ids, "an unknown slot is not found", "front_desk_a", 404, f"{BACKFILLS}/{uuid.uuid4()}/decide", json=body(), body=_fixed(404)),
    ]
    return cases + _unauthenticated(ids, path, body())


def backfill_reply_cases(ids) -> list[tuple]:
    slot = ids.pools["extra"].pop()
    path = f"{BACKFILLS}/{slot}/reply"
    body = lambda **o: {"outcome": "timed_out", **o}
    cases = _backfill_gate_cases(ids, lambda: path, body, allowed={"front_desk_a"}, status_for_visible=SLOT_VISIBLE_NOT_FRONT_DESK)
    cases += [
        _case(ids, "a reply without its message is rejected", "front_desk_a", 422, path, json={"outcome": "replied"}, body=_rejected),
        _case(ids, "a message with a timed-out outcome is rejected", "front_desk_a", 422, path, json=body(message_id=str(uuid.uuid4())), body=_rejected),
        _case(ids, "an outcome that is not listed is rejected", "front_desk_a", 422, path, json=body(outcome="maybe"), body=_fixed(422)),
        _case(ids, "missing X-Idempotency-Key is rejected", "front_desk_a", 422, path, json=body(), key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, path, body())


# ---- clinic set-up: POST /locations, /providers, /visit-types, /slots (front desk and admin of the clinic only) ----------------------------------------------------------------------------
#
# Front desk and admin create; nurse and doctor can SEE the clinic and are refused with 403; every other identity gets the same 404 as a clinic that is not there. A replay answers the original, the same key
# with different content is a 409, a slot overlapping the provider's live slot is a 409, and a slot naming another clinic's provider is a 404. The created rows are committed and removed with the clinics.

SETUP_BASE = PREFIX
SETUP_ROUTES_PATHS = {"locations": PREFIX + "/locations", "providers": PREFIX + "/providers", "visit-types": PREFIX + "/visit-types", "slots": PREFIX + "/slots"}
SETUP_ALLOWED = ("front_desk_a", "admin_a")
SETUP_VISIBLE_NOT_ALLOWED = {"nurse_a": 403, "doctor_a": 403, "multi": 403, "business_owner_a": 403, "auditor_a": 403}  # every staff role of the clinic can SEE the clinic row (clinics_select_own_staff)


def _setup_body(ids, kind, **over):
    nonce = uuid.uuid4().hex[:8]
    body = {
        "locations": {"clinic_id": str(ids.clinic_a), "name": f"Room {nonce}"},
        "providers": {"clinic_id": str(ids.clinic_a), "display_name": f"Provider {nonce}"},
        "visit-types": {"clinic_id": str(ids.clinic_a), "name": f"Visit {nonce}", "duration_minutes": 20},
    }
    if kind == "slots":
        start = datetime(2100, 1, 1, tzinfo=UTC) + timedelta(days=int(uuid.uuid4().int % 20000), hours=int(uuid.uuid4().int % 20))
        body["slots"] = {
            "clinic_id": str(ids.clinic_a), "provider_id": str(ids.provider_a), "location_id": str(ids.location_a), "visit_type_id": str(ids.vt_a),
            "starts_at": start.isoformat(), "ends_at": (start + timedelta(minutes=30)).isoformat(),
        }
    return {**body[kind], **over}


def setup_create_cases(kind):
    def cases(ids) -> list[tuple]:
        path = SETUP_ROUTES_PATHS[kind]
        ok_body = _setup_body(ids, kind)
        key = _new_key()
        created = lambda replayed: (lambda b: set(b) == {"id", "clinic_id", "replayed"} and b["clinic_id"] == str(ids.clinic_a) and b["replayed"] is replayed)
        cases = [
            _case(ids, f"front desk creates ({kind})", "front_desk_a", 200, path, json=ok_body, key=key, body=created(False)),
            _case(ids, "the same key and body is a replay", "front_desk_a", 200, path, json=ok_body, key=key, body=created(True)),
            _case(ids, "the same key with different content is a conflict", "front_desk_a", 409, path, json=_setup_body(ids, kind), key=key,
                  body=lambda b: b == {"detail": "this idempotency key was already used by a different request"}),
            _case(ids, "an admin of the clinic creates too", "admin_a", 200, path, json=_setup_body(ids, kind), body=created(False)),
            _case(ids, "an unknown field is rejected", "front_desk_a", 422, path, json={**_setup_body(ids, kind), "surprise": 1}, body=_fixed(422)),
            _case(ids, "missing X-Idempotency-Key is rejected", "front_desk_a", 422, path, json=_setup_body(ids, kind), key=None, body=_fixed(422)),
            _case(ids, "a clinic id that is not a uuid is rejected", "front_desk_a", 422, path, json=_setup_body(ids, kind, clinic_id="nope"), body=_fixed(422)),
            _case(ids, "a clinic that does not exist is not found", "front_desk_a", 404, path, json=_setup_body(ids, kind, clinic_id=str(uuid.uuid4())), body=_fixed(404)),
        ]
        if kind == "visit-types":
            cases += [_case(ids, f"duration {bad} is rejected", "front_desk_a", 422, path, json=_setup_body(ids, kind, duration_minutes=bad), body=_fixed(422)) for bad in (0, -5, 1441)]
        if kind != "slots":
            field = "name" if kind != "providers" else "display_name"
            cases.append(_case(ids, "a blank name is rejected", "front_desk_a", 422, path, json=_setup_body(ids, kind, **{field: "   "}), body=_fixed(422)))
        else:
            first = _setup_body(ids, kind)
            overlap = {**first, "starts_at": (datetime.fromisoformat(first["starts_at"]) + timedelta(minutes=10)).isoformat(), "ends_at": (datetime.fromisoformat(first["ends_at"]) + timedelta(minutes=10)).isoformat()}
            cases += [
                _case(ids, "a first slot is created", "front_desk_a", 200, path, json=first, body=created(False)),
                _case(ids, "an overlapping slot of the same provider is a conflict", "front_desk_a", 409, path, json=overlap, body=lambda b: b == {"detail": "that time overlaps another slot of this provider"}),
                _case(ids, "an end before the start is rejected", "front_desk_a", 422, path, json=_setup_body(ids, kind, ends_at="2000-01-01T00:00:00+00:00"), body=_rejected),
                _case(ids, "a time without a zone is rejected", "front_desk_a", 422, path, json=_setup_body(ids, kind, starts_at="2100-01-01T10:00:00", ends_at="2100-01-01T10:30:00"), body=_rejected),
                _case(ids, "a slot longer than a day is rejected", "front_desk_a", 422, path, json=_setup_body(ids, kind, starts_at="2100-01-01T00:00:00+00:00", ends_at="2100-01-03T00:00:00+00:00"), body=_rejected),
                _case(ids, "an unknown provider is not found", "front_desk_a", 404, path, json=_setup_body(ids, kind, provider_id=str(uuid.uuid4())), body=_fixed(404)),
            ]
        for p in [*ids.people, "no account"]:
            if p in SETUP_ALLOWED:
                continue
            status = SETUP_VISIBLE_NOT_ALLOWED.get(p, 404)
            who = bearer(mint(ids.no_account)) if p == "no account" else p
            cases.append(_case(ids, f"{p} is refused", who, status, path, json=_setup_body(ids, kind), body=_fixed(status) if status == 404 else (lambda b: b == {"detail": "your role may not do this"})))
        return cases + _unauthenticated(ids, path, _setup_body(ids, kind))

    return cases


# ---- /v1/staff/audit (the audit-log reader) --------------------------------------------------------------------------------------------------------------------
#
# Auditor of the clinic only (the existing select policy); every other identity is refused with 403 before any query. Newest first, keyset paging, filters on closed columns. `reason` is never
# returned. The cases write their own probe rows as the owner (audit.log is append-only, so they stay; nothing reads an action named `matrix.probe`).

AUDIT = "/v1/staff/audit"


def _probe_rows(ids, n):
    async def make(conn):
        marks = []
        for _ in range(n):
            mark = uuid.uuid4().hex
            await conn.execute(
                "insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key) "
                "values ('test', $1, $2, 'front_desk', 'matrix.probe', 'probe', $3, '{\"n\": 1}'::jsonb, $4)",
                ids.clinic_a, ids.people["front_desk_a"][0], mark, f"matrix-probe-{mark}",
            )
            marks.append(mark)
        return marks

    return _run(_owner(make))


def audit_reader_cases(ids) -> list[tuple]:
    marks = _probe_rows(ids, 3)
    fd = str(ids.people["front_desk_a"][0])
    only = lambda items, n: len(items) == n and all(i["action"] == "matrix.probe" for i in items)
    shape = {"audit_id", "occurred_at", "origin", "clinic_id", "actor_id", "actor_role", "action", "resource_type", "resource_id", "details"}
    cases = [
        _case(ids, "the auditor lists the probe rows by action, newest first, with closed columns only", "auditor_a", 200, AUDIT + "?action=matrix.probe", key=None,
              body=_page_audit(lambda b: only(b["items"], 3) and [i["resource_id"] for i in b["items"]] == marks[::-1] and all(set(i) == shape for i in b["items"]) and all(i["clinic_id"] == str(ids.clinic_a) for i in b["items"]))),
        _case(ids, "filter by resource id", "auditor_a", 200, AUDIT + f"?resource_id={marks[1]}", key=None, body=_page_audit(lambda b: [i["resource_id"] for i in b["items"]] == [marks[1]])),
        _case(ids, "filter by actor", "auditor_a", 200, AUDIT + f"?action=matrix.probe&actor_id={fd}", key=None, body=_page_audit(lambda b: only(b["items"], 3))),
        _case(ids, "filter by another actor finds nothing", "auditor_a", 200, AUDIT + f"?action=matrix.probe&actor_id={uuid.uuid4()}", key=None, body=_page_audit(lambda b: b["items"] == [])),
        _case(ids, "a window in the future finds nothing", "auditor_a", 200, AUDIT + "?action=matrix.probe&since=2999-01-01T00:00:00Z", key=None, body=_page_audit(lambda b: b["items"] == [])),
        _case(ids, "a window ending before the rows finds nothing", "auditor_a", 200, AUDIT + "?action=matrix.probe&until=2000-01-01T00:00:00Z", key=None, body=_page_audit(lambda b: b["items"] == [])),
        _case(ids, "limit=2 returns two rows, says there are more and names the next page", "auditor_a", 200, AUDIT + "?action=matrix.probe&limit=2", key=None,
              body=_page_audit(lambda b: len(b["items"]) == 2 and b["has_more"] is True and b["next_before_id"] == b["items"][-1]["audit_id"])),
        _case(ids, "nobody is written to by reading: an unknown action finds nothing", "auditor_a", 200, AUDIT + "?action=no.such.action", key=None, body=_page_audit(lambda b: b["items"] == [])),
        _case(ids, "an action with capital letters is rejected", "auditor_a", 422, AUDIT + "?action=Bad%20Action", key=None, body=_fixed(422)),
        _case(ids, "before_id=0 is rejected", "auditor_a", 422, AUDIT + "?before_id=0", key=None, body=_fixed(422)),
        _case(ids, "an actor id that is not a uuid is rejected", "auditor_a", 422, AUDIT + "?actor_id=nope", key=None, body=_fixed(422)),
        _case(ids, "a time that is not a time is rejected", "auditor_a", 422, AUDIT + "?since=yesterday", key=None, body=_fixed(422)),
        _case(ids, "limit=0 is rejected", "auditor_a", 422, AUDIT + "?limit=0", key=None, body=_fixed(422)),
        _case(ids, "clinic_id of the audited clinic returns its rows", "auditor_a", 200, AUDIT + f"?action=matrix.probe&clinic_id={ids.clinic_a}", key=None, body=_page_audit(lambda b: only(b["items"], 3))),
        _case(ids, "clinic_id of a clinic the auditor does not audit is the one not-found", "auditor_a", 404, AUDIT + f"?clinic_id={ids.clinic_b}", key=None, body=_fixed(404)),
        _case(ids, "clinic_id of a clinic that does not exist is the same not-found", "auditor_a", 404, AUDIT + f"?clinic_id={uuid.uuid4()}", key=None, body=_fixed(404)),
        _case(ids, "a clinic_id that is not a uuid is rejected", "auditor_a", 422, AUDIT + "?clinic_id=nope", key=None, body=_fixed(422)),
        _case(ids, "a non-auditor who names a clinic is still refused first", "front_desk_a", 403, AUDIT + f"?clinic_id={ids.clinic_a}", key=None, body=READ_403_BODY),
    ]
    denied = [p for p in ids.people if p != "auditor_a"]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused", p, 403, AUDIT, key=None, body=READ_403_BODY) for p in denied]
    cases.append(_case(ids, "valid user with no iam account is refused", bearer(mint(ids.no_account)), 403, AUDIT, key=None, body=READ_403_BODY))
    return cases + _unauthenticated(ids, AUDIT, None)


def _page_audit(check):
    """The paging envelope of the audit list is well formed (items, limit, has_more, next_before_id) and `check(body)` holds."""
    return lambda b: set(b) == {"items", "limit", "has_more", "next_before_id"} and isinstance(b["items"], list) and check(b)


# ---- /v1/staff/patients/{id}/view and /v1/staff/patients/labels (patient view; DEMO profile with synthetic data only) -------------------------------------------
#
# The matrix app is built with the view feature ON (a stand-in config below) so the role cases can see real answers; the disabled, unconfigured and rate-limited behaviours are the live tests after the
# matrix. Opening one record: front desk, nurse and doctor of the clinic (and `multi`, a nurse here) get the full record; everyone else cannot even see the patient (404), so the refusal is the same
# as for a missing record. Labels: the same three roles of that clinic; who SEES the clinic row but holds none of them (admin, business owner, auditor) gets 403, everyone else 404. Rows carry the
# name, the year of birth and the last four digits of the phone only.

PATIENT_VIEW = "/v1/staff/patients/{patient_id}/view"
PATIENT_LABELS = "/v1/staff/patients/labels"
VIEW_READERS = ("front_desk_a", "nurse_a", "doctor_a", "multi")
LABEL_VISIBLE_NOT_ALLOWED = {r: 403 for r in ("admin_a", "business_owner_a", "auditor_a")}
SYNTHETIC = {"name": "Zed Testperson", "dob": "1977-07-07", "phone": "+919876500123"}


async def _new_real_patient(conn, clinic, **person):
    """A synthetic patient whose demographics are REALLY encrypted with the matrix vault (so the view routes can decrypt them), written by the owner."""
    person = {**SYNTHETIC, **person}
    pid = uuid.uuid4()
    enc = lambda value, field: TEST_VAULT.encrypt(value, field=field, key_version=1)
    await conn.execute(
        "insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, phone_ct, phone_key_version, origin, idempotency_key) values ($1, $2, $3, 1, $4, 1, $5, 1, 'test', $6)",
        pid, clinic, enc(person["name"], "name"), enc(person["dob"], "dob"), enc(person["phone"], "phone"), f"matrix-real-{uuid.uuid4().hex}",
    )
    return pid


def new_real_patient(ids, **person):
    return _run(_owner(lambda conn: _new_real_patient(conn, ids.clinic_a, **person)))


def patient_view_cases(ids) -> list[tuple]:
    pid = new_real_patient(ids)
    path = lambda p: PATIENT_VIEW.format(patient_id=p)
    full = lambda b: b == {"patient_id": str(pid), "clinic_id": str(ids.clinic_a), "name": SYNTHETIC["name"], "date_of_birth": SYNTHETIC["dob"], "phone": SYNTHETIC["phone"]}
    cases = [_case(ids, f"{who} opens the record in full", who, 200, path(pid), key=None, body=full) for who in VIEW_READERS]
    cases += [
        _case(ids, "the response is marked no-store", "front_desk_a", 200, path(pid), key=None, body=full),
        _case(ids, "a reader of another clinic gets the same 404 as a missing record", "front_desk_b", 404, path(pid), key=None, body=_fixed(404)),
        _case(ids, "an unknown record is not found", "nurse_a", 404, path(uuid.uuid4()), key=None, body=_fixed(404)),
        _case(ids, "a patient id that is not a uuid is rejected", "nurse_a", 422, path("nope"), key=None, body=_fixed(422)),
    ]
    others = [p for p in ids.people if p not in (*VIEW_READERS, "front_desk_b")]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) cannot see the record: the same 404", p, 404, path(pid), key=None, body=_fixed(404)) for p in others]
    cases.append(_case(ids, "valid user with no iam account gets the same 404", bearer(mint(ids.no_account)), 404, path(pid), key=None, body=_fixed(404)))
    return cases + _unauthenticated(ids, path(pid), None)


def patient_labels_cases(ids) -> list[tuple]:
    a, b = new_real_patient(ids), new_real_patient(ids, name="Yara Sample", dob="1962-01-30", phone="+919000011111")
    foreign = _run(_owner(lambda conn: _new_real_patient(conn, ids.clinic_b)))
    body = lambda *p, **o: {"clinic_id": str(ids.clinic_a), "patient_ids": [str(x) for x in p], **o}
    rows = lambda b_: sorted(b_["items"], key=lambda i: i["patient_id"])
    expect = sorted(
        [{"patient_id": str(a), "name": SYNTHETIC["name"], "birth_year": 1977, "phone_last4": "0123"}, {"patient_id": str(b), "name": "Yara Sample", "birth_year": 1962, "phone_last4": "1111"}],
        key=lambda i: i["patient_id"],
    )
    cases = [_case(ids, f"{who} reads two list rows: name, year of birth, last four digits", who, 200, PATIENT_LABELS, json=body(a, b), key=None, body=lambda x: rows(x) == expect) for who in VIEW_READERS]
    cases += [
        _case(ids, "no full date of birth and no full phone appear in a row", "front_desk_a", 200, PATIENT_LABELS, json=body(a), key=None,
              body=lambda x: set(x["items"][0]) == {"patient_id", "name", "birth_year", "phone_last4"} and SYNTHETIC["dob"] not in str(x) and SYNTHETIC["phone"] not in str(x)),
        _case(ids, "an id of another clinic is simply absent", "front_desk_a", 200, PATIENT_LABELS, json=body(a, foreign), key=None, body=lambda x: [i["patient_id"] for i in x["items"]] == [str(a)]),
        _case(ids, "an id nobody has is simply absent", "front_desk_a", 200, PATIENT_LABELS, json=body(uuid.uuid4()), key=None, body=lambda x: x == {"items": []}),
        _case(ids, "more than 25 ids is rejected (the hard page cap)", "front_desk_a", 422, PATIENT_LABELS, json=body(*[uuid.uuid4() for _ in range(26)]), key=None, body=_fixed(422)),
        _case(ids, "an empty list is rejected", "front_desk_a", 422, PATIENT_LABELS, json=body(), key=None, body=_fixed(422)),
        _case(ids, "a repeated id is rejected", "front_desk_a", 422, PATIENT_LABELS, json=body(a, a), key=None, body=lambda x: x == {"detail": "too many or repeated patient ids for one page"}),
        _case(ids, "an unknown field is rejected", "front_desk_a", 422, PATIENT_LABELS, json=body(a, export=True), key=None, body=_fixed(422)),
        _case(ids, "an unknown clinic is not found", "front_desk_a", 404, PATIENT_LABELS, json=body(a, clinic_id=str(uuid.uuid4())), key=None, body=_fixed(404)),
    ]
    for p in [*ids.people, "no account"]:
        if p in VIEW_READERS:
            continue
        status = LABEL_VISIBLE_NOT_ALLOWED.get(p, 404)
        who = bearer(mint(ids.no_account)) if p == "no account" else p
        cases.append(_case(ids, f"{p} is refused", who, status, PATIENT_LABELS, json=body(a), key=None, body=_fixed(status)))
    return cases + _unauthenticated(ids, PATIENT_LABELS, body(a))


# One entry per route: add the new route's cases here in the same change that adds the route.
# ---- /v1/staff/console: alerts, reviewer statistics, the cost canvas, the reason-code list (Phase 4; migrations 0039 and 0040) ---------------------------------------------------------------------------
#
# Alerts: front desk, nurse, doctor and admin of the clinic read their own audience's alerts; business owner and auditor see the clinic but are refused (403); everyone else gets the one 404. Acknowledge
# is exercised here on an alert that does not exist (the same 404 for everyone: the database shows nobody a row that is not theirs); the real acknowledge, once-only, audience and canary-secrecy cases
# are in tests/test_alerts_live.py against the real tables. Reviewer statistics and the cost canvas are ADMIN only. The reason-code list is for a nurse or doctor somewhere.

CONSOLE = "/v1/staff/console"
CONSOLE_ALERTS = CONSOLE + "/alerts"
CONSOLE_ACK = CONSOLE + "/alerts/{alert_id}/acknowledge"
CONSOLE_STATS = CONSOLE + "/reviewer-stats"
CONSOLE_COST = CONSOLE + "/cost-canvas"
CONSOLE_REASONS = CONSOLE + "/reason-codes"
NOT_YOUR_ROLE = lambda b: b == {"detail": "your role may not do this"}


def _console_clinic_cases(ids, make_path, *, allowed, ok_status, ok_body, visible_refused):
    """One case per identity for a route that takes `clinic_id`: `allowed` get `ok_status`; the roles that see the clinic but are refused get 403; everyone else the one 404."""
    cases = []
    for p in [*ids.people, "no account"]:
        who = bearer(mint(ids.no_account)) if p == "no account" else p
        if p in allowed:
            cases.append(_case(ids, f"{p} is allowed", who, ok_status, make_path(), key=None, body=ok_body))
        elif p in visible_refused:
            cases.append(_case(ids, f"{p} sees the clinic but is refused", who, 403, make_path(), key=None, body=NOT_YOUR_ROLE))
        else:
            cases.append(_case(ids, f"{p} is refused with the one not-found", who, 404, make_path(), key=None, body=_fixed(404)))
    return cases


def console_alerts_cases(ids) -> list[tuple]:
    path = lambda **o: CONSOLE_ALERTS + "?" + "&".join(f"{k}={v}" for k, v in {"clinic_id": ids.clinic_a, **o}.items())
    allowed = ("front_desk_a", "nurse_a", "doctor_a", "admin_a", "multi")
    cases = _console_clinic_cases(ids, path, allowed=allowed, ok_status=200, ok_body=_page(ids), visible_refused=("business_owner_a", "auditor_a"))
    cases += [
        _case(ids, "an unknown clinic is not found", "admin_a", 404, path(clinic_id=uuid.uuid4()), key=None, body=_fixed(404)),
        _case(ids, "limit=0 is rejected", "admin_a", 422, path(limit=0), key=None, body=_fixed(422)),
        _case(ids, "a clinic id that is not a uuid is rejected", "admin_a", 422, path(clinic_id="nope"), key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, path(), None)[:4]


def console_ack_cases(ids) -> list[tuple]:
    path = lambda: f"{CONSOLE}/alerts/{uuid.uuid4()}/acknowledge"
    cases = [_case(ids, f"{p} cannot acknowledge an alert that does not exist", p, 404, path(), body=_fixed(404)) for p in ids.people]
    cases += [
        _case(ids, "missing X-Idempotency-Key is rejected", "admin_a", 422, path(), key=None, body=_fixed(422)),
        _case(ids, "an alert id that is not a uuid is rejected", "admin_a", 422, f"{CONSOLE}/alerts/nope/acknowledge", body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, path(), None)


def console_stats_cases(ids) -> list[tuple]:
    path = lambda **o: CONSOLE_STATS + "?" + "&".join(f"{k}={v}" for k, v in {"clinic_id": ids.clinic_a, **o}.items())
    ok = lambda b: set(b) == {"clinic_id", "days", "policy_configured", "reviewers"} and b["policy_configured"] is False and isinstance(b["reviewers"], list)
    visible = ("front_desk_a", "nurse_a", "doctor_a", "business_owner_a", "auditor_a", "multi")
    cases = _console_clinic_cases(ids, path, allowed=("admin_a",), ok_status=200, ok_body=ok, visible_refused=visible)
    cases += [
        _case(ids, "days=0 is rejected", "admin_a", 422, path(days=0), key=None, body=_fixed(422)),
        _case(ids, "days=366 is rejected", "admin_a", 422, path(days=366), key=None, body=_fixed(422)),
        _case(ids, "an unknown clinic is not found", "admin_a", 404, path(clinic_id=uuid.uuid4()), key=None, body=_fixed(404)),
    ]
    return cases + _unauthenticated(ids, path(), None)[:4]


def console_cost_cases(ids) -> list[tuple]:
    path = lambda **o: CONSOLE_COST + "?" + "&".join(f"{k}={v}" for k, v in {"clinic_id": ids.clinic_a, **o}.items())
    ok = lambda b: b["state"] == "insufficient_data" and b["actual"] is None and b["shadow"] is None and b["reasons"]
    visible = ("front_desk_a", "nurse_a", "doctor_a", "business_owner_a", "auditor_a", "multi")
    cases = _console_clinic_cases(ids, path, allowed=("admin_a",), ok_status=200, ok_body=ok, visible_refused=visible)
    cases += [
        _case(ids, "days=0 is rejected", "admin_a", 422, path(days=0), key=None, body=_fixed(422)),
        _case(ids, "an unknown clinic is not found", "admin_a", 404, path(clinic_id=uuid.uuid4()), key=None, body=_fixed(404)),
    ]
    return cases + _unauthenticated(ids, path(), None)[:4]


def console_reason_codes_cases(ids) -> list[tuple]:
    allowed = ("nurse_a", "doctor_a", "multi")
    cases = [_case(ids, f"{p} may read the list", p, 200, CONSOLE_REASONS, key=None, body=lambda b: b == {"configured": False, "codes": []}) for p in allowed]
    denied = [p for p in ids.people if p not in allowed]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused", p, 403, CONSOLE_REASONS, key=None, body=lambda b: b == {"detail": "your role may not read this"}) for p in denied]
    cases.append(_case(ids, "valid user with no iam account is refused", bearer(mint(ids.no_account)), 403, CONSOLE_REASONS, key=None, body=lambda b: b == {"detail": "your role may not read this"}))
    return cases + _unauthenticated(ids, CONSOLE_REASONS, None)[:4]


# ---- Care Gaps and the patient voice routes (migrations 0044 and 0045) --------------------------------------------------------------------------------------------------------------------------------------
#
# Care Gaps: nurse, doctor and admin of the clinic read the panel and record barriers; front desk, business owner and auditor see the clinic and are refused (403); everyone else gets the one 404. The
# real record/replay/resolve flows against the tables are in tests/test_care_gaps_db_live.py. Voice: PATIENT accounts only (every other identity gets the one 403). The session route answers 503 in this
# app (no voice flag, no engine, no retention setting configured); the database rules (opt-in, binding, expiry, consent time) are in tests/test_voice_db_live.py.

CARE_GAPS = "/v1/staff/care-gaps"
CARE_BARRIERS = CARE_GAPS + "/barriers"
CARE_BARRIER_RESOLVE = CARE_GAPS + "/barriers/{barrier_id}/resolve"
VOICE_PREF = "/v1/portal/voice/preference"
VOICE_SESSIONS = "/v1/portal/voice/sessions"
CARE_ROLES = ("nurse_a", "doctor_a", "admin_a", "multi")
CARE_VISIBLE_REFUSED = ("front_desk_a", "business_owner_a", "auditor_a")


def care_gap_panel_cases(ids) -> list[tuple]:
    path = lambda **o: CARE_GAPS + "?" + "&".join(f"{k}={v}" for k, v in {"clinic_id": ids.clinic_a, **o}.items())
    fields = {"clinic_id", "rows", "summaries", "unreadable", "recurrence_rules_with_unknown_last_done", "alert_age_configured", "after_the_fact_error_rate"}
    ok = lambda b: set(b) == fields and b["rows"] == [] and len(b["summaries"]) == 3 and b["alert_age_configured"] is False and b["after_the_fact_error_rate"] is None
    cases = _console_clinic_cases(ids, path, allowed=CARE_ROLES, ok_status=200, ok_body=ok, visible_refused=CARE_VISIBLE_REFUSED)
    cases += [
        _case(ids, "an unknown clinic is not found", "admin_a", 404, path(clinic_id=uuid.uuid4()), key=None, body=_fixed(404)),
        _case(ids, "a clinic id that is not a uuid is rejected", "admin_a", 422, path(clinic_id="nope"), key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, path(), None)[:4]


def care_barrier_record_cases(ids) -> list[tuple]:
    body = lambda **o: {"clinic_id": str(ids.clinic_a), "patient_id": str(ids.patients_a[0]), "barrier": "transport", **o}
    created = lambda b: set(b) == {"barrier_id", "created"} and b["created"] is True
    cases = [_case(ids, f"{p} may record a barrier", p, 201, CARE_BARRIERS, json=body(), body=created) for p in ("nurse_a", "doctor_a", "admin_a")]
    key = _new_key()
    first = _case(ids, "a replay of the same key is the same barrier (first)", "nurse_a", 201, CARE_BARRIERS, json=body(), key=key, body=created)
    again = _case(ids, "a replay of the same key is the same barrier (again)", "nurse_a", 201, CARE_BARRIERS, json=body(), key=key, body=lambda b: set(b) == {"barrier_id", "created"} and b["created"] is False)
    cases += [first, again]
    cases += [_case(ids, f"{p} sees the clinic but is refused", p, 403, CARE_BARRIERS, json=body(), body=NOT_YOUR_ROLE) for p in CARE_VISIBLE_REFUSED]
    others = [p for p in ids.people if p not in (*CARE_ROLES, *CARE_VISIBLE_REFUSED)]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused with the one not-found", p, 404, CARE_BARRIERS, json=body(), body=_fixed(404)) for p in others]
    cases += [
        _case(ids, "a patient of another clinic (an unknown id) is not found", "nurse_a", 404, CARE_BARRIERS, json=body(patient_id=str(uuid.uuid4())), body=_fixed(404)),
        _case(ids, "a barrier outside the closed set is rejected", "nurse_a", 422, CARE_BARRIERS, json=body(barrier="bad"), body=_fixed(422)),
        _case(ids, "an extra field is rejected", "nurse_a", 422, CARE_BARRIERS, json=body(extra=1), body=_fixed(422)),
        _case(ids, "missing X-Idempotency-Key is rejected", "nurse_a", 422, CARE_BARRIERS, json=body(), key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, CARE_BARRIERS, body())


def care_barrier_resolve_cases(ids) -> list[tuple]:
    path = lambda: f"{CARE_GAPS}/barriers/{uuid.uuid4()}/resolve"
    cases = [_case(ids, f"{p} cannot resolve a barrier that does not exist", p, 404, path(), body=_fixed(404)) for p in ids.people]
    cases += [
        _case(ids, "missing X-Idempotency-Key is rejected", "nurse_a", 422, path(), key=None, body=_fixed(422)),
        _case(ids, "a barrier id that is not a uuid is rejected", "nurse_a", 422, f"{CARE_GAPS}/barriers/nope/resolve", body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, path(), None)


def voice_preference_get_cases(ids) -> list[tuple]:
    refused = lambda b: b == {"detail": "your role may not do this"}
    cases = [_case(ids, "an active patient account reads its own preference (off by default)", "patient", 200, VOICE_PREF, key=None, body=lambda b: b == {"voice_enabled": False})]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused", p, 403, VOICE_PREF, key=None, body=refused) for p in ids.people if p != "patient"]
    cases.append(_case(ids, "valid user with no iam account is refused", bearer(mint(ids.no_account)), 403, VOICE_PREF, key=None, body=refused))
    return cases + _unauthenticated(ids, VOICE_PREF, None)[:4]


def voice_preference_put_cases(ids) -> list[tuple]:
    refused = lambda b: b == {"detail": "your role may not do this"}
    off = {"voice_enabled": False}
    cases = [_case(ids, "an active patient account sets its own preference", "patient", 200, VOICE_PREF, json=off, body=lambda b: b == off)]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused", p, 403, VOICE_PREF, json=off, body=refused) for p in ids.people if p != "patient"]
    cases += [
        _case(ids, "valid user with no iam account is refused", bearer(mint(ids.no_account)), 403, VOICE_PREF, json=off, body=refused),
        _case(ids, "a non-boolean value is rejected", "patient", 422, VOICE_PREF, json={"voice_enabled": "maybe"}, body=_fixed(422)),
        _case(ids, "an extra field is rejected", "patient", 422, VOICE_PREF, json={**off, "x": 1}, body=_fixed(422)),
        _case(ids, "missing X-Idempotency-Key is rejected", "patient", 422, VOICE_PREF, json=off, key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, VOICE_PREF, off)


def voice_session_cases(ids) -> list[tuple]:
    refused = lambda b: b == {"detail": "your role may not do this"}
    body = lambda **o: {"clinic_id": str(ids.clinic_a), "workflow": "slot_offer", "consent": True, **o}
    cases = [_case(ids, "an active patient account gets 503 while voice is not configured (no flag, no engine, no retention setting)", "patient", 503, VOICE_SESSIONS, json=body(), body=_fixed(503))]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused", p, 403, VOICE_SESSIONS, json=body(), body=refused) for p in ids.people if p != "patient"]
    cases += [
        _case(ids, "valid user with no iam account is refused", bearer(mint(ids.no_account)), 403, VOICE_SESSIONS, json=body(), body=refused),
        _case(ids, "consent that is not true is rejected", "patient", 422, VOICE_SESSIONS, json=body(consent=False), body=_fixed(422)),
        _case(ids, "a workflow outside the three is rejected", "patient", 422, VOICE_SESSIONS, json=body(workflow="open_ended"), body=_fixed(422)),
        _case(ids, "missing X-Idempotency-Key is rejected", "patient", 422, VOICE_SESSIONS, json=body(), key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, VOICE_SESSIONS, body())


# ---- admin-only voice transcripts (migration 0047) -------------------------------------------------------------------------------------------------------------------------------------------------------------
# Admin of the clinic only (and the database checks a verified second factor again). Everyone else who sees the clinic gets 403, everyone else the one 404. The functions against the real tables
# (read, purge, expiry, audit rows, password-only refusal) are in tests/test_voice_admin_db_live.py.

VOICE_ADMIN_VISIBLE = ("front_desk_a", "nurse_a", "doctor_a", "business_owner_a", "auditor_a", "multi")
VOICE_ADMIN = "/v1/staff/voice"


def voice_sessions_list_cases(ids) -> list[tuple]:
    path = lambda **o: VOICE_ADMIN + "/sessions?" + "&".join(f"{k}={v}" for k, v in {"clinic_id": ids.clinic_a, **o}.items())
    ok = lambda b: b == {"sessions": []}
    cases = _console_clinic_cases(ids, path, allowed=("admin_a",), ok_status=200, ok_body=ok, visible_refused=VOICE_ADMIN_VISIBLE)
    cases += [
        _case(ids, "an unknown clinic is not found", "admin_a", 404, path(clinic_id=uuid.uuid4()), key=None, body=_fixed(404)),
        _case(ids, "a clinic id that is not a uuid is rejected", "admin_a", 422, path(clinic_id="nope"), key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, path(), None)[:4]


def voice_transcript_read_cases(ids) -> list[tuple]:
    path = lambda **o: f"{VOICE_ADMIN}/sessions/{uuid.uuid4()}/transcript?" + "&".join(f"{k}={v}" for k, v in {"clinic_id": ids.clinic_a, **o}.items())
    cases = _console_clinic_cases(ids, path, allowed=(), ok_status=200, ok_body=None, visible_refused=VOICE_ADMIN_VISIBLE)
    cases = [c for c in cases if "admin_a" not in c[0]]  # the admin's own case: a session that does not exist is the one 404 (the same answer as a session in another clinic)
    cases.append(_case(ids, "admin_a reading a session that does not exist is the one not-found", "admin_a", 404, path(), key=None, body=_fixed(404)))
    cases += [
        _case(ids, "an unknown clinic is not found", "admin_a", 404, path(clinic_id=uuid.uuid4()), key=None, body=_fixed(404)),
        _case(ids, "a session id that is not a uuid is rejected", "admin_a", 422, f"{VOICE_ADMIN}/sessions/nope/transcript?clinic_id={ids.clinic_a}", key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, path(), None)[:4]


def voice_purge_cases(ids) -> list[tuple]:
    body = lambda **o: {"clinic_id": str(ids.clinic_a), **o}
    done = lambda b: b == {"deleted": 0}
    cases = [_case(ids, "admin_a purges (nothing has expired)", "admin_a", 200, VOICE_ADMIN + "/purge", json=body(), body=done)]
    cases += [_case(ids, f"{p} sees the clinic but is refused", p, 403, VOICE_ADMIN + "/purge", json=body(), body=NOT_YOUR_ROLE) for p in VOICE_ADMIN_VISIBLE]
    others = [p for p in ids.people if p != "admin_a" and p not in VOICE_ADMIN_VISIBLE]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused with the one not-found", p, 404, VOICE_ADMIN + "/purge", json=body(), body=_fixed(404)) for p in others]
    cases += [
        _case(ids, "a limit above 1000 is rejected", "admin_a", 422, VOICE_ADMIN + "/purge", json=body(limit=1001), body=_fixed(422)),
        _case(ids, "an extra field is rejected", "admin_a", 422, VOICE_ADMIN + "/purge", json=body(extra=1), body=_fixed(422)),
        _case(ids, "missing X-Idempotency-Key is rejected", "admin_a", 422, VOICE_ADMIN + "/purge", json=body(), key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, VOICE_ADMIN + "/purge", body())


# ---- autonomy, care-gap producers, dashboard (migration 0048) --------------------------------------------------------------------------------------------------------------------------------------------------
# Every case here is a route this change added (rule 8). The producer success cases write append-only rows (episodes, rules, the autonomy history); the cleanup leaves the patients and clinics they pin
# behind, tagged origin='test', like the barrier cases above. The database-level behaviour (the aal2 policy, the guards, the worker read) is in tests/test_care_gap_producers_db_live.py.

AUTONOMY = "/v1/staff/console/autonomy"
DASHBOARD = "/v1/staff/console/dashboard"
EPISODES = "/v1/staff/care-gaps/episodes"
EPISODE_RULE_DONE = "/v1/staff/care-gaps/episode-rules/{episode_rule_id}/done"
SAMPLE_RECHECK = "/v1/staff/care-gaps/samples/{sample_id}/recheck"
CARE_WRITERS = ("nurse_a", "doctor_a", "multi")
CARE_WRITERS_VISIBLE_REFUSED = ("front_desk_a", "admin_a", "business_owner_a", "auditor_a")
ADMIN_ONLY_VISIBLE_REFUSED = ("front_desk_a", "nurse_a", "doctor_a", "business_owner_a", "auditor_a", "multi")
CARE_RULES_SETTING = '{"rules": {"demo_rule": {"interval_days": 30, "overdue_after_days": 5}}, "episodes": {"demo_episode": ["demo_rule"]}}'  # a labelled DEMO placeholder, matrix only
os.environ.setdefault("CAREFLOW_CARE_GAP_RULES", CARE_RULES_SETTING)


def autonomy_get_cases(ids) -> list[tuple]:
    path = lambda **o: AUTONOMY + "?" + "&".join(f"{k}={v}" for k, v in {"clinic_id": ids.clinic_a, **o}.items())
    ok = lambda b: set(b) == {"clinic_id", "mode", "configured"} and b["mode"] in ("shadow", "assisted")
    allowed = ("front_desk_a", "nurse_a", "doctor_a", "admin_a", "multi")
    cases = _console_clinic_cases(ids, path, allowed=allowed, ok_status=200, ok_body=ok, visible_refused=("business_owner_a", "auditor_a"))
    cases += [
        _case(ids, "an unknown clinic is not found", "admin_a", 404, path(clinic_id=uuid.uuid4()), key=None, body=_fixed(404)),
        _case(ids, "a clinic id that is not a uuid is rejected", "admin_a", 422, path(clinic_id="nope"), key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, path(), None)[:4]


def autonomy_set_cases(ids) -> list[tuple]:
    body = lambda **o: {"clinic_id": str(ids.clinic_a), "mode": "assisted", **o}
    key = _new_key()
    cases = [
        _case(ids, "admin_a sets the mode", "admin_a", 200, AUTONOMY, json=body(), key=key, body=lambda b: b == {"mode": "assisted", "created": True}),
        _case(ids, "the same key again is the same change", "admin_a", 200, AUTONOMY, json=body(), key=key, body=lambda b: b == {"mode": "assisted", "created": False}),
        _case(ids, "admin_a may set it back to shadow", "admin_a", 200, AUTONOMY, json=body(mode="shadow"), body=lambda b: b == {"mode": "shadow", "created": True}),
    ]
    cases += [_case(ids, f"{p} sees the clinic but is refused", p, 403, AUTONOMY, json=body(), body=NOT_YOUR_ROLE) for p in ADMIN_ONLY_VISIBLE_REFUSED]
    others = [p for p in ids.people if p != "admin_a" and p not in ADMIN_ONLY_VISIBLE_REFUSED]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused with the one not-found", p, 404, AUTONOMY, json=body(), body=_fixed(404)) for p in others]
    cases += [
        _case(ids, "autonomous is a two-person act and is not accepted here", "admin_a", 422, AUTONOMY, json=body(mode="autonomous"), body=_fixed(422)),
        _case(ids, "an unknown mode is rejected", "admin_a", 422, AUTONOMY, json=body(mode="bad"), body=_fixed(422)),
        _case(ids, "an extra field is rejected", "admin_a", 422, AUTONOMY, json=body(extra=1), body=_fixed(422)),
        _case(ids, "missing X-Idempotency-Key is rejected", "admin_a", 422, AUTONOMY, json=body(), key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, AUTONOMY, body())


def dashboard_cases(ids) -> list[tuple]:
    path = lambda **o: DASHBOARD + "?" + "&".join(f"{k}={v}" for k, v in {"clinic_id": ids.clinic_a, **o}.items())

    def ok(b):
        by_id = {m["metric_id"]: m for m in b["metrics"]}
        return (set(b) == {"clinic_id", "metrics", "not_measured"} and {"queue_depth_and_oldest_age", "dead_letter_count", "review_queue_age", "care_gap_open_count"} <= set(by_id)
                and all((m["value"] is None) == (m["state"] != "measured") for m in b["metrics"]) and not set(by_id) & set(b["not_measured"]) and len(b["not_measured"]) > 10)

    cases = _console_clinic_cases(ids, path, allowed=("admin_a",), ok_status=200, ok_body=ok, visible_refused=ADMIN_ONLY_VISIBLE_REFUSED)
    cases += [
        _case(ids, "an unknown clinic is not found", "admin_a", 404, path(clinic_id=uuid.uuid4()), key=None, body=_fixed(404)),
        _case(ids, "a clinic id that is not a uuid is rejected", "admin_a", 422, path(clinic_id="nope"), key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, path(), None)[:4]


def episode_create_cases(ids) -> list[tuple]:
    body = lambda **o: {"clinic_id": str(ids.clinic_a), "patient_id": str(ids.patients_a[41]), "episode_code": "demo_episode", "started_on": "2026-01-15", **o}
    made = lambda b: set(b) == {"episode_id", "created", "episode_rule_ids"} and b["created"] is True and len(b["episode_rule_ids"]) == 1
    key = _new_key()
    cases = [
        _case(ids, "nurse_a starts an episode", "nurse_a", 201, EPISODES, json=body(), key=key, body=made),
        _case(ids, "the same key again is the same episode", "nurse_a", 201, EPISODES, json=body(), key=key, body=lambda b: b["created"] is False and len(b["episode_rule_ids"]) == 1),
        _case(ids, "doctor_a starts an episode", "doctor_a", 201, EPISODES, json=body(), body=made),
        _case(ids, "multi (a nurse here) starts an episode", "multi", 201, EPISODES, json=body(), body=made),
    ]
    cases += [_case(ids, f"{p} sees the clinic but is refused", p, 403, EPISODES, json=body(), body=NOT_YOUR_ROLE) for p in CARE_WRITERS_VISIBLE_REFUSED]
    others = [p for p in ids.people if p not in (*CARE_WRITERS, *CARE_WRITERS_VISIBLE_REFUSED)]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused with the one not-found", p, 404, EPISODES, json=body(), body=_fixed(404)) for p in others]
    cases += [
        _case(ids, "an episode code that is not in the setting is not found", "nurse_a", 404, EPISODES, json=body(episode_code="unknown_episode"), body=_fixed(404)),
        _case(ids, "a patient that is not in this clinic is not found", "nurse_a", 404, EPISODES, json=body(patient_id=str(uuid.uuid4())), body=_fixed(404)),
        _case(ids, "an episode code with a bad shape is rejected", "nurse_a", 422, EPISODES, json=body(episode_code="Bad Code"), body=_fixed(422)),
        _case(ids, "an extra field is rejected", "nurse_a", 422, EPISODES, json=body(extra=1), body=_fixed(422)),
        _case(ids, "missing X-Idempotency-Key is rejected", "nurse_a", 422, EPISODES, json=body(), key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, EPISODES, body())


def episode_rule_done_cases(ids) -> list[tuple]:
    path = lambda: EPISODE_RULE_DONE.format(episode_rule_id=ids.episode_rule)
    today = time.strftime("%Y-%m-%d", time.gmtime())
    yesterday = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 86400))
    cases = [
        _case(ids, "nurse_a records today", "nurse_a", 200, path(), json={"done_on": today}, body=lambda b: b["recorded"] is True),
        _case(ids, "doctor_a records the same day again", "doctor_a", 200, path(), json={"done_on": today}, body=lambda b: b["recorded"] is True),
        _case(ids, "an earlier date than the recorded one is refused", "nurse_a", 409, path(), json={"done_on": yesterday}, body=lambda b: b["detail"] == "the date cannot move backwards or into the future"),
        _case(ids, "a future date is refused", "nurse_a", 409, path(), json={"done_on": "2999-01-01"}, body=lambda b: b["detail"] == "the date cannot move backwards or into the future"),
    ]
    refused = [p for p in ids.people if p not in CARE_WRITERS]  # not able to change the row: it is invisible (404), or visible but not theirs to change (admin: also 404)
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused with the one not-found", p, 404, path(), json={"done_on": today}, body=_fixed(404)) for p in refused]
    cases += [
        _case(ids, "an unknown episode rule is not found", "nurse_a", 404, EPISODE_RULE_DONE.format(episode_rule_id=uuid.uuid4()), json={"done_on": today}, body=_fixed(404)),
        _case(ids, "a date that is not a date is rejected", "nurse_a", 422, path(), json={"done_on": "soon"}, body=_fixed(422)),
        _case(ids, "an extra field is rejected", "nurse_a", 422, path(), json={"done_on": today, "extra": 1}, body=_fixed(422)),
        _case(ids, "missing X-Idempotency-Key is rejected", "nurse_a", 422, path(), json={"done_on": today}, key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, path(), {"done_on": today})


def sample_recheck_cases(ids) -> list[tuple]:
    path = lambda: SAMPLE_RECHECK.format(sample_id=uuid.uuid4())
    cases = [_case(ids, f"{p} cannot re-check a sample that does not exist", p, 404, path(), body=_fixed(404)) for p in ids.people]
    cases += [
        _case(ids, "missing X-Idempotency-Key is rejected", "nurse_a", 422, path(), key=None, body=_fixed(422)),
        _case(ids, "a sample id that is not a uuid is rejected", "nurse_a", 422, "/v1/staff/care-gaps/samples/nope/recheck", body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, path(), None)


# ---- patient portal (migration 0049) --------------------------------------------------------------------------------------------------------------------------------------------------------------------------
# Patient accounts only. The `patient` identity is an ACTIVE patient account; `guardian`, `closed` and every staff identity get the one 403. Registration is the one route a user with no account may call; it
# refuses a staff account. The claim cases use a dedicated active patient (`ids.claimant`) whose rows are left behind, so the shared identities stay deletable. The database behaviour (consent, activation, the
# claim match and its lock) is in tests/test_patient_portal_db_live.py.

PORTAL = "/v1/portal"
PORTAL_ME, PORTAL_CONSENTS, PORTAL_ACTIVATE, PORTAL_CLAIMS, PORTAL_REGISTER = PORTAL + "/me", PORTAL + "/consents", PORTAL + "/activate", PORTAL + "/claims", PORTAL + "/register"
NOT_A_PATIENT = lambda b: b == {"detail": "your role may not do this"}


def _non_patients(ids):
    return [p for p in ids.people if p != "patient"]


def portal_register_cases(ids) -> list[tuple]:
    fresh = uuid.uuid4()
    who = bearer(mint(fresh))
    cases = [
        _case(ids, "a signed-in user with no account registers", who, 201, PORTAL_REGISTER, body=lambda b: b == {"result": "registered"}),
        _case(ids, "registering again is the same registration", who, 201, PORTAL_REGISTER, body=lambda b: b == {"result": "exists"}),
        _case(ids, "an existing patient account gets `exists`", "patient", 201, PORTAL_REGISTER, body=lambda b: b == {"result": "exists"}),
    ]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) holds a non-patient account and is refused", p, 403, PORTAL_REGISTER, body=NOT_A_PATIENT) for p in ids.people if p not in ("patient", "closed")]
    cases += [_case(ids, "missing X-Idempotency-Key is rejected", "patient", 422, PORTAL_REGISTER, key=None, body=_fixed(422))]
    return cases + _unauthenticated(ids, PORTAL_REGISTER, None)


def portal_me_cases(ids) -> list[tuple]:
    ok = lambda b: set(b) == {"status", "bound_to_a_patient_record", "consents", "stage"} and b["stage"] == "new_patient" and b["status"] == "active" and b["bound_to_a_patient_record"] is False and set(b["consents"]) == {"care_delivery", "ai_processing", "service_improvement"}
    cases = [_case(ids, "an active patient account reads its state", "patient", 200, PORTAL_ME, key=None, body=ok)]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused", p, 403, PORTAL_ME, key=None, body=NOT_A_PATIENT) for p in _non_patients(ids)]
    cases.append(_case(ids, "valid user with no iam account is refused", bearer(mint(ids.no_account)), 403, PORTAL_ME, key=None, body=NOT_A_PATIENT))
    return cases + _unauthenticated(ids, PORTAL_ME, None)[:4]


def portal_consents_get_cases(ids) -> list[tuple]:
    cases = [_case(ids, "an active patient account reads the current notices (none are published in the test database)", "patient", 200, PORTAL_CONSENTS, key=None, body=lambda b: set(b) == {"notices"} and isinstance(b["notices"], list))]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused", p, 403, PORTAL_CONSENTS, key=None, body=NOT_A_PATIENT) for p in _non_patients(ids)]
    cases.append(_case(ids, "valid user with no iam account is refused", bearer(mint(ids.no_account)), 403, PORTAL_CONSENTS, key=None, body=NOT_A_PATIENT))
    return cases + _unauthenticated(ids, PORTAL_CONSENTS, None)[:4]


def portal_consents_post_cases(ids) -> list[tuple]:
    body = lambda **o: {"purpose": "care_delivery", "notice_id": str(uuid.uuid4()), "action": "granted", **o}
    cases = [_case(ids, "a notice that is not the current one is refused", "patient", 409, PORTAL_CONSENTS, json=body(), body=lambda b: b == {"detail": "that is not the current notice for this purpose"})]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused", p, 403, PORTAL_CONSENTS, json=body(), body=NOT_A_PATIENT) for p in _non_patients(ids)]
    cases += [
        _case(ids, "an unknown purpose is rejected", "patient", 422, PORTAL_CONSENTS, json=body(purpose="marketing"), body=_fixed(422)),
        _case(ids, "an unknown action is rejected", "patient", 422, PORTAL_CONSENTS, json=body(action="maybe"), body=_fixed(422)),
        _case(ids, "an extra field is rejected", "patient", 422, PORTAL_CONSENTS, json=body(extra=1), body=_fixed(422)),
        _case(ids, "missing X-Idempotency-Key is rejected", "patient", 422, PORTAL_CONSENTS, json=body(), key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, PORTAL_CONSENTS, body())


def portal_activate_cases(ids) -> list[tuple]:
    cases = [_case(ids, "an already active patient account gets `already_active`", "patient", 200, PORTAL_ACTIVATE, body=lambda b: b == {"result": "already_active"})]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused", p, 403, PORTAL_ACTIVATE, body=NOT_A_PATIENT) for p in _non_patients(ids)]
    cases += [_case(ids, "missing X-Idempotency-Key is rejected", "patient", 422, PORTAL_ACTIVATE, key=None, body=_fixed(422))]
    return cases + _unauthenticated(ids, PORTAL_ACTIVATE, None)


def portal_claim_cases(ids) -> list[tuple]:
    phone = "+919876543210"
    claimant = bearer(mint(ids.claimant, phone=phone))
    no_phone = bearer(mint(ids.claimant))
    body = lambda **o: {"clinic_id": str(ids.clinic_a), "name": "DEMO Claimant", "dob": "1990-01-01", **o}
    cases = [
        _case(ids, "the claimant has not consented to the current notice (none is published in the test database)", claimant, 200, PORTAL_CLAIMS, json=body(), body=lambda b: b == {"outcome": "consent_required"}),
        _case(ids, "the same claim again gives the same word", claimant, 200, PORTAL_CLAIMS, json=body(), body=lambda b: b == {"outcome": "consent_required"}),
        _case(ids, "a token with no verified phone cannot claim", no_phone, 403, PORTAL_CLAIMS, json=body(), body=NOT_A_PATIENT),
        _case(ids, "an unknown clinic is refused the same way", claimant, 403, PORTAL_CLAIMS, json=body(clinic_id=str(uuid.uuid4())), body=NOT_A_PATIENT),
        _case(ids, "a date that is not a date is rejected", claimant, 422, PORTAL_CLAIMS, json=body(dob="soon"), body=_rejected),
        _case(ids, "an age is not a date of birth", claimant, 422, PORTAL_CLAIMS, json=body(dob="34"), body=_rejected),
        _case(ids, "a blank name is rejected", claimant, 422, PORTAL_CLAIMS, json=body(name=""), body=_fixed(422)),
        _case(ids, "an extra field is rejected", claimant, 422, PORTAL_CLAIMS, json=body(extra=1), body=_fixed(422)),
        _case(ids, "missing X-Idempotency-Key is rejected", claimant, 422, PORTAL_CLAIMS, json=body(), key=None, body=_fixed(422)),
    ]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused even with a phone", bearer(mint(ids.people[p][0], phone=phone)), 403, PORTAL_CLAIMS, json=body(), body=NOT_A_PATIENT) for p in _non_patients(ids)]
    return cases + _unauthenticated(ids, PORTAL_CLAIMS, body())


# ---- care plans and results (migration 0050) ------------------------------------------------------------------------------------------------------------------------------------------------------------------
# The plan routes answer 503 here (no Care Journey components are wired in the matrix app: nothing is written); who may call them, who sees the clinic and who gets the one 404 is what the cases pin. Recording a
# result needs no components, so its success cases write append-only rows (the cleanup leaves their patients behind). The graph wired to the SQL ports is in tests/test_care_journey_db_live.py.

CARE_PLANS = "/v1/staff/care-plans"
CARE_PLAN_CONFIRM = CARE_PLANS + "/{plan_id}/confirm"
RESULTS = "/v1/staff/results"
PLAN_UNAVAILABLE = lambda b: b == {"detail": "care plans are unavailable: not configured"}
PLAN_VISIBLE_REFUSED = ("front_desk_a", "nurse_a", "admin_a", "business_owner_a", "auditor_a", "multi")
RESULT_VISIBLE_REFUSED = ("front_desk_a", "admin_a", "business_owner_a", "auditor_a")


def care_plan_create_cases(ids) -> list[tuple]:
    body = lambda **o: {"clinic_id": str(ids.clinic_a), "patient_id": str(ids.patients_a[41]), "plan_text": "DEMO plan text", **o}
    cases = [_case(ids, "doctor_a is told care plans are not configured, and nothing is written", "doctor_a", 503, CARE_PLANS, json=body(), body=PLAN_UNAVAILABLE)]
    cases += [_case(ids, f"{p} sees the clinic but is refused", p, 403, CARE_PLANS, json=body(), body=NOT_YOUR_ROLE) for p in PLAN_VISIBLE_REFUSED]
    others = [p for p in ids.people if p != "doctor_a" and p not in PLAN_VISIBLE_REFUSED]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused with the one not-found", p, 404, CARE_PLANS, json=body(), body=_fixed(404)) for p in others]
    cases += [
        _case(ids, "empty plan text is rejected", "doctor_a", 422, CARE_PLANS, json=body(plan_text=""), body=_fixed(422)),
        _case(ids, "plan text over the limit is rejected", "doctor_a", 422, CARE_PLANS, json=body(plan_text="x" * 4001), body=_fixed(422)),
        _case(ids, "an extra field is rejected", "doctor_a", 422, CARE_PLANS, json=body(extra=1), body=_fixed(422)),
        _case(ids, "missing X-Idempotency-Key is rejected", "doctor_a", 422, CARE_PLANS, json=body(), key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, CARE_PLANS, body())


def care_plan_list_cases(ids) -> list[tuple]:
    path = lambda **o: CARE_PLANS + "?" + "&".join(f"{k}={v}" for k, v in {"clinic_id": ids.clinic_a, **o}.items())
    cases = _console_clinic_cases(ids, path, allowed=("nurse_a", "doctor_a", "multi"), ok_status=200, ok_body=lambda b: set(b) == {"plans"} and isinstance(b["plans"], list), visible_refused=("front_desk_a", "admin_a", "business_owner_a", "auditor_a"))
    cases += [
        _case(ids, "an unknown clinic is not found", "doctor_a", 404, path(clinic_id=uuid.uuid4()), key=None, body=_fixed(404)),
        _case(ids, "limit=0 is rejected", "doctor_a", 422, path(limit=0), key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, path(), None)[:4]


def care_plan_confirm_cases(ids) -> list[tuple]:
    path = lambda: CARE_PLAN_CONFIRM.format(plan_id=uuid.uuid4())
    cases = [_case(ids, f"{p} cannot confirm a plan that does not exist", p, 404, path(), json={"action": "approve"}, body=_fixed(404)) for p in ids.people]
    cases += [
        _case(ids, "an unknown action is rejected", "doctor_a", 422, path(), json={"action": "maybe"}, body=_fixed(422)),
        _case(ids, "an extra field is rejected", "doctor_a", 422, path(), json={"action": "approve", "extra": 1}, body=_fixed(422)),
        _case(ids, "missing X-Idempotency-Key is rejected", "doctor_a", 422, path(), json={"action": "approve"}, key=None, body=_fixed(422)),
        _case(ids, "a plan id that is not a uuid is rejected", "doctor_a", 422, CARE_PLANS + "/nope/confirm", json={"action": "approve"}, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, path(), {"action": "approve"})


def result_record_cases(ids) -> list[tuple]:
    body = lambda **o: {"clinic_id": str(ids.clinic_a), "patient_id": str(ids.patients_a[43]), "code": "DEMO-T1.a", "measures": {"demo_value": 5.0}, "verified": True, "valid_from": "2026-01-01T00:00:00Z", **o}
    made = lambda b: set(b) == {"result_id", "created", "wake_queued"} and b["created"] is True and b["wake_queued"] is False
    key = _new_key()
    cases = [
        _case(ids, "nurse_a records a result", "nurse_a", 201, RESULTS, json=body(), key=key, body=made),
        _case(ids, "the same key again is the same result", "nurse_a", 201, RESULTS, json=body(), key=key, body=lambda b: b["created"] is False),
        _case(ids, "doctor_a records a result", "doctor_a", 201, RESULTS, json=body(), body=made),
        _case(ids, "multi (a nurse here) records a result", "multi", 201, RESULTS, json=body(), body=made),
    ]
    cases += [_case(ids, f"{p} sees the clinic but is refused", p, 403, RESULTS, json=body(), body=NOT_YOUR_ROLE) for p in RESULT_VISIBLE_REFUSED]
    others = [p for p in ids.people if p not in ("nurse_a", "doctor_a", "multi", *RESULT_VISIBLE_REFUSED)]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused with the one not-found", p, 404, RESULTS, json=body(), body=_fixed(404)) for p in others]
    cases += [
        _case(ids, "a patient that is not in this clinic is not found", "nurse_a", 404, RESULTS, json=body(patient_id=str(uuid.uuid4())), body=_fixed(404)),
        _case(ids, "a result that supersedes nothing is not found", "nurse_a", 404, RESULTS, json=body(supersedes_id=str(uuid.uuid4())), body=_fixed(404)),
        _case(ids, "a measure name with a bad shape is rejected", "nurse_a", 422, RESULTS, json=body(measures={"Bad Name": 1.0}), body=_rejected),
        _case(ids, "an end before the start is rejected", "nurse_a", 422, RESULTS, json=body(valid_to="2025-01-01T00:00:00Z"), body=_rejected),
        _case(ids, "a measure that is text is rejected", "nurse_a", 422, RESULTS, json=body(measures={"demo_value": "high"}), body=_fixed(422)),
        _case(ids, "verified is required (no default)", "nurse_a", 422, RESULTS, json={k: v for k, v in body().items() if k != "verified"}, body=_fixed(422)),
        _case(ids, "an extra field is rejected", "nurse_a", 422, RESULTS, json=body(extra=1), body=_fixed(422)),
        _case(ids, "missing X-Idempotency-Key is rejected", "nurse_a", 422, RESULTS, json=body(), key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, RESULTS, body())


# ---- portal claim resolution and consent notice publication (migration 0051) --------------------------------------------------------------------------------------------------------------------------------
# Front desk of the clinic resolves pending claims (no request exists in the matrix world, so the resolve cases are the refusals; the approve and reject flows are in tests/test_patient_portal_db_live.py). The
# (Consent notices are no longer published by any route: migration 0053.)

PORTAL_CLAIM_LIST = "/v1/staff/portal-claims"
PORTAL_CLAIM_RESOLVE = PORTAL_CLAIM_LIST + "/{request_id}/resolve"
CLAIM_VISIBLE_REFUSED = ("nurse_a", "doctor_a", "admin_a", "business_owner_a", "auditor_a", "multi")


def portal_claim_list_cases(ids) -> list[tuple]:
    path = lambda **o: PORTAL_CLAIM_LIST + "?" + "&".join(f"{k}={v}" for k, v in {"clinic_id": ids.clinic_a, **o}.items())
    cases = _console_clinic_cases(ids, path, allowed=("front_desk_a",), ok_status=200, ok_body=lambda b: set(b) == {"claims"} and isinstance(b["claims"], list), visible_refused=CLAIM_VISIBLE_REFUSED)
    cases += [
        _case(ids, "an unknown clinic is not found", "front_desk_a", 404, path(clinic_id=uuid.uuid4()), key=None, body=_fixed(404)),
        _case(ids, "limit=0 is rejected", "front_desk_a", 422, path(limit=0), key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, path(), None)[:4]


def portal_claim_resolve_cases(ids) -> list[tuple]:
    path = lambda: PORTAL_CLAIM_RESOLVE.format(request_id=uuid.uuid4())
    body = lambda **o: {"decision": "reject", **o}
    cases = [_case(ids, f"{p} cannot resolve a request that does not exist", p, 404, path(), json=body(), body=_fixed(404)) for p in ids.people]
    cases += [
        _case(ids, "an unknown decision is rejected", "front_desk_a", 422, path(), json=body(decision="maybe"), body=_fixed(422)),
        _case(ids, "an extra field is rejected", "front_desk_a", 422, path(), json=body(extra=1), body=_fixed(422)),
        _case(ids, "a request id that is not a uuid is rejected", "front_desk_a", 422, PORTAL_CLAIM_LIST + "/nope/resolve", json=body(), body=_fixed(422)),
        _case(ids, "missing X-Idempotency-Key is rejected", "front_desk_a", 422, path(), json=body(), key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, path(), body())




# ---- Visit Readiness (migration 0052) --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
# A visit is a booking. The matrix app has no readiness components wired, so nurse and doctor get the visible 503 (and nothing is written); front desk sees the booking and is refused (403); everyone who cannot
# see a booking gets the one 404. The agent over its SQL ports (with scripted model fakes) is in tests/test_visit_readiness_db_live.py.

VISITS = "/v1/staff/visits"
VR_UNAVAILABLE = lambda b: b == {"detail": "visit readiness is unavailable: not configured"}
VR_ALLOWED = ("nurse_a", "doctor_a", "multi")


def _vr_path(ids, suffix):
    return f"{VISITS}/{ids.pools['cancel'][-1]}/readiness{suffix}"


def _vr_roles(ids, path, *, method_json=None, key="new"):
    cases = [_case(ids, f"{p} is told visit readiness is not configured", p, 503, path, json=method_json, key=key, body=VR_UNAVAILABLE) for p in VR_ALLOWED]
    cases.append(_case(ids, "front_desk_a sees the booking but is refused", "front_desk_a", 403, path, json=method_json, key=key, body=NOT_YOUR_ROLE))
    others = [p for p in ids.people if p not in (*VR_ALLOWED, "front_desk_a")]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused with the one not-found", p, 404, path, json=method_json, key=key, body=_fixed(404)) for p in others]
    return cases


def visit_readiness_start_cases(ids) -> list[tuple]:
    path = _vr_path(ids, "/start")
    cases = _vr_roles(ids, path)
    cases += [
        _case(ids, "a visit that does not exist is not found", "nurse_a", 404, f"{VISITS}/{uuid.uuid4()}/readiness/start", body=_fixed(404)),
        _case(ids, "a visit id that is not a uuid is rejected", "nurse_a", 422, f"{VISITS}/nope/readiness/start", body=_fixed(422)),
        _case(ids, "missing X-Idempotency-Key is rejected", "nurse_a", 422, path, key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, path, None)


def visit_readiness_get_cases(ids) -> list[tuple]:
    path = _vr_path(ids, "")
    cases = _vr_roles(ids, path, key=None)
    cases += [
        _case(ids, "a visit that does not exist is not found", "nurse_a", 404, f"{VISITS}/{uuid.uuid4()}/readiness", key=None, body=_fixed(404)),
        _case(ids, "a visit id that is not a uuid is rejected", "nurse_a", 422, f"{VISITS}/nope/readiness", key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, path, None)[:4]


def visit_readiness_reply_cases(ids) -> list[tuple]:
    path = _vr_path(ids, "/reply")
    body = {"outcome": "replied", "body": "DEMO reply text"}
    cases = _vr_roles(ids, path, method_json=body)
    cases += [
        _case(ids, "a visit that does not exist is not found", "nurse_a", 404, f"{VISITS}/{uuid.uuid4()}/readiness/reply", json=body, body=_fixed(404)),
        _case(ids, "a reply with no text is rejected", "nurse_a", 422, path, json={"outcome": "replied"}, body=_rejected),
        _case(ids, "no_reply with text is rejected", "nurse_a", 422, path, json={"outcome": "no_reply", "body": "x"}, body=_rejected),
        _case(ids, "an unknown outcome is rejected", "nurse_a", 422, path, json={"outcome": "maybe"}, body=_fixed(422)),
        _case(ids, "an extra field is rejected", "nurse_a", 422, path, json={**body, "extra": 1}, body=_fixed(422)),
        _case(ids, "missing X-Idempotency-Key is rejected", "nurse_a", 422, path, json=body, key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, path, body)


# ---- the two-person switch to autonomous (migration 0053) ---------------------------------------------------------------------------------------------------------------------------------------------------
# The enabling setting is unset in the matrix app, so a proposal answers the visible 503 after the role check (nothing is written). The approve / reject flows with two real admins are in
# tests/test_phase12_db_live.py.

AUTONOMY_PROPOSALS = "/v1/staff/console/autonomy/proposals"
AUTONOMY_RESOLVE = AUTONOMY_PROPOSALS + "/{proposal_id}/resolve"


PROPOSAL_VISIBLE_REFUSED = ("front_desk_a", "nurse_a", "business_owner_a", "auditor_a", "multi")  # (admin and doctor may propose / resolve; multi is a nurse here)


def autonomy_propose_cases(ids) -> list[tuple]:
    body = lambda **o: {"clinic_id": str(ids.clinic_a), **o}
    off = lambda b: b == {"detail": "the switch to autonomous is unavailable: not enabled"}
    cases = [_case(ids, f"{p} is told the switch is not enabled", p, 503, AUTONOMY_PROPOSALS, json=body(), body=off) for p in ("admin_a", "doctor_a")]
    cases += [_case(ids, f"{p} sees the clinic but is refused", p, 403, AUTONOMY_PROPOSALS, json=body(), body=NOT_YOUR_ROLE) for p in PROPOSAL_VISIBLE_REFUSED]
    others = [p for p in ids.people if p not in ("admin_a", "doctor_a") and p not in PROPOSAL_VISIBLE_REFUSED]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused with the one not-found", p, 404, AUTONOMY_PROPOSALS, json=body(), body=_fixed(404)) for p in others]
    cases += [
        _case(ids, "an unknown clinic is not found", "admin_a", 404, AUTONOMY_PROPOSALS, json=body(clinic_id=str(uuid.uuid4())), body=_fixed(404)),
        _case(ids, "an extra field is rejected", "admin_a", 422, AUTONOMY_PROPOSALS, json=body(extra=1), body=_fixed(422)),
        _case(ids, "missing X-Idempotency-Key is rejected", "admin_a", 422, AUTONOMY_PROPOSALS, json=body(), key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, AUTONOMY_PROPOSALS, body())


def autonomy_proposals_list_cases(ids) -> list[tuple]:
    path = lambda **o: AUTONOMY_PROPOSALS + "?" + "&".join(f"{k}={v}" for k, v in {"clinic_id": ids.clinic_a, **o}.items())
    cases = _console_clinic_cases(ids, path, allowed=("admin_a", "doctor_a"), ok_status=200, ok_body=lambda b: b == {"proposals": []}, visible_refused=PROPOSAL_VISIBLE_REFUSED)
    cases += [_case(ids, "an unknown clinic is not found", "admin_a", 404, path(clinic_id=uuid.uuid4()), key=None, body=_fixed(404))]
    return cases + _unauthenticated(ids, path(), None)[:4]


def autonomy_resolve_cases(ids) -> list[tuple]:
    path = lambda: AUTONOMY_RESOLVE.format(proposal_id=uuid.uuid4())
    cases = [_case(ids, f"{p} cannot resolve a proposal that does not exist", p, 404, path(), json={"decision": "approve"}, body=_fixed(404)) for p in ids.people]
    cases += [
        _case(ids, "an unknown decision is rejected", "admin_a", 422, path(), json={"decision": "maybe"}, body=_fixed(422)),
        _case(ids, "an extra field is rejected", "admin_a", 422, path(), json={"decision": "approve", "extra": 1}, body=_fixed(422)),
        _case(ids, "missing X-Idempotency-Key is rejected", "admin_a", 422, path(), json={"decision": "approve"}, key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, path(), {"decision": "approve"})


# ---- red-flag rule approval (migration 0072): list for doctors and admins; the decision is a doctor act. The two-doctor flows against real draft rules are in tests/test_rule_approval_db_live.py. ----

RULES = "/v1/staff/console/red-flag-rules"
RULE_DECISION = RULES + "/{rule_id}/decision"
RULE_VISIBLE_REFUSED = ("front_desk_a", "nurse_a", "business_owner_a", "auditor_a", "multi")  # (doctor and admin read the list; multi is a nurse here)


def rules_list_cases(ids) -> list[tuple]:
    path = lambda **o: RULES + "?" + "&".join(f"{k}={v}" for k, v in {"clinic_id": ids.clinic_a, **o}.items())
    cases = _console_clinic_cases(ids, path, allowed=("doctor_a", "admin_a"), ok_status=200, ok_body=lambda b: set(b) == {"rules"}, visible_refused=RULE_VISIBLE_REFUSED)
    cases += [
        _case(ids, "an unknown clinic is not found", "doctor_a", 404, path(clinic_id=uuid.uuid4()), key=None, body=_fixed(404)),
        _case(ids, "limit=0 is rejected", "doctor_a", 422, path(limit=0), key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, path(), None)[:4]


def rule_decision_cases(ids) -> list[tuple]:
    path = lambda: RULE_DECISION.format(rule_id=uuid.uuid4())
    cases = [_case(ids, f"{p} cannot decide a rule version that does not exist", p, 404, path(), json={"decision": "approve"}, body=_fixed(404)) for p in ids.people]
    cases += [
        _case(ids, "an unknown decision is rejected", "doctor_a", 422, path(), json={"decision": "maybe"}, body=_fixed(422)),
        _case(ids, "no doctor can retire a version that does not exist", "doctor_a", 404, path(), json={"decision": "retire"}, body=_fixed(404)),
        _case(ids, "an extra field is rejected", "doctor_a", 422, path(), json={"decision": "approve", "extra": 1}, body=_fixed(422)),
        _case(ids, "missing X-Idempotency-Key is rejected", "doctor_a", 422, path(), json={"decision": "approve"}, key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, path(), {"decision": "approve"})


# ---- medications (migration 0074): nurse and doctor record and read; with the vocabulary setting unset the record route answers the visible 503 after the role check. The success path is the standalone test below. ----

MEDICATIONS = "/v1/staff/medications"
MED_UNAVAILABLE = lambda b: b == {"detail": "medications are unavailable: not configured"}
MED_VISIBLE_REFUSED = ("front_desk_a", "admin_a", "business_owner_a", "auditor_a")


def medication_record_cases(ids) -> list[tuple]:
    body = lambda **o: {"clinic_id": str(ids.clinic_a), "patient_id": str(ids.patients_a[43]), "code": "DEMO-M1", "verified": True, "valid_from": "2026-01-01T00:00:00Z", **o}
    cases = [_case(ids, f"{p} is told medications are not configured, and nothing is written", p, 503, MEDICATIONS, json=body(), body=MED_UNAVAILABLE) for p in ("nurse_a", "doctor_a", "multi")]
    cases += [_case(ids, f"{p} sees the clinic but is refused", p, 403, MEDICATIONS, json=body(), body=NOT_YOUR_ROLE) for p in MED_VISIBLE_REFUSED]
    others = [p for p in ids.people if p not in ("nurse_a", "doctor_a", "multi", *MED_VISIBLE_REFUSED)]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused with the one not-found", p, 404, MEDICATIONS, json=body(), body=_fixed(404)) for p in others]
    cases += [
        _case(ids, "an unknown clinic is not found", "nurse_a", 404, MEDICATIONS, json=body(clinic_id=str(uuid.uuid4())), body=_fixed(404)),
        _case(ids, "a code with a bad shape is rejected", "nurse_a", 422, MEDICATIONS, json=body(code="bad code"), body=_fixed(422)),
        _case(ids, "verified is required (no default)", "nurse_a", 422, MEDICATIONS, json={k: v for k, v in body().items() if k != "verified"}, body=_fixed(422)),
        _case(ids, "an extra field is rejected (no dose, no free text)", "nurse_a", 422, MEDICATIONS, json=body(dose="x"), body=_fixed(422)),
        _case(ids, "missing X-Idempotency-Key is rejected", "nurse_a", 422, MEDICATIONS, json=body(), key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, MEDICATIONS, body())


def medication_list_cases(ids) -> list[tuple]:
    path = lambda **o: MEDICATIONS + "?" + "&".join(f"{k}={v}" for k, v in {"clinic_id": ids.clinic_a, "patient_id": ids.patients_a[43], **o}.items())
    cases = _console_clinic_cases(ids, path, allowed=("nurse_a", "doctor_a", "multi"), ok_status=200, ok_body=lambda b: set(b) == {"medications"}, visible_refused=MED_VISIBLE_REFUSED)
    cases += [
        _case(ids, "an unknown clinic is not found", "nurse_a", 404, path(clinic_id=uuid.uuid4()), key=None, body=_fixed(404)),
        _case(ids, "limit=0 is rejected", "nurse_a", 422, path(limit=0), key=None, body=_fixed(422)),
        _case(ids, "a missing patient id is rejected", "nurse_a", 422, MEDICATIONS + f"?clinic_id={ids.clinic_a}", key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, path(), None)[:4]

RULE_AUTHOR_REFUSED = ("front_desk_a", "nurse_a", "admin_a", "business_owner_a", "auditor_a", "multi")  # (only a doctor authors; multi is a nurse here)


def rule_author_cases(ids) -> list[tuple]:
    # MEANINGLESS marker words: no clinical content in a test
    body = lambda **o: {"clinic_id": str(ids.clinic_a), "rule_code": "zz_marker", "phrases": ["zzmarker"], "token_sets": [["zzalpha", "zzbeta"]], **o}
    made = lambda b: set(b) == {"rule_id", "status"} and b["status"] == "draft_unreviewed"
    key = _new_key()
    cases = [
        _case(ids, "doctor_a writes a draft", "doctor_a", 201, RULES, json=body(), key=key, body=made),
        _case(ids, "the same key again is the same draft", "doctor_a", 201, RULES, json=body(), key=key, body=made),
    ]
    cases += [_case(ids, f"{p} sees the clinic but is refused", p, 403, RULES, json=body(), body=NOT_YOUR_ROLE) for p in RULE_AUTHOR_REFUSED]
    others = [p for p in ids.people if p != "doctor_a" and p not in RULE_AUTHOR_REFUSED]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused with the one not-found", p, 404, RULES, json=body(), body=_fixed(404)) for p in others]
    cases += [
        _case(ids, "the same key with a different draft is a conflict", "doctor_a", 409, RULES, json=body(phrases=["zzother"]), key=key, body=lambda b: b == {"detail": "this idempotency key was already used by a different request"}),
        _case(ids, "an unknown clinic is not found", "doctor_a", 404, RULES, json=body(clinic_id=str(uuid.uuid4())), body=_fixed(404)),
        _case(ids, "a rule with no criterion is rejected", "doctor_a", 422, RULES, json=body(phrases=[], token_sets=[]), body=_rejected),
        _case(ids, "a regular expression has no place in the language (an extra field)", "doctor_a", 422, RULES, json=body(patterns=["a.*b"]), body=_fixed(422)),
        _case(ids, "a bad rule code is rejected", "doctor_a", 422, RULES, json=body(rule_code="Bad Code"), body=_fixed(422)),
        _case(ids, "a phrase over the limit is rejected", "doctor_a", 422, RULES, json=body(phrases=["x" * 201]), body=_fixed(422)),
        _case(ids, "a token set of one word is rejected", "doctor_a", 422, RULES, json=body(token_sets=[["zzalpha"]]), body=_fixed(422)),
        _case(ids, "a token with a space is rejected", "doctor_a", 422, RULES, json=body(token_sets=[["zz alpha", "zzbeta"]]), body=_fixed(422)),
        _case(ids, "more than 100 phrases are rejected", "doctor_a", 422, RULES, json=body(phrases=["zz"] * 101), body=_fixed(422)),
        _case(ids, "an extra field is rejected", "doctor_a", 422, RULES, json=body(extra=1), body=_fixed(422)),
        _case(ids, "missing X-Idempotency-Key is rejected", "doctor_a", 422, RULES, json=body(), key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, RULES, body())

RULE_WITHDRAW = RULES + "/{rule_id}/withdraw"


def rule_withdraw_cases(ids) -> list[tuple]:
    path = lambda: RULE_WITHDRAW.format(rule_id=uuid.uuid4())
    cases = [_case(ids, f"{p} cannot withdraw a rule version that does not exist", p, 404, path(), key=_new_key(), body=_fixed(404)) for p in ids.people]
    cases += [
        _case(ids, "missing X-Idempotency-Key is rejected", "doctor_a", 422, path(), key=None, body=_fixed(422)),
        _case(ids, "a rule id that is not a uuid is rejected", "doctor_a", 422, RULES + "/nope/withdraw", key=_new_key(), body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, path(), None)

# ---- patient profile, notifications, requests and the staff request list (migration 0080). The matrix patient is an ACTIVE account bound to no record, so filing a request is refused (the flows are in
# tests/test_portal_extras_db_live.py); the public grievance route is outside /v1 and has its own offline tests (tests/test_public_grievance_route.py).

PORTAL_PROFILE = PORTAL + "/profile"
PORTAL_NOTIFICATIONS = PORTAL + "/notifications"
PORTAL_REQUESTS = PORTAL + "/requests"
STAFF_PATIENT_REQUESTS = "/v1/staff/patient-requests"
PROFILE_KEYS = {"display_name", "sex", "preferred_language", "sex_words", "languages"}


def portal_profile_get_cases(ids) -> list[tuple]:
    cases = [_case(ids, "an active patient account reads its own profile", "patient", 200, PORTAL_PROFILE, key=None, body=lambda b: set(b) == PROFILE_KEYS and b["languages"] == ["en", "ta"])]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused", p, 403, PORTAL_PROFILE, key=None, body=NOT_A_PATIENT) for p in _non_patients(ids)]
    cases.append(_case(ids, "valid user with no iam account is refused", bearer(mint(ids.no_account)), 403, PORTAL_PROFILE, key=None, body=NOT_A_PATIENT))
    return cases + _unauthenticated(ids, PORTAL_PROFILE, None)[:4]


def portal_profile_put_cases(ids) -> list[tuple]:
    cases = [
        _case(ids, "an active patient account sets its preferred language", "patient", 200, PORTAL_PROFILE, json={"preferred_language": "ta"}, body=lambda b: set(b) == PROFILE_KEYS and b["preferred_language"] == "ta"),
        _case(ids, "a sex word while the clinic list is not configured is told so (503), nothing is written", "patient", 503, PORTAL_PROFILE, json={"sex": "some_word"}, body=lambda b: "not configured" in b["detail"]),
        _case(ids, "a sex word with a bad shape is rejected", "patient", 422, PORTAL_PROFILE, json={"sex": "Bad Word"}, body=_fixed(422)),
        _case(ids, "a language outside en and ta is rejected", "patient", 422, PORTAL_PROFILE, json={"preferred_language": "fr"}, body=_fixed(422)),
        _case(ids, "an extra field is rejected", "patient", 422, PORTAL_PROFILE, json={"name": "x"}, body=_fixed(422)),
        _case(ids, "missing X-Idempotency-Key is rejected", "patient", 422, PORTAL_PROFILE, json={"preferred_language": "en"}, key=None, body=_fixed(422)),
    ]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused", p, 403, PORTAL_PROFILE, json={"preferred_language": "en"}, body=NOT_A_PATIENT) for p in _non_patients(ids)]
    cases.append(_case(ids, "valid user with no iam account is refused", bearer(mint(ids.no_account)), 403, PORTAL_PROFILE, json={"preferred_language": "en"}, body=NOT_A_PATIENT))
    return cases + _unauthenticated(ids, PORTAL_PROFILE, {"preferred_language": "en"})


def portal_notifications_list_cases(ids) -> list[tuple]:
    cases = [_case(ids, "an active patient account lists its own notices (none)", "patient", 200, PORTAL_NOTIFICATIONS, key=None, body=lambda b: b == {"notifications": []})]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused", p, 403, PORTAL_NOTIFICATIONS, key=None, body=NOT_A_PATIENT) for p in _non_patients(ids)]
    cases.append(_case(ids, "valid user with no iam account is refused", bearer(mint(ids.no_account)), 403, PORTAL_NOTIFICATIONS, key=None, body=NOT_A_PATIENT))
    return cases + _unauthenticated(ids, PORTAL_NOTIFICATIONS, None)[:4]


def portal_notification_read_cases(ids) -> list[tuple]:
    path = lambda: f"{PORTAL_NOTIFICATIONS}/{uuid.uuid4()}/read"
    cases = [_case(ids, "a notice that is not yours or does not exist is one not-found", "patient", 404, path(), body=_fixed(404))]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused", p, 403, path(), body=NOT_A_PATIENT) for p in _non_patients(ids)]
    cases += [
        _case(ids, "missing X-Idempotency-Key is rejected", "patient", 422, path(), key=None, body=_fixed(422)),
        _case(ids, "a notice id that is not a uuid is rejected", "patient", 422, f"{PORTAL_NOTIFICATIONS}/nope/read", body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, path(), None)


def portal_request_file_cases(ids) -> list[tuple]:
    body = lambda **o: {"clinic_id": str(ids.clinic_a), "kind": "erasure", **o}
    cases = [_case(ids, "an active patient account bound to no record is refused", "patient", 403, PORTAL_REQUESTS, json=body(), body=NOT_A_PATIENT)]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused", p, 403, PORTAL_REQUESTS, json=body(), body=NOT_A_PATIENT) for p in _non_patients(ids)]
    cases.append(_case(ids, "valid user with no iam account is refused", bearer(mint(ids.no_account)), 403, PORTAL_REQUESTS, json=body(), body=NOT_A_PATIENT))
    cases += [
        _case(ids, "a correction without a field is rejected", "patient", 422, PORTAL_REQUESTS, json=body(kind="correction"), body=_rejected),
        _case(ids, "another kind with a field is rejected", "patient", 422, PORTAL_REQUESTS, json=body(field="phone"), body=_rejected),
        _case(ids, "an unknown kind is rejected", "patient", 422, PORTAL_REQUESTS, json=body(kind="rename"), body=_fixed(422)),
        _case(ids, "free text has no place in a request (an extra field)", "patient", 422, PORTAL_REQUESTS, json=body(text="x"), body=_fixed(422)),
        _case(ids, "missing X-Idempotency-Key is rejected", "patient", 422, PORTAL_REQUESTS, json=body(), key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, PORTAL_REQUESTS, body())


def portal_request_list_cases(ids) -> list[tuple]:
    cases = [_case(ids, "an active patient account lists its own requests (none)", "patient", 200, PORTAL_REQUESTS, key=None, body=lambda b: b == {"requests": []})]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused", p, 403, PORTAL_REQUESTS, key=None, body=NOT_A_PATIENT) for p in _non_patients(ids)]
    cases.append(_case(ids, "valid user with no iam account is refused", bearer(mint(ids.no_account)), 403, PORTAL_REQUESTS, key=None, body=NOT_A_PATIENT))
    return cases + _unauthenticated(ids, PORTAL_REQUESTS, None)[:4]


def staff_patient_request_list_cases(ids) -> list[tuple]:
    path = lambda **o: STAFF_PATIENT_REQUESTS + "?" + "&".join(f"{k}={v}" for k, v in {"clinic_id": ids.clinic_a, **o}.items())
    visible = ("nurse_a", "doctor_a", "admin_a", "business_owner_a", "auditor_a", "multi")
    cases = _console_clinic_cases(ids, path, allowed=("front_desk_a",), ok_status=200, ok_body=lambda b: b == {"requests": []}, visible_refused=visible)
    cases += [
        _case(ids, "an unknown clinic is not found", "front_desk_a", 404, path(clinic_id=uuid.uuid4()), key=None, body=_fixed(404)),
        _case(ids, "limit=0 is rejected", "front_desk_a", 422, path(limit=0), key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, path(), None)[:4]


def staff_patient_request_status_cases(ids) -> list[tuple]:
    path = lambda: f"{STAFF_PATIENT_REQUESTS}/{uuid.uuid4()}/status"
    body = {"status": "acknowledged"}
    cases = [_case(ids, f"{p} cannot update a request that does not exist", p, 404, path(), json=body, body=_fixed(404)) for p in ids.people]
    cases += [
        _case(ids, "a status that is not one of the three is rejected", "front_desk_a", 422, path(), json={"status": "open"}, body=_fixed(422)),
        _case(ids, "an extra field is rejected", "front_desk_a", 422, path(), json={**body, "text": "x"}, body=_fixed(422)),
        _case(ids, "missing X-Idempotency-Key is rejected", "front_desk_a", 422, path(), json=body, key=None, body=_fixed(422)),
        _case(ids, "a request id that is not a uuid is rejected", "front_desk_a", 422, f"{STAFF_PATIENT_REQUESTS}/nope/status", json=body, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, path(), body)


VOICE_CONSENT_NOTICE = "/v1/portal/voice/consent-notice"


def voice_consent_notice_cases(ids) -> list[tuple]:
    cases = [_case(ids, "an active patient account reads the notice setting (unset here: nothing configured, no text)", "patient", 200, VOICE_CONSENT_NOTICE, key=None, body=lambda b: b == {"configured": False, "text": None, "is_placeholder": False})]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused", p, 403, VOICE_CONSENT_NOTICE, key=None, body=NOT_A_PATIENT) for p in _non_patients(ids)]
    cases.append(_case(ids, "valid user with no iam account is refused", bearer(mint(ids.no_account)), 403, VOICE_CONSENT_NOTICE, key=None, body=NOT_A_PATIENT))
    return cases + _unauthenticated(ids, VOICE_CONSENT_NOTICE, None)[:4]


# ---- break-glass access (migration 0058; BUILD-SPEC.md 18.4) ------------------------------------------------------------------------------------------------------------------------------------------------
# Front desk, nurse and doctor open a time-limited grant for one patient (a reason code from the clinic's owner-kept list); the holder reads that patient's messages (this world has no encryption keys, so the
# read answers 503 after the grant is found; the decrypting path and the database behaviour are in tests/test_break_glass_db_live.py); an admin of the clinic who did not open it reviews. Opening writes
# a grant and an audit row that stay behind with the clinic (append-only).

BREAK_GLASS = "/v1/staff/break-glass"
BG_OPENERS = ("front_desk_a", "nurse_a", "doctor_a", "multi")
BG_VISIBLE_REFUSED = ("admin_a", "business_owner_a", "auditor_a")


def break_glass_open_cases(ids) -> list[tuple]:
    body = lambda **o: {"clinic_id": str(ids.clinic_a), "patient_id": str(ids.patients_a[46]), "reason_code": "demo_reason", **o}
    made = lambda b: set(b) == {"grant_id", "expires_at", "created"} and b["created"] is True
    key = _new_key()
    cases = [_case(ids, f"{p} opens a grant", p, 201, BREAK_GLASS, json=body(), body=made) for p in BG_OPENERS]
    cases += [
        _case(ids, "the same key again is the same grant", "nurse_a", 201, BREAK_GLASS, json=body(), key=key, body=made),
        _case(ids, "replaying that key returns the grant", "nurse_a", 201, BREAK_GLASS, json=body(), key=key, body=lambda b: b["created"] is False),
    ]
    cases += [_case(ids, f"{p} sees the clinic but is refused", p, 403, BREAK_GLASS, json=body(), body=NOT_YOUR_ROLE) for p in BG_VISIBLE_REFUSED]
    others = [p for p in ids.people if p not in (*BG_OPENERS, *BG_VISIBLE_REFUSED)]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused with the one not-found", p, 404, BREAK_GLASS, json=body(), body=_fixed(404)) for p in others]
    cases += [
        _case(ids, "a patient that is not in this clinic is not found", "nurse_a", 404, BREAK_GLASS, json=body(patient_id=str(uuid.uuid4())), body=_fixed(404)),
        _case(ids, "a reason that is not on the clinic's list is rejected", "nurse_a", 422, BREAK_GLASS, json=body(reason_code="not_listed"), body=_rejected),
        _case(ids, "a reason with a bad shape is rejected", "nurse_a", 422, BREAK_GLASS, json=body(reason_code="Free text here"), body=_fixed(422)),
        _case(ids, "an extra field is rejected", "nurse_a", 422, BREAK_GLASS, json=body(extra=1), body=_fixed(422)),
        _case(ids, "missing X-Idempotency-Key is rejected", "nurse_a", 422, BREAK_GLASS, json=body(), key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, BREAK_GLASS, body())


def break_glass_read_cases(ids) -> list[tuple]:
    mine = f"{BREAK_GLASS}/{ids.bg_grants[0]}/messages"
    unknown = f"{BREAK_GLASS}/{uuid.uuid4()}/messages"
    cases = [
        _case(ids, "the holder reads (the seeded patient has no messages)", "nurse_a", 200, mine, key=None, body=lambda b: b == {"messages": []}),
        _case(ids, "an admin of the clinic sees the grant but is not its holder", "admin_a", 403, mine, key=None, body=NOT_YOUR_ROLE),
    ]
    others = [p for p in ids.people if p not in ("nurse_a", "admin_a")]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) cannot see the grant: the one not-found", p, 404, mine, key=None, body=_fixed(404)) for p in others]
    cases += [_case(ids, f"{p} asks for a grant that does not exist", p, 404, unknown, key=None, body=_fixed(404)) for p in ids.people]
    cases += [_case(ids, "a grant id that is not a uuid is rejected", "nurse_a", 422, f"{BREAK_GLASS}/nope/messages", key=None, body=_fixed(422))]
    return cases + _unauthenticated(ids, mine, None)[:4]


def break_glass_list_cases(ids) -> list[tuple]:
    path = lambda **o: BREAK_GLASS + "?" + "&".join(f"{k}={v}" for k, v in {"clinic_id": ids.clinic_a, **o}.items())
    ok = lambda b: set(b) == {"grants"} and isinstance(b["grants"], list) and all(set(g) == {"grant_id", "patient_id", "opened_by", "reason_code", "opened_at", "expires_at", "reviewed_by", "reviewed_at", "review_outcome"} for g in b["grants"])
    visible = ("front_desk_a", "nurse_a", "doctor_a", "business_owner_a", "auditor_a", "multi")
    cases = _console_clinic_cases(ids, path, allowed=("admin_a",), ok_status=200, ok_body=ok, visible_refused=visible)
    cases += [
        _case(ids, "an unknown clinic is not found", "admin_a", 404, path(clinic_id=uuid.uuid4()), key=None, body=_fixed(404)),
        _case(ids, "limit=0 is rejected", "admin_a", 422, path(limit=0), key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, path(), None)[:4]


def break_glass_review_cases(ids) -> list[tuple]:
    path = lambda: f"{BREAK_GLASS}/{ids.bg_grants[1]}/review"
    unknown = f"{BREAK_GLASS}/{uuid.uuid4()}/review"
    body = {"outcome": "appropriate"}
    cases = [
        _case(ids, "an admin who did not open it reviews", "admin_a", 200, path(), json=body, body=lambda b: b == {"result": "reviewed"}),
        _case(ids, "the same outcome again is a replay", "admin_a", 200, path(), json=body, body=lambda b: b == {"result": "reviewed"}),
        _case(ids, "a different outcome after a review is refused as already reviewed", "admin_a", 200, path(), json={"outcome": "inappropriate"}, body=lambda b: b == {"result": "already_reviewed"}),
        _case(ids, "the holder sees the grant but is not an admin", "nurse_a", 403, path(), json=body, body=NOT_YOUR_ROLE),
    ]
    others = [p for p in ids.people if p not in ("admin_a", "nurse_a")]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) cannot see the grant: the one not-found", p, 404, path(), json=body, body=_fixed(404)) for p in others]
    cases += [_case(ids, f"{p} reviews a grant that does not exist", p, 404, unknown, json=body, body=_fixed(404)) for p in ids.people]
    cases += [
        _case(ids, "an outcome that is not one of the two is rejected", "admin_a", 422, path(), json={"outcome": "fine"}, body=_fixed(422)),
        _case(ids, "missing X-Idempotency-Key is rejected", "admin_a", 422, path(), json=body, key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, path(), body)


# ---- patient rights: grievances and the download of one's own data (migration 0059) -------------------------------------------------------------------------------------------------------------------------
# The matrix patient is an ACTIVE account bound to no record and this world has no encryption keys, so filing answers 503 (the refusals and the whole flow are in tests/test_patient_rights_db_live.py).

os.environ.setdefault("CAREFLOW_GRIEVANCE_CATEGORIES", "demo_category")
PORTAL_GRIEVANCES = PORTAL + "/grievances"
PORTAL_MY_DATA = PORTAL + "/my-data"
STAFF_GRIEVANCES = "/v1/staff/grievances"
GRIEVANCE_STATUS = STAFF_GRIEVANCES + "/{grievance_id}/status"


def portal_grievance_file_cases(ids) -> list[tuple]:
    body = lambda **o: {"clinic_id": str(ids.clinic_a), "category": "demo_category", "text": "DEMO text", **o}
    cases = [_case(ids, "an active patient account bound to no record is refused (the matrix patient claims nothing)", "patient", 403, PORTAL_GRIEVANCES, json=body(), body=NOT_A_PATIENT)]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused", p, 403, PORTAL_GRIEVANCES, json=body(), body=NOT_A_PATIENT) for p in _non_patients(ids)]
    cases.append(_case(ids, "valid user with no iam account is refused", bearer(mint(ids.no_account)), 403, PORTAL_GRIEVANCES, json=body(), body=NOT_A_PATIENT))
    cases += [
        _case(ids, "a category with a bad shape is rejected", "patient", 422, PORTAL_GRIEVANCES, json=body(category="Free text"), body=_fixed(422)),
        _case(ids, "an empty text is rejected", "patient", 422, PORTAL_GRIEVANCES, json=body(text=""), body=_fixed(422)),
        _case(ids, "an extra field is rejected", "patient", 422, PORTAL_GRIEVANCES, json=body(extra=1), body=_fixed(422)),
        _case(ids, "missing X-Idempotency-Key is rejected", "patient", 422, PORTAL_GRIEVANCES, json=body(), key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, PORTAL_GRIEVANCES, body())


def portal_grievance_list_cases(ids) -> list[tuple]:
    cases = [_case(ids, "an active patient account lists its own (none)", "patient", 200, PORTAL_GRIEVANCES, key=None, body=lambda b: b == {"grievances": []})]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused", p, 403, PORTAL_GRIEVANCES, key=None, body=NOT_A_PATIENT) for p in _non_patients(ids)]
    cases.append(_case(ids, "valid user with no iam account is refused", bearer(mint(ids.no_account)), 403, PORTAL_GRIEVANCES, key=None, body=NOT_A_PATIENT))
    return cases + _unauthenticated(ids, PORTAL_GRIEVANCES, None)[:4]


def portal_my_data_cases(ids) -> list[tuple]:
    ok = lambda b: set(b) == {"account_status", "stage", "records", "consents", "bookings", "messages", "grievances", "truncated"} and b["truncated"] is False and b["stage"] == "new_patient" and b["records"] == [] and b["messages"] == [] and b["bookings"] == []
    cases = [_case(ids, "an active patient account downloads its own data", "patient", 200, PORTAL_MY_DATA, key=None, body=ok)]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused", p, 403, PORTAL_MY_DATA, key=None, body=NOT_A_PATIENT) for p in _non_patients(ids)]
    cases.append(_case(ids, "valid user with no iam account is refused", bearer(mint(ids.no_account)), 403, PORTAL_MY_DATA, key=None, body=NOT_A_PATIENT))
    return cases + _unauthenticated(ids, PORTAL_MY_DATA, None)[:4]


def staff_grievance_list_cases(ids) -> list[tuple]:
    path = lambda **o: STAFF_GRIEVANCES + "?" + "&".join(f"{k}={v}" for k, v in {"clinic_id": ids.clinic_a, **o}.items())
    visible = ("front_desk_a", "nurse_a", "doctor_a", "business_owner_a", "auditor_a", "multi")
    cases = _console_clinic_cases(ids, path, allowed=("admin_a",), ok_status=200, ok_body=lambda b: set(b) == {"grievances"} and isinstance(b["grievances"], list), visible_refused=visible)
    cases += [
        _case(ids, "an unknown clinic is not found", "admin_a", 404, path(clinic_id=uuid.uuid4()), key=None, body=_fixed(404)),
        _case(ids, "limit=0 is rejected", "admin_a", 422, path(limit=0), key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, path(), None)[:4]


def staff_grievance_status_cases(ids) -> list[tuple]:
    path = lambda: f"{STAFF_GRIEVANCES}/{uuid.uuid4()}/status"
    body = {"status": "acknowledged"}
    cases = [_case(ids, f"{p} cannot update a grievance that does not exist", p, 404, path(), json=body, body=_fixed(404)) for p in ids.people]
    cases += [
        _case(ids, "a status that is not one of the two is rejected", "admin_a", 422, path(), json={"status": "closed"}, body=_fixed(422)),
        _case(ids, "missing X-Idempotency-Key is rejected", "admin_a", 422, path(), json=body, key=None, body=_fixed(422)),
        _case(ids, "a grievance id that is not a uuid is rejected", "admin_a", 422, f"{STAFF_GRIEVANCES}/nope/status", json=body, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, path(), body)


# ---- staff recovery through two admins (migration 0060; BUILD-SPEC.md 18.1) ----------------------------------------------------------------------------------------------------------------------------------
# admin_a requests (a fresh nurse account is the target); the approve, cancel and complete cases run against requests the owner seeded (a second fresh admin approves). The Auth admin port is not configured
# in this world; the matrix wires the FAKE Auth admin port, so complete answers 200 (the 503 for no port is in tests/test_staff_routes.py style unit coverage).

RECOVERY = "/v1/staff/recovery"


def _seed_recovery(ids, status="requested", approver=None):
    """A recovery request for a fresh nurse account, written by the owner (the functions are the only application path); returns (request id, requester id)."""
    target = new_staff_account(ids, roles=("nurse",))
    requester = ids.people["admin_a"][0]

    async def make(conn):
        return await conn.fetchval(
            "insert into iam.recovery_requests (clinic_id, origin, target_account_id, requested_by, approved_by, status, decided_at, idempotency_key) values ($1, 'test', $2, $3, $4, $5, "
            "case when $5 = 'requested' then null else now() end, $6) returning id",
            ids.clinic_a, target, requester, approver, status, f"mx-rec-{uuid.uuid4()}",
        )

    return _run(_owner(make))


def staff_recovery_request_cases(ids) -> list[tuple]:
    target = new_staff_account(ids, roles=("nurse",))
    body = lambda **o: {"clinic_id": str(ids.clinic_a), "account_id": str(target), **o}
    admin, key = _admin_stepped(ids), _new_key()
    made = lambda created: (lambda b: set(b) == {"request_id", "created"} and b["created"] is created)
    ok = [
        _case(ids, "an admin asks for a recovery", admin, 201, RECOVERY, json=body(), key=key, body=made(True)),
        _case(ids, "the same request again is a replay", admin, 201, RECOVERY, json=body(), key=key, body=made(False)),
        _case(ids, "a second recovery for the same account is refused while one is open", admin, 403, RECOVERY, json=body(), body=NOT_YOUR_ROLE),
        _case(ids, "an admin cannot ask for their own recovery", admin, 403, RECOVERY, json=body(account_id=str(ids.people["admin_a"][0])), body=NOT_YOUR_ROLE),
        _case(ids, "an account that is not staff of the clinic is refused", admin, 403, RECOVERY, json=body(account_id=str(uuid.uuid4())), body=NOT_YOUR_ROLE),
        _case(ids, "missing X-Idempotency-Key is rejected", admin, 422, RECOVERY, json=body(), key=None, body=_fixed(422)),
        _case(ids, "an unknown clinic is not found", admin, 404, RECOVERY, json=body(clinic_id=str(uuid.uuid4())), body=_fixed(404)),
    ]
    return _staff_write_cases(ids, lambda: RECOVERY, body, ok)


def staff_recovery_list_cases(ids) -> list[tuple]:
    path = lambda **o: RECOVERY + "?" + "&".join(f"{k}={v}" for k, v in {"clinic_id": ids.clinic_a, **o}.items())
    visible = ("front_desk_a", "nurse_a", "doctor_a", "business_owner_a", "auditor_a", "multi")
    ok = lambda b: set(b) == {"requests"} and isinstance(b["requests"], list)
    cases = _console_clinic_cases(ids, path, allowed=("admin_a",), ok_status=200, ok_body=ok, visible_refused=visible)
    cases += [
        _case(ids, "an unknown clinic is not found", "admin_a", 404, path(clinic_id=uuid.uuid4()), key=None, body=_fixed(404)),
        _case(ids, "limit=0 is rejected", "admin_a", 422, path(limit=0), key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, path(), None)[:4]


def _recovery_action_cases(ids, action, seeded_ok) -> list[tuple]:
    path = lambda rid: f"{RECOVERY}/{rid}/{action}"
    unknown = path(uuid.uuid4())
    admin = _admin_stepped(ids)
    cases = list(seeded_ok)
    cases += [_case(ids, f"{p} with a second factor cannot see a request that does not exist", stepped(ids.people[p][0]), 404, unknown, body=_fixed(404)) for p in ids.people]
    cases += [
        _case(ids, "missing X-Idempotency-Key is rejected", admin, 422, unknown, key=None, body=_fixed(422)),
        _case(ids, "a request id that is not a uuid is rejected", admin, 422, f"{RECOVERY}/nope/{action}", body=_fixed(422)),
    ]
    cases += [_case(ids, f"admin_a with {label} is told to step up", headers, 401, unknown, body=_fixed(401)) for label, headers in not_stepped_variants(ids.people["admin_a"][0])]
    return cases + _unauthenticated(ids, unknown, None)


def staff_recovery_approve_cases(ids) -> list[tuple]:
    rid = _seed_recovery(ids)
    approver = new_staff_account(ids, roles=("admin",))
    path = f"{RECOVERY}/{rid}/approve"
    seeded = [
        _case(ids, "the requester cannot approve their own request", _admin_stepped(ids), 403, path, body=NOT_YOUR_ROLE),
        _case(ids, "a nurse cannot see the request", stepped(ids.people["nurse_a"][0]), 404, path, body=_fixed(404)),
        _case(ids, "a second admin approves", stepped(approver), 200, path, body=lambda b: b == {"result": "approved"}),
        _case(ids, "the approver again is a replay", stepped(approver), 200, path, body=lambda b: b == {"result": "approved"}),
    ]
    return _recovery_action_cases(ids, "approve", seeded)


def staff_recovery_cancel_cases(ids) -> list[tuple]:
    rid = _seed_recovery(ids)
    path = f"{RECOVERY}/{rid}/cancel"
    seeded = [
        _case(ids, "an admin cancels an open request", _admin_stepped(ids), 200, path, body=lambda b: b == {"result": "cancelled"}),
        _case(ids, "cancelling again is a replay", _admin_stepped(ids), 200, path, body=lambda b: b == {"result": "cancelled"}),
        _case(ids, "a doctor cannot see the request", stepped(ids.people["doctor_a"][0]), 404, path, body=_fixed(404)),
    ]
    return _recovery_action_cases(ids, "cancel", seeded)


def staff_recovery_complete_cases(ids) -> list[tuple]:
    approver = new_staff_account(ids, roles=("admin",))
    rid = _seed_recovery(ids, status="approved", approver=approver)
    path = f"{RECOVERY}/{rid}/complete"
    seeded = [
        _case(ids, "an admin completes (the matrix wires the FAKE Auth admin port)", _admin_stepped(ids), 200, path, body=lambda b: b == {"result": "completed"}),
        _case(ids, "a nurse cannot see the request", stepped(ids.people["nurse_a"][0]), 404, path, body=_fixed(404)),
    ]
    return _recovery_action_cases(ids, "complete", seeded)


# ---- clinical notes (migration 0061; BUILD-SPEC.md 9.6) ---------------------------------------------------------------------------------------------------------------------------------------------------
# Nurses and doctors write and read; this world has no encryption keys, so a write answers 503 after the clinic and patient are found (the encrypted flow, addenda and the database behaviour are in
# tests/test_clinical_record_db_live.py); a read answers 200 with the text withheld.

NOTES = "/v1/staff/notes"
NOTE_VISIBLE_REFUSED = ("front_desk_a", "admin_a", "business_owner_a", "auditor_a")


def clinical_note_write_cases(ids) -> list[tuple]:
    body = lambda **o: {"clinic_id": str(ids.clinic_a), "patient_id": str(ids.patients_a[47]), "text": "DEMO note", **o}
    made = lambda b: set(b) == {"note_id", "created"} and b["created"] is True
    cases = [_case(ids, f"{p} writes a note", p, 201, NOTES, json=body(), body=made) for p in ("nurse_a", "doctor_a", "multi")]
    cases += [_case(ids, f"{p} sees the clinic but is refused", p, 403, NOTES, json=body(), body=NOT_YOUR_ROLE) for p in NOTE_VISIBLE_REFUSED]
    others = [p for p in ids.people if p not in ("nurse_a", "doctor_a", "multi", *NOTE_VISIBLE_REFUSED)]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused with the one not-found", p, 404, NOTES, json=body(), body=_fixed(404)) for p in others]
    cases += [
        _case(ids, "a patient that is not in this clinic is not found", "nurse_a", 404, NOTES, json=body(patient_id=str(uuid.uuid4())), body=_fixed(404)),
        _case(ids, "an empty text is rejected", "nurse_a", 422, NOTES, json=body(text=""), body=_fixed(422)),
        _case(ids, "an extra field is rejected", "nurse_a", 422, NOTES, json=body(extra=1), body=_fixed(422)),
        _case(ids, "missing X-Idempotency-Key is rejected", "nurse_a", 422, NOTES, json=body(), key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, NOTES, body())


def clinical_note_list_cases(ids) -> list[tuple]:
    path = lambda **o: NOTES + "?" + "&".join(f"{k}={v}" for k, v in {"clinic_id": ids.clinic_a, "patient_id": ids.patients_a[47], **o}.items())
    cases = _console_clinic_cases(ids, path, allowed=("nurse_a", "doctor_a", "multi"), ok_status=200, ok_body=lambda b: b == {"notes": []}, visible_refused=NOTE_VISIBLE_REFUSED)
    cases += [
        _case(ids, "an unknown clinic is not found", "nurse_a", 404, path(clinic_id=uuid.uuid4()), key=None, body=_fixed(404)),
        _case(ids, "a patient that is not in this clinic is not found", "nurse_a", 404, path(patient_id=uuid.uuid4()), key=None, body=_fixed(404)),
        _case(ids, "limit=0 is rejected", "nurse_a", 422, path(limit=0), key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, path(), None)[:4]


# ---- autonomy history (read-only) ---------------------------------------------------------------------------------------------------------------------------------------------------------------------
AUTONOMY_HISTORY = "/v1/staff/console/autonomy/history"


def autonomy_history_cases(ids) -> list[tuple]:
    path = lambda **o: AUTONOMY_HISTORY + "?" + "&".join(f"{k}={v}" for k, v in {"clinic_id": ids.clinic_a, **o}.items())
    ok = lambda b: set(b) == {"items"} and isinstance(b["items"], list) and all(set(i) == {"kind", "at", "by", "mode", "proposer_role", "outcome", "decided_by", "decided_at"} for i in b["items"])
    cases = _console_clinic_cases(ids, path, allowed=("admin_a", "doctor_a"), ok_status=200, ok_body=ok, visible_refused=PROPOSAL_VISIBLE_REFUSED)
    cases += [
        _case(ids, "an unknown clinic is not found", "admin_a", 404, path(clinic_id=uuid.uuid4()), key=None, body=_fixed(404)),
        _case(ids, "limit=0 is rejected", "admin_a", 422, path(limit=0), key=None, body=_fixed(422)),
        _case(ids, "a clinic id that is not a uuid is rejected", "admin_a", 422, path(clinic_id="nope"), key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, path(), None)[:4]


# ---- pick-lists and the upcoming visits (migration 0070) ---------------------------------------------------------------------------------------------------------------------------------------------------
PORTAL_GRIEVANCE_CATEGORIES = PORTAL + "/grievance-categories"
BG_REASONS = BREAK_GLASS + "/reasons"
BG_MINE = BREAK_GLASS + "/mine"
VISITS_UPCOMING = VISITS + "/upcoming"


def portal_grievance_categories_cases(ids) -> list[tuple]:
    cases = [_case(ids, "an active patient account reads the list", "patient", 200, PORTAL_GRIEVANCE_CATEGORIES, key=None, body=lambda b: b == {"categories": ["demo_category"]})]
    cases += [_case(ids, f"{p} ({ids.people[p][2]}) is refused", p, 403, PORTAL_GRIEVANCE_CATEGORIES, key=None, body=NOT_A_PATIENT) for p in _non_patients(ids)]
    cases.append(_case(ids, "valid user with no iam account is refused", bearer(mint(ids.no_account)), 403, PORTAL_GRIEVANCE_CATEGORIES, key=None, body=NOT_A_PATIENT))
    return cases + _unauthenticated(ids, PORTAL_GRIEVANCE_CATEGORIES, None)[:4]


def break_glass_reasons_cases(ids) -> list[tuple]:
    path = lambda **o: BG_REASONS + "?" + "&".join(f"{k}={v}" for k, v in {"clinic_id": ids.clinic_a, **o}.items())
    cases = _console_clinic_cases(ids, path, allowed=("front_desk_a", "nurse_a", "doctor_a", "admin_a", "multi"), ok_status=200, ok_body=lambda b: b == {"reasons": ["demo_reason"]}, visible_refused=("business_owner_a", "auditor_a"))
    cases += [
        _case(ids, "an unknown clinic is not found", "admin_a", 404, path(clinic_id=uuid.uuid4()), key=None, body=_fixed(404)),
        _case(ids, "a clinic id that is not a uuid is rejected", "admin_a", 422, path(clinic_id="nope"), key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, path(), None)[:4]


def break_glass_mine_cases(ids) -> list[tuple]:
    path = lambda **o: BG_MINE + "?" + "&".join(f"{k}={v}" for k, v in {"clinic_id": ids.clinic_a, **o}.items())
    ok = lambda b: set(b) == {"grants"} and all(set(g) == {"grant_id", "patient_id", "reason_code", "opened_at", "expires_at"} for g in b["grants"])
    cases = _console_clinic_cases(ids, path, allowed=("front_desk_a", "nurse_a", "doctor_a", "multi"), ok_status=200, ok_body=ok, visible_refused=("admin_a", "business_owner_a", "auditor_a"))
    cases += [
        _case(ids, "the holder sees the seeded open grants", "nurse_a", 200, path(), key=None, body=lambda b: len(b["grants"]) >= 2),
        _case(ids, "someone else sees none of them", "doctor_a", 200, path(), key=None, body=lambda b: b == {"grants": []}),
        _case(ids, "an unknown clinic is not found", "nurse_a", 404, path(clinic_id=uuid.uuid4()), key=None, body=_fixed(404)),
    ]
    return cases + _unauthenticated(ids, path(), None)[:4]


def visits_upcoming_cases(ids) -> list[tuple]:
    path = lambda **o: VISITS_UPCOMING + "?" + "&".join(f"{k}={v}" for k, v in {"clinic_id": ids.clinic_a, **o}.items())
    ok = lambda b: set(b) == {"visits"} and all(set(v) == {"visit_id", "patient_id", "visit_type_id", "starts_at", "ends_at"} for v in b["visits"])
    cases = _console_clinic_cases(ids, path, allowed=("nurse_a", "doctor_a", "multi"), ok_status=200, ok_body=ok, visible_refused=("front_desk_a", "admin_a", "business_owner_a", "auditor_a"))
    cases += [
        _case(ids, "an unknown clinic is not found", "nurse_a", 404, path(clinic_id=uuid.uuid4()), key=None, body=_fixed(404)),
        _case(ids, "limit=0 is rejected", "nurse_a", 422, path(limit=0), key=None, body=_fixed(422)),
    ]
    return cases + _unauthenticated(ids, path(), None)[:4]


MATRIX: dict[tuple[str, str], Callable] = {
    ("GET", "/v1/iam/me"): me_cases,
    ("POST", HOLD): lambda ids: slot_write_cases(ids, "hold"),
    ("POST", BOOK): lambda ids: slot_write_cases(ids, "book"),
    ("POST", CANCEL): cancel_cases,
    ("POST", JOIN): join_cases,
    ("GET", PREFIX + "/slots"): lambda ids: read_cases(ids, "slots"),
    ("GET", PREFIX + "/bookings"): lambda ids: read_cases(ids, "bookings"),
    ("GET", PREFIX + "/waitlist"): lambda ids: read_cases(ids, "waitlist"),
    ("GET", PREFIX + "/providers"): lambda ids: reference_cases(ids, "providers"),
    ("GET", PREFIX + "/locations"): lambda ids: reference_cases(ids, "locations"),
    ("GET", PREFIX + "/visit-types"): lambda ids: reference_cases(ids, "visit_types"),
    ("GET", PREFIX + "/priority-levels"): lambda ids: reference_cases(ids, "priority_levels"),
    ("POST", INGEST): ingest_cases,
    ("POST", MERGES): merge_propose_cases,
    ("POST", APPROVE): merge_approve_cases,
    ("POST", UNMERGE): merge_unmerge_cases,
    ("POST", CONFIRM): patient_confirm_cases,
    ("POST", REVOKE): patient_revoke_cases,
    ("GET", TASKS): review_task_list_cases,
    ("GET", TASK): review_task_open_cases,
    ("POST", TASK_ACK): review_task_acknowledge_cases,
    ("POST", TASK_RESOLVE): review_task_resolve_cases,
    ("GET", OFFER_DRAFT): offer_draft_cases,
    ("POST", OFFER_SEND): offer_send_cases,
    ("POST", DOC_BEGIN): document_begin_cases,
    ("POST", DOC_REGISTER): document_register_cases,
    ("GET", REFERRALS): referral_list_cases,
    ("GET", REFERRAL): referral_open_cases,
    ("GET", DUPLICATES): duplicate_list_cases,
    ("GET", MERGE_LIST): merge_list_cases,
    ("GET", MERGE_OPEN): merge_open_cases,
    ("GET", AUDIT): audit_reader_cases,
    ("GET", HOLDS): hold_list_cases,
    ("POST", BACKFILLS): backfill_start_cases,
    ("GET", BACKFILLS + "/{slot_id}"): backfill_get_cases,
    ("POST", BACKFILLS + "/{slot_id}/decide"): backfill_decide_cases,
    ("POST", BACKFILLS + "/{slot_id}/reply"): backfill_reply_cases,
    ("GET", CONSOLE_ALERTS): console_alerts_cases,
    ("POST", CONSOLE_ACK): console_ack_cases,
    ("GET", CONSOLE_STATS): console_stats_cases,
    ("GET", CONSOLE_COST): console_cost_cases,
    ("GET", CONSOLE_REASONS): console_reason_codes_cases,
    ("GET", CARE_GAPS): care_gap_panel_cases,
    ("POST", CARE_BARRIERS): care_barrier_record_cases,
    ("POST", CARE_BARRIER_RESOLVE): care_barrier_resolve_cases,
    ("GET", VOICE_PREF): voice_preference_get_cases,
    ("PUT", VOICE_PREF): voice_preference_put_cases,
    ("POST", VOICE_SESSIONS): voice_session_cases,
    ("POST", PORTAL_REGISTER): portal_register_cases,
    ("GET", PORTAL_ME): portal_me_cases,
    ("GET", PORTAL_CONSENTS): portal_consents_get_cases,
    ("POST", PORTAL_CONSENTS): portal_consents_post_cases,
    ("POST", PORTAL_ACTIVATE): portal_activate_cases,
    ("POST", PORTAL_CLAIMS): portal_claim_cases,
    ("POST", CARE_PLANS): care_plan_create_cases,
    ("GET", CARE_PLANS): care_plan_list_cases,
    ("POST", CARE_PLAN_CONFIRM): care_plan_confirm_cases,
    ("POST", RESULTS): result_record_cases,
    ("GET", AUTONOMY_HISTORY): autonomy_history_cases,
    ("POST", NOTES): clinical_note_write_cases,
    ("GET", NOTES): clinical_note_list_cases,
    ("POST", RECOVERY): staff_recovery_request_cases,
    ("GET", RECOVERY): staff_recovery_list_cases,
    ("POST", RECOVERY + "/{request_id}/approve"): staff_recovery_approve_cases,
    ("POST", RECOVERY + "/{request_id}/cancel"): staff_recovery_cancel_cases,
    ("POST", RECOVERY + "/{request_id}/complete"): staff_recovery_complete_cases,
    ("POST", PORTAL_GRIEVANCES): portal_grievance_file_cases,
    ("GET", PORTAL_GRIEVANCES): portal_grievance_list_cases,
    ("GET", PORTAL_MY_DATA): portal_my_data_cases,
    ("GET", STAFF_GRIEVANCES): staff_grievance_list_cases,
    ("POST", GRIEVANCE_STATUS): staff_grievance_status_cases,
    ("POST", BREAK_GLASS): break_glass_open_cases,
    ("GET", BREAK_GLASS + "/{grant_id}/messages"): break_glass_read_cases,
    ("GET", BREAK_GLASS): break_glass_list_cases,
    ("GET", PORTAL_GRIEVANCE_CATEGORIES): portal_grievance_categories_cases,
    ("GET", BG_REASONS): break_glass_reasons_cases,
    ("GET", BG_MINE): break_glass_mine_cases,
    ("GET", VISITS_UPCOMING): visits_upcoming_cases,
    ("POST", BREAK_GLASS + "/{grant_id}/review"): break_glass_review_cases,
    ("GET", PORTAL_CLAIM_LIST): portal_claim_list_cases,
    ("POST", PORTAL_CLAIM_RESOLVE): portal_claim_resolve_cases,
    ("POST", VISITS + "/{visit_id}/readiness/start"): visit_readiness_start_cases,
    ("GET", VISITS + "/{visit_id}/readiness"): visit_readiness_get_cases,
    ("POST", VISITS + "/{visit_id}/readiness/reply"): visit_readiness_reply_cases,
    ("POST", AUTONOMY_PROPOSALS): autonomy_propose_cases,
    ("GET", AUTONOMY_PROPOSALS): autonomy_proposals_list_cases,
    ("POST", AUTONOMY_RESOLVE): autonomy_resolve_cases,
    ("GET", RULES): rules_list_cases,
    ("POST", RULES): rule_author_cases,
    ("POST", RULE_DECISION): rule_decision_cases,
    ("POST", RULE_WITHDRAW): rule_withdraw_cases,
    ("GET", PORTAL_PROFILE): portal_profile_get_cases,
    ("GET", VOICE_CONSENT_NOTICE): voice_consent_notice_cases,
    ("PUT", PORTAL_PROFILE): portal_profile_put_cases,
    ("GET", PORTAL_NOTIFICATIONS): portal_notifications_list_cases,
    ("POST", PORTAL_NOTIFICATIONS + "/{notification_id}/read"): portal_notification_read_cases,
    ("POST", PORTAL_REQUESTS): portal_request_file_cases,
    ("GET", PORTAL_REQUESTS): portal_request_list_cases,
    ("GET", STAFF_PATIENT_REQUESTS): staff_patient_request_list_cases,
    ("POST", STAFF_PATIENT_REQUESTS + "/{request_id}/status"): staff_patient_request_status_cases,
    ("POST", MEDICATIONS): medication_record_cases,
    ("GET", MEDICATIONS): medication_list_cases,
    ("GET", AUTONOMY): autonomy_get_cases,
    ("POST", AUTONOMY): autonomy_set_cases,
    ("GET", DASHBOARD): dashboard_cases,
    ("POST", EPISODES): episode_create_cases,
    ("POST", EPISODE_RULE_DONE): episode_rule_done_cases,
    ("POST", SAMPLE_RECHECK): sample_recheck_cases,
    ("GET", VOICE_ADMIN + "/sessions"): voice_sessions_list_cases,
    ("GET", VOICE_ADMIN + "/sessions/{session_id}/transcript"): voice_transcript_read_cases,
    ("POST", VOICE_ADMIN + "/purge"): voice_purge_cases,
    ("GET", ACCOUNTS): staff_list_cases,
    ("POST", ACCOUNTS + "/invite"): staff_invite_cases,
    ("POST", ACCOUNTS + "/activate-me"): staff_activate_cases,
    ("POST", ACCOUNTS + "/{account_id}/roles"): staff_grant_cases,
    ("POST", ACCOUNTS + "/{account_id}/roles/revoke"): staff_revoke_cases,
    ("POST", ACCOUNTS + "/{account_id}/suspend"): staff_suspend_cases,
    ("POST", ACCOUNTS + "/{account_id}/reinstate"): staff_reinstate_cases,
    ("POST", ERASE_REQUEST): erase_request_cases,
    ("GET", ERASURES): erasure_list_cases,
    ("GET", ERASURES + "/reasons"): erasure_reasons_cases,
    ("POST", ERASURES + "/{request_id}/approve"): erase_approve_cases,
    ("POST", ERASURES + "/{request_id}/cancel"): erase_cancel_cases,
    ("GET", OFFERS): offer_list_cases,
    ("POST", OFFER_DELIVERED): offer_delivered_cases,
    ("POST", PREFIX + "/locations"): setup_create_cases("locations"),
    ("POST", PREFIX + "/providers"): setup_create_cases("providers"),
    ("POST", PREFIX + "/visit-types"): setup_create_cases("visit-types"),
    ("POST", PREFIX + "/slots"): setup_create_cases("slots"),
    ("POST", HOLD_RELEASE): hold_release_cases,
    ("POST", PATIENT_SEARCH): patient_search_cases,
    ("GET", PATIENT_STATUS): patient_status_cases,
    ("GET", PATIENT_VIEW): patient_view_cases,
    ("POST", PATIENT_LABELS): patient_labels_cases,
    ("GET", READ): message_read_cases,
    ("GET", THREADS): thread_list_cases,
    ("GET", THREAD_HEADERS): thread_headers_cases,
    ("POST", RUN_REFERRAL): referral_run_cases,
    ("POST", REVIEW_REFERRAL): referral_review_cases,
    ("GET", REVIEW_VIEW): referral_review_view_cases,
    ("POST", INTERPRET): interpret_reply_cases,
    ("POST", RECORD_REPLY): record_reply_cases,
}


# ---- tests -----------------------------------------------------------------------------------------


# Routes whose role matrix runs in a DEDICATED module because their rows are append-only (msg.messages, msg.screenings, audit.log) and
# so cannot be committed and cleaned up by this module's committed-fixture harness. That module drives the same app over HTTP with real
# signed tokens inside one never-committed transaction. Listing a route here is not an exemption: the dedicated file must exist and
# must cover the route (see test_separately_tested_routes_have_a_dedicated_matrix_that_covers_them).
SEPARATE_HARNESS: dict[tuple[str, str], str] = {
    ("POST", "/v1/staff/messages/{message_id}/screen"): "test_api_screen_message_live.py",
}


# Routes tested in TWO halves: their refusal paths that need no stored message are in MATRIX above (committed, real pooler role), and their
# topic-dependent paths are in a dedicated never-committed module because they read append-only msg rows. Unlike SEPARATE_HARNESS, a split
# route MUST also be in MATRIX, and the dedicated half must cover every required role on every topic.
SPLIT_HARNESS: dict[tuple[str, str], str] = {
    ("GET", READ): "test_api_read_message_live.py",
    ("GET", THREADS): "test_api_read_message_live.py",
    ("GET", THREAD_HEADERS): "test_api_read_message_live.py",
    # Both Front Door run routes: the committed half above (random document ids: 401, 404, 422) and the dedicated half (existing documents, the worker, the whole run).
    ("POST", RUN_REFERRAL): "test_api_referrals_live.py",
    ("POST", REVIEW_REFERRAL): "test_api_referrals_live.py",
    # Reply interpretation: the committed half above (random message ids) and the dedicated half (real encrypted messages, the rules first, the fake model) in the screening harness.
    ("POST", INTERPRET): "test_api_screen_message_live.py",
    # Recording a reply the patient gave to the front desk: the committed half above (a random patient: 401, 403, 404, 422) and the dedicated half (a real patient, encrypted, the rules first, the replay, the audit row).
    ("POST", RECORD_REPLY): "test_api_screen_message_live.py",
}


def test_every_route_is_in_the_matrix(app):
    """Rule 8: a route with no role-matrix case is not done."""
    # The generated OpenAPI document lists every served operation, including routers added by include_router.
    served = {(m.upper(), path) for path, ops in app.openapi()["paths"].items() if path.startswith("/v1") for m in ops}
    covered = set(MATRIX) | set(SEPARATE_HARNESS)
    assert served == covered, f"routes without a matrix case: {served - covered}; stale cases: {covered - served}"
    assert not set(MATRIX) & set(SEPARATE_HARNESS), "a route is either in MATRIX or in SEPARATE_HARNESS, not both"
    assert set(SPLIT_HARNESS) <= set(MATRIX), "a split route needs its committed half in MATRIX"
    assert not set(SPLIT_HARNESS) & set(SEPARATE_HARNESS), "a route is split or separately tested, not both"


# Every kind of identity the main matrix covers must also be exercised by a separately tested route: each staff role, a nurse here who is a
# doctor elsewhere, another clinic's staff, a queue worker, a patient, a guardian, and invited / suspended staff accounts.
REQUIRED_SEPARATE_ROLES = {
    "nurse_a", "doctor_a", "fd_a", "worker_a", "admin_a", "auditor_a", "owner_a", "nurse_b", "fd_b", "multi", "patient", "guardian",
    "invited", "suspended",
}


def test_separately_tested_routes_have_a_dedicated_matrix_that_covers_them():
    """Not a text search: the dedicated module is IMPORTED, it must export the roles it covers, and the test that drives them must be a real
    parametrized test function over exactly those roles, so commenting the tests out or emptying them fails here."""
    import importlib

    for (method, path), filename in SEPARATE_HARNESS.items():
        module = importlib.import_module(f"tests.{filename.removesuffix('.py')}")
        roles = module.COVERED[(method, path)]
        assert REQUIRED_SEPARATE_ROLES <= set(roles), f"{filename} is missing roles: {REQUIRED_SEPARATE_ROLES - set(roles)}"
        driver = module.test_who_can_screen_which_topic
        parametrized = [m for m in getattr(driver, "pytestmark", []) if m.name == "parametrize"]
        assert parametrized and list(parametrized[0].args[1]) == list(roles), "the driving test must be parametrized over every covered role"
        for needed in ("test_authentication_comes_first_for_every_kind_of_bad_credential", "test_the_idempotency_key_and_the_path_are_validated",
                       "test_a_replay_with_the_same_key_returns_the_original_and_a_new_key_is_a_no_op_not_a_second_task"):
            assert callable(getattr(module, needed, None)), f"{filename} no longer defines {needed}"


def test_split_routes_have_both_halves_and_the_dedicated_half_covers_every_role_on_every_topic():
    """Imports the dedicated module: it must export the grid it covers (every required role tried on all three topics), and the test that
    drives the grid must be a real parametrized function over exactly those roles, so emptying or commenting out either half fails here."""
    import importlib

    for (method, path), filename in SPLIT_HARNESS.items():
        assert (method, path) in MATRIX and callable(MATRIX[(method, path)]), "the committed half is missing"
        module = importlib.import_module(f"tests.{filename.removesuffix('.py')}")
        grid = module.COVERED_GRID[(method, path)]
        assert REQUIRED_SEPARATE_ROLES <= set(grid), f"{filename} is missing roles: {REQUIRED_SEPARATE_ROLES - set(grid)}"
        # A module declares its own axis (GRID_AXIS), its driving test (GRID_DRIVER) and the tests that must exist (GRID_REQUIRED_TESTS). One that declares none is the
        # message-read module: three topics, its own driver and required tests.
        axis = tuple(getattr(module, "GRID_AXIS", ())) or tuple(module.TOPICS)
        if not hasattr(module, "GRID_AXIS"):
            assert set(module.TOPICS) == {"care", "appointments", "safety"}
        driver_name = getattr(module, "GRID_DRIVER", "test_who_can_read_which_topic")
        required_tests = getattr(module, "GRID_REQUIRED_TESTS", (
            "test_every_successful_read_writes_exactly_one_audit_row_with_ids_only", "test_refused_reads_write_no_audit_row",
            "test_an_erased_message_is_410_for_those_who_can_see_it_and_404_for_those_who_cannot",
            "test_authentication_comes_first_for_every_kind_of_bad_credential"))
        assert all(set(values) == set(axis) for values in grid.values()), "every role must be tried on every axis value"
        driver = getattr(module, driver_name)
        parametrized = [m for m in getattr(driver, "pytestmark", []) if m.name == "parametrize"]
        assert parametrized and list(parametrized[0].args[1]) == list(grid), "the driving test must be parametrized over every covered role"
        for needed in required_tests:
            assert callable(getattr(module, needed, None)), f"{filename} no longer defines {needed}"


@pytest.mark.parametrize(("method", "path"), sorted(MATRIX))
def test_role_matrix(client, ids, method, path):
    check_route_matrix(client, method, path, MATRIX[(method, path)](ids))


# ---- every staff route demands a verified second factor (review decision 2026-11-04; careflow.auth.mfa_failure) -------------------------------------------------------


def _filled(path: str) -> str:
    return re.sub(r"\{[^}]+\}", lambda _m: str(uuid.uuid4()), path)


PASSWORD_ONLY = [
    {"aal": None, "amr": None},  # no second factor at all
    {"aal": "aal1", "amr": [{"method": "totp", "timestamp": 1}]},  # aal1 even with a totp entry
    {"aal": "aal2", "amr": [{"method": "password", "timestamp": 1}]},  # aal2 but no totp entry
    {"aal": "aal2", "amr": ["totp"]},  # legacy string amr: not the verified shape
]


@pytest.mark.parametrize(("method", "path"), sorted(k for k in MATRIX if k[1] != "/v1/iam/me" and not k[1].startswith("/v1/portal/")))  # patient routes take no staff second factor: tests/test_mfa.py pins that exemption
def test_every_staff_route_refuses_a_session_without_a_verified_second_factor(client, ids, method, path):
    sub = ids.people["front_desk_a"][0]
    for over in PASSWORD_ONLY:
        r = client.request(method, _filled(path), headers=bearer(mint(sub, **over)), json={} if method == "POST" else None)
        assert r.status_code == 401, (method, path, over, r.status_code)
        assert r.json() in ({"detail": "multi-factor sign-in required"}, {"detail": "step-up required"}), (method, path, r.text)  # the merge routes' own step-up refusal may come first
        assert r.headers["www-authenticate"] in {"mfa", "step-up"}


def test_me_alone_is_open_to_a_password_only_session(client, ids):
    """The console needs /v1/iam/me to find out what to enrol, so it is the one route exempt from the second-factor rule."""
    r = client.get("/v1/iam/me", headers=bearer(mint(ids.people["front_desk_a"][0], aal=None, amr=None)))
    assert r.status_code == 200


def test_the_harness_catches_a_route_that_lets_everyone_in(ids):
    """The proof the harness has teeth: an unprotected route must make check_route_matrix fail."""
    leaky = FastAPI()

    @leaky.get("/v1/iam/me")
    async def open_door():
        return {"account_id": str(uuid.uuid4()), "account_type": "staff", "status": "active", "roles": []}

    with TestClient(leaky) as c, pytest.raises(AssertionError, match="expected 401, got 200"):
        check_route_matrix(c, "GET", "/v1/iam/me", me_cases(ids))


def test_a_caller_sees_only_their_own_grants_even_if_rls_would_let_an_admin_read_more(client, ids):
    """RLS lets a clinic admin SELECT every grant in their clinic; /me must still return only their own."""
    body = client.get("/v1/iam/me", headers=bearer(mint(ids.people["admin_a"][0]))).json()
    assert body["roles"] == [{"clinic_id": str(ids.clinic_a), "role": "admin", "clinic_name": "Matrix A", "clinic_origin": "test"}]


def test_a_multi_clinic_account_gets_exactly_its_own_grants(client, ids):
    body = client.get("/v1/iam/me", headers=bearer(mint(ids.people["multi"][0]))).json()
    assert {(g["clinic_id"], g["role"]) for g in body["roles"]} == {(str(ids.clinic_a), "nurse"), (str(ids.clinic_b), "doctor")}


def test_error_bodies_never_say_why_a_token_failed(client, ids):
    bodies = {
        client.get("/v1/iam/me", headers=bearer(mint(ids.people["nurse_a"][0], exp=int(time.time()) - 3600))).text,
        client.get("/v1/iam/me", headers=bearer(mint(ids.people["nurse_a"][0], key=ATTACKER))).text,
        client.get("/v1/iam/me", headers=bearer("garbage")).text,
        client.get("/v1/iam/me").text,
    }
    assert bodies == {'{"detail":"invalid or missing credentials"}'}
    assert client.get("/v1/iam/me").headers["www-authenticate"] == "Bearer"


POOL_TIMEOUT_SECONDS = 120  # the two forty-request pool tests below only: see _pool_test_app


def _pool_test_app():
    """An app for the two tests that send forty concurrent requests through a pool of five (+5 overflow). Their claim is isolation under a SMALL pool, so the pool stays 5+5 and all forty requests
    are kept; only the pool's WAIT is longer than the app's 30 seconds. On the shared CI database a request can wait that long for a connection when the other two shards are busy (a pool
    timeout, never a wrong row, failed this test twice in CI: runs 40521334349 and 40879700466). No app pool setting changes: this builds the test's own engine. The caller disposes it."""
    engine = make_app_engine(APP_URL or "", pool_size=5, max_overflow=5, pool_pre_ping=True, pool_timeout=POOL_TIMEOUT_SECONDS)
    settings = Settings(_env_file=None, supabase_url=PROJECT, database_url_app=APP_URL or "", step_up_max_age_seconds=STEP_UP_WINDOW, erase_reasons="entered_in_error")
    return create_app(settings, jwks=fake_jwks_client(), vault=TEST_VAULT, front_door=FrontDoorComponents(None, None, None, None, None, None, None, None, None), engine=engine), engine


class PoolDiagnostics:
    """WHAT THE FORTY REQUESTS WAIT ON (owner request 2026-10-29): per-request latency, how long a connection is held (checkout to checkin), and the pooler round trips (`select 1` and
    `set_config`, each in its own transaction). Observation only: it changes no behaviour and asserts nothing. One `POOL-DIAG` line per test is printed into the CI log."""

    def __init__(self, engine):
        from sqlalchemy import event

        self.latencies, self.holds, self.engine = [], [], engine
        pool = engine.sync_engine.pool

        @event.listens_for(pool, "checkout")
        def _out(dbapi_connection, record, proxy):
            record.info["t0"] = time.perf_counter()

        @event.listens_for(pool, "checkin")
        def _in(dbapi_connection, record):
            t0 = record.info.pop("t0", None)
            if t0 is not None:
                self.holds.append(time.perf_counter() - t0)

    def timed(self, coro_fn):
        async def run(*args, **kwargs):
            t0 = time.perf_counter()
            try:
                return await coro_fn(*args, **kwargs)
            finally:
                self.latencies.append(time.perf_counter() - t0)

        return run

    async def round_trips(self):
        from sqlalchemy import text

        out = {}
        async with self.engine.connect() as conn:
            for name, sql in (("select1", "select 1"), ("set_config", "select set_config('request.jwt.claims', '{}', true)")):
                samples = []
                for _ in range(5):
                    t0 = time.perf_counter()
                    async with conn.begin():
                        await conn.execute(text(sql))
                    samples.append(time.perf_counter() - t0)
                out[name] = samples
        return out

    @staticmethod
    def _stats(values):
        if not values:
            return "n=0"
        v = sorted(values)
        return f"n={len(v)} min={v[0]:.2f}s p50={v[len(v) // 2]:.2f}s p95={v[min(len(v) - 1, int(len(v) * 0.95))]:.2f}s max={v[-1]:.2f}s"

    def report(self, name, trips, capsys):
        with capsys.disabled():
            print(
                f"\nPOOL-DIAG {name} requests[{self._stats(self.latencies)}] connection_held[{self._stats(self.holds)}] "
                f"select1_txn[{self._stats(trips['select1'])}] set_config_txn[{self._stats(trips['set_config'])}]"
            )


def test_confirm_identity_answers_a_patient_of_another_clinic_exactly_as_it_answers_a_patient_that_does_not_exist(client, ids):
    """The route must never reveal that a patient exists in another clinic: the status, the body and the headers that matter are IDENTICAL for (1) a real patient of clinic A asked about
    by clinic B's front desk and (2) an id that exists nowhere. (The origin trigger's 23514 is only reachable below the route, which looks the patient up under row-level security first.)"""
    foreign = new_worker_patient(ids)  # clinic A
    ghost = uuid.uuid4()
    headers = {**bearer(mint(ids.people["front_desk_b"][0])), "X-Idempotency-Key": _new_key()}
    a = client.post(CONFIRM.format(patient_id=foreign), headers=headers)
    b = client.post(CONFIRM.format(patient_id=ghost), headers=headers)
    assert (a.status_code, a.json()) == (b.status_code, b.json()) == (404, {"detail": "not found"})
    volatile = {"content-length", "date"}
    assert {k: v for k, v in a.headers.items() if k.lower() not in volatile} == {k: v for k, v in b.headers.items() if k.lower() not in volatile}
    assert _run(_fetch("select count(*) as n from mpi.identity_confirmations where patient_id = $1", foreign))[0]["n"] == 0  # and nothing was written


def test_identity_never_leaks_between_pooled_requests(ids, capsys):
    """Forty concurrent requests from six identities over a pool of five connections: each must get exactly
    its own profile back, which only holds if the claims are transaction-local and set before any query."""
    keys = ["front_desk_a", "nurse_a", "admin_a", "auditor_a", "patient", "front_desk_b"]
    app, engine = _pool_test_app()
    diag = PoolDiagnostics(engine)

    async def run():
        try:
            async with app.router.lifespan_context(app):
                transport = httpx.ASGITransport(app=app)
                trips = await diag.round_trips()
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:

                    @diag.timed
                    async def call(i):
                        key = keys[i % len(keys)]
                        r = await http.get("/v1/iam/me", headers=bearer(mint(ids.people[key][0])))
                        return key, r.status_code, r.json()

                    done = await bounded(asyncio.gather(*(call(i) for i in range(40))), "the forty concurrent requests", seconds=2 * POOL_TIMEOUT_SECONDS)
                    diag.report("identity-never-leaks", trips, capsys)
                    return done
        finally:
            await engine.dispose()

    for key, status, body in _run(run()):
        assert status == 200, key
        assert body == expected_me(ids, key), key


# ---- scheduling routes: behaviour beyond the allow/deny matrix --------------------------------------------------


async def _fetch(sql, *args):
    conn = await asyncpg.connect((MIGRATOR or "").replace("+asyncpg", ""), statement_cache_size=0)
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


def _post(client, ids, who, path, json=None, key="new"):
    headers = {**bearer(mint(ids.people[who][0]))}
    if key is not None:
        headers["X-Idempotency-Key"] = _new_key() if key == "new" else key
    return client.post(path, headers=headers, json=json)


def test_a_write_is_committed_before_its_response_is_returned(client, ids):
    """The 200 must mean the row is durable: a separate connection sees it the moment the response arrives."""
    slot = ids.pools["extra"].pop()
    r = _post(client, ids, "front_desk_a", HOLD.format(slot_id=slot), {"duration_minutes": 10})
    assert r.status_code == 200
    (hold,) = _run(_fetch("select status, slot_id from sched.holds where id = $1", uuid.UUID(r.json()["hold_id"])))
    assert (hold["status"], hold["slot_id"]) == ("active", slot)
    (row,) = _run(_fetch("select status from sched.slots where id = $1", slot))
    assert row["status"] == "held"


def test_a_refused_write_changes_nothing(client, ids):
    slot = ids.pools["extra"].pop()
    for who, status in (("admin_a", 403), ("nurse_a", 404), ("front_desk_b", 404), ("auditor_a", 404)):
        assert _post(client, ids, who, HOLD.format(slot_id=slot), {"duration_minutes": 10}).status_code == status, who
        assert _post(client, ids, who, BOOK.format(slot_id=slot), {"patient_id": str(ids.patients_a[0])}).status_code == status, who
    assert _run(_fetch("select status from sched.slots where id = $1", slot))[0]["status"] == "open"
    assert _run(_fetch("select count(*) as n from sched.holds where slot_id = $1", slot))[0]["n"] == 0
    assert _run(_fetch("select count(*) as n from sched.bookings where slot_id = $1", slot))[0]["n"] == 0


def test_replay_returns_the_original_result_and_says_so(client, ids):
    slot, key = ids.pools["extra"].pop(), _new_key()
    first = _post(client, ids, "front_desk_a", BOOK.format(slot_id=slot), {"patient_id": str(ids.patients_a[0])}, key=key).json()
    again = _post(client, ids, "front_desk_a", BOOK.format(slot_id=slot), {"patient_id": str(ids.patients_a[0])}, key=key).json()
    assert (first["replayed"], again["replayed"]) == (False, True)
    assert {k: v for k, v in first.items() if k != "replayed"} == {k: v for k, v in again.items() if k != "replayed"}
    assert first["status"] == "confirmed"
    assert _run(_fetch("select count(*) as n from sched.bookings where slot_id = $1", slot))[0]["n"] == 1


def test_cancel_returns_candidates_by_clinical_priority_not_by_who_waited_longer(client, ids):
    slot = ids.pools["extra"].pop()
    booked, low, high = ids.patients_a[40], ids.patients_a[41], ids.patients_a[42]
    booking = _post(client, ids, "front_desk_a", BOOK.format(slot_id=slot), {"patient_id": str(booked)}).json()["booking_id"]
    for patient, level in ((low, "level_two"), (high, "level_one")):  # the lower-priority patient joins FIRST
        r = _post(client, ids, "front_desk_a", JOIN, {"patient_id": str(patient), "visit_type_id": str(ids.vt_a), "clinical_priority": level})
        assert r.status_code == 200 and r.json()["clinical_priority"] == level
    r = _post(client, ids, "front_desk_a", CANCEL.format(booking_id=booking))
    assert r.status_code == 200
    everyone = r.json()["candidates"]  # the visit type's whole queue, which other tests' waiting patients are also in
    assert [(c["rank"], c["joined_at"]) for c in everyone] == sorted((c["rank"], c["joined_at"]) for c in everyone)
    got = [c for c in everyone if c["patient_id"] in (str(high), str(low))]
    assert [(c["patient_id"], c["priority_code"], c["rank"]) for c in got] == [(str(high), "level_one", 1), (str(low), "level_two", 2)]
    # Informational only: the slot is open again and nobody was held, booked or contacted.
    assert _run(_fetch("select status from sched.slots where id = $1", slot))[0]["status"] == "open"
    both = [high, low]
    assert _run(_fetch("select count(*) as n from sched.bookings where patient_id = any($1)", both))[0]["n"] == 0
    assert _run(_fetch("select count(*) as n from sched.holds where patient_id = any($1)", both))[0]["n"] == 0
    assert {r["status"] for r in _run(_fetch("select status from sched.waitlist where patient_id = any($1)", both))} == {"waiting"}


def test_concurrent_writes_from_two_clinics_neither_leak_identity_nor_connections(app, ids):
    """Twenty concurrent holds over a pool of five connections: front desk of clinic A succeeds on every slot, front desk
    of clinic B is refused on every slot, and every connection is back in the pool afterwards."""
    slots = [ids.pools["extra"].pop() for _ in range(10)]

    async def run():
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:

                async def hold(who, slot):
                    r = await http.post(
                        HOLD.format(slot_id=slot), json={"duration_minutes": 5},
                        headers={**bearer(mint(ids.people[who][0])), "X-Idempotency-Key": _new_key()},
                    )
                    return who, r.status_code

                results = await bounded(
                    asyncio.gather(*(hold(w, s) for s in slots for w in ("front_desk_a", "front_desk_b"))), "the concurrent holds"
                )
                return results, app.state.engine.pool.checkedout()

    results, checked_out = _run(run())
    assert sorted(results) == sorted([("front_desk_a", 200)] * 10 + [("front_desk_b", 404)] * 10)
    assert checked_out == 0
    assert {r["status"] for r in _run(_fetch("select status from sched.slots where id = any($1)", slots))} == {"held"}


# ---- scheduling reads: behaviour beyond the allow/deny matrix -------------------------------------------------------


def _get(client, ids, who, path):
    return client.get(path, headers=bearer(mint(ids.people[who][0])))


def _window(start):
    """Query string that makes the slot starting at `start` the first row returned."""
    return f"{PREFIX}/slots?limit=1&starts_after={(start - timedelta(seconds=1)).isoformat().replace('+', '%2B')}"


def test_a_held_slot_shows_its_live_hold_and_an_expired_hold_shows_as_open(client, ids):
    slot = ids.pools["extra"].pop()
    window = _window(_run(_fetch("select starts_at from sched.slots where id = $1", slot))[0]["starts_at"])
    assert _post(client, ids, "front_desk_a", HOLD.format(slot_id=slot), {"duration_minutes": 10}).status_code == 200
    (item,) = _get(client, ids, "front_desk_a", window).json()["items"]
    assert (item["slot_id"], item["status"]) == (str(slot), "held") and item["hold_expires_at"] is not None
    # Let the hold lapse with no sweep having run: the listing must report the slot as bookable.
    _run(_fetch("update sched.holds set expires_at = created_at + interval '1 millisecond' where slot_id = $1 returning id", slot))
    (item,) = _get(client, ids, "front_desk_a", window).json()["items"]
    assert (item["slot_id"], item["status"], item["hold_expires_at"]) == (str(slot), "open", None)


def test_a_booked_slot_leaves_the_slot_list_and_a_cancelled_booking_leaves_the_booking_list(client, ids):
    slot = ids.pools["extra"].pop()
    window = _window(_run(_fetch("select starts_at from sched.slots where id = $1", slot))[0]["starts_at"])
    booking = _post(client, ids, "front_desk_a", BOOK.format(slot_id=slot), {"patient_id": str(ids.patients_a[0])}).json()["booking_id"]
    assert _get(client, ids, "front_desk_a", window).json()["items"][0]["slot_id"] != str(slot)
    listed = {b["booking_id"] for b in _get(client, ids, "nurse_a", f"{PREFIX}/bookings?limit=500").json()["items"]}
    assert booking in listed
    assert _post(client, ids, "front_desk_a", CANCEL.format(booking_id=booking)).status_code == 200
    listed = {b["booking_id"] for b in _get(client, ids, "nurse_a", f"{PREFIX}/bookings?limit=500").json()["items"]}
    assert booking not in listed
    assert _get(client, ids, "front_desk_a", window).json()["items"][0]["slot_id"] == str(slot)  # open again


def test_list_responses_carry_identifiers_only(client, ids):
    """Nothing in a list response is a name, date of birth or phone: only ids, times, status and rank."""
    allowed = {"slot_id", "clinic_id", "provider_id", "location_id", "visit_type_id", "starts_at", "ends_at", "status",
               "hold_expires_at", "booking_id", "patient_id", "created_at", "waitlist_id", "clinical_priority", "rank", "joined_at"}
    for route in ("slots", "bookings", "waitlist"):
        items = _get(client, ids, "doctor_a", f"{PREFIX}/{route}?limit=5").json()["items"]
        assert items, route
        for item in items:
            assert set(item) <= allowed, (route, set(item) - allowed)


def test_concurrent_reads_from_different_identities_neither_mix_rows_nor_leak_connections(ids, capsys):
    """Forty concurrent reads over a pool of five: clinic A readers see only clinic A rows, clinic B's front desk sees
    none, admin, auditor and a patient are refused, and every connection is back in the pool."""
    plan = [("front_desk_a", 200), ("nurse_a", 200), ("front_desk_b", 200), ("admin_a", 403), ("auditor_a", 403), ("patient", 403)]
    app, engine = _pool_test_app()
    diag = PoolDiagnostics(engine)

    async def run():
        try:
            async with app.router.lifespan_context(app):
                transport = httpx.ASGITransport(app=app)
                trips = await diag.round_trips()
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:

                    @diag.timed
                    async def read(i):
                        who, _ = plan[i % len(plan)]
                        route = ("slots", "bookings", "waitlist")[i % 3]
                        r = await http.get(f"{PREFIX}/{route}?limit=5", headers=bearer(mint(ids.people[who][0])))
                        return who, r.status_code, (r.json()["items"] if r.status_code == 200 else None)

                    done = await bounded(asyncio.gather(*(read(i) for i in range(40))), "the forty concurrent reads", seconds=2 * POOL_TIMEOUT_SECONDS)
                    diag.report("concurrent-reads", trips, capsys)
                    return done, app.state.engine.pool.checkedout()
        finally:
            await engine.dispose()

    results, checked_out = _run(run())
    assert checked_out == 0
    expected = dict(plan)
    for who, status, items in results:
        assert status == expected[who], who
        if who == "front_desk_b":
            assert items == []
        if who == "front_desk_a":
            assert all(i["clinic_id"] == str(ids.clinic_a) for i in items)


# ---- patient registration: behaviour beyond the allow/deny matrix ---------------------------------------------------------------------------


def _register(client, ids, person, *, who="front_desk_a", key="new", **over):
    return _post(client, ids, who, INGEST, {"clinic_id": str(ids.clinic_a), **person, **over}, key=key)


def test_registration_stores_only_ciphertext_and_tokens_never_the_plaintext(client, ids):
    person = _person()
    r = _register(client, ids, person)
    assert r.status_code == 200 and r.json()["outcome"] == "created"
    (row,) = _run(_fetch("select name_ct, dob_ct, phone_ct, name_key_version, origin from mpi.patients where id = $1", uuid.UUID(r.json()["patient_id"])))
    for column, value in (("name_ct", person["name"]), ("dob_ct", person["dob"]), ("phone_ct", "+91" + person["phone"])):
        assert value.encode() not in bytes(row[column]) and person["phone"].encode() not in bytes(row[column])
    assert TEST_VAULT.decrypt(bytes(row["name_ct"]), field="name", key_version=row["name_key_version"]) == person["name"]
    assert row["origin"] == "test"  # read from the clinic's own row, not from the request
    kinds = {t["kind"] for t in _run(_fetch("select kind from mpi.identity_tokens where patient_id = $1", uuid.UUID(r.json()["patient_id"])))}
    assert kinds == {"name_dob", "phone"}


def test_a_replay_returns_the_original_patient_and_writes_nothing_more(client, ids):
    person, key = _person(), _new_key()
    first, again = _register(client, ids, person, key=key).json(), _register(client, ids, person, key=key).json()
    assert (first["outcome"], again["outcome"]) == ("created", "replayed") and first["patient_id"] == again["patient_id"]
    assert _run(_fetch("select count(*) as n from mpi.patients where idempotency_key = $1", scoped_key(ids, "front_desk_a", key)))[0]["n"] == 1


def test_a_matched_person_creates_no_new_record_and_a_near_match_queues_a_pending_review(client, ids):
    person = _person()
    original = _register(client, ids, person).json()
    matched = _register(client, ids, person).json()
    assert (matched["outcome"], matched["patient_id"]) == ("matched", original["patient_id"])
    near = _register(client, ids, person, phone="9" + str(random.randrange(10**8, 10**9))).json()
    assert near["outcome"] == "review" and near["candidate_ids"] == [original["patient_id"]] and near["patient_id"] != original["patient_id"]
    (cand,) = _run(_fetch("select status, reason from mpi.match_candidates where patient_id = $1", uuid.UUID(near["patient_id"])))
    assert cand["status"] == "pending"


def test_a_refused_registration_writes_nothing(client, ids):
    person, key = _person(), _new_key()
    for who, status in (("nurse_a", 403), ("front_desk_b", 404), ("patient", 404), ("auditor_a", 403)):
        assert _register(client, ids, person, who=who, key=key).status_code == status, who
    assert _register(client, ids, person, key=key, dob="1990-13-45").status_code == 422
    assert _run(_fetch("select count(*) as n from mpi.patients where idempotency_key = $1", key))[0]["n"] == 0


def test_error_responses_and_logs_never_contain_the_person(client, ids, caplog):
    caplog.set_level("DEBUG")
    person = _person()
    attempts = [
        _register(client, ids, person, who="nurse_a"), _register(client, ids, person, who="front_desk_b"),
        _register(client, ids, person, dob="2999-01-01"), _register(client, ids, person, phone="12345"),
        _register(client, ids, person, key=None), _post(client, ids, "front_desk_a", INGEST, {"clinic_id": str(ids.clinic_a), **person, "extra": person["name"]}),
    ]
    assert all(r.status_code >= 400 for r in attempts)
    for r in attempts:
        assert not any(v in r.text for v in (person["name"], person["phone"], person["dob"])), r.status_code
    assert not any(v in caplog.text for v in (person["name"], person["phone"], person["dob"]))


def test_concurrent_registrations_of_the_same_person_converge_on_one_record(app, ids):
    person = _person()

    async def run():
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:

                async def register(_):
                    r = await http.post(
                        INGEST, json={"clinic_id": str(ids.clinic_a), **person},
                        headers={**bearer(mint(ids.people["front_desk_a"][0])), "X-Idempotency-Key": _new_key()},
                    )
                    return r.status_code, r.json()

                return await bounded(asyncio.gather(*(register(i) for i in range(8))), "the concurrent registrations")

    results = _run(run())
    assert {status for status, _ in results} == {200}
    outcomes = sorted(body["outcome"] for _, body in results)
    assert outcomes == ["created"] + ["matched"] * 7  # the token locks serialise them: exactly one creates
    assert len({body["patient_id"] for _, body in results}) == 1


# ---- merges: behaviour beyond allow/deny --------------------------------------------------------------------------------------------------


def _merge_post(client, headers, path, json=None, key="new"):
    h = {**headers}
    if key is not None:
        h["X-Idempotency-Key"] = _new_key() if key == "new" else key
    return client.post(path, headers=h, json=json)


def _propose_as_fd(client, ids, master, sub):
    return _merge_post(client, stepped(ids.people["front_desk_a"][0]), MERGES,
                       {"clinic_id": str(ids.clinic_a), "master_patient_id": str(master), "subordinate_patient_id": str(sub)})


def test_the_two_person_rule_end_to_end(client, ids):
    master, sub, _ = new_pair(ids)
    proposed = _propose_as_fd(client, ids, master, sub)
    assert proposed.status_code == 200
    merge_id = proposed.json()["merge_id"]
    same = _merge_post(client, stepped(ids.people["front_desk_a"][0]), APPROVE.format(merge_id=merge_id))
    nurse = _merge_post(client, stepped(ids.people["nurse_a"][0]), APPROVE.format(merge_id=merge_id))
    assert (same.status_code, same.json()) == (409, {"detail": "this merge cannot be carried out"})
    assert nurse.status_code == 403
    (row,) = _run(_fetch("select status, second_approved_by from mpi.merges where id = $1", uuid.UUID(merge_id)))
    assert (row["status"], row["second_approved_by"]) == ("awaiting_second_approval", None)  # neither refusal changed anything
    second = _merge_post(client, stepped(ids.fd2), APPROVE.format(merge_id=merge_id))
    assert (second.status_code, second.json()["status"]) == (200, "active")
    (row,) = _run(_fetch("select status, approved_by, second_approved_by from mpi.merges where id = $1", uuid.UUID(merge_id)))
    assert row["status"] == "active" and row["approved_by"] == ids.people["front_desk_a"][0] and row["second_approved_by"] == ids.fd2
    # each human step wrote exactly one audit row, as the person who took it, with ids only; the two refusals above wrote none
    audit = _run(_fetch("select action, actor_id, actor_role, resource_id, details from audit.log where resource_type = 'merge' and resource_id = $1 order by action", merge_id))
    assert [(r["action"], r["actor_id"], r["actor_role"]) for r in audit] == [
        ("merge.approve", ids.fd2, "front_desk"), ("merge.propose", ids.people["front_desk_a"][0], "front_desk"),
    ]
    for r in audit:
        details = without_scope(r["details"])
        assert set(details) <= {"master_patient_id", "subordinate_patient_id", "replayed"} and all(isinstance(v, (str, bool)) for v in details.values())


def test_unmerge_raises_one_nurse_task_for_an_active_link_and_none_for_a_cancelled_proposal(client, ids):
    active, _, _ = new_merge(ids, "active")
    awaiting, _, _ = new_merge(ids)
    fd = stepped(ids.people["front_desk_a"][0])
    out = _merge_post(client, fd, UNMERGE.format(merge_id=active)).json()
    cancelled = _merge_post(client, fd, UNMERGE.format(merge_id=awaiting)).json()
    assert out["review_task_id"] is not None and cancelled["review_task_id"] is None
    tasks = _run(_fetch("select kind, assigned_role, priority from agents.review_tasks where idempotency_key = any($1)",
                        [f"unmerge-review:{active}", f"unmerge-review:{awaiting}"]))
    assert [(t["kind"], t["assigned_role"], t["priority"]) for t in tasks] == [("unmerge_review", "nurse", "immediate")]


def test_a_request_that_is_not_stepped_up_writes_nothing(client, ids):
    master, sub, _ = new_pair(ids)
    awaiting, _, _ = new_merge(ids)
    for label, headers in not_stepped_variants(ids.people["front_desk_a"][0]):
        r1 = _merge_post(client, headers, MERGES, {"clinic_id": str(ids.clinic_a), "master_patient_id": str(master), "subordinate_patient_id": str(sub)})
        r2 = _merge_post(client, headers, APPROVE.format(merge_id=awaiting))
        r3 = _merge_post(client, headers, UNMERGE.format(merge_id=awaiting))
        assert [r.status_code for r in (r1, r2, r3)] == [401, 401, 401], label
        assert all(r.headers["www-authenticate"] == "step-up" for r in (r1, r2, r3)), label
    assert _run(_fetch("select count(*) as n from mpi.merges where master_patient_id = $1", master))[0]["n"] == 0
    (row,) = _run(_fetch("select status from mpi.merges where id = $1", awaiting))
    assert row["status"] == "awaiting_second_approval"


def test_a_patient_in_another_clinic_looks_exactly_like_one_that_does_not_exist(client, ids):
    master, _, _ = new_pair(ids)
    other = _propose_as_fd(client, ids, master, ids.patient_b)
    absent = _propose_as_fd(client, ids, master, uuid.uuid4())
    assert (other.status_code, other.json(), other.headers.get("content-length")) == (absent.status_code, absent.json(), absent.headers.get("content-length"))
    assert other.status_code == 404


def test_merge_responses_carry_ids_and_statuses_only(client, ids):
    master, sub, _ = new_pair(ids)
    r = _propose_as_fd(client, ids, master, sub)
    assert set(r.json()) == MERGE_KEYS and all(isinstance(v, (str, bool)) for v in r.json().values())


def test_the_step_up_check_is_off_the_database_path_when_the_window_is_unset(ids):
    """With no window configured the routes answer a visible 503 before touching the database, even for a stepped-up front desk token."""
    settings = Settings(_env_file=None, supabase_url=PROJECT, database_url_app=APP_URL or "", step_up_max_age_seconds=None)
    unconfigured = create_app(settings, jwks=fake_jwks_client(), vault=TEST_VAULT)
    master, sub, _ = new_pair(ids)
    with TestClient(unconfigured, raise_server_exceptions=False) as c:
        r = _merge_post(c, stepped(ids.people["front_desk_a"][0]), MERGES,
                        {"clinic_id": str(ids.clinic_a), "master_patient_id": str(master), "subordinate_patient_id": str(sub)})
    assert (r.status_code, r.json()) == (503, {"detail": "step-up re-authentication is not configured"})


def test_a_failing_audit_insert_rolls_the_decision_back_and_the_replay_of_a_real_key_still_succeeds(client, ids, monkeypatch):
    """Fail closed: an audit write that fails for any reason except a duplicate key makes the request fail and the decision (the confirmation row) does not commit."""
    from careflow import app as app_module

    patient = new_worker_patient(ids)
    fd = ids.people["front_desk_a"][0]

    async def broken(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(app_module, "write_audit", broken)
    failed = client.post(CONFIRM.format(patient_id=patient), headers={**bearer(mint(fd)), "X-Idempotency-Key": _new_key()})
    assert failed.status_code == 500
    assert _run(_fetch("select count(*) as n from mpi.identity_confirmations where patient_id = $1", patient))[0]["n"] == 0  # nothing committed
    monkeypatch.undo()
    ok = client.post(CONFIRM.format(patient_id=patient), headers={**bearer(mint(fd)), "X-Idempotency-Key": _new_key()})
    assert ok.status_code == 200 and ok.json()["replayed"] is False


def test_a_revoke_writes_one_event_one_audit_row_and_with_the_task_settings_configured_one_nurse_task_with_ids_only(client, ids):
    from careflow import app as app_module
    from careflow.mpi import RevokeTaskSettings

    patient = new_confirmed_patient(ids)
    fd = ids.people["front_desk_a"][0]
    # test inputs only: stand-ins for the clinician-owned task settings, not recommendations
    client.app.dependency_overrides[app_module.revoke_task_settings] = lambda: RevokeTaskSettings(_env_file=None, priority="standard", assigned_role="nurse")
    try:
        key = _new_key()
        ok = client.post(REVOKE.format(patient_id=patient), headers={**bearer(mint(fd)), "X-Idempotency-Key": key}, json=REASON)
        assert ok.status_code == 200 and ok.json() == {"patient_id": str(patient), "confirmed": False, "replayed": False, "review_task": "raised", "admin_alert": "not_configured"}
        again = client.post(REVOKE.format(patient_id=patient), headers={**bearer(mint(fd)), "X-Idempotency-Key": key}, json=REASON)
        assert again.json()["replayed"] is True and again.json()["review_task"] == "on_record"
    finally:
        client.app.dependency_overrides.pop(app_module.revoke_task_settings, None)
    events = _run(_fetch("select kind, reason, confirmed_by from mpi.identity_confirmations where patient_id = $1 order by seq", patient))
    assert [(e["kind"], e["reason"]) for e in events] == [("confirm", None), ("revoke", "wrong_person")] and events[1]["confirmed_by"] == fd
    tasks = _run(_fetch("select kind, priority, assigned_role, provenance from agents.review_tasks where provenance ->> 'patient_id' = $1", str(patient)))
    assert len(tasks) == 1 and (tasks[0]["kind"], tasks[0]["priority"], tasks[0]["assigned_role"]) == ("identity_confirmation_revoked", "standard", "nurse")
    provenance = json.loads(tasks[0]["provenance"]) if isinstance(tasks[0]["provenance"], str) else tasks[0]["provenance"]
    assert set(provenance) == {"patient_id", "revoke_event_id", "reason", "confirmed_at", "record_created_by"}
    audit = "select actor_id, actor_role, details, clinic_id from audit.log where resource_type = 'patient' and resource_id = $1 and action = 'patient.revoke_identity'"
    (row,) = _run(_fetch(audit, str(patient)))  # one row: the replay wrote none
    details = without_scope(row["details"])
    assert (row["actor_id"], row["actor_role"], row["clinic_id"]) == (fd, "front_desk", ids.clinic_a)
    assert details == {"reason": "wrong_person", "replayed": False, "review_task": "raised", "admin_alert": "not_configured"}


def test_with_the_vocabulary_set_a_medication_is_recorded_replayed_superseded_and_listed_and_a_code_outside_the_vocabulary_is_refused(client, ids):
    from careflow import app as app_module

    nurse = ids.people["nurse_a"][0]
    patient = ids.patients_a[44]
    client.app.dependency_overrides[app_module.medication_codes] = lambda: frozenset({"DEMO-M1"})  # a test stand-in for the clinic's vocabulary, not a recommendation
    try:
        body = {"clinic_id": str(ids.clinic_a), "patient_id": str(patient), "code": "DEMO-M1", "verified": True, "valid_from": "2026-01-01T00:00:00Z"}
        key = _new_key()
        ok = client.post(MEDICATIONS, headers={**bearer(mint(nurse)), "X-Idempotency-Key": key}, json=body)
        assert ok.status_code == 201 and ok.json()["created"] is True
        first = ok.json()["medication_id"]
        again = client.post(MEDICATIONS, headers={**bearer(mint(nurse)), "X-Idempotency-Key": key}, json=body)
        assert again.status_code == 201 and again.json() == {"medication_id": first, "created": False}
        stop = client.post(MEDICATIONS, headers={**bearer(mint(nurse)), "X-Idempotency-Key": _new_key()}, json={**body, "supersedes_id": first, "valid_to": "2026-02-01T00:00:00Z", "valid_from": "2026-01-01T00:00:00Z"})
        assert stop.status_code == 201
        twice = client.post(MEDICATIONS, headers={**bearer(mint(nurse)), "X-Idempotency-Key": _new_key()}, json={**body, "supersedes_id": first})
        assert twice.status_code == 409  # a row is superseded at most once
        outside = client.post(MEDICATIONS, headers={**bearer(mint(nurse)), "X-Idempotency-Key": _new_key()}, json={**body, "code": "NOT-IN-LIST"})
        assert outside.status_code == 422
        listed = client.get(MEDICATIONS + f"?clinic_id={ids.clinic_a}&patient_id={patient}", headers=bearer(mint(nurse)))
        assert listed.status_code == 200 and [(m["superseded"], m["verified"]) for m in listed.json()["medications"]] == [(False, True), (True, True)]
    finally:
        client.app.dependency_overrides.pop(app_module.medication_codes, None)
    audits = _run(_fetch("select details from audit.log where action = 'medication.record' and resource_id = $1", first))
    assert len(audits) == 1 and set(without_scope(audits[0]["details"])) == {"verified", "supersedes"}  # ids and flags only

def test_the_rules_list_says_authored_by_you_only_to_the_author_and_shows_no_author_id(client, ids):
    doctor, admin = ids.people["doctor_a"][0], ids.people["admin_a"][0]
    made = client.post(RULES, headers={**bearer(mint(doctor)), "X-Idempotency-Key": _new_key()}, json={"clinic_id": str(ids.clinic_a), "rule_code": "zz_flag", "phrases": ["zzflag"]})  # a meaningless marker
    assert made.status_code == 201
    rule_id = made.json()["rule_id"]
    for who, expected in ((doctor, True), (admin, False)):
        listed = client.get(RULES + f"?clinic_id={ids.clinic_a}", headers=bearer(mint(who)))
        assert listed.status_code == 200
        (mine,) = [r for r in listed.json()["rules"] if r["rule_id"] == rule_id]
        assert mine["authored_by_you"] is expected and "authored_by" not in mine and str(doctor) not in listed.text
        assert mine["status"] == "draft_unreviewed" and set(mine) >= {"rule_id", "rule_code", "version_id", "status", "phrases", "token_sets", "authored_by_you", "decisions"}

def test_with_the_task_settings_unset_the_revoke_is_recorded_and_says_no_task_was_raised(client, ids):
    patient = new_confirmed_patient(ids)
    fd = ids.people["front_desk_a"][0]
    done = client.post(REVOKE.format(patient_id=patient), headers={**bearer(mint(fd)), "X-Idempotency-Key": _new_key()}, json=REASON)
    assert done.status_code == 200 and done.json()["review_task"] == "not_configured"
    assert _run(_fetch("select count(*) as n from mpi.identity_confirmations where patient_id = $1 and kind = 'revoke'", patient))[0]["n"] == 1
    assert _run(_fetch("select count(*) as n from agents.review_tasks where provenance ->> 'patient_id' = $1", str(patient)))[0]["n"] == 0
    (row,) = _run(_fetch("select details from audit.log where resource_id = $1 and action = 'patient.revoke_identity'", str(patient)))
    details = without_scope(row["details"])
    assert details["review_task"] == "not_configured"  # the audit row says so too


def test_with_the_alert_reasons_listed_a_revoke_raises_one_admin_alert_bound_to_the_event_and_the_task_can_go_to_the_supervisor_role(client, ids):
    from careflow import app as app_module
    from careflow.mpi import RevokeTaskSettings

    patient = new_confirmed_patient(ids)
    fd = ids.people["front_desk_a"][0]
    # test inputs only: stand-ins for the clinician-owned settings, not recommendations
    client.app.dependency_overrides[app_module.revoke_task_settings] = lambda: RevokeTaskSettings(_env_file=None, priority="standard", assigned_role="front_desk_supervisor", alert_reasons="wrong_person")
    try:
        key = _new_key()
        ok = client.post(REVOKE.format(patient_id=patient), headers={**bearer(mint(fd)), "X-Idempotency-Key": key}, json=REASON)
        assert ok.status_code == 200 and ok.json() == {"patient_id": str(patient), "confirmed": False, "replayed": False, "review_task": "raised", "admin_alert": "raised"}
        again = client.post(REVOKE.format(patient_id=patient), headers={**bearer(mint(fd)), "X-Idempotency-Key": key}, json=REASON)
        assert again.json()["admin_alert"] == "on_record"
        other = new_confirmed_patient(ids)
        mistaken = client.post(REVOKE.format(patient_id=other), headers={**bearer(mint(fd)), "X-Idempotency-Key": _new_key()}, json={"reason": "mistaken_click"})
        assert mistaken.json()["admin_alert"] == "not_for_this_reason"
    finally:
        client.app.dependency_overrides.pop(app_module.revoke_task_settings, None)
    (event,) = _run(_fetch("select id::text as id from mpi.identity_confirmations where patient_id = $1 and kind = 'revoke'", patient))
    alerts = _run(_fetch("select class, audience_role, alert_count, ref from ops.alerts where ref = $1", event["id"]))
    assert [(a["class"], a["audience_role"], a["alert_count"]) for a in alerts] == [("identity_revoked_suspect", "admin", 1)]  # one alert, for an admin, carrying the event id only
    assert _run(_fetch("select count(*) as n from ops.alerts where class = 'identity_revoked_suspect' and ref in (select id::text from mpi.identity_confirmations where patient_id = $1)", other))[0]["n"] == 0
    tasks = _run(_fetch("select assigned_role from agents.review_tasks where provenance ->> 'patient_id' = $1", str(patient)))
    assert [t["assigned_role"] for t in tasks] == ["front_desk_supervisor"]
    admin_alerts = client.get(CONSOLE_ALERTS + f"?clinic_id={ids.clinic_a}", headers=bearer(mint(ids.people["admin_a"][0])))
    assert admin_alerts.status_code == 200 and any(i["alert_class"] == "identity_revoked_suspect" and i["ref"] == event["id"] for i in admin_alerts.json()["items"])


def test_acknowledging_and_releasing_a_hold_write_one_audit_row_each_as_the_person_with_ids_only_and_a_replay_writes_none(client, ids):
    task = new_task(ids, "referral_hold", "front_desk")
    fd = ids.people["front_desk_a"][0]
    key = _new_key()
    audit = "select actor_id, actor_role, action, details, clinic_id from audit.log where resource_type = 'review-task' and resource_id = $1 order by id"
    for path, wanted in ((TASK_ACK, "acknowledged"), (TASK_RESOLVE, "resolved")):
        done = client.post(path.format(task_id=task), headers={**bearer(mint(fd)), "X-Idempotency-Key": key})
        assert done.status_code == 200 and done.json()["status"] == wanted and done.json()["replayed"] is False
    again = client.post(TASK_RESOLVE.format(task_id=task), headers={**bearer(mint(fd)), "X-Idempotency-Key": key})
    assert again.json()["replayed"] is True
    rows = _run(_fetch(audit, str(task)))
    assert [r["action"] for r in rows] == ["review_task.acknowledge", "review_task.resolve"]  # the replay wrote nothing more
    for r in rows:
        assert (r["actor_id"], r["actor_role"], r["clinic_id"]) == (fd, "front_desk", ids.clinic_a)
    details = without_scope(rows[1]["details"])
    assert details == {"kind": "referral_hold", "replayed": False}
    state = _run(_fetch("select status, acknowledged_by, resolved_at is not null as closed from agents.review_tasks where id = $1", task))[0]
    assert (state["status"], state["acknowledged_by"], state["closed"]) == ("resolved", fd, True)
    refused = new_task(ids, "unmerge_review", "nurse")
    assert client.post(TASK_RESOLVE.format(task_id=refused), headers={**bearer(mint(fd)), "X-Idempotency-Key": _new_key()}).status_code == 403
    assert _run(_fetch(audit, str(refused))) == []  # a refusal writes no audit row


# The configured offer paths (a real draft, a real send, replays, conflicts, the audit row) write an ENCRYPTED MESSAGE, which can never be cleaned up and would break the key-rotation tests' precondition
# if committed: they are in tests/test_api_offers_live.py, inside a never-committed transaction.


def test_registering_a_document_writes_one_row_and_one_audit_row_as_the_person_and_a_replay_writes_none(client, ids):
    fd = ids.people["front_desk_a"][0]
    key = _new_key()
    document = _put_object(ids, key)
    headers = {**bearer(mint(fd)), "X-Idempotency-Key": key}
    body = {"clinic_id": str(ids.clinic_a), "kind": "referral"}
    ok = client.post(DOC_REGISTER, headers=headers, json=body)
    assert ok.status_code == 200 and ok.json()["document_id"] == str(document) and ok.json()["replayed"] is False
    assert client.post(DOC_REGISTER, headers=headers, json=body).json()["replayed"] is True
    rows = _run(_fetch("select registered_by, kind, content_type, size_bytes, storage_path from docs.documents where id = $1", document))
    assert len(rows) == 1 and rows[0]["registered_by"] == fd and rows[0]["storage_path"] == f"clinic/{ids.clinic_a}/doc/{document}"
    audit = _run(_fetch("select actor_id, actor_role, details, clinic_id from audit.log where resource_type = 'document' and resource_id = $1 and action = 'document.register'", str(document)))
    assert len(audit) == 1 and (audit[0]["actor_id"], audit[0]["actor_role"], audit[0]["clinic_id"]) == (fd, "front_desk", ids.clinic_a)
    details = without_scope(audit[0]["details"])
    assert details == {"kind": "referral", "scan": "not_checked", "replayed": False}
    refused = client.post(DOC_REGISTER, headers={**bearer(mint(ids.people["doctor_a"][0])), "X-Idempotency-Key": key}, json=body)
    assert refused.status_code == 403 and _run(_fetch("select count(*) as n from audit.log where resource_id = $1 and action = 'document.register'", str(document)))[0]["n"] == 1


def test_an_unconfigured_storage_answers_503_and_a_clinical_profile_without_a_scanner_refuses_uploads(ids):
    from careflow.app import Settings as _Settings

    def build(intake):
        return create_app(_Settings(_env_file=None, supabase_url=PROJECT, database_url_app=APP_URL or ""), jwks=fake_jwks_client(), vault=TEST_VAULT, document_intake=intake)

    fd = ids.people["front_desk_a"][0]
    body = {"clinic_id": str(ids.clinic_a), "kind": "referral", "content_type": "application/pdf"}
    headers = {**bearer(mint(fd)), "X-Idempotency-Key": _new_key()}
    with TestClient(build(None), raise_server_exceptions=False) as c:
        r = c.post(DOC_BEGIN, headers=headers, json=body)
        assert (r.status_code, r.json()) == (503, {"detail": "uploads are unavailable: not configured"})
    with TestClient(build(DocumentIntake(UPLOADS, profile="clinical")), raise_server_exceptions=False) as c:
        r = c.post(DOC_BEGIN, headers=headers, json=body)
        assert (r.status_code, r.json()) == (503, {"detail": "uploads are disabled in this profile"})


def test_a_registered_person_is_found_by_exact_details_with_flags_only_and_a_worker_record_shows_unverified(client, ids):
    fd = ids.people["front_desk_a"][0]
    headers = bearer(mint(fd))
    person = {"name": "Meena Kulkarni", "dob": "1984-03-09", "phone": "9876543999"}
    registered = client.post("/v1/staff/patients", headers={**headers, "X-Idempotency-Key": _new_key()}, json={"clinic_id": str(ids.clinic_a), **person})
    assert registered.status_code == 200
    pid = registered.json()["patient_id"]
    found = client.post(PATIENT_SEARCH, headers=headers, json={"clinic_id": str(ids.clinic_a), **person})
    assert found.status_code == 200
    (hit,) = [i for i in found.json()["items"] if i["patient_id"] == pid]
    assert hit == {
        "patient_id": pid, "matched_kinds": ["name_dob", "phone"], "verified": True, "registered_via": "front_desk",
        "label": {"patient_id": pid, "name": person["name"], "birth_year": 1984, "phone_last4": "3999"},
    }
    assert not ({"name", "dob", "phone"} & set(hit))  # the stored identifiers are not top-level fields
    assert person["dob"] not in found.text and person["phone"] not in found.text  # the list label has the name, the YEAR and the last four digits; never the full date or the full number
    name_only = client.post(PATIENT_SEARCH, headers=headers, json={"clinic_id": str(ids.clinic_a), "name": person["name"], "dob": person["dob"]})
    assert [i["matched_kinds"] for i in name_only.json()["items"] if i["patient_id"] == pid] == [["name_dob"]]
    # another clinic's front desk cannot search this clinic, and a different person finds nothing
    other = client.post(PATIENT_SEARCH, headers=bearer(mint(ids.people["front_desk_b"][0])), json={"clinic_id": str(ids.clinic_a), **person})
    assert other.status_code == 404
    nobody = client.post(PATIENT_SEARCH, headers=headers, json={"clinic_id": str(ids.clinic_a), **person, "name": "Somebody Else"})
    probe = [i for i in nobody.json()["items"] if i["patient_id"] == pid]
    assert [i["matched_kinds"] for i in probe] == [["phone"]]  # found by the number alone: never as a name match
    status = client.get(PATIENT_STATUS.format(patient_id=pid), headers=headers)
    assert status.status_code == 200 and status.json()["verified"] is True and status.json()["registered_via"] == "front_desk"
    # one audit row per search that reached the lookup: who, role, the KIND of search and the result count; never the query text or the results
    rows = _run(_fetch("select actor_id, actor_role, details, resource_id from audit.log where action = 'patient.search' and clinic_id = $1 order by id", ids.clinic_a))
    assert len(rows) >= 3 and all((r["actor_id"], r["actor_role"]) == (fd, "front_desk") for r in rows)
    parsed = [without_scope(r["details"]) for r in rows]
    assert {"kind": "name_dob_phone", "labels": 1, "results": 1} in parsed and {"kind": "name_dob", "labels": 1, "results": 1} in parsed  # (the third search, another name with the same phone, finds the number's owner: one phone-only hit, no label)
    assert {"kind": "name_dob_phone", "labels": 0, "results": 1} in parsed
    assert all(set(d) == {"kind", "labels", "results"} for d in parsed)  # counts only, never ids or text
    blob = json.dumps([[str(r["resource_id"]), d] for r, d in zip(rows, parsed, strict=True)])
    assert person["name"] not in blob and person["dob"] not in blob and person["phone"] not in blob and pid not in blob


def test_releasing_a_hold_reopens_its_slot_writes_one_audit_row_and_the_slot_can_be_held_again(client, ids):
    fd = ids.people["front_desk_a"][0]
    hold, slot = new_hold(ids)
    key = _new_key()
    done = client.post(HOLD_RELEASE.format(hold_id=hold), headers={**bearer(mint(fd)), "X-Idempotency-Key": key})
    assert done.status_code == 200 and done.json()["replayed"] is False
    assert client.post(HOLD_RELEASE.format(hold_id=hold), headers={**bearer(mint(fd)), "X-Idempotency-Key": key}).json()["replayed"] is True
    assert _run(_fetch("select status from sched.slots where id = $1", slot))[0]["status"] == "open"
    assert _run(_fetch("select status from sched.holds where id = $1", hold))[0]["status"] == "released"
    audit = _run(_fetch("select actor_id, actor_role, details, clinic_id from audit.log where resource_type = 'hold' and resource_id = $1 and action = 'hold.release'", str(hold)))
    assert len(audit) == 1 and (audit[0]["actor_id"], audit[0]["actor_role"], audit[0]["clinic_id"]) == (fd, "front_desk", ids.clinic_a)
    again = client.post(f"{PREFIX}/slots/{slot}/hold", headers={**bearer(mint(fd)), "X-Idempotency-Key": _new_key()}, json={"duration_minutes": 15})
    assert again.status_code == 200  # the slot is free to be held again
    refused = client.post(HOLD_RELEASE.format(hold_id=again.json()["hold_id"]), headers={**bearer(mint(ids.people["nurse_a"][0])), "X-Idempotency-Key": _new_key()})
    assert refused.status_code == 403 and _run(_fetch("select status from sched.holds where id = $1", uuid.UUID(again.json()["hold_id"])))[0]["status"] == "active"


def _view_override(client, *, enabled=True, settings=None):
    from careflow.app import patient_view_config
    from careflow.patient_view import (
        OpenRateLimiter,
        PatientViewConfig,
        PatientViewSettings,
    )

    # test inputs only: stand-ins for the engineering and required settings
    configured = PatientViewSettings(_env_file=None, **{"max_opens_per_minute": 100, "max_label_rows_per_minute": 1000, **(settings or {})})
    config = PatientViewConfig(
        enabled, configured, None if configured.max_opens_per_minute is None else OpenRateLimiter(configured.max_opens_per_minute),
        None if configured.max_label_rows_per_minute is None else OpenRateLimiter(configured.max_label_rows_per_minute),
    )
    _PRIOR_VIEW_OVERRIDE[id(client.app)] = client.app.dependency_overrides.get(patient_view_config)  # the module-wide stand-in (_enable_patient_view): put back afterwards, never dropped
    client.app.dependency_overrides[patient_view_config] = lambda: config
    return patient_view_config


_PRIOR_VIEW_OVERRIDE: dict[int, object] = {}


def _end_view_override(client, key):
    prior = _PRIOR_VIEW_OVERRIDE.pop(id(client.app), None)
    if prior is None:
        client.app.dependency_overrides.pop(key, None)
    else:
        client.app.dependency_overrides[key] = prior


def test_opening_a_record_is_audited_without_its_contents_and_is_rate_limited_per_user_with_a_required_setting(client, ids):
    pid = new_real_patient(ids, name="Quentin Auditprobe", dob="1955-05-05", phone="+919111122222")
    fd, nurse = ids.people["front_desk_a"][0], ids.people["nurse_a"][0]
    key = _view_override(client, settings={"max_opens_per_minute": 2})
    try:
        url = PATIENT_VIEW.format(patient_id=pid)
        first = client.get(url, headers=bearer(mint(fd)))
        assert first.status_code == 200 and first.headers["cache-control"] == "no-store" and first.json()["phone"] == "+919111122222"
        assert client.get(url, headers=bearer(mint(fd))).status_code == 200
        third = client.get(url, headers=bearer(mint(fd)))
        assert third.status_code == 429 and third.json() == {"detail": "too many records opened; wait a moment"}  # the limit is per user:
        assert client.get(url, headers=bearer(mint(nurse))).status_code == 200  # another user is not affected
        rows = _run(_fetch("select actor_id, actor_role, details, resource_id from audit.log where action = 'patient.view' and resource_id = $1 order by id", str(pid)))
        assert [(r["actor_id"], r["actor_role"]) for r in rows] == [(fd, "front_desk"), (fd, "front_desk"), (nurse, "nurse")]  # the refused third open wrote nothing
        blob = json.dumps([[str(r["resource_id"]), str(r["details"])] for r in rows])
        assert "Quentin" not in blob and "1955" not in blob and "9111122222" not in blob  # who, role and the patient id; never the contents
    finally:
        _end_view_override(client, key)
    # the required setting has no default: unset, opening is unavailable (never unlimited); list rows do not need it
    key = _view_override(client, settings={"max_opens_per_minute": None})
    try:
        unset = client.get(PATIENT_VIEW.format(patient_id=pid), headers=bearer(mint(fd)))
        assert unset.status_code == 503 and unset.json() == {"detail": "patient view is unavailable: the per-user open rate limit is not configured"}
        rows_ok = client.post(PATIENT_LABELS, headers=bearer(mint(fd)), json={"clinic_id": str(ids.clinic_a), "patient_ids": [str(pid)]})
        assert rows_ok.status_code == 200 and rows_ok.json()["items"][0]["name"] == "Quentin Auditprobe"
    finally:
        _end_view_override(client, key)
    from careflow.patient_view import PatientViewSettings

    assert PatientViewSettings(_env_file=None).max_opens_per_minute is None  # no default anywhere


def test_outside_the_demo_profile_with_synthetic_data_nothing_is_decrypted_and_search_rows_carry_no_label(client, ids):
    fd = ids.people["front_desk_a"][0]
    pid = new_real_patient(ids)
    key = _view_override(client, enabled=False)
    try:
        for response in (
            client.get(PATIENT_VIEW.format(patient_id=pid), headers=bearer(mint(fd))),
            client.post(PATIENT_LABELS, headers=bearer(mint(fd)), json={"clinic_id": str(ids.clinic_a), "patient_ids": [str(pid)]}),
        ):
            assert response.status_code == 503 and response.json() == {"detail": "patient view is disabled outside the demo profile with synthetic data"}
        search = client.post(PATIENT_SEARCH, headers=bearer(mint(fd)), json={"clinic_id": str(ids.clinic_a), "name": "Nobody Atall", "dob": "1990-01-01"})
        assert search.status_code == 200 and search.json() == {"items": [], "truncated": False, "labels_withheld": False}
    finally:
        _end_view_override(client, key)


def test_list_rows_are_audited_with_the_count_only_and_the_page_limit_setting_can_lower_the_cap_but_never_raise_it(client, ids):
    from careflow.patient_view import HARD_PAGE_CAP, PatientViewSettings

    fd = ids.people["front_desk_a"][0]
    a, b = new_real_patient(ids), new_real_patient(ids)
    key = _view_override(client, settings={"page_limit": 1})
    try:
        refused = client.post(PATIENT_LABELS, headers=bearer(mint(fd)), json={"clinic_id": str(ids.clinic_a), "patient_ids": [str(a), str(b)]})
        assert refused.status_code == 422  # the setting lowered the page to one row
        ok = client.post(PATIENT_LABELS, headers=bearer(mint(fd)), json={"clinic_id": str(ids.clinic_a), "patient_ids": [str(a)]})
        assert ok.status_code == 200 and ok.headers["cache-control"] == "no-store"
    finally:
        _end_view_override(client, key)
    rows = _run(_fetch("select actor_role, details, resource_type from audit.log where action = 'patient.view_labels' and clinic_id = $1 and actor_id = $2 order by id", ids.clinic_a, fd))  # this user's rows only: other tests of the clinic read labels as nurse and doctor
    parsed = [without_scope(r["details"]) for r in rows]
    assert {"count": 1} in parsed and all(set(d) == {"count"} for d in parsed) and all(r["actor_role"] == "front_desk" for r in rows)  # a count, never ids
    assert HARD_PAGE_CAP == 25
    with pytest.raises(ValueError):
        PatientViewSettings(_env_file=None, page_limit=26)  # the cap cannot be raised by configuration


def test_search_rows_carry_the_list_label_when_the_feature_is_on(client, ids):
    fd = ids.people["front_desk_a"][0]
    person = {"name": "Odile Searchprobe", "dob": "1990-02-02", "phone": "9876500777"}
    client.post("/v1/staff/patients", headers={**bearer(mint(fd)), "X-Idempotency-Key": _new_key()}, json={"clinic_id": str(ids.clinic_a), **person})
    found = client.post(PATIENT_SEARCH, headers=bearer(mint(fd)), json={"clinic_id": str(ids.clinic_a), **person})
    assert found.status_code == 200 and found.headers["cache-control"] == "no-store" and found.json()["truncated"] is False
    (hit,) = found.json()["items"]
    assert hit["label"] == {"patient_id": hit["patient_id"], "name": "Odile Searchprobe", "birth_year": 1990, "phone_last4": "0777"}
    assert "1990-02-02" not in found.text and "9876500777" not in found.text  # not the full date, not the full number


def test_the_list_row_budget_is_required_per_user_and_counted_in_rows_for_the_label_read_and_for_search_labels(client, ids):
    fd, nurse = ids.people["front_desk_a"][0], ids.people["nurse_a"][0]
    patients = [new_real_patient(ids) for _ in range(3)]
    body = lambda *p: {"clinic_id": str(ids.clinic_a), "patient_ids": [str(x) for x in p]}
    key = _view_override(client, settings={"max_label_rows_per_minute": 4})
    try:
        assert client.post(PATIENT_LABELS, headers=bearer(mint(fd)), json=body(*patients)).status_code == 200  # 3 rows
        over = client.post(PATIENT_LABELS, headers=bearer(mint(fd)), json=body(*patients[:2]))  # 3 + 2 > 4
        assert over.status_code == 429 and over.json() == {"detail": "too many list rows read; wait a moment"}
        assert client.post(PATIENT_LABELS, headers=bearer(mint(fd)), json=body(patients[0])).status_code == 200  # 3 + 1 = 4: the refused call charged nothing
        assert client.post(PATIENT_LABELS, headers=bearer(mint(nurse)), json=body(*patients)).status_code == 200  # another user has their own budget
        # search labels draw on the SAME budget: the front desk user has spent it, so the hit comes back without a label and says so
        person = {"name": "Budget Probe", "dob": "1981-01-01", "phone": "9876500888"}
        client.post("/v1/staff/patients", headers={**bearer(mint(fd)), "X-Idempotency-Key": _new_key()}, json={"clinic_id": str(ids.clinic_a), **person})
        found = client.post(PATIENT_SEARCH, headers=bearer(mint(fd)), json={"clinic_id": str(ids.clinic_a), **person})
        assert found.status_code == 200 and found.json()["labels_withheld"] is True and all(i["label"] is None for i in found.json()["items"]) and found.json()["items"]
    finally:
        _end_view_override(client, key)
    key = _view_override(client, settings={"max_label_rows_per_minute": None})
    try:
        unset = client.post(PATIENT_LABELS, headers=bearer(mint(fd)), json=body(patients[0]))
        assert unset.status_code == 503 and unset.json() == {"detail": "patient view is unavailable: the per-user list-row limit is not configured"}
        assert client.get(PATIENT_VIEW.format(patient_id=patients[0]), headers=bearer(mint(fd))).status_code == 200  # opening is a separate setting
    finally:
        _end_view_override(client, key)


def test_a_phone_only_hit_carries_no_label_so_a_number_cannot_be_turned_into_a_name(client, ids):
    fd = ids.people["front_desk_a"][0]
    owner = {"name": "Phone Owner", "dob": "1970-10-10", "phone": "9876500999"}
    client.post("/v1/staff/patients", headers={**bearer(mint(fd)), "X-Idempotency-Key": _new_key()}, json={"clinic_id": str(ids.clinic_a), **owner})
    probe = client.post(PATIENT_SEARCH, headers=bearer(mint(fd)), json={"clinic_id": str(ids.clinic_a), "name": "Somebody Different", "dob": "1999-09-09", "phone": owner["phone"]})
    assert probe.status_code == 200
    phone_only = [i for i in probe.json()["items"] if i["matched_kinds"] == ["phone"]]
    assert phone_only and all(i["label"] is None for i in phone_only)  # found by the number alone: no name is revealed
    assert "Phone Owner" not in probe.text


def test_a_patient_account_bound_to_a_record_cannot_open_it_or_read_its_label_and_an_erased_record_is_gone(client, ids):
    async def bind(conn, account):
        pid = await _new_real_patient(conn, ids.clinic_a)
        await conn.execute("update mpi.patients set account_id = $1 where id = $2", account, pid)
        return pid

    for key in ("patient",):  # a guardian account cannot be bound to a record at all (migration 0012 refuses it), so the bound case is a patient account
        account = ids.people[key][0]
        bound = _run(_owner(lambda conn, a=account: bind(conn, a)))
        token = bearer(mint(account))
        opened = client.get(PATIENT_VIEW.format(patient_id=bound), headers=token)
        assert opened.status_code in (403, 404) and "Testperson" not in opened.text and SYNTHETIC["dob"] not in opened.text  # can see its own row, but is not staff: nothing is decrypted
        labels = client.post(PATIENT_LABELS, headers=token, json={"clinic_id": str(ids.clinic_a), "patient_ids": [str(bound)]})
        assert labels.status_code in (403, 404) and "Testperson" not in labels.text
    erased = new_real_patient(ids)
    _run(_owner(lambda conn: conn.execute("update mpi.patients set erased_at = now(), name_ct = null, name_key_version = null, dob_ct = null, dob_key_version = null, phone_ct = null, phone_key_version = null where id = $1", erased)))
    fd = ids.people["front_desk_a"][0]
    assert client.get(PATIENT_VIEW.format(patient_id=erased), headers=bearer(mint(fd))).status_code == 404
    gone = client.post(PATIENT_LABELS, headers=bearer(mint(fd)), json={"clinic_id": str(ids.clinic_a), "patient_ids": [str(erased)]})
    assert gone.status_code == 200 and gone.json() == {"items": []}


def test_a_record_that_cannot_be_decrypted_is_a_fixed_503_with_no_audit_row_and_every_response_is_no_store_errors_included(client, ids):
    fd = ids.people["front_desk_a"][0]

    async def damaged(conn):
        pid = await _new_real_patient(conn, ids.clinic_a)
        await conn.execute("update mpi.patients set dob_ct = $2 where id = $1", pid, b"\x00" * 40)
        return pid

    pid = _run(_owner(damaged))
    broken = client.get(PATIENT_VIEW.format(patient_id=pid), headers=bearer(mint(fd)))
    assert broken.status_code == 503 and broken.json() == {"detail": "this record cannot be shown"} and broken.headers["cache-control"] == "no-store"
    assert _run(_fetch("select count(*) as n from audit.log where action = 'patient.view' and resource_id = $1", str(pid)))[0]["n"] == 0
    for response in (
        client.get(PATIENT_VIEW.format(patient_id=uuid.uuid4()), headers=bearer(mint(fd))),  # 404
        client.get(PATIENT_VIEW.format(patient_id="nope"), headers=bearer(mint(fd))),  # 422
        client.get(PATIENT_VIEW.format(patient_id=pid)),  # 401
        client.post(PATIENT_LABELS, headers=bearer(mint(ids.people["admin_a"][0])), json={"clinic_id": str(ids.clinic_a), "patient_ids": [str(pid)]}),  # 403
    ):
        assert response.status_code in (401, 403, 404, 422) and response.headers["cache-control"] == "no-store", response.status_code


def test_nurse_and_doctor_label_reads_are_audited_with_their_own_role_and_the_count_only(client, ids):
    patient = new_real_patient(ids)
    for who, role in (("nurse_a", "nurse"), ("doctor_a", "doctor")):
        account = ids.people[who][0]
        ok = client.post(PATIENT_LABELS, headers=bearer(mint(account)), json={"clinic_id": str(ids.clinic_a), "patient_ids": [str(patient)]})
        assert ok.status_code == 200
        rows = _run(_fetch("select actor_role, details from audit.log where action = 'patient.view_labels' and actor_id = $1", account))
        assert rows and {r["actor_role"] for r in rows} == {role}


# ---- message reads: the committed half's behaviour -----------------------------------------------------------------------------------------


def test_refused_message_reads_write_no_audit_row(client, ids):
    actors = [ids.people[p][0] for p in ("nurse_a", "doctor_a", "front_desk_a", "admin_a")] + [ids.no_account]
    for who in ("nurse_a", "doctor_a", "front_desk_a", "admin_a"):
        r = client.get(READ.format(message_id=uuid.uuid4()), headers=bearer(mint(ids.people[who][0])))
        assert (r.status_code, r.json()) == (404, {"detail": "not found"}), who
        assert "cache-control" not in r.headers
    rows = _run(_fetch("select count(*) as n from audit.log where action = 'message.read' and actor_id = any($1)", actors))
    assert rows[0]["n"] == 0


def test_a_confirmation_writes_exactly_one_audit_row_as_the_confirmer_with_ids_only_and_a_replay_or_refusal_writes_none(client, ids):
    patient = new_worker_patient(ids)
    fd = ids.people["front_desk_a"][0]
    key = _new_key()
    audit = "select actor_id, actor_role, action, details, clinic_id from audit.log where resource_type = 'patient' and resource_id = $1 and action = 'patient.confirm_identity'"
    refused = client.post(CONFIRM.format(patient_id=patient), headers={**bearer(mint(ids.people["nurse_a"][0])), "X-Idempotency-Key": _new_key()})
    assert refused.status_code == 403 and _run(_fetch(audit, str(patient))) == []
    ok = client.post(CONFIRM.format(patient_id=patient), headers={**bearer(mint(fd)), "X-Idempotency-Key": key})
    assert ok.status_code == 200 and ok.json()["replayed"] is False
    (row,) = _run(_fetch(audit, str(patient)))
    assert (row["actor_id"], row["actor_role"], row["clinic_id"]) == (fd, "front_desk", ids.clinic_a)
    details = without_scope(row["details"])
    assert details == {"replayed": False}
    again = client.post(CONFIRM.format(patient_id=patient), headers={**bearer(mint(fd)), "X-Idempotency-Key": key})
    assert again.status_code == 200 and again.json()["replayed"] is True
    assert len(_run(_fetch(audit, str(patient)))) == 1  # the same key writes no second row
