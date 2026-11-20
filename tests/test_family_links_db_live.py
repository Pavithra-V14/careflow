"""Migration 0091 against the real database: family links are DENY BY DEFAULT (no rule row for the clinic and link type means `not_configured`: nothing is requested or verified), a patient asks and can revoke only their own
link, a FRONT-DESK person of the clinic (with a verified second factor) verifies it after checking the dependant's age against the rule and recording the linked person's consent, a verified link grants nothing, and the
tables cannot be reached directly. The rule numbers in these tests are arbitrary TEST VALUES; counsel's real values are not here. Synthetic data only."""

import uuid

import pytest
import pytest_asyncio

from tests.test_patient_booking_db_live import (  # noqa: F401  (the seeded world and harness are reused)
    MIGRATOR,
    _conn,
    pg,
    world,
)
from tests.test_visit_prep_db_live import as_app, owner

pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured")]

REQUEST = "select o_result, o_link_id from iam.family_link_request(:c, :t, :k)"
MINE = "select o_link_id, o_link_type, o_status from iam.patient_family_links()"
REVOKE = "select o_result from iam.family_link_revoke(:l, :k)"
STAFF = "select o_link_id, o_link_type, o_status from iam.staff_family_links(:c)"
DECIDE = "select o_result from iam.family_link_decide(:c, :l, :d, :p, :a, :s, :r, :k)"


@pytest_asyncio.fixture(loop_scope="module")
async def db(_conn):  # noqa: F811
    sp = await _conn.begin_nested()
    try:
        yield _conn
    finally:
        await sp.rollback()


async def fam_world(db, *, rule=True, consent=True):
    w = await world(db)
    h = await pg(db)
    for k, clinic in (("child", "clinic"), ("adult", "clinic"), ("other_child", "other")):
        w[k] = uuid.uuid4()
        await h.execute(
            "insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, phone_ct, phone_key_version, origin, idempotency_key) "
            "values ($1, $2, '\\x0102'::bytea, 1, '\\x0304'::bytea, 1, '\\x0506'::bytea, 1, 'test', $3)", w[k], w[clinic], f"fam-{w[k]}",
        )
    w["fd_b"] = uuid.uuid4()
    await h.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test')", w["fd_b"])
    await h.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, 'front_desk', 'test')", w["fd_b"], w["other"])
    w["nurse"] = uuid.uuid4()
    await h.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test')", w["nurse"])
    await h.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, 'nurse', 'test')", w["nurse"], w["clinic"])
    if rule:  # TEST VALUES only: dependants of age 0 to 12, consent required
        await h.execute("insert into iam.family_link_rules (clinic_id, link_type, min_dependant_age_years, max_dependant_age_years, requires_dependant_consent) values ($1, 'guardian_of_minor', 0, 12, $2)", w["clinic"], consent)
    return w


async def ask(db, w, who="pa1", t="guardian_of_minor", key=None, clinic="clinic"):
    return await as_app(db, w[who], REQUEST, c=w[clinic], t=t, k=key or f"k-{uuid.uuid4()}")


async def verify(db, w, link, *, who="fd", dep="child", age=8, consent=True, key=None, **kw):
    return await as_app(db, w[who], DECIDE, c=kw.get("c", w["clinic"]), l=link, d="verify", p=w[dep] if dep else None, a=age, s=consent, r=None, k=key or f"d-{uuid.uuid4()}", **{k: v for k, v in kw.items() if k == "aal2"})


async def test_it_is_deny_by_default_until_counsels_rule_row_exists(db):
    w = await fam_world(db, rule=False)
    assert await ask(db, w) == [("not_configured", None)]
    assert await owner(db, "select count(*) from iam.family_links where clinic_id = :c", c=w["clinic"]) == [(0,)]  # nothing was created
    await owner(db, "insert into iam.family_link_rules (clinic_id, link_type, min_dependant_age_years, max_dependant_age_years, requires_dependant_consent) values (:c, 'guardian_of_minor', 0, 12, true)", c=w["clinic"])
    got = await ask(db, w)
    assert got[0][0] == "recorded" and got[0][1] is not None
    assert await ask(db, w, t="carer_of_adult") == [("not_configured", None)]  # a link type with no rule stays denied


async def test_who_may_ask_and_the_request_is_idempotent_and_one_open_per_type(db):
    w = await fam_world(db)
    for who in ("pa3", "fd", "nurse"):
        assert await ask(db, w, who) == "42501", who  # no record at the clinic, or not a patient
    assert await ask(db, w, "pa1", clinic="other") == "42501"  # no record at that clinic
    assert await ask(db, w, "pa1", t="owner_of_pet") == "42501"
    assert await as_app(db, uuid.uuid4(), REQUEST, c=w["clinic"], t="guardian_of_minor", k="x") == "42501"
    first = await ask(db, w, key="same")
    assert first[0][0] == "recorded"
    assert await ask(db, w, key="same") == [("replay", first[0][1])]
    await owner(db, "insert into iam.family_link_rules (clinic_id, link_type, min_dependant_age_years, max_dependant_age_years, requires_dependant_consent) values (:c, 'carer_of_adult', 30, 130, true)", c=w["clinic"])
    assert await ask(db, w, key="same", t="carer_of_adult") == "23505"  # the key for a different request (the rule check comes first, so this type needs a rule)
    assert await ask(db, w, key="another") == [("already_requested", None)]  # one open request per link type
    assert await owner(db, "select count(*) from iam.family_links where clinic_id = :c", c=w["clinic"]) == [(1,)]
    audit = await owner(db, "select count(*), bool_and(idempotency_key like 'sys:family-link:%') from audit.log where action = 'iam.family_link.requested' and clinic_id = :c", c=w["clinic"])
    assert audit == [(1, True)]


async def test_a_patient_sees_and_revokes_only_their_own_links(db):
    w = await fam_world(db)
    link = (await ask(db, w))[0][1]
    assert await as_app(db, w["pa1"], MINE) == [(link, "guardian_of_minor", "requested")]
    assert await as_app(db, w["pa2"], MINE) == []  # not theirs
    for who in ("fd", "nurse"):
        assert await as_app(db, w[who], MINE) == "42501", who
    assert await as_app(db, w["pa2"], REVOKE, l=link, k="r1") == "42501"  # someone else's link: the same answer as a missing one
    assert await as_app(db, w["pa1"], REVOKE, l=uuid.uuid4(), k="r1") == "42501"
    assert await as_app(db, w["pa1"], REVOKE, l=link, k="r1") == [("recorded",)]
    assert await as_app(db, w["pa1"], REVOKE, l=link, k="r1") == [("replay",)]
    assert await as_app(db, w["pa1"], REVOKE, l=link, k="r2") == [("invalid_state",)]
    assert await as_app(db, w["pa1"], MINE) == [(link, "guardian_of_minor", "revoked")]


async def test_only_front_desk_with_a_second_factor_lists_and_verifies_and_the_rule_is_applied(db):
    w = await fam_world(db)
    link = (await ask(db, w))[0][1]
    assert await as_app(db, w["fd"], STAFF, c=w["clinic"]) == [(link, "guardian_of_minor", "requested")]
    assert await as_app(db, w["fd"], STAFF, aal2=False, c=w["clinic"]) == "42501"
    for who in ("nurse", "pa1", "fd_b"):
        assert await as_app(db, w[who], STAFF, c=w["clinic"]) == "42501", who
    for who in ("nurse", "pa1", "fd_b"):
        assert await verify(db, w, link, who=who) == "42501", who
    assert await verify(db, w, link, aal2=False) == "42501"
    assert await verify(db, w, link, age=40) == [("age_not_allowed",)]  # outside the rule's range
    assert await verify(db, w, link, consent=False) == [("consent_required",)]  # the rule requires the linked person's consent
    assert await verify(db, w, link, dep="other_child") == "42501"  # a record of another clinic
    assert await owner(db, "select requester_patient_id from iam.family_links where id = :l", l=link) is not None
    own_record = await owner(db, "select requester_patient_id from iam.family_links where id = :l", l=link)
    assert await as_app(db, w["fd"], DECIDE, c=w["clinic"], l=link, d="verify", p=own_record[0][0], a=8, s=True, r=None, k="self") == "42501"  # never the requester's own record
    assert await as_app(db, w["fd"], DECIDE, c=w["clinic"], l=link, d="verify", p=None, a=8, s=True, r=None, k="n") == "42501"
    assert await as_app(db, w["fd"], DECIDE, c=w["clinic"], l=link, d="reject", p=None, a=None, s=None, r=None, k="n") == "42501"  # a reject needs a reason word
    assert await verify(db, w, link, key="ok") == [("recorded",)]
    assert await verify(db, w, link, key="ok") == [("replay",)]
    assert await verify(db, w, link, key="again") == [("invalid_state",)]
    assert await as_app(db, w["pa1"], MINE) == [(link, "guardian_of_minor", "verified")]
    assert await owner(db, "select dependant_age_years, consent_recorded_at is not null, decided_by is not null from iam.family_links where id = :l", l=link) == [(8, True, True)]
    assert await as_app(db, w["fd"], STAFF, c=w["clinic"]) == [(link, "guardian_of_minor", "verified")]


async def test_a_rejected_link_is_closed_and_a_rule_not_requiring_consent_does_not_need_it(db):
    w = await fam_world(db, consent=False)
    link = (await ask(db, w))[0][1]
    assert await as_app(db, w["fd"], DECIDE, c=w["clinic"], l=link, d="reject", p=None, a=None, s=None, r="zz_not_verified", k="r1") == [("recorded",)]
    assert await verify(db, w, link, key="late") == [("invalid_state",)]
    second = (await ask(db, w))[0][1]  # a new request is allowed once the old one is closed
    assert await verify(db, w, second, consent=False) == [("recorded",)]


async def test_the_guards_hold_even_for_the_owner_and_nothing_is_reachable_directly(db):
    w = await fam_world(db)
    link = (await ask(db, w))[0][1]
    await verify(db, w, link)
    assert await owner(db, "update iam.family_links set status = 'requested' where id = :l", l=link) == "23001"  # a link moves only forward
    assert await owner(db, "update iam.family_links set dependant_patient_id = :d where id = :l", d=w["adult"], l=link) == "23001"
    assert await owner(db, "delete from iam.family_links where id = :l", l=link) == "23001"
    assert await owner(db, "truncate iam.family_links") == "23001"
    assert await owner(db, "update iam.family_links set dependant_patient_id = requester_patient_id where id = :l", l=link) in ("23001", "23514")
    for who in ("pa1", "fd", "nurse"):
        for table in ("iam.family_links", "iam.family_link_rules"):
            assert await as_app(db, w[who], f"select count(*) from {table} where clinic_id = :c", c=w["clinic"]) == "42501", (who, table)
    for table in ("iam.family_links", "iam.family_link_rules"):
        assert await owner(db, "select relrowsecurity, relforcerowsecurity from pg_class where oid = cast(:t as regclass)", t=table) == [(True, True)]
        assert await owner(db, "select count(*) from pg_policy where polrelid = cast(:t as regclass)", t=table) == [(0,)]
    for role in ("anon", "authenticated", "service_role"):
        if await owner(db, "select 1 from pg_roles where rolname = :r", r=role):
            assert await owner(db, "select has_function_privilege(:r, 'iam.family_link_decide(uuid, uuid, text, uuid, integer, boolean, text, text)', 'execute')", r=role) == [(False,)], role


# ---- migration 0093 -----------------------------------------------------------------------------------------------------------------------------------

STAFF_REVOKE = "select o_result from iam.family_link_staff_revoke(:c, :l, :r, :k)"


async def test_verification_re_checks_the_requester_and_the_front_desk_can_revoke_with_a_reason(db):
    w = await fam_world(db)
    link = (await ask(db, w))[0][1]
    await owner(db, "update mpi.patients set erased_at = clock_timestamp(), name_ct = null, dob_ct = null, phone_ct = null, name_key_version = null, dob_key_version = null, phone_key_version = null where id = :p", p=w["rec1"])
    assert await verify(db, w, link) == [("requester_not_active",)]  # the requester's record was erased since the request
    assert await as_app(db, w["fd"], DECIDE, c=w["clinic"], l=link, d="reject", p=None, a=None, s=None, r="zz_erased", k="rj") == [("recorded",)]  # a reject is still possible
    w2 = await fam_world(db)
    first = (await ask(db, w2))[0][1]
    assert await verify(db, w2, first) == [("recorded",)]
    for who in ("nurse", "pa1", "fd_b"):
        assert await as_app(db, w2[who], STAFF_REVOKE, c=w2["clinic"], l=first, r="zz_consent_withdrawn", k="x") == "42501", who
    assert await as_app(db, w2["fd"], STAFF_REVOKE, aal2=False, c=w2["clinic"], l=first, r="zz_consent_withdrawn", k="x") == "42501"
    assert await as_app(db, w2["fd"], STAFF_REVOKE, c=w2["clinic"], l=first, r="Not A Word", k="x") == "42501"
    assert await as_app(db, w2["fd"], STAFF_REVOKE, c=w2["clinic"], l=uuid.uuid4(), r="zz_consent_withdrawn", k="x") == "42501"
    assert await as_app(db, w2["fd"], STAFF_REVOKE, c=w2["clinic"], l=first, r="zz_consent_withdrawn", k="k1") == [("recorded",)]
    assert await as_app(db, w2["fd"], STAFF_REVOKE, c=w2["clinic"], l=first, r="zz_consent_withdrawn", k="k1") == [("replay",)]
    assert await as_app(db, w2["fd"], STAFF_REVOKE, c=w2["clinic"], l=first, r="zz_consent_withdrawn", k="k2") == [("invalid_state",)]
    assert await as_app(db, w2["pa1"], MINE) == [(first, "guardian_of_minor", "revoked")]
    assert await owner(db, "select revoke_reason, revoked_by is not null from iam.family_links where id = :l", l=first) == [("zz_consent_withdrawn", True)]
    actions = await owner(db, "select action from audit.log where resource_id = :l and action like 'iam.family_link.%' order by id", l=str(first))
    assert [a[0] for a in actions] == ["iam.family_link.requested", "iam.family_link.verified", "iam.family_link.staff_revoked"]  # past tense


async def test_a_second_verification_for_the_same_dependant_is_a_result_not_an_error(db):
    w = await fam_world(db)
    first = (await ask(db, w))[0][1]
    assert await verify(db, w, first) == [("recorded",)]
    second = (await ask(db, w))[0][1]
    assert second != first
    assert await verify(db, w, second) == [("already_linked",)]  # not a unique-index error (a 500)
