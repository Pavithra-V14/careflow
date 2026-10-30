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
import time
import uuid
from collections.abc import Callable
from datetime import UTC, timedelta
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
from careflow.app import Settings, create_app
from careflow.auth import JWKSClient
from careflow.crypto import DemographicVault
from careflow.db import make_app_engine
from careflow.documents import ALLOWED_TYPES as ALLOWED_DOC_TYPES
from careflow.documents import DocumentIntake, document_id_for
from careflow.front_door_runner import TASK_TYPE as FRONT_DOOR_TASK
from careflow.front_door_runner import FrontDoorComponents
from tests.bounds import bounded

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
    claims = {"sub": str(sub), "iss": ISSUER, "aud": "authenticated", "role": "authenticated", "exp": int(time.time()) + 3000, **over}
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
    except Exception:
        await _cleanup(ids, conn)
        await conn.close()
        raise
    await conn.close()
    return ids


POOL = {"hold": 40, "book": 40, "extra": 40, "cancel": 30}  # slots / bookings handed out one per test case
N_PATIENTS = 45


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
    bookings = [(uuid.uuid4(), sid, ids.patients_a[i % N_PATIENTS], k(f"bk{i}")) for i, sid in enumerate(pools["cancel"])]
    await conn.executemany(
        "insert into sched.bookings (id, clinic_id, slot_id, patient_id, origin, idempotency_key) values ($1, $2, $3, $4, 'test', $5)",
        [(bid, a, sid, pid, key) for bid, sid, pid, key in bookings],
    )
    ids.pools = {**{p: list(v) for p, v in pools.items() if p != "cancel"}, "cancel": [bid for bid, *_ in bookings]}


async def _cleanup(ids, conn=None):
    own = conn is None
    conn = conn or await asyncpg.connect((MIGRATOR or "").replace("+asyncpg", ""), statement_cache_size=0)
    try:
        accounts = [a for a, *_ in ids.people.values()] + ([ids.fd2] if hasattr(ids, "fd2") else [])
        clinics = [ids.clinic_a, ids.clinic_b]
        for table in ("sched.bookings", "sched.holds", "sched.waitlist", "sched.slots", "sched.priority_levels",
                      "mpi.merges", "agents.review_tasks", "mpi.match_candidates", "mpi.identity_tokens", "mpi.patients", "sched.visit_types", "sched.providers",
                      "sched.locations"):
            if table == "mpi.patients":
                # A confirmed patient cannot be deleted: mpi.identity_confirmations is append-only (migration 0023, a trigger blocks the owner too) and references it. Such patients, and
                # whatever references them (the confirming accounts, the clinics), stay behind, tagged origin='test'; nothing reads them and every other suite is scoped to its own clinic.
                await conn.execute(
                    "delete from mpi.patients p where p.clinic_id = any($1) and not exists (select 1 from mpi.identity_confirmations c where c.patient_id = p.id)", clinics
                )
                continue
            if table == "agents.review_tasks":
                # A task that an append-only triage proposal points at cannot be deleted (the referral-queue cases seed one); it stays behind with its clinic, tagged origin='test'.
                await conn.execute("delete from agents.review_tasks t where t.clinic_id = any($1) and not exists (select 1 from agents.triage_proposals p where p.review_task_id = t.id)", clinics)
                continue
            await conn.execute(f"delete from {table} where clinic_id = any($1)", clinics)
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


@pytest.fixture(scope="module")
def app() -> FastAPI:
    settings = Settings(supabase_url=PROJECT, database_url_app=APP_URL or "", step_up_max_age_seconds=STEP_UP_WINDOW)
    # Stand-in components (all None): the referral routes need SOME configured front door to get past their 503; the paths tested here (404, 422, 401) never touch a component.
    built = create_app(
        settings, jwks=fake_jwks_client(), vault=TEST_VAULT, front_door=FrontDoorComponents(None, None, None, None, None, None, None, None, None),
        document_intake=DocumentIntake(UPLOADS, profile="demo"),  # a fake storage in the demo profile with no scanner: registration reports scan=not_checked
    )
    return _enable_patient_view(built)


@pytest.fixture(scope="module")
def client(app, ids):
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def expected_me(ids, key):
    account_id, kind, status, grants = ids.people[key]
    return {
        "account_id": str(account_id),
        "account_type": kind,
        "status": status,
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
        ("a password-only session (no aal, no amr)", bearer(mint(sub))),
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
    401: {"detail": "step-up required"}, 403: {"detail": "your role may not do this"}, 404: {"detail": "not found"},
}


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
    return lambda b: b == {"patient_id": str(patient), "confirmed": False, "replayed": replayed, "review_task": review_task}


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
              body=lambda b: b["status"] == "unavailable" and b["reason"].startswith("not_configured:") and b["data"] is None),
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


def _put_object(ids, key, content=PDF_BYTES, content_type="application/pdf", *, clinic=None):
    """The browser's half of an upload: take a ticket from the real intake for (clinic, key) and PUT the bytes at the server-chosen path."""
    clinic = clinic or ids.clinic_a
    ticket = _run(DocumentIntake(UPLOADS, profile="demo").begin(clinic, "referral", content_type if content_type in ALLOWED_DOC_TYPES else "application/pdf", key))
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
              body=lambda b: b["document_id"] == str(document_id_for(ids.clinic_a, key))),
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
    doc_nurse = _put_object(ids, k_nurse)
    _put_object(ids, k_mismatch, b"this is not a pdf at all", "application/pdf")
    _put_object(ids, k_empty, b"", "application/pdf")
    registered = lambda doc, replayed: (lambda b: b == {
        "document_id": str(doc), "kind": "referral", "content_type": "application/pdf", "size_bytes": len(PDF_BYTES), "replayed": replayed, "scan": "not_checked"})
    cases = [
        _case(ids, "front desk registers an uploaded file", "front_desk_a", 200, DOC_REGISTER, json=body(), key=k_ok, body=registered(doc_ok, False)),
        _case(ids, "replaying the same key is a replay", "front_desk_a", 200, DOC_REGISTER, json=body(), key=k_ok, body=registered(doc_ok, True)),
        _case(ids, "the same key for a different kind is a conflict", "front_desk_a", 409, DOC_REGISTER, json=body(kind="report"), key=k_ok,
              body=lambda b: b == {"detail": "this idempotency key was already used by a different request"}),
        _case(ids, "a nurse registers an uploaded file", "nurse_a", 200, DOC_REGISTER, json=body(), key=k_nurse, body=registered(doc_nurse, False)),
        _case(ids, "a key with nothing uploaded is refused with a fixed word", "front_desk_a", 422, DOC_REGISTER, json=body(), body=lambda b: b == {"detail": "not_found"}),
        _case(ids, "an object that is not what its type says is refused", "front_desk_a", 422, DOC_REGISTER, json=body(), key=k_mismatch, body=lambda b: b == {"detail": "content_mismatch"}),
        _case(ids, "an empty object is refused", "front_desk_a", 422, DOC_REGISTER, json=body(), key=k_empty, body=lambda b: b == {"detail": "empty"}),
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
        _case(ids, "a reader of another clinic is allowed and sees none of this clinic's rows", "front_desk_b", 200, path, key=None, body=_page(ids, check=lambda items: items == [])),
        _case(ids, "include_inactive is accepted", "front_desk_a", 200, path + "?include_inactive=true", key=None, body=_page(ids, clinic_only=ids.clinic_a, non_empty=True, check=seeded)),
        _case(ids, "limit=1 returns one row", "front_desk_a", 200, path + "?limit=1", key=None, body=lambda b: len(b["items"]) == 1 and b["limit"] == 1 and (b["has_more"] is True or route != "priority_levels")),
        _case(ids, "limit=0 is rejected", "front_desk_a", 422, path + "?limit=0", key=None, body=_fixed(422)),
        _case(ids, "limit=501 is rejected", "front_desk_a", 422, path + "?limit=501", key=None, body=_fixed(422)),
        _case(ids, "include_inactive that is not a boolean is rejected", "front_desk_a", 422, path + "?include_inactive=maybe", key=None, body=_fixed(422)),
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
    ("POST", HOLD_RELEASE): hold_release_cases,
    ("POST", PATIENT_SEARCH): patient_search_cases,
    ("GET", PATIENT_STATUS): patient_status_cases,
    ("GET", PATIENT_VIEW): patient_view_cases,
    ("POST", PATIENT_LABELS): patient_labels_cases,
    ("GET", READ): message_read_cases,
    ("POST", RUN_REFERRAL): referral_run_cases,
    ("POST", REVIEW_REFERRAL): referral_review_cases,
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
    # Both Front Door run routes: the committed half above (random document ids: 401, 404, 422) and the dedicated half (existing documents, the worker, the whole run).
    ("POST", RUN_REFERRAL): "test_api_referrals_live.py",
    ("POST", REVIEW_REFERRAL): "test_api_referrals_live.py",
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
    settings = Settings(supabase_url=PROJECT, database_url_app=APP_URL or "", step_up_max_age_seconds=STEP_UP_WINDOW)
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
    assert _run(_fetch("select count(*) as n from mpi.patients where idempotency_key = $1", key))[0]["n"] == 1


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
        details = json.loads(r["details"]) if isinstance(r["details"], str) else r["details"]
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
    settings = Settings(supabase_url=PROJECT, database_url_app=APP_URL or "", step_up_max_age_seconds=None)
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
        assert ok.status_code == 200 and ok.json() == {"patient_id": str(patient), "confirmed": False, "replayed": False, "review_task": "raised"}
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
    details = json.loads(row["details"]) if isinstance(row["details"], str) else row["details"]
    assert (row["actor_id"], row["actor_role"], row["clinic_id"]) == (fd, "front_desk", ids.clinic_a)
    assert details == {"reason": "wrong_person", "replayed": False, "review_task": "raised"}


def test_with_the_task_settings_unset_the_revoke_is_recorded_and_says_no_task_was_raised(client, ids):
    patient = new_confirmed_patient(ids)
    fd = ids.people["front_desk_a"][0]
    done = client.post(REVOKE.format(patient_id=patient), headers={**bearer(mint(fd)), "X-Idempotency-Key": _new_key()}, json=REASON)
    assert done.status_code == 200 and done.json()["review_task"] == "not_configured"
    assert _run(_fetch("select count(*) as n from mpi.identity_confirmations where patient_id = $1 and kind = 'revoke'", patient))[0]["n"] == 1
    assert _run(_fetch("select count(*) as n from agents.review_tasks where provenance ->> 'patient_id' = $1", str(patient)))[0]["n"] == 0
    (row,) = _run(_fetch("select details from audit.log where resource_id = $1 and action = 'patient.revoke_identity'", str(patient)))
    details = json.loads(row["details"]) if isinstance(row["details"], str) else row["details"]
    assert details["review_task"] == "not_configured"  # the audit row says so too


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
    details = json.loads(rows[1]["details"]) if isinstance(rows[1]["details"], str) else rows[1]["details"]
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
    details = json.loads(audit[0]["details"]) if isinstance(audit[0]["details"], str) else audit[0]["details"]
    assert details == {"kind": "referral", "scan": "not_checked", "replayed": False}
    refused = client.post(DOC_REGISTER, headers={**bearer(mint(ids.people["doctor_a"][0])), "X-Idempotency-Key": key}, json=body)
    assert refused.status_code == 403 and _run(_fetch("select count(*) as n from audit.log where resource_id = $1 and action = 'document.register'", str(document)))[0]["n"] == 1


def test_an_unconfigured_storage_answers_503_and_a_clinical_profile_without_a_scanner_refuses_uploads(ids):
    from careflow.app import Settings as _Settings

    def build(intake):
        return create_app(_Settings(supabase_url=PROJECT, database_url_app=APP_URL or ""), jwks=fake_jwks_client(), vault=TEST_VAULT, document_intake=intake)

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
    parsed = [json.loads(r["details"]) if isinstance(r["details"], str) else r["details"] for r in rows]
    assert {"kind": "name_dob_phone", "results": 1} in parsed and {"kind": "name_dob", "results": 1} in parsed and {"kind": "name_dob_phone", "results": 0} in parsed
    assert all(set(d) == {"kind", "results"} for d in parsed)
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
    client.app.dependency_overrides[patient_view_config] = lambda: config
    return patient_view_config


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
        client.app.dependency_overrides.pop(key, None)
    # the required setting has no default: unset, opening is unavailable (never unlimited); list rows do not need it
    key = _view_override(client, settings={"max_opens_per_minute": None})
    try:
        unset = client.get(PATIENT_VIEW.format(patient_id=pid), headers=bearer(mint(fd)))
        assert unset.status_code == 503 and unset.json() == {"detail": "patient view is unavailable: the per-user open rate limit is not configured"}
        rows_ok = client.post(PATIENT_LABELS, headers=bearer(mint(fd)), json={"clinic_id": str(ids.clinic_a), "patient_ids": [str(pid)]})
        assert rows_ok.status_code == 200 and rows_ok.json()["items"][0]["name"] == "Quentin Auditprobe"
    finally:
        client.app.dependency_overrides.pop(key, None)
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
        client.app.dependency_overrides.pop(key, None)


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
        client.app.dependency_overrides.pop(key, None)
    rows = _run(_fetch("select actor_role, details, resource_type from audit.log where action = 'patient.view_labels' and clinic_id = $1 order by id", ids.clinic_a))
    parsed = [json.loads(r["details"]) if isinstance(r["details"], str) else r["details"] for r in rows]
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
        client.app.dependency_overrides.pop(key, None)
    key = _view_override(client, settings={"max_label_rows_per_minute": None})
    try:
        unset = client.post(PATIENT_LABELS, headers=bearer(mint(fd)), json=body(patients[0]))
        assert unset.status_code == 503 and unset.json() == {"detail": "patient view is unavailable: the per-user list-row limit is not configured"}
        assert client.get(PATIENT_VIEW.format(patient_id=patients[0]), headers=bearer(mint(fd))).status_code == 200  # opening is a separate setting
    finally:
        client.app.dependency_overrides.pop(key, None)


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
    _run(_owner(lambda conn: conn.execute("update mpi.patients set erased_at = now(), name_ct = null, dob_ct = null, phone_ct = null where id = $1", erased)))
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
    details = json.loads(row["details"]) if isinstance(row["details"], str) else row["details"]
    assert details == {"replayed": False}
    again = client.post(CONFIRM.format(patient_id=patient), headers={**bearer(mint(fd)), "X-Idempotency-Key": key})
    assert again.status_code == 200 and again.json()["replayed"] is True
    assert len(_run(_fetch(audit, str(patient)))) == 1  # the same key writes no second row
