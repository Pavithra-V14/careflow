"""careflow/patient_view.py, offline: the gate (demo + synthetic only), the required setting with no default, the hard cap of 25, the per-user rate limit, list rows that carry only the year and the
last four digits, and a value that cannot be decrypted being a fixed refusal. The routes' behaviour as each role is in the role matrix and its live tests."""

import base64
import os
import uuid
from types import SimpleNamespace

import pytest

from careflow import patient_view as pv
from careflow.crypto import DemographicVault


def vault():
    k = lambda: base64.b64encode(os.urandom(32)).decode()
    return DemographicVault.from_env({"CAREFLOW_ENC_SECRET_V1": k(), "CAREFLOW_HMAC_SECRET_V1": k()})


def row(v, *, name="Asha Rao", dob="1980-03-05", phone="+919876543210", pid=None):
    enc = lambda value, field: v.encrypt(value, field=field, key_version=1)
    return SimpleNamespace(
        id=pid or uuid.uuid4(), clinic_id=uuid.uuid4(), name_ct=enc(name, "name"), name_key_version=1, dob_ct=enc(dob, "dob"), dob_key_version=1,
        phone_ct=enc(phone, "phone") if phone else None, phone_key_version=1,
    )


class Result:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


class Conn:
    def __init__(self, rows):
        self.rows, self.statements = rows, []

    async def execute(self, statement, params=None):
        self.statements.append(str(statement))
        return Result(self.rows)


def test_the_feature_is_on_only_for_the_demo_profile_with_a_synthetic_only_declaration():
    on = {"CAREFLOW_DATA_POLICY": "synthetic_only"}
    assert pv.feature_enabled("demo", on) and pv.feature_enabled("demo", {"CAREFLOW_DATA_POLICY": " Synthetic_Only "})
    for profile, env in (("clinical", on), ("demo", {}), ("demo", {"CAREFLOW_DATA_POLICY": "phi_approved"}), ("demo", {"CAREFLOW_DATA_POLICY": ""}), ("", on)):
        assert not pv.feature_enabled(profile, env), (profile, env)


def test_the_open_limit_is_required_and_has_no_default_and_the_page_limit_can_be_lowered_but_not_raised():
    assert pv.PatientViewSettings(_env_file=None).max_opens_per_minute is None
    assert pv.PatientViewSettings(_env_file=None).page_limit == 25
    assert pv.PatientViewSettings(_env_file=None, page_limit=10).page_limit == 10
    for bad in ({"page_limit": 26}, {"page_limit": 0}, {"max_opens_per_minute": 0}, {"max_opens_per_minute": 10_001}):
        with pytest.raises(ValueError):
            pv.PatientViewSettings(_env_file=None, **bad)


def test_require_ready_refuses_when_off_and_when_the_required_limit_is_missing_but_the_label_read_needs_no_limit():
    off = pv.PatientViewConfig(False, pv.PatientViewSettings(_env_file=None, max_opens_per_minute=5), pv.OpenRateLimiter(5))
    with pytest.raises(pv.ViewDisabled):
        pv.require_ready(off, needs_open_limit=False)
    unset = pv.PatientViewConfig(True, pv.PatientViewSettings(_env_file=None), None)
    with pytest.raises(pv.ViewNotConfigured):
        pv.require_ready(unset, needs_open_limit=True)
    pv.require_ready(unset, needs_open_limit=False)  # list rows do not need the open limit


def test_the_rate_limit_is_per_user_a_sliding_minute_and_refused_opens_do_not_count():
    now = [0.0]
    limiter = pv.OpenRateLimiter(2, clock=lambda: now[0])
    a, b = uuid.uuid4(), uuid.uuid4()
    assert limiter.allow(a) and limiter.allow(a) and not limiter.allow(a) and limiter.allow(b)
    now[0] = 59.0
    assert not limiter.allow(a)
    now[0] = 60.5
    assert limiter.allow(a) and limiter.allow(a) and not limiter.allow(a)  # the window slid; the refused attempts earlier did not extend it


@pytest.mark.asyncio
async def test_a_list_row_has_the_name_the_year_and_the_last_four_digits_and_nothing_else():
    v = vault()
    conn = Conn([row(v)])
    (label,) = await pv.patient_labels(conn, v, uuid.uuid4(), [uuid.uuid4()], page_limit=25)  # type: ignore[arg-type]
    assert (label.name, label.birth_year, label.phone_last4) == ("Asha Rao", 1980, "3210")
    assert set(vars(label)) == {"patient_id", "name", "birth_year", "phone_last4"}
    assert "9876543210" not in repr(label) and "1980-03-05" not in repr(label)


@pytest.mark.asyncio
async def test_a_record_without_a_phone_has_no_last_four_and_an_open_is_the_full_record():
    v = vault()
    (label,) = await pv.patient_labels(Conn([row(v, phone=None)]), v, uuid.uuid4(), [uuid.uuid4()], page_limit=25)  # type: ignore[arg-type]
    assert label.phone_last4 is None
    full = await pv.open_patient(Conn([row(v)]), v, uuid.uuid4())  # type: ignore[arg-type]
    assert (full.name, full.date_of_birth, full.phone) == ("Asha Rao", "1980-03-05", "+919876543210")


@pytest.mark.asyncio
async def test_more_ids_than_the_page_limit_or_the_hard_cap_are_refused_before_any_query():
    v = vault()
    conn = Conn([])
    with pytest.raises(pv.ViewError):
        await pv.patient_labels(conn, v, uuid.uuid4(), [uuid.uuid4() for _ in range(11)], page_limit=10)  # type: ignore[arg-type]
    with pytest.raises(pv.ViewError):
        await pv.patient_labels(conn, v, uuid.uuid4(), [uuid.uuid4() for _ in range(26)], page_limit=999)  # type: ignore[arg-type]  # the cap is 25 whatever the setting
    assert conn.statements == []
    assert await pv.patient_labels(conn, v, uuid.uuid4(), [], page_limit=25) == []  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_value_that_cannot_be_decrypted_is_a_fixed_refusal_with_nothing_partial_and_no_value_in_the_message():
    v = vault()
    broken = row(v)
    broken.dob_ct = b"\x00" * 40
    with pytest.raises(pv.CannotShow) as raised:
        await pv.open_patient(Conn([broken]), v, uuid.uuid4())  # type: ignore[arg-type]
    assert str(raised.value) == "this record cannot be shown" and "Asha" not in str(raised.value)
    other = row(vault())  # encrypted under a key this vault does not hold
    with pytest.raises(pv.CannotShow):
        await pv.patient_labels(Conn([other]), v, uuid.uuid4(), [uuid.uuid4()], page_limit=25)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_missing_or_erased_record_is_one_not_found():
    with pytest.raises(pv.PatientNotFound):
        await pv.open_patient(Conn([]), vault(), uuid.uuid4())  # type: ignore[arg-type]
