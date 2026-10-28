"""The role matrix for POST /v1/staff/messages/{message_id}/screen, against the real database (BUILD-SPEC.md §18.4, §21; CLAUDE.md rule 8).

WHY A SEPARATE HARNESS. tests/test_api_role_matrix.py commits its fixture rows and deletes them afterwards. This route screens ENCRYPTED
MESSAGES and writes screenings and audit rows, and msg.messages, msg.screenings and audit.log are append-only for every role, so a
committed fixture could never be cleaned up. Here the app is driven over HTTP (httpx ASGI transport) with real signed tokens and the real
auth dependency, but its database dependency is replaced by ONE shared connection inside a never-committed outer transaction: each
request runs in a savepoint with `set local role careflow_app` and the verified claims set, exactly as `db_conn` does, so row-level
security and msg.record_screening are genuinely in the path, and everything is rolled back at the end. tests/test_api_role_matrix.py
registers this route (SEPARATE_HARNESS) so `test_every_route_is_in_the_matrix` still fails if the route or this file goes missing.

# CLINICAL-PLACEHOLDER: needs clinician sign-off
The only rule used is a SYNTHETIC token (`zzalpha`); nothing here is a clinical term, and no emergency reply wording exists anywhere.

WHAT THIS HARNESS DOES NOT COVER, and where that IS covered (documented exception to CLAUDE.md rule 8; docs/design/role-matrix-exceptions.md):
  * the real transaction-mode pooler engine and the real restricted login: it uses SET LOCAL ROLE careflow_app on a shared connection.
    Covered instead by tests/test_db_engine_config.py, scripts/verify_app_role.py and the committed-fixture matrix in tests/test_api_role_matrix.py.
  * commit-before-response (a 200 means the write is durable): nothing here commits. Covered by the scheduling write tests in
    tests/test_api_role_matrix.py (test_a_write_is_committed_before_its_response_is_returned) and the TxConn scope="function" dependency.
  * the deferred "a patient-kind message needs a screening by commit" trigger: it only fires at commit, which never happens here.
    Covered by the 0013 live tests in tests/test_rls_screenings_live.py (SET CONSTRAINTS ... IMMEDIATE inside the rolled-back transaction).
It must run in CI with ZERO skips: its only skip condition is a missing DATABASE_URL_MIGRATOR, which CI provides, and CI fails on any skip.
"""

import base64
import json
import os
import time
import uuid
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from dotenv import dotenv_values
from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from careflow.app import Settings, create_app, db_conn, verified_claims
from careflow.crypto import DemographicVault
from careflow.db import set_request_claims
from careflow.messages import encrypt_body
from tests.db_helpers import ensure_app_role_sa
from tests.test_api_role_matrix import ATTACKER, PROJECT, bearer, fake_jwks_client, mint

ROOT = Path(__file__).resolve().parent.parent
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(ROOT / ".env").get("DATABASE_URL_MIGRATOR")
PATH = "/v1/staff/messages/{}/screen"
TOPICS = ("care", "appointments", "safety")
PRIVATE = "my-private-sentence-4417"  # appears in every stored body and must NEVER appear in a response

pytestmark = [
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured"),
]

# who may screen which topic (they must be able to SEE the message; everyone else gets the same 404)
SCREENS = {
    "nurse_a": set(TOPICS), "doctor_a": set(TOPICS), "fd_a": {"appointments", "safety"},
    "multi": set(TOPICS),  # a nurse in clinic A who is a doctor in clinic B: allowed here, and only here
    "worker_a": set(), "admin_a": set(), "auditor_a": set(), "owner_a": set(), "nurse_b": set(), "fd_b": set(),
    "patient": set(), "guardian": set(), "invited": set(), "suspended": set(),
}
# Read by tests/test_api_role_matrix.py::test_separately_tested_routes_have_a_dedicated_matrix_that_covers_them
COVERED = {("POST", "/v1/staff/messages/{message_id}/screen"): tuple(SCREENS)}


def _u() -> uuid.UUID:
    return uuid.uuid4()


def _keys(*versions) -> dict[str, str]:
    return {f"CAREFLOW_{k}_SECRET_V{v}": base64.b64encode(os.urandom(32)).decode() for v in versions for k in ("ENC", "HMAC")}


async def raw(conn, sql):
    handle = await conn.get_raw_connection()
    await handle.driver_connection.execute(sql)


def ct(enc) -> str:
    return "'\\x" + enc.body_ct.hex() + "'::bytea"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world():
    engine = create_async_engine(make_url(MIGRATOR or "").set(drivername="postgresql+asyncpg"), connect_args={"statement_cache_size": 0})
    conn = await engine.connect()
    outer = await conn.begin()
    try:
        # Read-only precondition: committed approved rules with criteria would change every outcome below. Nothing is deleted.
        n = (await conn.execute(text(
            "select count(*) from kb.red_flag_rules where origin = 'test' and status = 'approved' and (cardinality(phrases) > 0 or cardinality(patterns) > 0)"
        ))).scalar_one()
        if n:
            pytest.fail(f"This suite assumes the database holds NO committed approved origin='test' red-flag rule with criteria (it inserts its own inside a rolled-back transaction); found {n}. Such a rule changes what screenings and rule-set hashes the tests expect. Nothing was deleted: an engineer with the migrator credential must retire or remove those rules deliberately, then re-run.", pytrace=False)
        await ensure_app_role_sa(conn)
        vault = DemographicVault.from_env(_keys(1, 2))
        other = DemographicVault.from_env(_keys(9))  # a key this app does not have
        a, b = _u(), _u()
        who = [("nurse_a", a, "nurse"), ("doctor_a", a, "doctor"), ("fd_a", a, "front_desk"), ("worker_a", a, "queue_worker"),
               ("admin_a", a, "admin"), ("auditor_a", a, "auditor"), ("owner_a", a, "business_owner"), ("nurse_b", b, "nurse"),
               ("fd_b", b, "front_desk"), ("suspended", a, "nurse"), ("invited", a, "nurse"), ("multi", a, "nurse"), ("multi", b, "doctor")]
        ids = {k: _u() for k, _, _ in who}  # one account per key, however many grants it holds
        ids["patient"], ids["guardian"], ids["no_account"] = _u(), _u(), _u()
        statuses = {"suspended": "suspended", "invited": "invited"}
        await raw(
            conn,
            f"insert into iam.clinics (id, name, origin) values ('{a}', 'Screen A', 'test'), ('{b}', 'Screen B', 'test'); "
            "insert into iam.accounts (id, account_type, status, origin) values "
            + ", ".join(f"('{ids[k]}', 'staff', '{statuses.get(k, 'active')}', 'test')" for k in dict.fromkeys(k for k, _, _ in who))
            + f", ('{ids['patient']}', 'patient', 'active', 'test'), ('{ids['guardian']}', 'guardian', 'active', 'test'); "
            "insert into iam.staff_roles (account_id, clinic_id, role, origin) values "
            + ", ".join(f"('{ids[k]}', '{c}', '{r}', 'test')" for k, c, r in who) + ";",
        )
        patient, thread = _u(), {t: _u() for t in TOPICS}
        pct = "'\\x" + "01" * 40 + "'::bytea"
        await raw(
            conn,
            "insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, origin, idempotency_key) "
            f"values ('{patient}', '{a}', {pct}, 1, {pct}, 1, 'test', 'p-{patient}'); "
            f"select set_config('request.jwt.claims', '{{\"sub\": \"{ids['nurse_a']}\", \"role\": \"authenticated\"}}', true); "
            + " ".join(
                "insert into msg.threads (id, clinic_id, patient_id, topic, created_by, origin, idempotency_key) "
                f"values ('{thread[t]}', '{a}', '{patient}', '{t}', '{ids['nurse_a']}', 'test', 't-{thread[t]}'); " for t in TOPICS
            ),
        )
        messages: dict[str, uuid.UUID] = {}

        async def add(label, topic, body, *, v=vault, version=1, bind_to=None, erased=False):
            mid = _u()
            enc = encrypt_body(v, body, clinic_id=a, thread_id=thread[topic], key_version=version, message_id=bind_to or mid)
            await raw(
                conn,
                "insert into msg.messages (id, clinic_id, origin, topic, thread_id, sender_kind, recorded_by, body_ct, body_key_version, idempotency_key) "
                f"values ('{mid}', '{a}', 'test', '{topic}', '{thread[topic]}', 'patient', '{ids['nurse_a']}', {ct(enc)}, {version}, 'm-{mid}'); "
                + (f"update msg.messages set body_ct = null, body_key_version = null, erased_at = now() where id = '{mid}'; " if erased else ""),
            )
            messages[label] = mid

        for topic in TOPICS:
            await add(topic, topic, f"please note {PRIVATE} about this appointment")
        await add("tampered", "care", f"{PRIVATE} tampered", bind_to=_u())
        await add("erased", "care", f"{PRIVATE} erased", erased=True)
        await add("other_key", "care", f"{PRIVATE} other key", v=other, version=9)
        # patient-kind messages must be screened before commit; nothing here commits, so the deferred check never fires.
        await raw(conn, "select set_config('request.jwt.claims', '', true);")

        app = create_app(
            Settings(supabase_url=PROJECT, database_url_app="postgresql://unused"), jwks=fake_jwks_client(), engine=object(),  # type: ignore[arg-type]
            vault=vault,
        )

        async def shared_db(claims=Depends(verified_claims)):
            async with conn.begin_nested():
                await conn.exec_driver_sql("set local role careflow_app")
                await set_request_claims(conn, claims)
                yield conn
                # SET LOCAL survives a RELEASED savepoint (only a rollback undoes it), so put the owner role back for the next
                # fixture statement. If the route raised, the savepoint rollback restores both.
                await conn.exec_driver_sql("reset role")
                await conn.execute(text("select set_config('request.jwt.claims', '', true)"))

        app.dependency_overrides[db_conn] = shared_db
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                yield type("World", (), {"conn": conn, "client": client, "ids": ids, "a": a, "b": b, "messages": messages, "vault": vault,
                                         "thread": thread})
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


async def post(w, who, message, *, key="new", token=None):
    headers = {} if key is None else {"X-Idempotency-Key": f"k-{_u()}" if key == "new" else key}
    headers.update(bearer(token) if token else bearer(mint(w.ids[who])) if who else {})
    return await w.client.post(PATH.format(message), headers=headers)


async def one(w, sql, **params):
    return (await w.conn.execute(text(sql), params)).all()


def assert_ids_only(response, w):
    body = response.json()
    assert set(body) == {"message_id", "screening_id", "outcome", "replayed", "patient_reply"}
    assert PRIVATE not in response.text and "zzalpha" not in response.text
    return body


# ---- the matrix ------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("who", list(SCREENS))
async def test_who_can_screen_which_topic(w, who):
    for topic in TOPICS:
        r = await post(w, who, w.messages[topic])
        if topic in SCREENS[who]:
            assert r.status_code == 200, (who, topic, r.text)
            body = assert_ids_only(r, w)
            assert body["outcome"] in {"unassessable", "clear", "red_flag"} and body["message_id"] == str(w.messages[topic])
        else:
            assert (r.status_code, r.json()) == (404, {"detail": "not found"}), (who, topic)


async def test_a_valid_user_with_no_account_and_an_unknown_message_get_the_same_404(w):
    known_but_invisible = await post(w, "fd_a", w.messages["care"])
    unknown = await post(w, "nurse_a", _u())
    no_account = await w.client.post(PATH.format(w.messages["appointments"]), headers={**bearer(mint(w.ids["no_account"])), "X-Idempotency-Key": "k"})
    assert {(r.status_code, r.text) for r in (known_but_invisible, unknown, no_account)} == {(404, '{"detail":"not found"}')}


async def test_authentication_comes_first_for_every_kind_of_bad_credential(w):
    sub = w.ids["nurse_a"]
    target = w.messages["appointments"]
    for label, headers in (
        ("no header", {}),
        ("garbage", bearer("not.a.jwt")),
        ("expired", bearer(mint(sub, exp=int(time.time()) - 3600))),
        ("other key under the right kid", bearer(mint(sub, key=ATTACKER))),
        ("anonymous role", bearer(mint(sub, role="anon"))),
    ):
        for extra in ({"X-Idempotency-Key": "k"}, {}):  # even without the key: 401, not 422
            r = await w.client.post(PATH.format(target), headers={**headers, **extra})
            assert r.status_code == 401, (label, extra)


async def test_the_idempotency_key_and_the_path_are_validated(w):
    for key in (None, "", "   "):
        assert (await post(w, "nurse_a", w.messages["care"], key=key)).status_code == 422, key
    assert (await w.client.post(PATH.format("nope"), headers={**bearer(mint(w.ids["nurse_a"])), "X-Idempotency-Key": "k"})).status_code == 422


# ---- behaviour ---------------------------------------------------------------------------------------------------------------------


async def test_a_replay_with_the_same_key_returns_the_original_and_a_new_key_is_a_no_op_not_a_second_task(w):
    first = await post(w, "nurse_a", w.messages["care"], key="same")
    again = await post(w, "nurse_a", w.messages["care"], key="same")
    other = await post(w, "nurse_a", w.messages["care"], key="different")
    a, b, c = first.json(), again.json(), other.json()
    assert (a["replayed"], b["replayed"], c["replayed"]) == (False, True, True)
    assert a["screening_id"] == b["screening_id"] == c["screening_id"]
    assert len(await one(w, "select id from agents.review_tasks where source_message_id = :m", m=str(w.messages["care"]))) == 1


async def test_a_red_flag_raises_the_immediate_nurse_task_and_nothing_is_sent_to_the_patient(w, caplog):
    """# CLINICAL-PLACEHOLDER: needs clinician sign-off. No emergency reply exists: the screening only records that one is pending."""
    rid = _u()
    await w.conn.execute(
        text(
            "insert into kb.red_flag_rules (id, clinic_id, rule_code, version_id, status, phrases, patterns, governance_metadata, authored_by, "
            "approved_by_1, approved_by_2, approved_at, origin, idempotency_key) values (:i, null, 'screen_route_rule', 1, 'approved', "
            "array['zzalpha'], '{}', cast('{}' as jsonb), :d, :d, :n, now(), 'test', :k)"
        ),
        {"i": rid, "d": w.ids["doctor_a"], "n": w.ids["nurse_a"], "k": f"r-{rid}"},
    )
    mid = _u()
    enc = encrypt_body(w.vault, f"{PRIVATE} and zzalpha here", clinic_id=w.a, thread_id=w.thread["appointments"], key_version=1, message_id=mid)
    await raw(
        w.conn,
        f"select set_config('request.jwt.claims', '{{\"sub\": \"{w.ids['nurse_a']}\", \"role\": \"authenticated\"}}', true); "
        "insert into msg.messages (id, clinic_id, origin, topic, thread_id, sender_kind, recorded_by, body_ct, body_key_version, idempotency_key) "
        f"values ('{mid}', '{w.a}', 'test', 'appointments', '{w.thread['appointments']}', 'patient', '{w.ids['nurse_a']}', {ct(enc)}, 1, 'm-{mid}'); "
        "select set_config('request.jwt.claims', '', true);",
    )
    before = (await one(w, "select count(*) from msg.messages where clinic_id = :c", c=w.a))[0][0]
    caplog.set_level("DEBUG")
    r = await post(w, "fd_a", mid)  # front desk may screen an appointments message
    assert r.status_code == 200
    body = assert_ids_only(r, w)
    assert (body["outcome"], body["patient_reply"]) == ("red_flag", "pending_clinician_template")
    ((kind, priority, role, source),) = await one(w, "select kind, priority, assigned_role, source_message_id from agents.review_tasks where source_message_id = :m", m=str(mid))
    assert (kind, priority, role, source) == ("red_flag", "immediate", "nurse", str(mid))
    assert (await one(w, "select count(*) from msg.messages where clinic_id = :c", c=w.a))[0][0] == before  # no reply (or any message) was written
    assert (await one(w, "select count(*) from msg.messages where sender_kind = 'system' and clinic_id = :c", c=w.a))[0][0] == 0
    # Neither the stored task nor any log line carries the patient's words or the rule phrase (ids, versions and offsets only).
    ((provenance,),) = await one(w, "select provenance from agents.review_tasks where source_message_id = :m", m=str(mid))
    stored = provenance if isinstance(provenance, str) else json.dumps(provenance)
    assert PRIVATE not in stored and "zzalpha" not in stored and PRIVATE not in caplog.text and "zzalpha" not in caplog.text


async def test_a_body_that_fails_authentication_is_recorded_unassessable_with_a_nurse_task_and_one_route_warning(w, caplog):
    caplog.set_level("DEBUG")
    r = await post(w, "nurse_a", w.messages["tampered"])
    assert r.status_code == 200 and assert_ids_only(r, w)["outcome"] == "unassessable"
    ((kind,),) = await one(w, "select kind from agents.review_tasks where source_message_id = :m", m=str(w.messages["tampered"]))
    assert kind == "screening_unassessable"
    warnings = [x.getMessage() for x in caplog.records if x.name == "careflow.app" and x.levelno >= 30]
    assert len(warnings) == 1 and str(w.messages["tampered"]) in warnings[0] and str(w.a) in warnings[0] and "DecryptionError" in warnings[0]
    assert PRIVATE not in caplog.text  # ids and the error class only: never any body text or key material


async def test_an_erased_message_is_409_for_those_who_can_see_it_and_404_for_those_who_cannot(w):
    assert (await post(w, "nurse_a", w.messages["erased"])).status_code == 409
    assert (await post(w, "fd_a", w.messages["erased"])).status_code == 404  # a care message: invisible to front desk


async def test_a_key_version_that_is_not_loaded_is_recorded_unassessable_with_a_nurse_task_and_one_route_warning(w, caplog):
    """Unknown routes to a human: the message is NOT left unscreened and there is no bare 503 for a key version that is missing."""
    caplog.set_level("DEBUG")
    r = await post(w, "nurse_a", w.messages["other_key"])
    assert r.status_code == 200 and assert_ids_only(r, w)["outcome"] == "unassessable"
    ((kind,),) = await one(w, "select kind from agents.review_tasks where source_message_id = :m", m=str(w.messages["other_key"]))
    assert kind == "screening_unassessable"
    warnings = [x.getMessage() for x in caplog.records if x.name == "careflow.app" and x.levelno >= 30]
    assert len(warnings) == 1 and "UnknownKeyVersionError" in warnings[0] and str(w.messages["other_key"]) in warnings[0]
    assert PRIVATE not in caplog.text


async def test_every_screening_writes_an_audit_row_with_ids_only(w):
    r = await post(w, "doctor_a", w.messages["safety"])
    sid = r.json()["screening_id"]
    ((details, actor),) = await one(w, "select details, actor_id from audit.log where resource_id = :s", s=sid)
    details = details if isinstance(details, dict) else json.loads(details)
    assert set(details) == {"message_id", "outcome", "profile"} and actor == w.ids["doctor_a"] and PRIVATE not in json.dumps(details)


async def test_there_is_no_route_level_role_gate_a_database_role_change_applies_on_the_next_request(w):
    """Row-level security (who can SEE the message) and msg.record_screening decide, not this route: revoking a grant takes effect at once."""
    assert (await post(w, "nurse_a", w.messages["care"])).status_code == 200
    await w.conn.execute(text("update iam.staff_roles set revoked_at = now() where account_id = :a and role = 'nurse' and clinic_id = :c"), {"a": w.ids["nurse_a"], "c": w.a})
    assert (await post(w, "nurse_a", w.messages["care"])).status_code == 404
