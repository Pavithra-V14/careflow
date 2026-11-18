"""Migration 0090 against the real database: a card or message template is DRAFTED and EDITED only by its author, FROZEN on submit, and approved or rejected by a DOCTOR WHO IS NOT THE AUTHOR (two people, in the database, even
for the owner); an approval writes the next immutable version; every function needs a verified second factor and answers one 42501 to everyone else; the tables cannot be reached directly; the worker (not the approver)
reads the approved cards that still need indexing and indexes them through the existing function; only the cards the caller's role may read reach the search's keyword side. NOTHING is seeded: every row here is
typed by the test as invented marker words. Synthetic data only."""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from careflow.embeddings import FAKE_MODEL_ID, HashingEmbedding
from careflow.knowledge import KnowledgeSearch, policy_from
from careflow.knowledge_editor_sql import index_sweep
from tests.test_visit_prep_db_live import (  # noqa: F401  (the harness is reused, not copied)
    MIGRATOR,
    _conn,
    as_app,
    claims,
    owner,
)

pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured")]

CREATE = "select o_id, o_created from kb.knowledge_draft_create(:c, :k, :code, :t, :b, cast(:s as text[]), cast(:p as text[]), cast(:a as text[]), :key)"
EDIT = "select o_result from kb.knowledge_draft_edit(:c, :d, :t, :b, cast(:s as text[]), cast(:p as text[]), cast(:a as text[]), :key)"
SUBMIT = "select o_result from kb.knowledge_draft_submit(:c, :d, :key)"
DECIDE = "select o_result, o_version from kb.knowledge_draft_decide(:c, :d, :dec, :r, :key)"
DRAFTS = "select o_code, o_status from kb.knowledge_drafts_list(:c, 50)"
VERSIONS = "select o_code, o_version from kb.knowledge_versions_list(:c, 50)"
TO_INDEX = "select o_code, o_version from kb.knowledge_versions_to_index(:c, 50)"
CURRENT = "select o_code, o_version from kb.knowledge_current_cards(:c)"


@pytest_asyncio.fixture(loop_scope="module")
async def db(_conn):  # noqa: F811
    sp = await _conn.begin_nested()
    try:
        yield _conn
    finally:
        await sp.rollback()


async def world(db):
    raw = (await db.get_raw_connection()).driver_connection
    ids = {k: uuid.uuid4() for k in ("a", "b", "nurse", "nurse2", "doctor", "doctor2", "worker", "fd", "admin", "nurse_b", "doctor_b", "patient")}
    await raw.execute("insert into iam.clinics (id, name, origin) values ($1, 'KE-A', 'test'), ($2, 'KE-B', 'test')", ids["a"], ids["b"])
    for k, clinic, role in (("nurse", "a", "nurse"), ("nurse2", "a", "nurse"), ("doctor", "a", "doctor"), ("doctor2", "a", "doctor"), ("worker", "a", "queue_worker"), ("fd", "a", "front_desk"), ("admin", "a", "admin"),
                            ("nurse_b", "b", "nurse"), ("doctor_b", "b", "doctor")):
        await raw.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test')", ids[k])
        await raw.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, $3, 'test')", ids[k], ids[clinic], role)
    await raw.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'patient', 'active', 'test')", ids["patient"])
    return ids


async def draft(db, w, who="nurse", code="zz-card", kind="card", audience=("nurse", "doctor", "queue_worker"), key=None, title="ZZ title", body="ZZ marker body words", clinic="a", **kw):
    return await as_app(db, w[who], CREATE, c=w[clinic], k=kind, code=code, t=title, b=body, s=kw.get("s", ["zzsyn"]), p=kw.get("p", ["zzphrase"]), a=list(audience), key=key or f"k-{uuid.uuid4()}")


async def submitted(db, w, who="nurse", **kw):
    did = (await draft(db, w, who, **kw))[0][0]
    assert await as_app(db, w[who], SUBMIT, c=w["a"], d=did, key=f"s-{uuid.uuid4()}") == [("recorded",)]
    return did


async def approved(db, w, code="zz-card", author="nurse", approver="doctor", **kw):
    did = await submitted(db, w, author, code=code, **kw)
    got = await as_app(db, w[approver], DECIDE, c=w["a"], d=did, dec="approve", r=None, key=f"d-{uuid.uuid4()}")
    assert got[0][0] == "recorded", got
    return did, got[0][1]


async def test_only_a_nurse_or_doctor_with_a_second_factor_creates_a_draft_and_it_is_idempotent(db):
    w = await world(db)
    first = await draft(db, w, key="same")
    assert first[0][1] is True
    assert (await draft(db, w, key="same"))[0] == (first[0][0], False)  # a replay
    assert await draft(db, w, key="same", title="ZZ other") == "23505"  # the key for a different draft
    assert await draft(db, w, code="zz-card", key="other-key") == "23505"  # one OPEN draft per code
    for who in ("fd", "admin", "worker", "nurse_b", "patient"):
        assert await draft(db, w, who, code="zz-other") == "42501", who
    assert await as_app(db, w["nurse"], CREATE, aal2=False, c=w["a"], k="card", code="zz-no-aal2", t="t", b="b", s=[], p=[], a=["nurse"], key="x") == "42501"  # no verified second factor
    assert await draft(db, w, "nurse", clinic="b", code="zz-x") == "42501"  # not a nurse of that clinic
    for bad in ({"code": "Bad Code"}, {"kind": "poster"}, {"audience": []}, {"audience": ["janitor"]}, {"title": "   "}, {"body": "x" * 4001}):
        got = await draft(db, w, **{"code": "zz-bad", **bad})
        assert got in ("23514", "42501") or isinstance(got, str), bad  # refused by a check or by the function: never stored
    assert (await draft(db, w, "nurse", code="zz-tpl", kind="template", audience=()))[0][1] is True  # a template needs no audience
    assert await owner(db, "select count(*) from kb.knowledge_drafts where clinic_id = :c", c=w["a"]) == [(2,)]


async def test_only_the_author_edits_and_only_while_a_draft_and_a_submitted_draft_is_frozen_even_for_the_owner(db):
    w = await world(db)
    did = (await draft(db, w))[0][0]
    edit = lambda who, key, **o: as_app(db, w[who], EDIT, c=o.get("c", w["a"]), d=did, t=o.get("t", "ZZ new title"), b="ZZ new body", s=[], p=[], a=["nurse"], key=key)
    assert await edit("nurse", "e1") == [("recorded",)]
    assert await edit("nurse", "e1") == [("replay",)]
    for who in ("nurse2", "doctor", "fd", "worker", "nurse_b"):
        assert await edit(who, f"x-{who}") == "42501", who  # only the author
    assert await edit("nurse", "e9", c=w["b"]) == "42501"  # a draft of another clinic is nobody's
    assert await as_app(db, w["nurse"], EDIT, aal2=False, c=w["a"], d=did, t="t", b="b", s=[], p=[], a=["nurse"], key="e5") == "42501"
    assert await as_app(db, w["nurse"], SUBMIT, c=w["a"], d=did, key="s1") == [("recorded",)]
    assert await as_app(db, w["nurse"], SUBMIT, c=w["a"], d=did, key="s1") == [("replay",)]
    assert await as_app(db, w["nurse"], SUBMIT, c=w["a"], d=did, key="s2") == [("invalid_state",)]
    assert await edit("nurse", "e2") == [("invalid_state",)]  # frozen
    assert await as_app(db, w["doctor"], SUBMIT, c=w["a"], d=did, key="s3") == "42501"  # not the author
    assert await owner(db, "update kb.knowledge_drafts set title = 'tampered' where id = :d", d=did) == "23001"  # the freeze holds for the owner
    assert await owner(db, "delete from kb.knowledge_drafts where id = :d", d=did) == "23001"
    assert await owner(db, "truncate kb.knowledge_drafts") == "23001"


async def test_a_doctor_who_is_not_the_author_decides_and_an_approval_writes_the_next_immutable_version(db):
    w = await world(db)
    did = await submitted(db, w, "nurse")
    for who in ("nurse", "nurse2", "fd", "admin", "worker", "doctor_b"):
        assert await as_app(db, w[who], DECIDE, c=w["a"], d=did, dec="approve", r=None, key=f"x-{who}") == "42501", who  # not a doctor of the clinic
    assert await as_app(db, w["doctor"], DECIDE, aal2=False, c=w["a"], d=did, dec="approve", r=None, key="n") == "42501"
    assert await as_app(db, w["doctor"], DECIDE, c=w["a"], d=did, dec="reject", r=None, key="n") == "42501"  # a reject needs a reason word
    assert await as_app(db, w["doctor"], DECIDE, c=w["a"], d=did, dec="approve", r="some_reason", key="n") == "42501"  # an approve takes none
    assert await as_app(db, w["doctor"], DECIDE, c=w["a"], d=did, dec="maybe", r=None, key="n") == "42501"
    assert await as_app(db, w["doctor"], DECIDE, c=w["a"], d=did, dec="approve", r=None, key="ok") == [("recorded", 1)]
    assert await as_app(db, w["doctor"], DECIDE, c=w["a"], d=did, dec="approve", r=None, key="ok") == [("replay", 1)]
    assert await as_app(db, w["doctor2"], DECIDE, c=w["a"], d=did, dec="approve", r=None, key="again") == [("invalid_state", None)]  # already decided
    # a doctor's own draft is approved by ANOTHER doctor, never by the author
    own = await submitted(db, w, "doctor", code="zz-doctor-card")
    assert await as_app(db, w["doctor"], DECIDE, c=w["a"], d=own, dec="approve", r=None, key="self") == "42501"
    assert await as_app(db, w["doctor2"], DECIDE, c=w["a"], d=own, dec="approve", r=None, key="other") == [("recorded", 1)]
    # the second approved draft of the same code is version 2; a reject writes no version
    _, ver = await approved(db, w, code="zz-card", author="nurse2")
    assert ver == 2
    rej = await submitted(db, w, "nurse", code="zz-card")
    assert await as_app(db, w["doctor2"], DECIDE, c=w["a"], d=rej, dec="reject", r="zz_not_ready", key="r1") == [("recorded", None)]
    assert await owner(db, "select code, version from kb.knowledge_versions where clinic_id = :c order by code, version", c=w["a"]) == [("zz-card", 1), ("zz-card", 2), ("zz-doctor-card", 1)]
    assert await owner(db, "update kb.knowledge_versions set title = 'x'") == "23001"  # append-only, even for the owner
    assert await owner(db, "delete from kb.knowledge_versions") == "23001"
    assert await owner(db, "truncate kb.knowledge_versions") == "23001"
    # the two-person rule is also a CHECK: the owner cannot mark a draft approved by its own author
    open_draft = await submitted(db, w, "nurse", code="zz-check-card")
    assert await owner(db, "update kb.knowledge_drafts set status = 'approved', decided_by = author, decided_at = now() where id = :d", d=open_draft) == "23514"


async def test_the_tables_have_no_direct_access_and_every_list_needs_a_second_factor_and_a_nurse_or_doctor(db):
    w = await world(db)
    await approved(db, w)
    for who in ("nurse", "doctor", "fd", "admin", "worker"):
        for table in ("kb.knowledge_drafts", "kb.knowledge_versions"):
            assert await as_app(db, w[who], f"select count(*) from {table} where clinic_id = :c", c=w["a"]) == "42501", (who, table)
        assert await as_app(db, w[who], "insert into kb.knowledge_versions (clinic_id, origin, kind, code, version, title, body, synonyms, patient_phrases, audience, draft_id, author, approved_by) "
                                        "values (:c, 'test', 'card', 'x', 1, 't', 'b', '{}', '{}', '{nurse}', :d, :u, :u2)", c=w["a"], d=uuid.uuid4(), u=w[who], u2=w["doctor"]) == "42501", who
    for table in ("kb.knowledge_drafts", "kb.knowledge_versions"):
        assert await owner(db, "select relrowsecurity, relforcerowsecurity from pg_class where oid = cast(:t as regclass)", t=table) == [(True, True)]
        assert await owner(db, "select count(*) from pg_policy where polrelid = cast(:t as regclass)", t=table) == [(0,)]
    assert await as_app(db, w["nurse"], DRAFTS, c=w["a"]) == [("zz-card", "approved")]
    assert await as_app(db, w["doctor"], VERSIONS, c=w["a"]) == [("zz-card", 1)]
    for who in ("fd", "admin", "worker", "nurse_b", "patient"):
        assert await as_app(db, w[who], DRAFTS, c=w["a"]) == "42501", who
        assert await as_app(db, w[who], VERSIONS, c=w["a"]) == "42501", who
    assert await as_app(db, w["nurse"], DRAFTS, aal2=False, c=w["a"]) == "42501"
    assert await as_app(db, w["nurse_b"], DRAFTS, c=w["b"]) == []  # another clinic sees none of clinic A's drafts


async def test_the_worker_reads_what_needs_indexing_and_only_readable_cards_reach_the_search_and_templates_never_do(db):
    w = await world(db)
    await approved(db, w, code="zz-everyone", audience=("nurse", "doctor", "queue_worker"))
    await approved(db, w, code="zz-doctors", audience=("doctor", "queue_worker"))
    await approved(db, w, code="zz-template", kind="template", audience=())
    assert sorted(await as_app(db, w["worker"], TO_INDEX, c=w["a"])) == [("zz-doctors", 1), ("zz-everyone", 1)]  # cards only, not templates
    for who in ("nurse", "doctor", "fd", "admin", "nurse_b"):
        assert await as_app(db, w[who], TO_INDEX, c=w["a"]) == "42501", who  # the approver's session never reads or indexes
    assert sorted(await as_app(db, w["nurse"], CURRENT, c=w["a"])) == [("zz-everyone", 1)]  # the nurse may not read the doctors-only card
    assert sorted(await as_app(db, w["doctor"], CURRENT, c=w["a"])) == [("zz-doctors", 1), ("zz-everyone", 1)]
    assert await as_app(db, w["fd"], CURRENT, c=w["a"]) == "42501" and await as_app(db, w["nurse_b"], CURRENT, c=w["a"]) == "42501"


async def test_the_workers_sweep_indexes_approved_cards_once_and_the_search_then_finds_them_by_role(db):
    w = await world(db)
    await approved(db, w, code="zz-everyone", title="Zorblat marker returns", body="How a zorblat marker goes back to the quibble desk", s=["zorblat"], p=["where does the zorblat go"], audience=("nurse", "doctor", "queue_worker"))
    await approved(db, w, code="zz-doctors", title="Flimmer marker polish", body="Polishing the flimmer marker with the grizzle cloth", s=["flimmer"], p=["how to polish a flimmer"], audience=("doctor", "queue_worker"))

    async def as_person(person):
        await db.exec_driver_sql("set local role careflow_app")
        await db.execute(text("select set_config('request.jwt.claims', :c, true)"), {"c": claims(person)})

    class Route:  # the embedding route the sweep and the search use: the local fake, nothing else
        _embedder_for = staticmethod(lambda clinic: (HashingEmbedding(), FAKE_MODEL_ID))

    await as_person(w["worker"])
    assert await index_sweep(db, Route()) == 2
    assert await index_sweep(db, Route()) == 0  # once: a second pass indexes nothing
    assert await index_sweep(db, None) == 0  # no embedding route (the clinical profile): nothing is indexed and nothing is invented
    await db.exec_driver_sql("reset role")
    assert await owner(db, "select card_id, card_version from kb.card_embeddings where clinic_id = :c order by card_id", c=w["a"]) == [("zz-doctors", 1), ("zz-everyone", 1)]
    search = KnowledgeSearch((), lambda clinic: (HashingEmbedding(), FAKE_MODEL_ID), policy_from({"CAREFLOW_RETRIEVAL_MIN_DENSE_SIMILARITY": "0.30", "CAREFLOW_RETRIEVAL_MIN_BM25_RATIO": "0.30"}), db_cards=True)
    await as_person(w["nurse"])
    got = await search.search(db, w["a"], "test", "where does the zorblat marker go back to the quibble desk")
    assert not got.retrieval.abstained and got.retrieval.candidates[0].card_id == "zz-everyone"
    hidden = await search.search(db, w["a"], "test", "how to polish a flimmer marker with the grizzle cloth")
    assert "zz-doctors" not in {c.card_id for c in hidden.retrieval.candidates}  # a doctors-only card is never shown to a nurse
    await db.exec_driver_sql("reset role")
    await as_person(w["doctor"])
    seen = await search.search(db, w["a"], "test", "how to polish a flimmer marker with the grizzle cloth")
    assert not seen.retrieval.abstained and seen.retrieval.candidates[0].card_id == "zz-doctors"
