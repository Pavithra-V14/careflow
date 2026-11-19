"""The knowledge editor (migration 0090; owner instruction 2026-11-14, deferred item 27). MECHANISM ONLY, OFF unless `CAREFLOW_KNOWLEDGE_EDITOR` is exactly `on`.

Draft, edit, submit, approve or reject a protocol CARD or a message TEMPLATE. NOTHING IS SEEDED and this module authors no word: every field is typed by a nurse or doctor (cards stay clinician-owned; `kb/cards` stays
empty). A draft is edited only by its author and frozen on submit; a DOCTOR who is NOT the author decides it (two people, enforced by the database); an approval writes the next immutable version. Approving a card does
NOT index it: the queue worker does (`index_sweep` below, through the existing `kb.index_card_embedding`), and only when an embedding route exists (`app.state.knowledge`, demo + synthetic only today); in the clinical
profile an approved card is stored and not searchable until an approved embedding route exists. A newer version of a card retires the older one in retrieval (the existing rule, an open question for the clinician).
Every database call is as the signed-in caller; a refusal is one fixed word.
"""

import logging
import os
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from careflow.care_gap_sql import sqlstate
from careflow.demo_cards import DemoCard
from careflow.knowledge_sql import index_cards
from careflow.retrieval import CardFields

SETTING: Final = "CAREFLOW_KNOWLEDGE_EDITOR"
log = logging.getLogger("careflow.knowledge_editor")


class Refused(Exception):
    """Not permitted: the wrong role or clinic, not the author, an author deciding their own draft, no recent second factor: one answer for every reason."""


class Conflict(Exception):
    """An idempotency key reused for a different request, or an open draft for that code already exists."""


def enabled(environ: Mapping[str, str] | None = None) -> bool:
    return ((environ if environ is not None else os.environ).get(SETTING) or "").strip().lower() == "on"


@dataclass(frozen=True)
class DraftRow:
    id: uuid.UUID
    kind: str
    code: str
    title: str
    body: str
    synonyms: list[str]
    patient_phrases: list[str]
    audience: list[str]
    status: str
    author: uuid.UUID
    decided_by: uuid.UUID | None
    reject_reason: str | None
    updated_at: datetime


@dataclass(frozen=True)
class VersionRow:
    kind: str
    code: str
    version: int
    title: str
    audience: list[str]
    author: uuid.UUID
    approved_by: uuid.UUID
    approved_at: datetime


async def _call(conn: AsyncConnection, sql: str, params: dict[str, Any]) -> list[Any]:
    try:
        async with conn.begin_nested():
            return list((await conn.execute(text(sql), params)).all())
    except DBAPIError as exc:
        state = sqlstate(exc)
        if state == "42501":
            raise Refused("refused") from None
        if state == "23505":
            raise Conflict("conflict") from None
        raise


async def create_draft(conn: AsyncConnection, *, clinic_id: uuid.UUID, kind: str, code: str, title: str, body: str, synonyms: Sequence[str], patient_phrases: Sequence[str], audience: Sequence[str], idempotency_key: str) -> tuple[uuid.UUID, bool]:
    rows = await _call(
        conn, "select o_id, o_created from kb.knowledge_draft_create(:c, :k, :code, :t, :b, cast(:s as text[]), cast(:p as text[]), cast(:a as text[]), :key)",
        {"c": clinic_id, "k": kind, "code": code, "t": title, "b": body, "s": list(synonyms), "p": list(patient_phrases), "a": list(audience), "key": idempotency_key},
    )
    return rows[0].o_id, bool(rows[0].o_created)


async def edit_draft(conn: AsyncConnection, *, clinic_id: uuid.UUID, draft_id: uuid.UUID, title: str, body: str, synonyms: Sequence[str], patient_phrases: Sequence[str], audience: Sequence[str], idempotency_key: str) -> str:
    rows = await _call(
        conn, "select o_result from kb.knowledge_draft_edit(:c, :d, :t, :b, cast(:s as text[]), cast(:p as text[]), cast(:a as text[]), :key)",
        {"c": clinic_id, "d": draft_id, "t": title, "b": body, "s": list(synonyms), "p": list(patient_phrases), "a": list(audience), "key": idempotency_key},
    )
    return rows[0].o_result


async def submit_draft(conn: AsyncConnection, *, clinic_id: uuid.UUID, draft_id: uuid.UUID, idempotency_key: str) -> str:
    return (await _call(conn, "select o_result from kb.knowledge_draft_submit(:c, :d, :key)", {"c": clinic_id, "d": draft_id, "key": idempotency_key}))[0].o_result


async def decide_draft(conn: AsyncConnection, *, clinic_id: uuid.UUID, draft_id: uuid.UUID, decision: str, reason: str | None, idempotency_key: str) -> tuple[str, int | None]:
    row = (await _call(conn, "select o_result, o_version from kb.knowledge_draft_decide(:c, :d, :dec, :r, :key)", {"c": clinic_id, "d": draft_id, "dec": decision, "r": reason, "key": idempotency_key}))[0]
    return row.o_result, row.o_version


async def list_drafts(conn: AsyncConnection, clinic_id: uuid.UUID, limit: int) -> list[DraftRow]:
    rows = await _call(
        conn, "select o_id, o_kind, o_code, o_title, o_body, o_synonyms, o_patient_phrases, o_audience, o_status, o_author, o_decided_by, o_reject_reason, o_updated_at from kb.knowledge_drafts_list(:c, :n)",
        {"c": clinic_id, "n": limit},
    )
    return [DraftRow(r.o_id, r.o_kind, r.o_code, r.o_title, r.o_body, list(r.o_synonyms), list(r.o_patient_phrases), list(r.o_audience), r.o_status, r.o_author, r.o_decided_by, r.o_reject_reason, r.o_updated_at) for r in rows]


async def list_versions(conn: AsyncConnection, clinic_id: uuid.UUID, limit: int) -> list[VersionRow]:
    rows = await _call(conn, "select o_kind, o_code, o_version, o_title, o_audience, o_author, o_approved_by, o_approved_at from kb.knowledge_versions_list(:c, :n)", {"c": clinic_id, "n": limit})
    return [VersionRow(r.o_kind, r.o_code, r.o_version, r.o_title, list(r.o_audience), r.o_author, r.o_approved_by, r.o_approved_at) for r in rows]


async def current_cards(conn: AsyncConnection, clinic_id: uuid.UUID) -> list[DemoCard]:
    """The current approved cards the caller's role may read, as retrieval's own card type (audience is not needed by retrieval: the database already filtered)."""
    rows = await _call(conn, "select o_code, o_version, o_title, o_body, o_synonyms, o_patient_phrases from kb.knowledge_current_cards(:c)", {"c": clinic_id})
    return [DemoCard(CardFields(r.o_code, r.o_version, r.o_title, r.o_body, tuple(r.o_synonyms), tuple(r.o_patient_phrases)), ()) for r in rows]


async def index_sweep(conn: AsyncConnection, search: Any, *, origins: Sequence[str] = ("test", "live"), batches: int = 1, errors: list[str] | None = None) -> int:
    """The worker's end-of-tick job: index every approved card version that is not in the vector table yet, in ascending version order, through the existing `kb.index_card_embedding`, per clinic the worker serves, one
    clinic failing never stopping the others. Needs an embedding route (`search`, which is None in the clinical profile): with none, nothing is indexed and nothing is invented. Up to `batches` batches of 50 per clinic
    (one by default). Returns the number indexed."""
    if search is None:
        return 0
    from careflow.producer_sweeps import _clinics

    done = 0
    for origin in origins:
        for clinic in await _clinics(conn, origin):
            try:
                async with conn.begin_nested():
                    for _ in range(max(1, batches)):
                        pending = await _call(conn, "select o_code, o_version, o_title, o_body, o_synonyms, o_patient_phrases, o_audience from kb.knowledge_versions_to_index(:c, 50)", {"c": clinic})
                        if not pending:
                            break
                        cards = [
                            DemoCard(CardFields(r.o_code, r.o_version, r.o_title, r.o_body, tuple(r.o_synonyms), tuple(r.o_patient_phrases)), tuple(r.o_audience))
                            for r in pending
                        ]
                        embedder, model_id = search._embedder_for(clinic)
                        result = await index_cards(conn, embedder, model_id, cards, clinic_id=clinic, origin=origin)
                        done += result.created
                        if result.created == 0:
                            break  # nothing new was written: stop rather than loop on the same batch
            except Exception as exc:  # noqa: BLE001 - one clinic failing must not stop the others; the class name only, never a card's words
                log.error("approved cards could not be indexed for one clinic (%s)", type(exc).__name__)
                if errors is not None:
                    errors.append(type(exc).__name__)
    return done


REBUILD_SETTING: Final = "CAREFLOW_VECTOR_REBUILD"
REBUILD_BATCHES: Final = 20


class RebuildRefused(Exception):
    """The vector index rebuild is a DEMO-PROFILE job (spec 16.6; owner decision 2026-11-18): refused in the clinical profile and anywhere that is not demo + synthetic_only."""


def rebuild_enabled(environ: Mapping[str, str] | None = None) -> bool:
    return ((environ if environ is not None else os.environ).get(REBUILD_SETTING) or "").strip().lower() == "on"


def refuse_unless_demo(profile: str, policy: str | None) -> None:
    if profile != "demo" or policy != "synthetic_only":
        raise RebuildRefused("the vector index rebuild runs only in profile=demo with data_policy=synthetic_only")


async def rebuild_sweep(conn: AsyncConnection, search: Any, *, profile: str, policy: str | None, state: dict[str, Any], today: date) -> int:
    """The nightly reconcile of the vector index from Postgres, the source of truth (spec 16.6, 10.8): at most once per calendar day per process, it drains every approved card version that is missing from the index
    (the index is append-only and idempotent, so a second run changes nothing). DEMO ONLY: raises `RebuildRefused` outside demo + synthetic_only, before anything is read. Returns the number indexed (0 when already done today)."""
    refuse_unless_demo(profile, policy)
    if state.get("last_day") == today:
        return 0
    errors: list[str] = []
    done = await index_sweep(conn, search, batches=REBUILD_BATCHES, errors=errors)
    if not errors:
        state["last_day"] = today  # only after a clean pass, so a failed night is tried again on the next tick
    return done
