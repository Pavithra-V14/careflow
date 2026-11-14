"""The patient-scoped wrapper for clinical-note vectors (BUILD-SPEC.md 10.2, 16.6). MECHANISM ONLY: no vector store or embedding model is approved, so this runs over the `VectorStorePort` fake.

THE ONLY DOOR to note vectors. An unfiltered search cannot be issued through it: the clinic id and the patient id are required, are real uuids, and become the pre-filter (10.7) together with `is_current`; a missing or malformed one RAISES, it never
defaults to searching everything. Vector payloads are ids only (the note text stays in Postgres, the source of truth: 16.6). Erasure deletes the vectors and then VERIFIES the deletion with a scoped search: a point that is still found is reported, never assumed gone.
The store's `card_id` field carries the note chunk id here; `version` is the note version the vector was built from, checked again at the join (10.8).
"""

import uuid
from dataclasses import dataclass

from careflow.ports import VectorHit, VectorPoint, VectorStorePort


class NoteScopeError(Exception):
    """The query has no usable clinic or patient scope. Fixed words; never a value."""


@dataclass(frozen=True)
class EraseResult:
    deleted: int
    still_found: tuple[str, ...]  # chunk ids the store still returns for this patient after the delete: the erasure is NOT complete while this is non-empty

    @property
    def verified(self) -> bool:
        return not self.still_found


def _scope(clinic_id: object, patient_id: object, *, current_only: bool = True) -> dict[str, str]:
    if not isinstance(clinic_id, uuid.UUID) or not isinstance(patient_id, uuid.UUID):
        raise NoteScopeError("a note search needs a clinic id and a patient id")
    scope = {"clinic_id": str(clinic_id), "patient_id": str(patient_id)}
    if current_only:
        scope["is_current"] = "true"
    return scope


class NoteVectors:
    def __init__(self, store: VectorStorePort) -> None:
        self._store = store

    async def index(self, *, clinic_id: uuid.UUID, patient_id: uuid.UUID, chunk_id: str, version: int, vector: tuple[float, ...], is_current: bool = True) -> None:
        scope = _scope(clinic_id, patient_id, current_only=False)
        scope["is_current"] = "true" if is_current else "false"
        await self._store.upsert([VectorPoint(chunk_id, version, vector, scope)])

    async def search(self, vector: tuple[float, ...], *, clinic_id: uuid.UUID, patient_id: uuid.UUID, limit: int = 5) -> list[VectorHit]:
        if limit < 1 or limit > 50:
            raise NoteScopeError("the limit must be from 1 to 50")
        return await self._store.search(vector, limit=limit, scope=_scope(clinic_id, patient_id))

    async def erase_patient(self, chunk_ids: list[str], *, clinic_id: uuid.UUID, patient_id: uuid.UUID) -> EraseResult:
        """Delete the patient's chunks, then look for them again (current and superseded alike) under the patient's own scope."""
        base = _scope(clinic_id, patient_id, current_only=False)
        await self._store.delete(chunk_ids)
        found: set[str] = set()
        for flag in ("true", "false"):
            for hit in await self._store.search((1.0,), limit=50, scope={**base, "is_current": flag}):
                found.add(hit.card_id)
        return EraseResult(len(chunk_ids), tuple(sorted(found & set(chunk_ids))))
