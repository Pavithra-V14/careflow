"""Running the Front Door graph: the worker job handler and the pieces the two routes share (BUILD-SPEC.md §7.2, §7.4, §12.1; CLAUDE.md rules 2, 4, 5, 8).

WHO RUNS WHAT (owner decision of 2026-10-24: the graph runs as the `queue_worker` system identity; humans decide through review tasks):

  * `POST /v1/staff/referrals/{document_id}/run` (front desk, nurse or doctor) only ENQUEUES a job of type `front_door_run` (payload: the document id; ids only, never text).
  * The WORKER (`front_door_handler`, registered with the in-process worker loop) claims the job and runs the graph as the worker account: screening, identity, matching,
    the triage proposal, and then it PAUSES at `review`. Every node opens its own transaction as the worker; every checkpoint is written by our own saver (migration 0021).
  * `POST /v1/staff/referrals/{document_id}/review` (nurse or doctor) resumes the paused thread IN THE REQUEST, as the reviewer: the reviewer id is the signed-in account
    (`claims["sub"]`), never a body field; the decision row is written with `decided_by = auth.uid()` (migration 0018 enforces it); apply_review is the only node that runs.

EXTERNAL ENGINES are stand-ins: the components the app is built with (`FrontDoorComponents`) hold the extractor, the retriever (embedder and vector store) and the text source.
Nothing real is wired here: with `components=None` the routes answer a visible 503 and no handler is registered (a queued job would be dead-lettered as "no handler", never lost).

KNOWN LIMIT ("no run ends silently" is about the GRAPH): a handler failure that is not retried into success (a malformed or mis-addressed job, an engine that keeps failing) ends in the
queue's dead-letter state after five attempts, and no review task is created for it; the dead-letter metric is what shows it. Closing that needs a human-visible task for a dead-lettered
front_door_run (recorded in docs/design/phase2-status.md).

The handler is safe to run twice and to cancel at any await: a second run resumes the thread from its last checkpoint (`ainvoke(None)`), a thread paused at review or already
finished is left alone, and every side effect inside the graph carries an idempotency key derived from the document.
"""

import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from careflow.checkpointer import PostgresCheckpointSaver, SqlCheckpointStore
from careflow.db import set_request_claims
from careflow.front_door import (
    FrontDoorDeps,
    HoldTaskWriter,
    ReviewRecorder,
    TextSource,
    TriageProposer,
    build_front_door,
    initial_state,
    run_config,
)
from careflow.referral import ReferralExtractor
from careflow.retrieval import CardRetriever
from careflow.tick import Handler, JobContext

TASK_TYPE = "front_door_run"
SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncConnection]]


class RunnerError(Exception):
    """Fixed words only; never a document's text or an extracted value."""


@dataclass
class FrontDoorComponents:
    """Everything the graph needs from outside the process, as ports. In this package every one of them is a stand-in supplied by the caller."""

    engine: Any  # RedFlagEngine
    texts: TextSource
    extractor: ReferralExtractor
    ingestor: Any  # PatientIngestionPipeline
    retriever: CardRetriever
    proposer: TriageProposer
    recorder: ReviewRecorder
    holds: HoldTaskWriter
    retrieval_scope: dict[str, str] = field(default_factory=dict)
    profile: str | None = None


def graph_for(components: FrontDoorComponents, session: SessionFactory):
    """The compiled graph with our Postgres saver, every node and the saver using `session` (a transaction with the caller's verified claims)."""
    deps = FrontDoorDeps(
        session=session, engine=components.engine, texts=components.texts, extractor=components.extractor, ingestor=components.ingestor,
        retriever=components.retriever, proposer=components.proposer, recorder=components.recorder, holds=components.holds,
        retrieval_scope=dict(components.retrieval_scope), profile=components.profile,
    )
    return build_front_door(deps, PostgresCheckpointSaver(SqlCheckpointStore(session)))


def worker_session(engine: AsyncEngine, account_id: uuid.UUID) -> SessionFactory:
    """One request-shaped transaction as the worker account per use, committed on a clean exit."""

    @asynccontextmanager
    async def session():
        async with engine.connect() as conn, conn.begin():
            await set_request_claims(conn, {"sub": str(account_id), "role": "authenticated"})
            yield conn

    return session


def request_session(conn: AsyncConnection) -> SessionFactory:
    """The request's own connection: each use is a savepoint inside the request transaction, so one failing step cannot poison the others."""

    @asynccontextmanager
    async def session():
        async with conn.begin_nested():
            yield conn

    return session


SessionFor = Callable[[AsyncEngine, uuid.UUID], SessionFactory]


def front_door_handler(components: FrontDoorComponents, *, session_for: SessionFor = worker_session) -> Handler:
    """The job handler for `front_door_run`. `session_for` is a seam for tests that cannot open their own connections."""

    async def handle(ctx: JobContext) -> None:
        try:
            document_id = uuid.UUID(str(ctx.job.payload["document_id"]))
        except (KeyError, ValueError):
            raise RunnerError("the job payload does not name a document") from None
        session = session_for(ctx.engine, ctx.worker_account_id)
        async with session() as conn:
            row = (await conn.execute(text("select clinic_id, origin from docs.documents where id = :d"), {"d": document_id})).first()
        if row is None or row.clinic_id != ctx.job.clinic_id or row.origin != ctx.job.origin:
            raise RunnerError("the document is not in the job's clinic and origin")  # a tampered or mis-addressed job: retried, then dead-lettered, never run
        graph = graph_for(components, session)
        config = run_config(document_id, ctx.job.clinic_id, ctx.job.origin)
        state = await graph.aget_state(config)
        if not state.values:
            await graph.ainvoke(initial_state(document_id, ctx.job.clinic_id, ctx.job.origin), config)
        elif state.next and state.next != ("review",):
            await graph.ainvoke(None, config)  # a crashed run: continue from the last checkpoint
        # paused at review (a person must decide) or finished: nothing to do, and the job is complete

    return handle
