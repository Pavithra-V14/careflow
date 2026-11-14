"""Migration 0085 against the real database: card vectors in pgvector, reached ONLY through the three definer functions as the restricted `careflow_app` role. Proves: the table cannot be read or written directly;
indexing is idempotent and append-only (a changed card is a NEW version, the same version with other content is refused); the search ALWAYS filters by clinic, origin, embedding model, CURRENT version and the
caller's ROLE (a newer version the caller may not read still retires the older one); front desk and other clinics are refused with one 42501; the table holds ids, a hash and numbers and no text; and the Python
store and `KnowledgeSearch` work end to end over the placeholder cards. Synthetic data only."""

import json
import os
import time
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from careflow import demo_cards
from careflow.care_gap_sql import sqlstate
from careflow.embeddings import (
    DIMENSIONS,
    FAKE_MODEL_ID,
    HashingEmbedding,
    vector_literal,
)
from careflow.knowledge import KnowledgeSearch, policy_from
from careflow.knowledge_sql import VersionConflict, content_hash, index_cards
from tests.db_helpers import ensure_app_role_sa

ROOT = Path(__file__).resolve().parent.parent
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(ROOT / ".env").get("DATABASE_URL_MIGRATOR")
pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured")]
SHA = "a" * 64
MODEL = "test-embed-768"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _conn():
    engine = create_async_engine(make_url(MIGRATOR or "").set(drivername="postgresql+asyncpg"), connect_args={"statement_cache_size": 0})
    conn = await engine.connect()
    outer = await conn.begin()
    try:
        await ensure_app_role_sa(conn)
        yield conn
    finally:
        await outer.rollback()
        await conn.close()
        await engine.dispose()


@pytest_asyncio.fixture(loop_scope="module")
async def db(_conn):
    sp = await _conn.begin_nested()
    try:
        yield _conn
    finally:
        await sp.rollback()


async def pg(db):
    return (await db.get_raw_connection()).driver_connection


def claims_for(person):
    return json.dumps({"sub": str(person), "role": "authenticated", "aal": "aal2", "amr": [{"method": "totp", "timestamp": int(time.time()) - 60}]})


async def as_app(db, person, sql, **params):
    try:
        async with db.begin_nested():
            await db.exec_driver_sql("set local role careflow_app")
            await db.execute(text("select set_config('request.jwt.claims', :c, true)"), {"c": claims_for(person)})
            result = await db.execute(text(sql), params)
            value = [tuple(r) for r in result.all()] if result.returns_rows else result.rowcount
            await db.exec_driver_sql("reset role")
            return value
    except DBAPIError as exc:
        return sqlstate(exc)


async def owner(db, sql, **params):
    try:
        async with db.begin_nested():
            result = await db.execute(text(sql), params)
            return [tuple(r) for r in result.all()] if result.returns_rows else result.rowcount
    except DBAPIError as exc:
        return sqlstate(exc)


def one_hot(i: int) -> str:
    v = [0.0] * DIMENSIONS
    v[i] = 1.0
    return vector_literal(v)


async def world(db):
    h = await pg(db)
    ids = {k: uuid.uuid4() for k in ("a", "b", "nurse", "doctor", "worker", "fd", "admin", "nurse_b")}
    await h.execute("insert into iam.clinics (id, name, origin) values ($1, 'KN-A', 'test'), ($2, 'KN-B', 'test')", ids["a"], ids["b"])
    for k, clinic, role in (("nurse", "a", "nurse"), ("doctor", "a", "doctor"), ("worker", "a", "queue_worker"), ("fd", "a", "front_desk"), ("admin", "a", "admin"), ("nurse_b", "b", "nurse")):
        await h.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test')", ids[k])
        await h.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, $3, 'test')", ids[k], ids[clinic], role)
    return ids


INDEX = "select o_id, o_created from kb.index_card_embedding(:c, :o, :id, :v, :m, cast(:a as text[]), :e, :h, :k)"
SEARCH = "select o_card_id, o_card_version, o_similarity from kb.search_card_embeddings(:c, :o, :m, :q, :n)"
VERSIONS = "select o_card_id, o_card_version from kb.current_card_versions(:c, :o, :m, cast(:ids as text[]))"


async def index(db, w, who, card, version, vec, *, audience=("nurse", "doctor", "queue_worker"), sha=SHA, key=None, clinic="a", origin="test", model=MODEL):
    return await as_app(db, w[who], INDEX, c=w[clinic], o=origin, id=card, v=version, m=model, a=list(audience), e=one_hot(vec), h=sha, k=key or f"k-{uuid.uuid4()}")


async def test_the_table_cannot_be_read_or_written_directly_and_holds_no_text(db):
    w = await world(db)
    await index(db, w, "worker", "card-a", 1, 0)
    for person in ("nurse", "doctor", "worker", "admin", "fd"):
        assert await as_app(db, w[person], "select count(*) from kb.card_embeddings where clinic_id = :c", c=w["a"]) == "42501"
        assert await as_app(db, w[person], "insert into kb.card_embeddings (clinic_id, origin, card_id, card_version, model_id, audience, embedding, content_sha256, indexed_by, idempotency_key) "
                                           "values (:c, 'test', 'x', 1, 'm', array['nurse'], cast(:e as extensions.vector), :h, :p, 'k')", c=w["a"], e=one_hot(1), h=SHA, p=w[person]) == "42501"
    columns = await owner(db, "select column_name from information_schema.columns where table_schema = 'kb' and table_name = 'card_embeddings' order by column_name")
    assert {c[0] for c in columns} == {"id", "clinic_id", "origin", "card_id", "card_version", "model_id", "audience", "embedding", "content_sha256", "indexed_by", "created_at", "idempotency_key"}  # ids, a hash, numbers: no text column
    flags = await owner(db, "select relrowsecurity, relforcerowsecurity from pg_class where oid = 'kb.card_embeddings'::regclass")
    assert flags == [(True, True)]
    assert await owner(db, "select count(*) from pg_policy where polrelid = 'kb.card_embeddings'::regclass") == [(0,)]  # no policy: nobody selects it


async def test_the_table_is_append_only_even_for_the_owner(db):
    w = await world(db)
    await index(db, w, "worker", "card-a", 1, 0)
    assert await owner(db, "update kb.card_embeddings set audience = array['nurse'] where clinic_id = :c", c=w["a"]) == "23001"
    assert await owner(db, "delete from kb.card_embeddings where clinic_id = :c", c=w["a"]) == "23001"
    assert await owner(db, "truncate kb.card_embeddings") == "23001"


async def test_indexing_is_idempotent_append_only_and_refuses_a_changed_version(db):
    w = await world(db)
    first = await index(db, w, "worker", "card-a", 1, 0, key="same")
    assert first[0][1] is True
    assert (await index(db, w, "worker", "card-a", 1, 0, key="same"))[0] == (first[0][0], False)  # a replay
    assert (await index(db, w, "worker", "card-a", 1, 0, key="other-key"))[0] == (first[0][0], False)  # the same version and content under another key
    assert await index(db, w, "worker", "card-a", 1, 1, sha="b" * 64, key="third") == "23505"  # the same version with other content: a NEW version is needed
    assert await index(db, w, "worker", "card-b", 1, 2, key="same") == "23505"  # the key reused for a different card
    assert (await index(db, w, "worker", "card-a", 2, 3, sha="b" * 64))[0][1] is True  # a new version
    assert (await index(db, w, "admin", "card-c", 1, 4))[0][1] is True  # an admin may index too
    assert await owner(db, "select count(*) from kb.card_embeddings where clinic_id = :c", c=w["a"]) == [(3,)]
    audit = await owner(db, "select count(*), bool_and(idempotency_key like 'sys:card-embedding:%'), bool_and(details::text not like '%e+%') from audit.log where clinic_id = :c and action = 'kb.card_embedding.indexed'", c=w["a"])
    assert audit == [(3, True, True)]  # one audit row per created row, a server-made key, ids only


async def test_only_the_worker_and_an_admin_of_the_clinic_may_index_and_only_with_the_clinics_origin(db):
    w = await world(db)
    for person in ("nurse", "doctor", "fd", "nurse_b"):
        assert await index(db, w, person, "card-a", 1, 0) == "42501"
    assert await index(db, w, "worker", "card-a", 1, 0, origin="live") == "42501"  # a test card never lands in a live clinic (and the reverse)
    assert await index(db, w, "worker", "card-a", 1, 0, clinic="b") == "42501"  # the worker of clinic A is nobody in clinic B
    assert await as_app(db, uuid.uuid4(), INDEX, c=w["a"], o="test", id="x", v=1, m=MODEL, a=["nurse"], e=one_hot(0), h=SHA, k="k") == "42501"  # no account at all
    assert await index(db, w, "worker", "Bad Id", 1, 0) == "23514"  # the id shape is a CHECK
    assert await index(db, w, "worker", "card-a", 1, 0, audience=("nurse", "janitor")) == "23514"  # a role that does not exist
    assert await as_app(db, w["worker"], INDEX, c=w["a"], o="test", id="short", v=1, m=MODEL, a=["nurse"], e="[1,2,3]", h=SHA, k="k-short") == "42501"  # a vector of the wrong size is refused with the one answer
    assert await as_app(db, w["worker"], INDEX, c=w["a"], o="test", id="junk", v=1, m=MODEL, a=["nurse"], e="not a vector", h=SHA, k="k-junk") == "42501"


async def test_the_search_filters_by_clinic_origin_model_and_returns_the_nearest_first(db):
    w = await world(db)
    await index(db, w, "worker", "near", 1, 5)
    await index(db, w, "worker", "mid", 1, 6)
    await as_app(db, w["admin"], INDEX, c=w["a"], o="test", id="other-model", v=1, m="other-model", a=["nurse"], e=one_hot(5), h=SHA, k="om")
    got = await as_app(db, w["nurse"], SEARCH, c=w["a"], o="test", m=MODEL, q=one_hot(5), n=10)
    assert [r[0] for r in got] == ["near", "mid"] and abs(got[0][2] - 1.0) < 1e-6 and abs(got[1][2]) < 1e-6  # only this model; nearest first; cosine 1 and 0
    assert [r[0] for r in await as_app(db, w["nurse"], SEARCH, c=w["a"], o="test", m=MODEL, q=one_hot(5), n=1)] == ["near"]  # the limit
    assert await as_app(db, w["nurse"], SEARCH, c=w["a"], o="live", m=MODEL, q=one_hot(5), n=10) == []  # another origin: nothing
    assert await as_app(db, w["nurse_b"], SEARCH, c=w["b"], o="test", m=MODEL, q=one_hot(5), n=10) == []  # clinic B holds nothing of clinic A's
    assert await as_app(db, w["nurse_b"], SEARCH, c=w["a"], o="test", m=MODEL, q=one_hot(5), n=10) == "42501"  # and may not ask about clinic A


async def test_search_refuses_front_desk_admin_strangers_and_bad_arguments_with_one_answer(db):
    w = await world(db)
    await index(db, w, "worker", "card-a", 1, 0)
    for person in ("fd", "admin", "nurse_b"):
        assert await as_app(db, w[person], SEARCH, c=w["a"], o="test", m=MODEL, q=one_hot(0), n=5) == "42501"
    assert await as_app(db, uuid.uuid4(), SEARCH, c=w["a"], o="test", m=MODEL, q=one_hot(0), n=5) == "42501"
    for n in (0, 51, -1):
        assert await as_app(db, w["nurse"], SEARCH, c=w["a"], o="test", m=MODEL, q=one_hot(0), n=n) == "42501"
    assert await as_app(db, w["nurse"], SEARCH, c=uuid.uuid4(), o="test", m=MODEL, q=one_hot(0), n=5) == "42501"
    for person in ("nurse", "doctor", "worker"):
        assert [r[0] for r in await as_app(db, w[person], SEARCH, c=w["a"], o="test", m=MODEL, q=one_hot(0), n=5)] == ["card-a"]


async def test_the_role_filter_and_the_current_version_rule(db):
    w = await world(db)
    await index(db, w, "worker", "doctors-only", 1, 7, audience=("doctor", "queue_worker"))
    await index(db, w, "worker", "everyone", 1, 7)
    assert [r[0] for r in await as_app(db, w["nurse"], SEARCH, c=w["a"], o="test", m=MODEL, q=one_hot(7), n=10)] == ["everyone"]  # the nurse never sees the doctors-only card
    assert {r[0] for r in await as_app(db, w["doctor"], SEARCH, c=w["a"], o="test", m=MODEL, q=one_hot(7), n=10)} == {"doctors-only", "everyone"}
    # version 2 of "everyone" is for doctors only: the nurse must NOT be served version 1 (a newer version, even one she may not read, retires the older)
    await index(db, w, "worker", "everyone", 2, 8, audience=("doctor", "queue_worker"), sha="c" * 64)
    assert await as_app(db, w["nurse"], SEARCH, c=w["a"], o="test", m=MODEL, q=one_hot(7), n=10) == []
    assert await as_app(db, w["nurse"], VERSIONS, c=w["a"], o="test", m=MODEL, ids=["everyone", "doctors-only"]) == []
    got = await as_app(db, w["doctor"], SEARCH, c=w["a"], o="test", m=MODEL, q=one_hot(8), n=10)
    assert got[0][:2] == ("everyone", 2) and ("everyone", 1) not in [r[:2] for r in got]
    assert sorted(await as_app(db, w["doctor"], VERSIONS, c=w["a"], o="test", m=MODEL, ids=["everyone", "doctors-only", "missing"])) == [("doctors-only", 1), ("everyone", 2)]
    assert await as_app(db, w["fd"], VERSIONS, c=w["a"], o="test", m=MODEL, ids=["everyone"]) == "42501"
    assert await as_app(db, w["doctor"], VERSIONS, c=w["a"], o="test", m=MODEL, ids=[f"c{i}" for i in range(101)]) == "42501"


async def test_the_python_store_and_the_search_work_end_to_end_over_the_placeholder_cards(db):
    w = await world(db)
    cards, queries = demo_cards.load()

    async def as_person(person):
        await db.exec_driver_sql("set local role careflow_app")
        await db.execute(text("select set_config('request.jwt.claims', :c, true)"), {"c": claims_for(person)})

    await as_person(w["worker"])
    embedder = HashingEmbedding()
    first = await index_cards(db, embedder, FAKE_MODEL_ID, cards, clinic_id=w["a"], origin="test")
    again = await index_cards(db, embedder, FAKE_MODEL_ID, cards, clinic_id=w["a"], origin="test")
    assert (first.created, first.existing) == (len(cards), 0) and (again.created, again.existing) == (0, len(cards))
    changed = [demo_cards.DemoCard(type(cards[0].fields)(cards[0].fields.card_id, cards[0].fields.version, "A different title", cards[0].fields.description), cards[0].audience)]
    with pytest.raises(VersionConflict):
        await index_cards(db, embedder, FAKE_MODEL_ID, changed, clinic_id=w["a"], origin="test")  # the same version, other content
    await db.exec_driver_sql("reset role")

    search = KnowledgeSearch(cards, lambda clinic: (embedder, FAKE_MODEL_ID), policy_from({"CAREFLOW_RETRIEVAL_MIN_DENSE_SIMILARITY": "0.30", "CAREFLOW_RETRIEVAL_MIN_BM25_RATIO": "0.30"}))
    await as_person(w["nurse"])
    for q in queries:
        out = await search.search(db, w["a"], "test", q.text)
        assert not out.retrieval.abstained and out.retrieval.candidates[0].card_id == q.expect, q.text
    nurse_ids = {c.card_id for q in queries for c in (await search.search(db, w["a"], "test", q.text)).retrieval.candidates}
    nork = await search.search(db, w["a"], "test", "how is a nork ledger line corrected with the green pen")
    assert "demo-card-nork" not in {c.card_id for c in nork.retrieval.candidates} and "demo-card-nork" not in nurse_ids  # a doctor-only card: the role filter holds through the whole path
    nothing = await search.search(db, w["a"], "test", "a question about nothing in the library at all")
    assert nothing.retrieval.abstained  # no strong match: an abstention, not a guess
    await db.exec_driver_sql("reset role")
    await as_person(w["doctor"])
    nork = await search.search(db, w["a"], "test", "how is a nork ledger line corrected with the green pen")
    assert nork.retrieval.candidates[0].card_id == "demo-card-nork"
    assert content_hash("x") != content_hash("y")
