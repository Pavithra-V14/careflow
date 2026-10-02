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
import os
import time
from datetime import timedelta
import uuid
from collections.abc import Callable
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
    from datetime import datetime, timedelta, timezone

    tag = uuid.uuid4().hex[:8]
    k = lambda name: f"matrix-{tag}-{name}"  # noqa: E731  (idempotency keys for seed rows)
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
    now = datetime.now(timezone.utc)
    await conn.executemany(
        "insert into sched.waitlist (clinic_id, patient_id, visit_type_id, priority_code, joined_at, origin, idempotency_key) "
        "values ($1, $2, $3, $4, $5, 'test', $6)",
        [(a, ids.wait_low, ids.vt_a, "level_two", now - timedelta(days=2), k("wl-low")),
         (a, ids.wait_high, ids.vt_a, "level_one", now - timedelta(days=1), k("wl-high"))],
    )

    start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(days=60)
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
        accounts = [a for a, *_ in ids.people.values()]
        clinics = [ids.clinic_a, ids.clinic_b]
        for table in ("sched.bookings", "sched.holds", "sched.waitlist", "sched.slots", "sched.priority_levels",
                      "mpi.patients", "sched.visit_types", "sched.providers", "sched.locations"):
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


@pytest.fixture(scope="module")
def app() -> FastAPI:
    settings = Settings(supabase_url=PROJECT, database_url_app=APP_URL or "")
    return create_app(settings, jwks=fake_jwks_client())


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
    path = lambda slot=None: template.format(slot_id=slot or ids.pools[op].pop())  # noqa: E731
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
    path = lambda booking=None: CANCEL.format(booking_id=booking or ids.pools["cancel"].pop())  # noqa: E731
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
    body = lambda **o: {"patient_id": str(next(patients)), "visit_type_id": str(ids.vt_a), "clinical_priority": "level_one", **o}  # noqa: E731
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
    in_order = lambda items, key: all(key(i) <= key(j) for i, j in zip(items, items[1:]))  # noqa: E731
    wait_pair = (str(ids.wait_low), str(ids.wait_high))
    shape = {
        "slots": dict(check=lambda items: all(i["status"] in ("open", "held") for i in items) and in_order(items, lambda i: i["starts_at"])),
        "bookings": dict(check=lambda items: in_order(items, lambda i: i["starts_at"])),
        "waitlist": dict(check=lambda items: in_order(items, lambda i: (i["rank"], i["joined_at"]))
                         and [i["patient_id"] for i in items if i["patient_id"] in wait_pair] == [wait_pair[1], wait_pair[0]]),
    }[route]  # waitlist: the higher-priority patient is listed first although they joined later
    reads = lambda who, label: _case(ids, label, who, 200, path, key=None, body=_page(ids, clinic_only=A, non_empty=True, **shape))  # noqa: E731
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
}


# ---- tests -----------------------------------------------------------------------------------------


def test_every_route_is_in_the_matrix(app):
    """Rule 8: a route with no role-matrix case is not done."""
    # The generated OpenAPI document lists every served operation, including routers added by include_router.
    served = {(m.upper(), path) for path, ops in app.openapi()["paths"].items() if path.startswith("/v1") for m in ops}
    assert served == set(MATRIX), f"routes without a matrix case: {served - set(MATRIX)}; stale cases: {set(MATRIX) - served}"


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
