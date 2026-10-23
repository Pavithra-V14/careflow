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

import asyncpg
import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from dotenv import dotenv_values
from fastapi import FastAPI
from starlette.testclient import TestClient

from careflow.app import Settings, create_app
from careflow.auth import JWKSClient
from careflow.crypto import DemographicVault

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
    claims = {"sub": str(sub), "iss": ISSUER, "aud": "authenticated", "role": "authenticated", "exp": int(time.time()) + 600, **over}
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


POOL = {"hold": 40, "book": 40, "extra": 30, "cancel": 30}  # slots / bookings handed out one per test case
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
            await conn.execute(f"delete from {table} where clinic_id = any($1)", clinics)
        await conn.execute("delete from iam.staff_roles where clinic_id = any($1)", [ids.clinic_a, ids.clinic_b])
        await conn.execute("delete from iam.accounts where id = any($1)", accounts)
        await conn.execute("delete from iam.clinics where id = any($1)", [ids.clinic_a, ids.clinic_b])
    finally:
        if own:
            await conn.close()


@pytest.fixture(scope="module")
def ids():
    seeded = _run(_seed())
    yield seeded
    _run(_cleanup(seeded))


# Per-run keys: nothing secret is committed. The patient-registration route encrypts demographics with them (version 1, the only one loaded).
TEST_VAULT = DemographicVault.from_env({f"CAREFLOW_{k}_SECRET_V1": base64.b64encode(os.urandom(32)).decode() for k in ("ENC", "HMAC")})


STEP_UP_WINDOW = 300  # seconds; the merge routes require a second factor verified within this window


@pytest.fixture(scope="module")
def app() -> FastAPI:
    settings = Settings(supabase_url=PROJECT, database_url_app=APP_URL or "", step_up_max_age_seconds=STEP_UP_WINDOW)
    return create_app(settings, jwks=fake_jwks_client(), vault=TEST_VAULT)


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
        "roles": sorted(({"clinic_id": str(c), "role": r} for c, r in grants), key=lambda g: (g["clinic_id"], g["role"])),
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


def _totp(seconds_ago: int = 5) -> list:
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
            cases.append(_case(ids, f"{p} with a second factor is refused", stepped(sub), status_for.get(p, default), make_path(), json=make_json()))
        for label, headers in not_stepped_variants(sub)[:2]:
            cases.append(_case(ids, f"{p} with {label} is told to step up", headers, 401, make_path(), json=make_json(),
                               body=lambda b: b == {"detail": "step-up required"}))
    return cases


def _not_stepped_cases(ids, make_path, make_json, who="front_desk_a"):
    sub = ids.people[who][0]
    return [_case(ids, f"{who} with {label} is told to step up", headers, 401, make_path(), json=make_json(), body=lambda b: b == {"detail": "step-up required"})
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
        _case(ids, "an unknown clinic is not found", fd, 404, MERGES, json=body(m4, s4, clinic_id=str(uuid.uuid4()))),
        _case(ids, "front desk of ANOTHER clinic cannot propose here", stepped(ids.people["front_desk_b"][0]), 404, MERGES, json=body(m4, s4)),
        _case(ids, "missing X-Idempotency-Key is rejected", fd, 422, MERGES, json=body(m4, s4), key=None),
        _case(ids, "blank X-Idempotency-Key is rejected", fd, 422, MERGES, json=body(m4, s4), key="   "),
        _case(ids, "a patient id that is not a uuid is rejected", fd, 422, MERGES, json=body(m4, "nope")),
        _case(ids, "a client-supplied origin is an unknown field", fd, 422, MERGES, json=body(m4, s4, origin="live")),
        _case(ids, "an empty body is rejected", fd, 422, MERGES, json={}),
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
        _case(ids, "missing X-Idempotency-Key is rejected", fd2, 422, path(own), key=None),
        _case(ids, "blank X-Idempotency-Key is rejected", fd2, 422, path(own), key="   "),
        _case(ids, "a merge id that is not a uuid is rejected", fd2, 422, path("nope")),
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
        _case(ids, "front desk of ANOTHER clinic cannot even see the merge", stepped(ids.people["front_desk_b"][0]), 404, path(refused)),
        _case(ids, "missing X-Idempotency-Key is rejected", fd, 422, path(refused), key=None),
        _case(ids, "blank X-Idempotency-Key is rejected", fd, 422, path(refused), key="   "),
        _case(ids, "a merge id that is not a uuid is rejected", fd, 422, path("nope")),
    ]
    cases += _everyone_at_the_gate(ids, lambda: path(refused), lambda: None, visible_but_not_front_desk, 404)
    cases += _not_stepped_cases(ids, lambda: path(refused), lambda: None)
    return cases + _unauthenticated(ids, path(refused), None)


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
    ("POST", INGEST): ingest_cases,
    ("POST", MERGES): merge_propose_cases,
    ("POST", APPROVE): merge_approve_cases,
    ("POST", UNMERGE): merge_unmerge_cases,
}


# ---- tests -----------------------------------------------------------------------------------------


# Routes whose role matrix runs in a DEDICATED module because their rows are append-only (msg.messages, msg.screenings, audit.log) and
# so cannot be committed and cleaned up by this module's committed-fixture harness. That module drives the same app over HTTP with real
# signed tokens inside one never-committed transaction. Listing a route here is not an exemption: the dedicated file must exist and
# must cover the route (see test_separately_tested_routes_have_a_dedicated_matrix_that_covers_them).
SEPARATE_HARNESS: dict[tuple[str, str], str] = {
    ("POST", "/v1/staff/messages/{message_id}/screen"): "test_api_screen_message_live.py",
}


def test_every_route_is_in_the_matrix(app):
    """Rule 8: a route with no role-matrix case is not done."""
    # The generated OpenAPI document lists every served operation, including routers added by include_router.
    served = {(m.upper(), path) for path, ops in app.openapi()["paths"].items() if path.startswith("/v1") for m in ops}
    covered = set(MATRIX) | set(SEPARATE_HARNESS)
    assert served == covered, f"routes without a matrix case: {served - covered}; stale cases: {covered - served}"
    assert not set(MATRIX) & set(SEPARATE_HARNESS), "a route is either in MATRIX or in SEPARATE_HARNESS, not both"


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
    assert body["roles"] == [{"clinic_id": str(ids.clinic_a), "role": "admin"}]


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


def test_identity_never_leaks_between_pooled_requests(app, ids):
    """Forty concurrent requests from six identities over a pool of five connections: each must get exactly
    its own profile back, which only holds if the claims are transaction-local and set before any query."""
    keys = ["front_desk_a", "nurse_a", "admin_a", "auditor_a", "patient", "front_desk_b"]

    async def run():
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:

                async def call(i):
                    key = keys[i % len(keys)]
                    r = await http.get("/v1/iam/me", headers=bearer(mint(ids.people[key][0])))
                    return key, r.status_code, r.json()

                return await asyncio.gather(*(call(i) for i in range(40)))

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

                results = await asyncio.gather(*(hold(w, s) for s in slots for w in ("front_desk_a", "front_desk_b")))
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


def test_concurrent_reads_from_different_identities_neither_mix_rows_nor_leak_connections(app, ids):
    """Forty concurrent reads over a pool of five: clinic A readers see only clinic A rows, clinic B's front desk sees
    none, admin, auditor and a patient are refused, and every connection is back in the pool."""
    plan = [("front_desk_a", 200), ("nurse_a", 200), ("front_desk_b", 200), ("admin_a", 403), ("auditor_a", 403), ("patient", 403)]

    async def run():
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:

                async def read(i):
                    who, _ = plan[i % len(plan)]
                    route = ("slots", "bookings", "waitlist")[i % 3]
                    r = await http.get(f"{PREFIX}/{route}?limit=5", headers=bearer(mint(ids.people[who][0])))
                    return who, r.status_code, (r.json()["items"] if r.status_code == 200 else None)

                return await asyncio.gather(*(read(i) for i in range(40))), app.state.engine.pool.checkedout()

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

                return await asyncio.gather(*(register(i) for i in range(8)))

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
