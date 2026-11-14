"""Card vectors in Postgres (migration 0085, pgvector): the store behind `VectorStorePort`, the version truth behind `CardTruth`, and the indexing path (BUILD-SPEC.md 10.1, 10.7, 10.8; CLAUDE.md rules 5, 6, 8).

EVERYTHING goes through the migration's three definer functions as the signed-in caller (the request's connection, or the worker's): there is no table grant, so nothing here can read or write a vector any other
way, and row-level security plus the functions' own checks decide what the caller may touch. A refusal is one fixed word (`Refused`), never a reason. A card's TEXT is embedded and discarded: what is stored is the
card id, its version, the model, the roles allowed to retrieve it, a hash of the embedded text and the 768 numbers.

This is the mechanism, not a clinical store: the cards indexed in this build are the invented placeholders of config/demo-cards.yaml.
"""

import hashlib
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from careflow.care_gap_sql import sqlstate
from careflow.demo_cards import DemoCard
from careflow.embeddings import MAX_TEXTS, vector_literal
from careflow.ports import EmbeddingPort, VectorHit, VectorPoint
from careflow.retrieval import retrieval_text

_REFUSED: Final = "42501"
_CONFLICT: Final = "23505"
_OUT_OF_SEQUENCE: Final = "23514"  # migration 0087: a version more than one above the highest indexed


class KnowledgeError(Exception):
    """Fixed words only; never a card's text, a vector or a query."""


class Refused(KnowledgeError):
    """The caller may not index or search here (not a member, the wrong role, or an origin that is not the clinic's): one answer for every reason."""


class VersionConflict(KnowledgeError):
    """A card version already holds different content, or the key was used for a different card: a changed card is a NEW version."""


def content_hash(card_text: str) -> str:
    return hashlib.sha256(card_text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class IndexResult:
    created: int
    existing: int


class PgVectorStore:
    """`VectorStorePort` for ONE request: `conn` carries the caller's verified claims. The scope must name `clinic_id` and `origin` (the pre-filter is never optional, 10.7); the caller's ROLE is checked by the database."""

    def __init__(self, conn: AsyncConnection, model_id: str) -> None:
        self._conn, self._model = conn, model_id

    @staticmethod
    def _scope(scope: Mapping[str, str]) -> tuple[uuid.UUID, str]:
        try:
            return uuid.UUID(scope["clinic_id"]), scope["origin"]
        except (KeyError, ValueError):
            raise KnowledgeError("a vector search needs a clinic and an origin in its scope (10.7)") from None

    async def index_one(self, point: VectorPoint) -> bool:
        """Index one card version; True if a row was created, False if the same version and content was already there."""
        clinic, origin = self._scope(point.scope)
        audience = [r for r in point.scope.get("audience", "").split(",") if r]
        sha = point.scope.get("content_sha256", "")
        key = f"{point.card_id}:{point.version}:{self._model}:{sha}"[:200]
        try:
            async with self._conn.begin_nested():
                row = (
                    await self._conn.execute(
                        text("select o_created from kb.index_card_embedding(:c, :o, :id, :v, :m, cast(:a as text[]), :e, :h, :k)"),
                        {"c": clinic, "o": origin, "id": point.card_id, "v": point.version, "m": self._model, "a": audience, "e": vector_literal(point.vector), "h": sha, "k": key},
                    )
                ).one()
        except DBAPIError as exc:
            state = sqlstate(exc)
            if state == _REFUSED:
                raise Refused("indexing is not permitted here") from None
            if state in (_CONFLICT, _OUT_OF_SEQUENCE):
                raise VersionConflict("this card version holds different content, or is not the next version") from None
            raise
        return bool(row.o_created)

    async def upsert(self, points: list[VectorPoint]) -> None:
        for p in points:
            await self.index_one(p)

    async def search(self, vector: tuple[float, ...], *, limit: int, scope: dict[str, str]) -> list[VectorHit]:
        clinic, origin = self._scope(scope)
        try:
            async with self._conn.begin_nested():
                rows = (
                    await self._conn.execute(
                        text("select o_card_id, o_card_version, o_similarity from kb.search_card_embeddings(:c, :o, :m, :q, :n)"),
                        {"c": clinic, "o": origin, "m": self._model, "q": vector_literal(vector), "n": limit},
                    )
                ).all()
        except DBAPIError as exc:
            if sqlstate(exc) == _REFUSED:
                raise Refused("search is not permitted here") from None
            raise
        return [VectorHit(r.o_card_id, r.o_card_version, float(r.o_similarity)) for r in rows]

    async def delete(self, card_ids: list[str]) -> None:
        raise KnowledgeError("card vectors are append-only: a card is retired by a newer version, never deleted")


class PgCardTruth:
    """`CardTruth` over the same table: the CURRENT indexed version of each named card that the caller may read (the version join of 10.8). Until a real card lifecycle exists, what is indexed is what is retrievable."""

    def __init__(self, conn: AsyncConnection, *, clinic_id: uuid.UUID, origin: str, model_id: str) -> None:
        self._conn, self._clinic, self._origin, self._model = conn, clinic_id, origin, model_id

    async def current_versions(self, card_ids: Sequence[str]) -> Mapping[str, int]:
        if not card_ids:
            return {}
        try:
            async with self._conn.begin_nested():
                rows = (
                    await self._conn.execute(
                        text("select o_card_id, o_card_version from kb.current_card_versions(:c, :o, :m, cast(:ids as text[]))"),
                        {"c": self._clinic, "o": self._origin, "m": self._model, "ids": list(card_ids)[:100]},
                    )
                ).all()
        except DBAPIError as exc:
            if sqlstate(exc) == _REFUSED:
                raise Refused("versions are not available here") from None
            raise
        return {r.o_card_id: r.o_card_version for r in rows}


async def index_cards(
    conn: AsyncConnection, embedder: EmbeddingPort, model_id: str, cards: Sequence[DemoCard], *, clinic_id: uuid.UUID, origin: str
) -> IndexResult:
    """Embed each card's retrieval text (title, description, synonyms, phrases: never anything else) in batches and index it, idempotently. Re-running changes nothing; a card whose text changed under the SAME
    version raises `VersionConflict` (bump the version)."""
    store = PgVectorStore(conn, model_id)
    created = existing = 0
    for start in range(0, len(cards), MAX_TEXTS):
        batch = list(cards[start : start + MAX_TEXTS])
        texts = [retrieval_text(c.fields) for c in batch]
        embedded = await embedder.embed(texts)
        for card, card_text, vector in zip(batch, texts, embedded.value.vectors, strict=True):
            point = VectorPoint(card.fields.card_id, card.fields.version, tuple(vector), {"clinic_id": str(clinic_id), "origin": origin, "audience": ",".join(card.audience), "content_sha256": content_hash(card_text)})
            if await store.index_one(point):
                created += 1
            else:
                existing += 1
    return IndexResult(created, existing)
