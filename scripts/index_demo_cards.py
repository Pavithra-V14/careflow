"""Index the SYNTHETIC placeholder cards into a DEV test clinic and measure the knowledge search over the real database. A person runs it by hand; nothing runs it. DEMO PROFILE, SYNTHETIC DATA ONLY.

    uv run python scripts/index_demo_cards.py --clinic-id <uuid>            (the local fake embedder: no key, no network)
    uv run python scripts/index_demo_cards.py --clinic-id <uuid> --real      (Gemini's embedding model through the gateway, when every provider gate holds; otherwise it says why and uses the fake)

What it does: embeds the invented placeholder cards of config/demo-cards.yaml (careflow/demo_cards.py; kb/cards stays empty and clinician-owned) and writes their vectors as the clinic's QUEUE WORKER through
kb.index_card_embedding (migration 0085: append-only, idempotent: a second run changes nothing). It then asks each made-up question of the invented cards through the real search (kb.search_card_embeddings, as the
worker) and counts how many found the card they were written for. It prints ONLY metadata: the mode (fake or real), the model id, the number of cards created and already there, seconds, cost, and counts. Never a card's
text, a question, a vector or a key.

What it refuses, before any connection is opened: unless the env file says CAREFLOW_ENV=dev, CAREFLOW_PROFILE=demo and CAREFLOW_DATA_POLICY=synthetic_only; unless the env names the DEV project (the same checks as
scripts/seed_dev_demo_data.py); unless YOU TYPE THE PROJECT HOST; unless the clinic exists and is a `test` clinic with the worker account holding the queue_worker role in it. Needs DATABASE_URL_APP and
CAREFLOW_WORKER_ACCOUNT_ID in the env file (as the app does). The thresholds come from the required settings (the demo pack under CAREFLOW_DEMO_PHASE4=1 supplies labelled DEMO ONLY values); with them unset the
search abstains and the report says so.
"""

import argparse
import asyncio
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import seed_dev_demo_data as seed
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from careflow import (
    demo_cards,
    demo_phase4,
    demo_real_models,
    knowledge,
    provider_setup,
    retrieval_eval,
)
from careflow.db import make_app_engine
from careflow.embeddings import FAKE_MODEL_ID, HashingEmbedding
from careflow.front_door_runner import worker_session
from careflow.gateway import SpendEntry
from careflow.knowledge_sql import index_cards
from careflow.spend import PostgresSpendLedger

Refused = seed.Refused


class RecordingLedger:
    def __init__(self, inner) -> None:
        self._inner, self.entries = inner, []

    async def spent_today(self, clinic_id, origin):
        return await self._inner.spent_today(clinic_id, origin)

    async def record(self, entry: SpendEntry) -> None:
        await self._inner.record(entry)
        self.entries.append(entry)


async def run(env: dict[str, str], clinic: uuid.UUID, *, real: bool, say=print) -> int:
    worker = uuid.UUID(env["CAREFLOW_WORKER_ACCOUNT_ID"])
    admin = create_async_engine(make_url(env["DATABASE_URL_MIGRATOR"].replace("postgresql://", "postgresql+asyncpg://", 1)), connect_args={"statement_cache_size": 0})
    app_engine = make_app_engine(env["DATABASE_URL_APP"], pool_size=2, max_overflow=2, pool_pre_ping=True)
    try:
        async with admin.connect() as conn:
            row = (await conn.execute(text("select origin from iam.clinics where id = :c"), {"c": clinic})).first()
            if row is None or row.origin != "test":
                raise Refused("refusing: that clinic does not exist here or is not a test clinic")
            held = (await conn.execute(text("select 1 from iam.staff_roles where account_id = :a and clinic_id = :c and role = 'queue_worker' and revoked_at is null"), {"a": worker, "c": clinic})).first()
            if held is None:
                raise Refused("refusing: the worker account does not hold the queue_worker role in this clinic")
        ledger = RecordingLedger(PostgresSpendLedger(app_engine, worker))
        environ = {**env, demo_real_models.SWITCH: "on"} if real else dict(env)
        providers = provider_setup.build_providers({k: v for k, v in env.items() if v}, profile="demo", ledger=ledger) if real else None
        hosted = providers.embedding_for(clinic) if providers is not None else None
        if real and hosted is None:
            why = providers.unavailable.get("gemini-embed", "the embedding provider was not built") if providers is not None else "not built"
            say(f"--real asked for, but the hosted embedding stayed fake: {why}")
        embedder, model_id, mode = (hosted, providers.embedding_model_id, "real") if hosted is not None else (HashingEmbedding(), FAKE_MODEL_ID, "fake")
        cards, queries = demo_cards.load()
        settings = demo_phase4.effective_environ(environ, "demo")
        session = worker_session(app_engine, worker)
        started = time.monotonic()
        async with session() as conn:
            done = await index_cards(conn, embedder, model_id, cards, clinic_id=clinic, origin="test")
        indexing_seconds = time.monotonic() - started
        search = knowledge.KnowledgeSearch(cards, lambda _clinic: (embedder, model_id), knowledge.policy_from(settings), budget_tokens=knowledge.budget_from(settings))
        found = abstained = 0
        started = time.monotonic()
        async with session() as conn:
            for q in queries:
                outcome = await search.search(conn, clinic, "test", q.text)
                if outcome.retrieval.abstained:
                    abstained += 1
                elif outcome.retrieval.candidates[0].card_id == q.expect:
                    found += 1
        search_seconds = time.monotonic() - started
        cost = float(sum(e.usd_actual for e in ledger.entries if e.role == "embed"))
        shadow = float(sum(e.usd_shadow for e in ledger.entries if e.role == "embed"))
        say(f"clinic {clinic}  mode: {mode}  model: {model_id}  dimensions: 768")
        say(f"indexing: {done.created} created, {done.existing} already there, {indexing_seconds:.2f}s, cost actual ${cost:.6f} / at scale ${shadow:.6f} (placeholder prices)")
        say(f"search over the database as the worker: {found} of {len(queries)} questions found their card first, {abstained} abstained, {len(queries) - found - abstained} answered with another card; {search_seconds:.2f}s")
        world = await retrieval_eval.OfflineWorld.build(cards, HashingEmbedding())
        report = await retrieval_eval.evaluate(world, queries, thresholds=retrieval_eval.thresholds_from(settings), policy=knowledge.policy_from(settings))
        recall = "not measured" if report.recall_at_k is None else f"{report.recall_at_k:.2f}"
        say(f"quality harness (offline, fake embedder): {report.status}; recall@k {recall}; reasons: {', '.join(report.reasons) or 'none'}")
        if knowledge.policy_from(settings) is None:
            say("the abstention thresholds are required settings and are not set, so the search abstains on every question (set them, or use CAREFLOW_DEMO_PHASE4=1 in the demo)")
        return 0
    finally:
        await admin.dispose()
        await app_engine.dispose()


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clinic-id", required=True, type=uuid.UUID)
    ap.add_argument("--env-file", default=".env")
    ap.add_argument("--real", action="store_true", help="use Gemini's embedding model through the gateway when every provider gate holds")
    args = ap.parse_args()
    env = {k: v for k, v in dotenv_values(Path(args.env_file)).items() if v is not None}
    try:
        seed.refuse_unless_dev_demo_synthetic(env)
        for name in ("DATABASE_URL_MIGRATOR", "DATABASE_URL_APP", "CAREFLOW_WORKER_ACCOUNT_ID"):
            if not (env.get(name) or "").strip():
                raise Refused(f"refusing: {name} is not set in the env file")
        try:
            uuid.UUID(env["CAREFLOW_WORKER_ACCOUNT_ID"])
        except ValueError:
            raise Refused("refusing: CAREFLOW_WORKER_ACCOUNT_ID is not an account id") from None
        host = seed.refuse_unless_same_project(env.get("SUPABASE_URL"), env["DATABASE_URL_MIGRATOR"], env.get("CAREFLOW_DEV_PROJECT_REF"))
        print(f"target project host: {host}")
        try:
            typed = input("Type that host exactly to confirm (anything else stops here): ")
        except EOFError:
            raise Refused("refusing: no host was typed") from None
        seed.confirm_target(typed, host)
        return await run(env, args.clinic_id, real=args.real)
    except Refused as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
