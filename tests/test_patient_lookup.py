"""Patient lookup and the verified-status read (careflow/mpi.py `lookup_patients`, `patient_status`), offline with a scripted connection. The lookup is EXACT, read-only and returns ids and
flags only; the status uses the matcher's own rule (the latest confirm/revoke event, the master's record for a subordinate of an active merge)."""

import uuid
from datetime import UTC, datetime

import pytest

from careflow import mpi
from tests.test_unverified_records import make

pytestmark = pytest.mark.asyncio
CLINIC = uuid.uuid4()
NOW = datetime(2030, 1, 1, tzinfo=UTC)


class Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class Conn:
    def __init__(self, *, hits=(), sources=(), patient=None, master=None, event=None):
        self.hits, self.sources, self.patient, self.master, self.event, self.statements = hits, sources, patient, master, event, []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        if "from mpi.identity_tokens t" in sql:
            return Result(self.hits)
        if "select id, registered_via from mpi.patients" in sql:
            return Result(self.sources)
        if "from mpi.patients p left join mpi.merges" in sql:
            return Result([self.patient] if self.patient else [])
        if "select registered_via from mpi.patients where id" in sql:
            return Result([(self.master,)] if self.master else [])
        if "order by seq desc limit 1" in sql:
            return Result([self.event] if self.event else [])
        raise AssertionError(sql)


ARGS = {"clinic_id": CLINIC, "origin": "test", "name": "  Asha   Venkatesh ", "dob": "1987-04-23", "phone": "9876543210"}


async def test_the_lookup_reports_ids_kinds_and_verification_only_and_writes_nothing():
    a, b = uuid.uuid4(), uuid.uuid4()
    conn = Conn(hits=[(b, ["name_dob"], True), (a, ["phone", "name_dob"], False)], sources=[(a, "referral_worker"), (b, "front_desk")])
    found = await mpi.lookup_patients(make(), conn, **ARGS)  # type: ignore[arg-type]
    assert {h.patient_id: (h.matched_kinds, h.verified, h.registered_via) for h in found} == {a: (("name_dob", "phone"), False, "referral_worker"), b: (("name_dob",), True, "front_desk")}
    sql = " ".join(conn.statements).lower()
    assert not any(w in sql for w in ("insert", "update", "delete", "advisory", "name_ct", "dob_ct", "phone_ct"))  # read-only, and nothing encrypted is selected


async def test_no_hit_is_an_empty_list_after_one_query_and_bad_input_is_refused_before_any_query():
    conn = Conn()
    assert await mpi.lookup_patients(make(), conn, **ARGS) == []  # type: ignore[arg-type]
    assert len(conn.statements) == 1
    for bad, error in (({"dob": "2999-01-01"}, mpi.InvalidDateOfBirth), ({"dob": "not a date"}, mpi.InvalidDateOfBirth), ({"phone": "123"}, mpi.InvalidPhone), ({"name": "   "}, mpi.MissingRequiredField),
                       ({"origin": "production"}, ValueError)):
        untouched = Conn()
        with pytest.raises(error):
            await mpi.lookup_patients(make(), untouched, **{**ARGS, **bad})  # type: ignore[arg-type]
        assert untouched.statements == []


async def test_the_phone_is_optional_and_a_phone_only_difference_changes_the_tokens_searched():
    seen = []

    class Spy(Conn):
        async def execute(self, statement, params=None):
            if "from mpi.identity_tokens t" in str(statement):
                seen.append(sorted(k for k in (params or {}) if k in ("nd", "ph")))
            return await super().execute(statement, params)

    await mpi.lookup_patients(make(), Spy(), **{**ARGS, "phone": None})  # type: ignore[arg-type]
    await mpi.lookup_patients(make(), Spy(), **ARGS)  # type: ignore[arg-type]
    assert seen == [["nd"], ["nd", "ph"]]


@pytest.mark.parametrize(
    ("via", "event", "verified", "latest"),
    [
        ("referral_worker", None, False, None), ("front_desk", None, True, None), ("referral_worker", ("confirm", uuid.uuid4(), NOW), True, "confirm"),
        ("front_desk", ("revoke", uuid.uuid4(), NOW), False, "revoke"), ("referral_worker", ("revoke", uuid.uuid4(), NOW), False, "revoke"),
    ],
)
async def test_the_status_is_the_latest_event_or_else_the_source(via, event, verified, latest):
    pid = uuid.uuid4()
    s = await mpi.patient_status(Conn(patient=(CLINIC, "test", via, None), event=event), pid)  # type: ignore[arg-type]
    assert (s.verified, s.latest_event, s.registered_via, s.merged_into) == (verified, latest, via, None) and s.patient_id == pid


async def test_a_subordinate_of_an_active_merge_takes_its_masters_status_and_names_the_master():
    pid, master = uuid.uuid4(), uuid.uuid4()
    s = await mpi.patient_status(Conn(patient=(CLINIC, "test", "front_desk", master), master="referral_worker"), pid)  # type: ignore[arg-type]
    assert s.merged_into == master and s.verified is False  # the master is worker-created and unconfirmed, so the subordinate's own front-desk source does not help
    confirmed = await mpi.patient_status(Conn(patient=(CLINIC, "test", "referral_worker", master), master="referral_worker", event=("confirm", uuid.uuid4(), NOW)), pid)  # type: ignore[arg-type]
    assert confirmed.verified is True and confirmed.merged_into == master


async def test_an_invisible_or_erased_record_is_not_found():
    with pytest.raises(mpi.PatientNotFound):
        await mpi.patient_status(Conn(), uuid.uuid4())  # type: ignore[arg-type]
