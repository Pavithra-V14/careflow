# ruff: noqa: F811  (the imported pytest fixtures `w` and `world` are used by name as test parameters)
"""The topic-dependent half of the role matrix for GET /v1/staff/messages/{message_id}, against the real database (BUILD-SPEC.md 9.4, 18.4, 21;
CLAUDE.md rule 8). It is the DEDICATED half of a SPLIT matrix: tests/test_api_role_matrix.py holds the committed half.

WHY A DEDICATED HALF. Reading needs stored messages, and msg.threads / msg.messages are append-only for every role, including the table
owner, so a committed fixture could never be cleaned up. Here the app is driven over HTTP (httpx ASGI transport) with real signed tokens and
the real auth dependency, but its database dependency is ONE shared connection inside a never-committed outer transaction: each request
runs in a savepoint with `set local role careflow_app` and the verified claims set, exactly as `db_conn` does, so row-level security on
msg.messages and the audit.log insert policy are genuinely in the path, and everything rolls back at the end. The world (clinics, accounts,
messages) is the one tests/test_api_screen_message_live.py builds; this module imports that fixture, NOT that module's exception to rule 8.

WHAT THIS HALF COVERS (and the committed half does not): who may read which topic (every required role on all three topics), the audit
row written by each 200 (ids only, the authorising role, a server-made key) and none for a refusal, no-store, 410 for an erased message,
the fixed 500/503 for a tampered or unreadable body, and that no body text reaches a log or an error.

WHAT IT DOES NOT COVER, and where that IS covered:
  * the real transaction-mode pooler engine and the real restricted login (it uses SET LOCAL ROLE on a shared connection): the committed
    half in tests/test_api_role_matrix.py runs this same route on the real pooler role (every identity, authentication, validation, and
    that refusals write no audit row), plus tests/test_db_engine_config.py and scripts/verify_app_role.py.
  * commit-before-response (a 200 means the audit row is durable): nothing commits here. Covered by the TxConn scope="function" dependency
    and test_a_write_is_committed_before_its_response_is_returned in tests/test_api_role_matrix.py (the audit insert is on that same
    TxConn connection: tests/test_api_read_message_route.py and tests/test_message_read.py).
  * the audit.log append-only trigger itself: tests/test_rls_iam_audit_live.py.
It must run in CI with ZERO skips: its only skip condition is a missing DATABASE_URL_MIGRATOR, which CI provides.
"""

import json
import time
import uuid

import pytest
from sqlalchemy import text

from tests.test_api_role_matrix import ATTACKER, bearer, mint
from tests.test_api_screen_message_live import (  # noqa: F401  (fixtures)
    MIGRATOR,
    PRIVATE,
    TOPICS,
    w,
    world,
)

pytestmark = [
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured"),
]

PATH = "/v1/staff/messages/{}"
BODY = f"please note {PRIVATE} about this appointment"

# who may READ which topic (they must be able to SEE the message; everyone else gets the same 404)
READS = {
    "nurse_a": set(TOPICS), "doctor_a": set(TOPICS), "fd_a": {"appointments", "safety"},
    "multi": set(TOPICS),  # a nurse in clinic A who is a doctor in clinic B: allowed here, and only here
    "worker_a": set(), "admin_a": set(), "auditor_a": set(), "owner_a": set(), "nurse_b": set(), "fd_b": set(),
    "patient": set(), "guardian": set(), "invited": set(), "suspended": set(),
}
# the role the audit row names for a successful read
AUDITED_AS = {"nurse_a": "nurse", "doctor_a": "doctor", "fd_a": "front_desk", "multi": "nurse"}
# Read by tests/test_api_role_matrix.py::test_split_routes_have_both_halves_and_the_dedicated_half_covers_every_role_on_every_topic
COVERED_GRID = {("GET", "/v1/staff/messages/{message_id}"): {who: tuple(TOPICS) for who in READS}}


async def get(w, who, message, *, token=None):
    headers = bearer(token) if token else bearer(mint(w.ids[who])) if who else {}
    return await w.client.get(PATH.format(message), headers=headers)


async def rows(w, sql, **params):
    return (await w.conn.execute(text(sql), params)).all()


async def audit_rows(w, message):
    return await rows(w, "select actor_id, actor_role, origin, clinic_id, details, idempotency_key, resource_type, resource_id, action "
                         "from audit.log where action = 'message.read' and resource_id = :m order by id", m=str(message))


# ---- the matrix ------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("who", list(READS))
async def test_who_can_read_which_topic(w, who):
    for topic in TOPICS:
        r = await get(w, who, w.messages[topic])
        if topic in READS[who]:
            assert r.status_code == 200, (who, topic, r.text)
            assert set(r.json()) == {"message_id", "thread_id", "topic", "sender_kind", "body"}
            assert r.json()["body"] == BODY and r.json()["topic"] == topic and r.json()["message_id"] == str(w.messages[topic])
            assert r.headers["cache-control"] == "no-store"
        else:
            assert (r.status_code, r.json()) == (404, {"detail": "not found"}), (who, topic)
            assert "cache-control" not in r.headers and PRIVATE not in r.text


async def test_a_valid_user_with_no_account_and_an_unknown_message_get_the_same_404(w):
    known_but_invisible = await get(w, "fd_a", w.messages["care"])
    unknown = await get(w, "nurse_a", uuid.uuid4())
    no_account = await get(w, "no_account", w.messages["appointments"])
    assert {(r.status_code, r.text) for r in (known_but_invisible, unknown, no_account)} == {(404, '{"detail":"not found"}')}


async def test_authentication_comes_first_for_every_kind_of_bad_credential(w):
    sub = w.ids["nurse_a"]
    for label, headers in (
        ("no header", {}),
        ("garbage", bearer("not.a.jwt")),
        ("expired", bearer(mint(sub, exp=int(time.time()) - 3600))),
        ("other key under the right kid", bearer(mint(sub, key=ATTACKER))),
        ("anonymous role", bearer(mint(sub, role="anon"))),
    ):
        r = await w.client.get(PATH.format(w.messages["appointments"]), headers=headers)
        assert r.status_code == 401 and PRIVATE not in r.text, label


async def test_a_malformed_id_is_a_422_and_needs_no_idempotency_key(w):
    assert (await w.client.get(PATH.format("nope"), headers=bearer(mint(w.ids["nurse_a"])))).status_code == 422
    assert (await get(w, "nurse_a", w.messages["care"])).status_code == 200  # no X-Idempotency-Key was sent


# ---- the audit trail ---------------------------------------------------------------------------------------------------------------


async def test_every_successful_read_writes_exactly_one_audit_row_with_ids_only(w):
    for who, audited_as in AUDITED_AS.items():
        mid = w.messages["appointments"]
        before = len(await audit_rows(w, mid))
        r = await get(w, who, mid)
        assert r.status_code == 200
        after = await audit_rows(w, mid)
        assert len(after) == before + 1, who
        actor, role, origin, clinic, details, key, rtype, rid, action = after[-1]
        details = details if isinstance(details, dict) else json.loads(details)
        assert (actor, role, origin, clinic, rtype, rid, action) == (w.ids[who], audited_as, "test", w.a, "msg.messages", str(mid), "message.read")
        assert details == {"thread_id": str(w.thread["appointments"]), "topic": "appointments"}
        assert key.startswith("message-read:") and uuid.UUID(key.removeprefix("message-read:"))
        assert PRIVATE not in json.dumps([details, key, role, rid])


async def test_a_repeated_read_is_logged_again_under_a_new_key(w):
    mid = w.messages["safety"]
    await get(w, "nurse_a", mid)
    await get(w, "nurse_a", mid)
    keys = [r[5] for r in await audit_rows(w, mid)]
    assert len(keys) == 2 and keys[0] != keys[1]


async def test_refused_reads_write_no_audit_row(w):
    # every refusal: not visible to the role/topic, unknown message, erased, a clinic that is not yours
    for who, mid in (("fd_a", w.messages["care"]), ("admin_a", w.messages["care"]), ("auditor_a", w.messages["appointments"]),
                     ("nurse_b", w.messages["appointments"]), ("patient", w.messages["care"]), ("nurse_a", uuid.uuid4()),
                     ("nurse_a", w.messages["erased"]), ("suspended", w.messages["care"])):
        assert (await get(w, who, mid)).status_code in {404, 410}, (who, mid)
    assert await rows(w, "select 1 from audit.log where action = 'message.read'") == []


async def test_an_audit_row_cannot_be_written_as_someone_else(w):
    """Defence in depth: the insert policy forces actor_id = auth.uid(), so even a route bug could not attribute a read to another person."""
    await w.conn.exec_driver_sql("set local role careflow_app")
    await w.conn.execute(text("select set_config('request.jwt.claims', :c, true)"), {"c": json.dumps({"sub": str(w.ids["nurse_a"]), "role": "authenticated"})})
    try:
        with pytest.raises(Exception, match="row-level security"):
            async with w.conn.begin_nested():
                await w.conn.execute(
                    text("insert into audit.log (origin, clinic_id, actor_id, action, idempotency_key) values ('test', :c, :other, 'message.read', :k)"),
                    {"c": w.a, "other": w.ids["doctor_a"], "k": f"forged-{uuid.uuid4()}"},
                )
    finally:
        await w.conn.exec_driver_sql("reset role")
        await w.conn.execute(text("select set_config('request.jwt.claims', '', true)"))


# ---- erased, tampered, unreadable --------------------------------------------------------------------------------------------------


async def test_an_erased_message_is_410_for_those_who_can_see_it_and_404_for_those_who_cannot(w):
    r = await get(w, "nurse_a", w.messages["erased"])
    assert (r.status_code, r.json()) == (410, {"detail": "the message body was erased"}) and "cache-control" not in r.headers
    assert (await get(w, "fd_a", w.messages["erased"])).status_code == 404  # a care message: invisible to front desk
    assert await audit_rows(w, w.messages["erased"]) == []


async def test_a_tampered_body_is_a_fixed_500_with_one_warning_of_ids_and_the_error_class_and_no_audit_row(w, caplog):
    caplog.set_level("DEBUG")
    r = await get(w, "nurse_a", w.messages["tampered"])
    assert (r.status_code, r.json()) == (500, {"detail": "this message cannot be read"})
    warnings = [x.getMessage() for x in caplog.records if x.name == "careflow.app" and x.levelno >= 30]
    assert len(warnings) == 1 and str(w.messages["tampered"]) in warnings[0] and "DecryptionError" in warnings[0]
    assert PRIVATE not in caplog.text and PRIVATE not in r.text
    assert await audit_rows(w, w.messages["tampered"]) == []


async def test_a_key_version_that_is_not_loaded_is_a_fixed_503_with_an_error_log_and_no_audit_row(w, caplog):
    caplog.set_level("DEBUG")
    r = await get(w, "nurse_a", w.messages["other_key"])
    assert (r.status_code, r.json()) == (503, {"detail": "this message cannot be read right now"})
    errors = [x.getMessage() for x in caplog.records if x.name == "careflow.app" and x.levelno >= 40]
    assert len(errors) == 1 and "UnknownKeyVersionError" in errors[0] and str(w.messages["other_key"]) in errors[0]
    assert PRIVATE not in caplog.text and await audit_rows(w, w.messages["other_key"]) == []


# ---- privacy ------------------------------------------------------------------------------------------------------------------------


async def test_the_body_never_reaches_a_log_even_on_a_successful_read(w, caplog):
    caplog.set_level("DEBUG")
    assert (await get(w, "nurse_a", w.messages["care"])).status_code == 200
    assert PRIVATE not in caplog.text


async def test_a_refused_read_logs_one_line_of_ids_only(w, caplog):
    caplog.set_level("DEBUG")
    await get(w, "fd_a", w.messages["care"])
    lines = [x.getMessage() for x in caplog.records if x.name == "careflow.app" and x.levelno == 30]
    assert len(lines) == 1 and str(w.messages["care"]) in lines[0] and str(w.ids["fd_a"]) in lines[0] and PRIVATE not in caplog.text


async def test_there_is_no_route_level_role_gate_a_database_role_change_applies_on_the_next_request(w):
    assert (await get(w, "nurse_a", w.messages["care"])).status_code == 200
    await w.conn.execute(text("update iam.staff_roles set revoked_at = now() where account_id = :a and role = 'nurse' and clinic_id = :c"),
                         {"a": w.ids["nurse_a"], "c": w.a})
    assert (await get(w, "nurse_a", w.messages["care"])).status_code == 404
