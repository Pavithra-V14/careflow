"""The patient-scoped note-vector wrapper (careflow/note_search.py), over the fake store. Offline; synthetic ids only."""

import uuid

import pytest

from careflow.adapters.fake import FakeVectorStore
from careflow.note_search import NoteScopeError, NoteVectors

pytestmark = pytest.mark.asyncio
A, B = uuid.uuid4(), uuid.uuid4()
P1, P2 = uuid.uuid4(), uuid.uuid4()


async def world():
    store = FakeVectorStore()
    nv = NoteVectors(store)
    await nv.index(clinic_id=A, patient_id=P1, chunk_id="c1", version=1, vector=(1.0,))
    await nv.index(clinic_id=A, patient_id=P1, chunk_id="c1-old", version=1, vector=(1.0,), is_current=False)
    await nv.index(clinic_id=A, patient_id=P2, chunk_id="c2", version=1, vector=(1.0,))
    await nv.index(clinic_id=B, patient_id=P1, chunk_id="c3", version=1, vector=(1.0,))  # the same patient id in ANOTHER clinic
    return nv


async def test_a_search_sees_only_this_patients_current_chunks_in_this_clinic():
    nv = await world()
    assert [h.card_id for h in await nv.search((1.0,), clinic_id=A, patient_id=P1)] == ["c1"]
    assert [h.card_id for h in await nv.search((1.0,), clinic_id=B, patient_id=P1)] == ["c3"]


@pytest.mark.parametrize(("clinic", "patient"), [(None, P1), (A, None), ("a", P1), (A, str(P1)), (None, None)])
async def test_a_missing_or_malformed_scope_raises_it_never_searches_everything(clinic, patient):
    nv = await world()
    with pytest.raises(NoteScopeError):
        await nv.search((1.0,), clinic_id=clinic, patient_id=patient)  # type: ignore[arg-type]


async def test_the_limit_is_bounded():
    nv = await world()
    for bad in (0, 51):
        with pytest.raises(NoteScopeError):
            await nv.search((1.0,), clinic_id=A, patient_id=P1, limit=bad)


async def test_erasure_deletes_the_patients_chunks_and_verifies_it_and_touches_nobody_else():
    nv = await world()
    result = await nv.erase_patient(["c1", "c1-old"], clinic_id=A, patient_id=P1)
    assert result.verified and result.deleted == 2
    assert await nv.search((1.0,), clinic_id=A, patient_id=P1) == []
    assert [h.card_id for h in await nv.search((1.0,), clinic_id=A, patient_id=P2)] == ["c2"] and [h.card_id for h in await nv.search((1.0,), clinic_id=B, patient_id=P1)] == ["c3"]


async def test_an_erasure_the_store_did_not_perform_is_reported_not_assumed():
    class Forgetful(FakeVectorStore):
        async def delete(self, card_ids):  # a store that silently keeps the points
            return None

    nv = NoteVectors(Forgetful())
    await nv.index(clinic_id=A, patient_id=P1, chunk_id="c1", version=1, vector=(1.0,))
    result = await nv.erase_patient(["c1"], clinic_id=A, patient_id=P1)
    assert not result.verified and result.still_found == ("c1",)
