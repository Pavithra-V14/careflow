"""The ACTIVE ROLE, offline (docs/design/active-role.md; owner decision 2026-10-31): the strict header parser, the claims the server writes, the one-statement check, the order of the checks on a step-up route,
the CORS allowance, and the INVENTORY that fails on any new direct reader of iam.staff_roles. The database behaviour (the helpers, the 13 role-matrix cases per staff route) is in
tests/test_api_role_matrix.py and tests/test_active_role_live.py.
"""

import json
import re
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from careflow import active_role
from careflow.app import Settings, _begin_request, create_app, db_conn, verified_claims
from careflow.auth import JWKSClient
from careflow.cors import ALLOW_HEADERS

ROOT = Path(__file__).resolve().parent.parent
CLINIC = uuid.UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
GOOD = f"{CLINIC}:nurse"


def test_the_header_parses_strictly():
    assert active_role.parse([]) is None
    assert active_role.parse([GOOD]) == active_role.ActiveRole(CLINIC, "nurse")
    for role in sorted(active_role.ROLES):
        assert active_role.parse([f"{CLINIC}:{role}"]).role == role  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "bad",
    [
        "", " ", GOOD + " ", " " + GOOD, GOOD.replace(":", "|"), GOOD.replace(":", "::"), f"{CLINIC}:Nurse", f"{CLINIC}:NURSE", f"{CLINIC}:", ":nurse", "nurse", str(CLINIC),
        f"{str(CLINIC).upper()}:nurse", f"{{{CLINIC}}}:nurse", f"{str(CLINIC).replace('-', '')}:nurse", "not-a-uuid:nurse", f"{CLINIC}:nurse'; drop table iam.staff_roles; --",
        f"{CLINIC}:nurse,doctor", f"{CLINIC}:superuser", f"{CLINIC}:patient", f"{CLINIC}:guardian", f"{CLINIC}:nurse\n", f"{CLINIC}:nurse\x00", "x" * 5000, f"{CLINIC}:" + "a" * 70,
    ],
)
def test_every_malformed_value_is_refused_with_a_reason_code_and_never_echoed(bad):
    with pytest.raises(active_role.ActiveRoleRefused) as caught:
        active_role.parse([bad])
    assert bad.strip() == "" or bad not in str(caught.value) + caught.value.reason


def test_a_repeated_header_is_refused():
    with pytest.raises(active_role.ActiveRoleRefused):
        active_role.parse([GOOD, GOOD])
    with pytest.raises(active_role.ActiveRoleRefused):
        active_role.parse([GOOD, f"{CLINIC}:doctor"])


def test_the_claims_carry_the_servers_own_active_claim_and_drop_any_forged_one():
    token = {"sub": "u", "role": "authenticated", "careflow_active": {"clinic_id": str(uuid.uuid4()), "role": "admin"}}
    active = active_role.ActiveRole(CLINIC, "nurse")
    scoped = active_role.claims_with_active(token, active)
    assert scoped["careflow_active"] == {"clinic_id": str(CLINIC), "role": "nurse"} and scoped["sub"] == "u"
    assert "careflow_active" in token  # the input is not mutated
    assert "careflow_active" not in active_role.claims_with_active(token, None)  # no header: a forged claim from the token is still dropped
    assert active_role.claims_with_active({"sub": "u"}, None) == {"sub": "u"}


class _Scalar:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class RecordingConn:
    def __init__(self, ok=True):
        self.ok, self.statements = ok, []

    async def execute(self, statement, params=None):
        self.statements.append((str(statement), params))
        return _Scalar(self.ok)


@pytest.mark.asyncio
async def test_without_a_header_one_statement_sets_the_claims_exactly_as_before():
    conn = RecordingConn()
    await _begin_request(conn, {"sub": "u", "careflow_active": {"role": "admin"}}, None)  # type: ignore[arg-type]
    (sql, params), = conn.statements
    assert "set_config('request.jwt.claims'" in sql and "active_grant_ok" not in sql
    assert json.loads(params["c"]) == {"sub": "u"}


@pytest.mark.asyncio
async def test_with_a_header_ONE_statement_sets_the_claims_and_checks_the_grant_no_extra_round_trip():
    conn = RecordingConn(ok=True)
    await _begin_request(conn, {"sub": "u"}, active_role.ActiveRole(CLINIC, "nurse"))  # type: ignore[arg-type]
    (sql, params), = conn.statements
    assert sql.count("set_config") == 1 and "iam.active_grant_ok(set_config(" in sql  # the argument is evaluated first: the claims are in force when the check runs
    assert json.loads(params["c"]) == {"sub": "u", "careflow_active": {"clinic_id": str(CLINIC), "role": "nurse"}}
    assert str(CLINIC) not in sql and "nurse" not in sql  # the header's values are bound parameters, never part of the SQL text


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [False, None, 0, "t", 1])
async def test_anything_but_a_true_answer_is_the_one_fixed_refusal(answer):
    conn = RecordingConn(ok=answer)
    with pytest.raises(HTTPException) as caught:
        await _begin_request(conn, {"sub": "u"}, active_role.ActiveRole(CLINIC, "nurse"))  # type: ignore[arg-type]
    assert caught.value.status_code == 403 and caught.value.detail == "active role not available"


# ---- the order of the checks on a step-up route -----------------------------------------------------------------------------------------------------

PROJECT = "https://example-project.supabase.co"
NOW = 1_800_000_000.0
MERGE_ID = uuid.uuid4()


def _client(claims):
    async def fake_claims():
        return claims

    async def fake_db():  # never reached by the refusals below
        raise AssertionError("the database was reached")
        yield

    settings = Settings(_env_file=None, supabase_url=PROJECT, database_url_app="postgresql://unused", step_up_max_age_seconds=300)
    app = create_app(settings, jwks=JWKSClient(PROJECT + "/auth/v1/.well-known/jwks.json"), engine=object(), clock=lambda: NOW)  # type: ignore[arg-type]
    app.dependency_overrides[verified_claims] = fake_claims
    app.dependency_overrides[db_conn] = fake_db
    return app


def test_a_bad_header_is_refused_before_the_step_up_check_and_a_good_one_does_not_skip_it():
    not_stepped = {"sub": str(uuid.uuid4()), "role": "authenticated"}
    with TestClient(_client(not_stepped), raise_server_exceptions=False) as c:
        _run_order(c)


def _run_order(c):
    url = f"/v1/staff/merges/{MERGE_ID}/approve"
    bad = c.post(url, headers={"X-Idempotency-Key": "k", active_role.HEADER: "garbage"})
    assert bad.status_code == 403 and bad.json() == {"detail": "active role not available"}  # syntax first, no database, and not a 401 (the console would sign the person out)
    good = c.post(url, headers={"X-Idempotency-Key": "k", active_role.HEADER: GOOD})
    assert good.status_code == 401 and good.headers["www-authenticate"] == "step-up"  # narrowing never replaces the step-up check
    none = c.post(url, headers={"X-Idempotency-Key": "k"})
    assert none.status_code == 401 and none.headers["www-authenticate"] == "step-up"  # no header: today's behaviour


# ---- CORS ------------------------------------------------------------------------------------------------------------------------------------------------


def test_the_browser_may_send_the_header_at_preflight():
    assert active_role.HEADER in ALLOW_HEADERS


# ---- the inventory: no new direct reader of iam.staff_roles ---------------------------------------------------------------------------------------


# Every file that may NAME iam.staff_roles, with the reason it is allowed. A new reader (Python or SQL) must go through iam.has_clinic_role / iam.has_any_role / iam.effective_roles, which honour the
# active role; if it genuinely cannot (it is about ANOTHER account, or it is not a request path), it is added here WITH ITS REASON in the change that adds it, so a reviewer sees it.
ALLOWED = {
    "careflow/app.py": "GET /v1/iam/me lists every live grant on purpose (exactly one query, checked below)",
    "careflow/ops_checks.py": "an operational report, not a request path (finds accounts holding queue_worker with another role)",
    "db/migrations/0001_initial_schema.sql": "the table, its policies and the two original helpers (replaced by 0028)",
    "db/migrations/0006_outbox_and_queue.sql": "alters the role check constraint only",
    "db/migrations/0007_red_flag_rules_schema.sql": "has_any_role (replaced by 0028) and policies that go through it",
    "db/migrations/0016_spend_ledger_and_registry_approval.sql": "the approval trigger checks OTHER accounts (created_by, approved_by), not the caller; narrowing applies to checks about the caller only",
    "db/migrations/0024_worker_role_exclusive.sql": "the queue_worker exclusivity trigger: a property of the grants table itself, not of a request",
    "db/migrations/0028_active_role.sql": "the helpers that implement the active role",
}


def _mentions(path: Path) -> bool:
    return "staff_roles" in path.read_text(encoding="utf-8")


def test_only_the_listed_files_name_staff_roles():
    files = [*sorted((ROOT / "careflow").rglob("*.py")), *sorted((ROOT / "db" / "migrations").glob("*.sql"))]
    named = {p.relative_to(ROOT).as_posix() for p in files if _mentions(p)}
    assert named <= set(ALLOWED), f"a new direct reader of iam.staff_roles (it would ignore the active role): {sorted(named - set(ALLOWED))}"


def test_the_application_reads_staff_roles_in_exactly_one_place_the_me_route():
    text = (ROOT / "careflow" / "app.py").read_text(encoding="utf-8")
    assert len(re.findall(r"iam\.staff_roles", text)) == 1
    assert text.index("iam.staff_roles") < text.index("# ---- staff scheduling")  # and it is inside the /v1/iam/me handler, before the staff routes


def test_no_python_module_selects_from_staff_roles_except_the_two_allowed():
    for path in sorted((ROOT / "careflow").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if _mentions(path):
            assert rel in {"careflow/app.py", "careflow/ops_checks.py"}, rel
