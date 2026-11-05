"""The ACTIVE ROLE on every staff route, through the role matrix (docs/design/active-role.md section 8; CLAUDE.md rule 8: same change as the code). Real signed tokens, the real app, the real restricted role.

It REUSES the main matrix's case builders (tests/test_api_role_matrix.py `MATRIX`), so a new staff route is covered the moment it is in the matrix, and `test_every_staff_route_has_the_header_dimension`
fails if one is not. What runs for EVERY route in `MATRIX` except the exempt `GET /v1/iam/me`:

  A. narrowing proof and "same result as no header" (8.2, 8.3, 8.6): every case of the route made by a single-role staff identity is replayed by an account that holds ALL six staff roles at clinic A,
     sending `X-CareFlow-Active-Role: A:<that identity's role>`. The status must equal what the single-role identity got: allowed where it was allowed, refused as it was refused where it was not.
     (A clinic-B identity replays itself with its own header.) This also proves a caller cannot widen: the other five roles it holds do not count.
  B. no header (8.1 phase 1): the same all-roles account WITHOUT a header gets a success wherever a single-role identity got one.
  C. the refusal battery (8.4, 8.5, 8.8): malformed, unheld, other-clinic, revoked, suspended, invited, patient, duplicate headers all give the byte-identical 403, and a malformed header on a step-up
     route is refused BEFORE the step-up check while a good header does not skip it.
Plus the cross-route behaviours: clinic narrowing on lists (8.7), `/me` ignores the header (8.9), no leak across pooled requests (8.10), a forged claim in a token is dropped (8.11), the audit row names the
active role and `role_scope` (section 6), and a refused header writes no audit row.
"""

import asyncio
import contextlib
import json
import re
import time
import uuid

import asyncpg
import httpx
import jwt
import pytest
from sqlalchemy import text

from careflow import active_role
from careflow.app import _begin_request
from careflow.db import make_app_engine
from tests.test_api_role_matrix import (  # noqa: F401  (the fixtures are imported so this module seeds its own world)
    APP_URL,
    APPROVE,
    CONFIRM,
    MATRIX,
    MERGES,
    PROJECT,
    STAFF_ROLES,
    UNMERGE,
    _fetch,
    _new_key,
    _owner,
    _run,
    app,
    bearer,
    check_route_matrix,
    client,
    fake_jwks_client,
    ids,
    mint,
    new_worker_patient,
    pytestmark,
)

EXEMPT = {("GET", "/v1/iam/me")}  # the picker needs every grant: the header is ignored there on purpose
ROUTES = sorted(set(MATRIX) - EXEMPT)
STEP_UP_ROUTES = {("POST", MERGES), ("POST", APPROVE), ("POST", UNMERGE)}
FIXED = {"detail": "active role not available"}
HEADER = active_role.HEADER


@pytest.fixture(scope="module")
def allroles(ids):  # noqa: F811
    """One staff account per ORIGINAL single-role clinic-A identity, each holding ALL six staff roles at clinic A (so a case that needs two different people, such as "one takes it, another cannot", still has two),
    plus a REVOKED-grant holder at clinic A: created here, removed after the module."""
    keys = [k for k, (_, kind, status, grants) in ids.people.items() if kind == "staff" and status == "active" and len(grants) == 1 and grants[0][0] == ids.clinic_a]
    ids.allroles_by_key = {k: uuid.uuid4() for k in keys}
    ids.allroles = ids.allroles_by_key["front_desk_a"]
    ids.revoked_holder = uuid.uuid4()
    holders = [*ids.allroles_by_key.values(), ids.revoked_holder]

    async def seed(conn):
        for account in holders:
            await conn.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test')", account)
        for account in ids.allroles_by_key.values():
            for role in STAFF_ROLES:
                await conn.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, $3, 'test')", account, ids.clinic_a, role)
        await conn.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin, revoked_at) values ($1, $2, 'nurse', 'test', now())", ids.revoked_holder, ids.clinic_a)

    _run(_owner(seed))
    yield ids.allroles

    async def drop(conn):
        await conn.execute("delete from iam.staff_roles where account_id = any($1)", holders)
        with contextlib.suppress(asyncpg.ForeignKeyViolationError):  # an account that resolved a match candidate is referenced by an append-only row: left behind on purpose, origin 'test', like the main matrix's confirmed patients
            await conn.execute("delete from iam.accounts where id = any($1)", holders)

    _run(_owner(drop))


def sub_of(headers) -> uuid.UUID | None:
    token = (headers.get("Authorization") or "")[len("Bearer "):]
    try:
        return uuid.UUID(jwt.decode(token, options={"verify_signature": False})["sub"])
    except Exception:  # noqa: BLE001 - a non-token header (the unauthenticated cases) has no sub
        return None


def single_role_identities(ids):  # noqa: F811
    """account id -> (key, clinic, role) for every ACTIVE staff account with exactly one grant (the identity a header can name)."""
    return {account: (key, *grants[0]) for key, (account, kind, status, grants) in ids.people.items() if kind == "staff" and status == "active" and len(grants) == 1 and key != "multi"}


# Cases whose expectation depends on WHICH PERSON acts, not on the role: an identity swap cannot reproduce them (the proposal's proposer is a fixed database row).
IDENTITY_BOUND = ("SAME person",)


def fresh(ids, method, path):  # noqa: F811
    """Call the route's case builder again. Its fixtures come from finite pools (slots, patients): rotate the waitlist patients so a second call does not reuse the ones a first call put on the waitlist."""
    ids.patients_a[1:] = ids.patients_a[9:] + ids.patients_a[1:9]
    return MATRIX[(method, path)](ids)


def replay_as(ids, cases, *, on_clinic_a: str):  # noqa: F811
    """The route's own cases, in order, with every case made by a single-role identity replaced:
    on_clinic_a = 'header'  -> the all-roles account WITH the header naming that identity's role (clinic-B identities use their own token and header);
    on_clinic_a = 'plain'   -> the all-roles account with NO header, and only the cases that expected a success.
    Cases by anyone else (multi-role, patients, bad tokens, refused accounts) are dropped: this pass is about the active role, not about those."""
    singles = single_role_identities(ids)
    out = []
    for label, headers, status, body, *rest in cases:
        extra = dict(rest[0]) if rest else {}
        sub = sub_of(headers)
        if sub not in singles or status == 401 or any(w in label for w in IDENTITY_BOUND):
            continue
        key, clinic, role = singles[sub]
        in_a = clinic == ids.clinic_a
        if on_clinic_a == "plain" and not (200 <= status < 300 and in_a):
            continue
        token_claims = jwt.decode(headers["Authorization"][len("Bearer "):], options={"verify_signature": False})
        carried = {k: v for k, v in token_claims.items() if k not in {"sub", "iss", "aud", "role", "exp"}}  # e.g. aal and amr on the step-up routes
        who = bearer(mint(ids.allroles_by_key[key] if in_a else sub, **carried))
        more = {**extra.get("headers", {})}
        if on_clinic_a == "header":
            more[HEADER] = f"{clinic}:{role}"
        out.append((f"{label} [as the all-roles account{' with header ' + role if on_clinic_a == 'header' else ', no header'}]" if in_a else f"{label} [own token, header {role}]",
                    who, status, None if 200 <= status < 300 else body, {**extra, "headers": more}))
    return out


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_the_header_names_the_role_a_caller_acts_under_and_the_other_roles_it_holds_do_not_count(client, ids, allroles, method, path):  # noqa: F811
    cases = replay_as(ids, fresh(ids, method, path), on_clinic_a="header")
    assert cases, f"{method} {path}: no single-role case to replay"
    check_route_matrix(client, method, path, cases)


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_without_the_header_a_caller_with_every_role_is_allowed_wherever_any_single_role_was(client, ids, allroles, method, path):  # noqa: F811
    cases = replay_as(ids, fresh(ids, method, path), on_clinic_a="plain")
    if cases:  # a route with no success case for a clinic-A role (a refusal-only matrix) has nothing to replay here
        check_route_matrix(client, method, path, cases)


# ---- the refusal battery (8.4, 8.5, 8.8) --------------------------------------------------------------------------------------------------------------


def concrete(path):
    return re.sub(r"\{[^}]+\}", lambda _: str(uuid.uuid4()), path)


def battery(ids):  # noqa: F811
    """(label, token owner key, header value or list of values). Every one must give the byte-identical fixed 403."""
    a, b = ids.clinic_a, ids.clinic_b
    out = []
    fd = ids.people["front_desk_a"][0]
    # cannot widen / cannot be held (each needs one database check): a narrow role names a broader one, another clinic, an unknown clinic, a revoked grant, dead and non-staff accounts
    out += [("front desk names the broader role admin", fd, f"{a}:admin"), ("front desk names the role doctor", fd, f"{a}:doctor"), ("front desk names its own role at the other clinic", fd, f"{b}:front_desk"),
            ("front desk names an unknown clinic", fd, f"{uuid.uuid4()}:front_desk")]
    out += [("multi names a role it holds at the OTHER clinic", ids.people["multi"][0], f"{a}:doctor"), ("a revoked grant", ids.revoked_holder, f"{a}:nurse")]
    for key in ("suspended", "invited"):
        out.append((f"a {ids.people[key][2]} account names its own grant", ids.people[key][0], f"{a}:{ids.people[key][3][0][1]}"))
    out += [("a patient account", ids.people["patient"][0], f"{a}:nurse"), ("a valid user with no iam account", ids.no_account, f"{a}:nurse")]
    # malformed values are refused BEFORE the database (syntax first): cheap
    malformed = ["", " ", f"{a}:Nurse", f"{a}: nurse", f"{a}|nurse", f"{a}:", "nurse", "not-a-uuid:nurse", f"{str(a).upper()}:nurse", f"{a}:nurse'; drop table iam.staff_roles; --", "x" * 5000, f"{a}:superuser"]
    out += [(f"malformed {m[:30]!r}", fd, m) for m in malformed]
    out += [("a duplicate header", fd, [f"{a}:front_desk", f"{a}:front_desk"]), ("a duplicate with two roles", fd, [f"{a}:front_desk", f"{a}:nurse"])]
    return out


def with_values(headers, value):
    """The headers plus one X-CareFlow-Active-Role line per value (a list gives a DUPLICATE header)."""
    pairs = list(httpx.Headers(headers).multi_items()) + [(HEADER, v) for v in (value if isinstance(value, list) else [value])]
    return httpx.Headers(pairs)


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_every_header_that_cannot_be_held_or_is_malformed_is_the_same_fixed_403_with_no_side_effect(client, ids, allroles, method, path):  # noqa: F811
    target = concrete(path)  # the refusal happens before the route body, so any well-formed path will do (and no fixture pool is touched)
    failures = []
    stepped = {"aal": "aal2", "amr": [{"method": "totp", "timestamp": int(time.time()) - 5}]}  # so a step-up route's check passes and the HELD check is what answers
    for label, owner, value in battery(ids):
        response = client.request(method, target, headers=with_values({**bearer(mint(owner, **stepped)), "X-Idempotency-Key": _new_key()}, value), json={})
        if (response.status_code, response.json()) != (403, FIXED):
            failures.append(f"{label}: {response.status_code} {str(response.json())[:120]}")
    assert not failures, f"{method} {path}:\n  " + "\n  ".join(failures)


@pytest.mark.parametrize(("method", "path"), sorted(STEP_UP_ROUTES))
def test_on_a_step_up_route_the_header_syntax_comes_first_and_a_good_header_never_skips_the_step_up(client, ids, allroles, method, path):  # noqa: F811
    target = concrete(path)
    a = ids.clinic_a
    not_stepped = bearer(mint(ids.allroles, aal=None, amr=None))  # a token with no aal / amr
    stepped = bearer(mint(ids.allroles, aal="aal2", amr=[{"method": "totp", "timestamp": int(time.time()) - 5}]))
    base = {"X-Idempotency-Key": _new_key()}
    bad = client.request(method, target, headers={**not_stepped, **base, HEADER: "garbage"}, json={})
    assert (bad.status_code, bad.json()) == (403, FIXED)  # syntax first: not a 401 (the console would sign the person out), and no database
    good = client.request(method, target, headers={**not_stepped, **base, HEADER: f"{a}:front_desk"}, json={})
    assert good.status_code == 401 and good.headers.get("www-authenticate") == "step-up"  # narrowing never replaces step-up
    held_bad = client.request(method, target, headers={**stepped, **base, HEADER: f"{a}:auditor"}, json={})  # stepped up, a role the gate refuses (auditor): the route's own refusal, not the step-up one
    assert held_bad.status_code in (403, 404, 422)
    unheld = client.request(method, target, headers={**stepped, **base, HEADER: f"{ids.clinic_b}:front_desk"}, json={})
    assert (unheld.status_code, unheld.json()) == (403, FIXED)  # stepped up with a role it does not hold


# ---- cross-route behaviours -------------------------------------------------------------------------------------------------------------------------------


def test_a_forged_active_claim_inside_a_token_is_dropped_and_the_caller_keeps_exactly_their_own_access(client, ids, allroles):  # noqa: F811
    fd = ids.people["front_desk_a"][0]
    forged = {"clinic_id": str(ids.clinic_b), "role": "doctor"}
    r = client.get("/v1/staff/scheduling/providers", headers=bearer(mint(fd, careflow_active=forged)))
    assert r.status_code == 200  # had the forged claim been honoured, a front desk named as clinic B's doctor would see nothing and could not even be authorised
    r = client.get("/v1/staff/scheduling/providers", headers=bearer(mint(fd, careflow_active=None)))
    assert r.status_code == 200


def test_me_ignores_the_header_and_returns_every_grant_even_when_the_header_names_a_revoked_one(client, ids, allroles):  # noqa: F811
    multi = ids.people["multi"][0]
    plain = client.get("/v1/iam/me", headers=bearer(mint(multi)))
    assert plain.status_code == 200 and len(plain.json()["roles"]) == 2
    for value in (f"{ids.clinic_a}:nurse", "garbage", f"{ids.clinic_b}:nurse", f"{ids.clinic_a}:auditor", [f"{ids.clinic_a}:nurse", f"{ids.clinic_a}:nurse"]):
        r = client.get("/v1/iam/me", headers=with_values(bearer(mint(multi)), value))
        assert r.status_code == 200 and r.json() == plain.json(), value
    revoked = client.get("/v1/iam/me", headers={**bearer(mint(ids.revoked_holder)), HEADER: f"{ids.clinic_a}:nurse"})
    assert revoked.status_code == 200 and revoked.json()["roles"] == []  # the console can still ask who it is after its remembered role was revoked


def test_a_clinic_a_nurse_header_hides_clinic_b_on_every_list_route_for_a_multi_clinic_account(client, ids, allroles):  # noqa: F811
    multi = ids.people["multi"][0]
    a, b = str(ids.clinic_a), str(ids.clinic_b)
    lists = ["/slots", "/bookings", "/waitlist", "/providers", "/locations", "/visit-types", "/priority-levels", "/holds"]
    for suffix in lists:
        narrowed = client.get(f"/v1/staff/scheduling{suffix}", headers={**bearer(mint(multi)), HEADER: f"{a}:nurse"})
        assert narrowed.status_code in (200, 403), (suffix, narrowed.status_code)
        if narrowed.status_code == 200:
            clinics = {item.get("clinic_id") for item in narrowed.json().get("items", []) if isinstance(item, dict) and item.get("clinic_id")}
            assert b not in clinics, f"{suffix}: clinic B rows with header nurse@A"
        as_b = client.get(f"/v1/staff/scheduling{suffix}", headers={**bearer(mint(multi)), HEADER: f"{b}:doctor"})
        if as_b.status_code == 200:
            assert a not in {item.get("clinic_id") for item in as_b.json().get("items", []) if isinstance(item, dict) and item.get("clinic_id")}, f"{suffix}: clinic A rows with header doctor@B"
    for suffix in ("/referrals", "/review-tasks", "/identity/possible-duplicates", "/identity/merges"):
        r = client.get(f"/v1/staff{suffix}", headers={**bearer(mint(multi)), HEADER: f"{a}:nurse"})
        if r.status_code == 200:
            assert b not in {item.get("clinic_id") for item in r.json().get("items", []) if isinstance(item, dict) and item.get("clinic_id")}, suffix


def test_the_audit_row_names_the_active_role_and_the_scope_and_a_refused_header_writes_no_audit_row(client, ids, allroles):  # noqa: F811
    audit = "select actor_id, actor_role, details from audit.log where resource_type = 'patient' and resource_id = $1 and action = 'patient.confirm_identity'"
    narrowed, plain = new_worker_patient(ids), new_worker_patient(ids)
    r = client.post(CONFIRM.format(patient_id=narrowed), headers={**bearer(mint(ids.allroles)), "X-Idempotency-Key": _new_key(), HEADER: f"{ids.clinic_a}:front_desk"})
    assert r.status_code == 200, r.text
    (row,) = _run(_fetch(audit, str(narrowed)))
    details = row["details"] if isinstance(row["details"], dict) else json.loads(row["details"])
    assert row["actor_role"] == "front_desk" and details["role_scope"] == "active" and row["actor_id"] == ids.allroles
    r = client.post(CONFIRM.format(patient_id=plain), headers={**bearer(mint(ids.people["front_desk_a"][0])), "X-Idempotency-Key": _new_key()})
    assert r.status_code == 200
    (row,) = _run(_fetch(audit, str(plain)))
    details = row["details"] if isinstance(row["details"], dict) else json.loads(row["details"])
    assert details["role_scope"] == "all_grants"
    before = _run(_fetch("select count(*) as n from audit.log where clinic_id = $1", ids.clinic_a))[0]["n"]
    refused = client.post(CONFIRM.format(patient_id=new_worker_patient(ids)), headers={**bearer(mint(ids.allroles)), "X-Idempotency-Key": _new_key(), HEADER: f"{ids.clinic_a}:auditor"})
    assert refused.status_code in (403, 404)  # held, but the auditor may not confirm: the route's own refusal
    garbage = client.post(CONFIRM.format(patient_id=new_worker_patient(ids)), headers={**bearer(mint(ids.allroles)), "X-Idempotency-Key": _new_key(), HEADER: "garbage"})
    assert (garbage.status_code, garbage.json()) == (403, FIXED)
    assert _run(_fetch("select count(*) as n from audit.log where clinic_id = $1", ids.clinic_a))[0]["n"] == before  # neither refusal wrote an audit row


def test_an_active_role_never_leaks_into_the_next_request_on_the_same_pooled_connection(ids, allroles):  # noqa: F811
    """One connection in the pool: the first transaction sets claims WITH an active role, the second sets none and must see every grant."""
    engine = make_app_engine(APP_URL or "", pool_size=1, max_overflow=0, pool_pre_ping=True)

    async def run():
        try:
            for _ in range(3):
                async with engine.connect() as conn, conn.begin():
                    await _begin_request(conn, {"sub": str(ids.allroles), "role": "authenticated"}, active_role.ActiveRole(ids.clinic_a, "nurse"))
                    assert (await conn.execute(text("select iam.role_scope()"))).scalar() == "active"
                    assert (await conn.execute(text("select iam.effective_roles(:c)"), {"c": ids.clinic_a})).scalar() == ["nurse"]
                async with engine.connect() as conn, conn.begin():
                    await _begin_request(conn, {"sub": str(ids.allroles), "role": "authenticated"}, None)
                    assert (await conn.execute(text("select iam.role_scope()"))).scalar() == "all_grants"
                    assert set((await conn.execute(text("select iam.effective_roles(:c)"), {"c": ids.clinic_a})).scalar()) == set(STAFF_ROLES)
                async with engine.connect() as conn, conn.begin():  # a transaction that never sets claims at all: nothing from the earlier ones
                    assert (await conn.execute(text("select coalesce(current_setting('request.jwt.claims', true), '')"))).scalar() in ("", None)
        finally:
            await engine.dispose()

    asyncio.run(asyncio.wait_for(run(), 120))


def test_every_staff_route_has_the_header_dimension(app):  # noqa: F811
    """Rule 8, extended: the header dimension is built from MATRIX, so it covers every staff route there; this fails if a served /v1 route is neither in MATRIX nor exempt."""
    served = {(m.upper(), p) for p, ops in app.openapi()["paths"].items() if p.startswith("/v1/staff") for m in ops}
    separate = {("POST", "/v1/staff/messages/{message_id}/screen")}  # the screen route's matrix is a never-committed harness (tests/test_api_screen_message_live.py): its header cases live there
    assert served - separate == set(ROUTES), "a staff route is outside the active-role matrix"
    assert EXEMPT == {("GET", "/v1/iam/me")}
