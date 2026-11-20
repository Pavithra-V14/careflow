"""Why the patient's list of bookable slots is empty (migration 0099), OFFLINE: the route says it in two fixed words and only when the list is empty; the function is static-checked. The database half is in tests/test_patient_booking_db_live.py."""

import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from careflow import app as app_module
from careflow import portal_booking_sql as pb

pytestmark = pytest.mark.asyncio
ROOT = Path(__file__).resolve().parent.parent
CLINIC = uuid.uuid4()
CLAIMS = {"sub": str(uuid.uuid4())}


@pytest.fixture
def route(monkeypatch):
    async def account(conn, claims, statuses=("active",)):
        return uuid.uuid4(), "test"

    monkeypatch.setattr(app_module, "_patient_account", account)
    monkeypatch.setattr(app_module, "_patient_booking_on", lambda: None)
    return monkeypatch


def slot_row():
    now = datetime.now(UTC)
    return pb.OpenSlot(uuid.uuid4(), now, now, uuid.uuid4(), "Visit", "Place")


async def call():
    return await app_module.list_bookable_slots(CLINIC, SimpleNamespace(), CLAIMS, 50)  # type: ignore[arg-type]


async def test_a_non_empty_list_carries_no_reason_and_the_state_is_not_even_asked(route):
    async def open_slots(conn, clinic, limit):
        return [slot_row()]

    async def state(conn, clinic):
        raise AssertionError("not asked when there are slots")

    route.setattr(pb, "open_slots", open_slots)
    route.setattr(pb, "booking_state", state)
    out = await call()
    assert len(out.slots) == 1 and out.empty_reason is None


@pytest.mark.parametrize(("state", "expected"), [("no_open_times", "no_open_times"), ("not_set_up", "not_set_up"), ("ok", None), ("anything else", None)])
async def test_an_empty_list_says_why_in_one_of_two_fixed_words(route, state, expected):
    async def open_slots(conn, clinic, limit):
        return []

    async def booking_state(conn, clinic):
        return state

    route.setattr(pb, "open_slots", open_slots)
    route.setattr(pb, "booking_state", booking_state)
    out = await call()
    assert out.slots == [] and out.empty_reason == expected  # a word the schema knows, or nothing: never a free sentence, a count or a name


async def test_a_refusal_of_the_state_is_the_same_fixed_403_as_everywhere(route):
    async def open_slots(conn, clinic, limit):
        return []

    async def booking_state(conn, clinic):
        raise pb.Refused

    route.setattr(pb, "open_slots", open_slots)
    route.setattr(pb, "booking_state", booking_state)
    with pytest.raises(app_module.HTTPException) as e:
        await call()
    assert e.value.status_code == 403 and e.value.detail == "your role may not do this"


def test_the_function_returns_a_word_never_a_count_and_is_executable_by_the_app_role_only():
    sql = (ROOT / "db" / "migrations" / "0099_patient_booking_state.sql").read_text(encoding="utf-8")
    code = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    assert "returns text" in code and "security definer set search_path = ''" in code and "stable" in code
    assert code.count("return '") == 3 and "'ok'" in code and "'no_open_times'" in code and "'not_set_up'" in code and "count(" not in code
    assert "grant execute on function sched.patient_booking_state(uuid) to careflow_app" in code and "insert into" not in code and "update " not in code
    # the same conditions as the list itself (0083), so the word and the list can never disagree
    for condition in ("s.status = 'open'", "s.starts_at > clock_timestamp()", "s.origin = v_origin", "vt.active", "l.active", "patient_bookable_visit_types"):
        assert condition in code


def test_the_schema_check_requires_the_function_and_the_response_declares_the_two_words():
    from careflow import schema_check

    assert ("0099", "sched.patient_booking_state(uuid)", "proc") in schema_check.REQUIRED
    assert app_module.PatientSlotList.model_fields["empty_reason"].default is None
