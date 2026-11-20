"""GET /public/clinic/profile (migration 0100), OFFLINE: the public, unauthenticated, read-only profile of the PUBLISHED clinic. The route function is called directly with stand-ins. What it must never do: reveal an
unpublished or another clinic, answer without a limit, show an empty string as content, claim a feature that would not work, or carry an internal label. The database half is in tests/test_public_clinic_db_live.py."""

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response

from careflow import app as app_module
from careflow import clinic_profile_sql, portal_extras_sql

ROOT = Path(__file__).resolve().parent.parent


def profile(**over):
    base = {
        "name": "DEMO Clinic", "about": None, "departments": None, "address": None, "opening_hours": None, "phone": None, "privacy_promise": None, "placeholder": False, "has_bookable_type": False, "has_family_rule": False,
    }
    base.update(over)
    return clinic_profile_sql.PublicProfile(**base)


class _Engine:
    current: object = None

    def __init__(self, result):
        self.result = result

    def connect(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


ONE_KEY = SimpleNamespace(key_versions=(1,))


def request(result, *, host="203.0.113.5", vault=ONE_KEY, visit_content=None, active_key_version=None):
    state = SimpleNamespace(engine=_Engine(result), vault=vault, active_key_version=active_key_version, visit_content=visit_content, p4env=None)
    return SimpleNamespace(app=SimpleNamespace(state=state), client=SimpleNamespace(host=host))


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    for name in ("CAREFLOW_PATIENT_BOOKING", "CAREFLOW_PATIENT_MESSAGING", "CAREFLOW_PATIENT_VISIT_PREP", "CAREFLOW_FAMILY_LINKS", "CAREFLOW_MESSAGE_MAX_PER_MINUTE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(portal_extras_sql.PUBLIC_READ_SETTING, "100")
    app_module._public_limiter_cache.clear()
    app_module._message_limit_cache.clear()

    async def read_public(conn):
        return _Engine.current

    monkeypatch.setattr(clinic_profile_sql, "read_public", read_public)
    yield
    app_module._public_limiter_cache.clear()
    app_module._message_limit_cache.clear()


async def call(result, **kw):
    _Engine.current = result
    response = Response()
    out = await app_module.public_clinic_profile(request(result, **kw), response)  # type: ignore[arg-type]
    return out, response


_Engine.current = None


@pytest.mark.asyncio
async def test_nothing_published_is_a_404_that_is_never_cached():
    response = Response()
    _Engine.current = None
    with pytest.raises(HTTPException) as e:
        await app_module.public_clinic_profile(request(None), response)  # type: ignore[arg-type]
    assert e.value.status_code == 404 and e.value.detail == "not found" and (e.value.headers or {}).get("Cache-Control") == "no-store"  # on the EXCEPTION: headers set on the injected response are dropped when it is raised


@pytest.mark.asyncio
async def test_without_the_per_address_limit_it_is_a_503_never_an_unlimited_door_and_over_the_limit_a_429(monkeypatch):
    monkeypatch.delenv(portal_extras_sql.PUBLIC_READ_SETTING, raising=False)
    app_module._public_limiter_cache.clear()
    with pytest.raises(HTTPException) as e:
        await call(profile())
    assert e.value.status_code == 503
    monkeypatch.setenv(portal_extras_sql.PUBLIC_READ_SETTING, "2")
    app_module._public_limiter_cache.clear()
    await call(profile())
    await call(profile())
    with pytest.raises(HTTPException) as e:
        await call(profile())
    assert e.value.status_code == 429


@pytest.mark.asyncio
async def test_a_profile_with_nothing_supplied_is_all_nulls_and_the_response_has_exactly_the_documented_fields():
    out, response = await call(profile())
    assert out.about is None and out.departments is None and out.address is None and out.opening_hours is None and out.phone is None and out.privacy_promise is None
    assert response.headers["cache-control"] == "public, max-age=60"
    assert set(out.model_dump()) == {"name", "about", "departments", "address", "opening_hours", "phone", "privacy_promise", "placeholder", "features", "languages"}
    assert set(out.features.model_dump()) == {"booking", "messaging", "offers", "records", "visit_preparation", "family_links"}
    assert out.languages == ["en", "ta"] and out.placeholder is False
    dump = str(out.model_dump())
    assert "provenance" not in dump and "clinic_id" not in dump and "approved" not in dump.lower()  # no internal label, no id, never a claim


@pytest.mark.asyncio
async def test_supplied_values_are_returned_as_they_are_and_departments_may_carry_published_doctor_names():
    out, _ = await call(profile(about="DEMO about", address="DEMO address", departments=[{"name": "DEMO one", "doctors": ["DEMO Doctor"]}, {"name": "DEMO two"}], placeholder=True))
    assert out.about == "DEMO about" and out.address == "DEMO address" and out.placeholder is True
    assert [(d.name, d.doctors) for d in out.departments or []] == [("DEMO one", ["DEMO Doctor"]), ("DEMO two", [])]


@pytest.mark.asyncio
async def test_a_feature_is_on_only_when_its_switch_is_on_and_the_route_would_really_work(monkeypatch):
    out, _ = await call(profile(has_bookable_type=True, has_family_rule=True))
    assert not any(out.features.model_dump().values())  # every switch is off
    monkeypatch.setenv("CAREFLOW_PATIENT_BOOKING", "on")
    assert (await call(profile(has_bookable_type=False)))[0].features.booking is False  # on, but nothing is bookable: it would not work
    assert (await call(profile(has_bookable_type=True)))[0].features.booking is True
    monkeypatch.setenv("CAREFLOW_PATIENT_MESSAGING", "on")
    assert (await call(profile()))[0].features.messaging is False  # no per-minute limit: patient send answers 503
    monkeypatch.setenv("CAREFLOW_MESSAGE_MAX_PER_MINUTE", "10")
    app_module._message_limit_cache.clear()
    both = (await call(profile()))[0].features
    assert both.messaging is True and both.offers is True
    assert (await call(profile(), vault=None))[0].features.messaging is False  # no encryption keys: nothing can be sent
    two = SimpleNamespace(key_versions=(1, 2))
    assert (await call(profile(), vault=two))[0].features.messaging is False  # two key versions and none chosen: the send route answers 503
    assert (await call(profile(), vault=two, active_key_version=2))[0].features.offers is True
    monkeypatch.setenv("CAREFLOW_FAMILY_LINKS", "on")
    assert (await call(profile(has_family_rule=False)))[0].features.family_links is False  # no rule: every request is refused
    assert (await call(profile(has_family_rule=True)))[0].features.family_links is True
    monkeypatch.setenv("CAREFLOW_PATIENT_VISIT_PREP", "on")
    assert (await call(profile()))[0].features.visit_preparation is False  # no checklist content
    assert (await call(profile(), visit_content=object()))[0].features.visit_preparation is True
    assert (await call(profile()))[0].features.records is False  # the portal has a download, not a records view


def test_the_write_route_and_the_database_functions_are_admin_only_aal2_idempotent_and_audited():
    sql = (ROOT / "db" / "migrations" / "0100_clinic_public_profile.sql").read_text(encoding="utf-8")
    code = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    assert "force row level security" in code and "revoke all on iam.clinic_public_profile from" in code and "grant select" not in code and "grant insert" not in code
    write = code[code.index("create function iam.set_clinic_public_profile") : code.index("do $$\ndeclare f text; r text;")]
    assert "iam.aal2_session()" in write and "iam.has_clinic_role(p_clinic_id, array['admin'])" in write and "p_idempotency_key" in write and "insert into audit.log" in write and "'23505'" in write.replace("23505", "'23505'")
    assert "jsonb_build_object('fields_set'" in write and "p_about" not in write.split("jsonb_build_object('fields_set'")[1].split(")")[0]  # the audit row names the fields, never their text
    read = code[code.index("create function iam.public_clinic_profile()") : code.index("create function iam.set_clinic_public_profile")]
    assert "from iam.public_clinics pc" in read and "count(" not in read and "clinic_id" not in read.split("returns table")[1].split("language")[0]  # only the published clinic; no id, no count in the answer
    column = re.search(r"privacy_promise\s+text.*", code)
    assert column and "is null or" in column.group(0) and "default" not in column.group(0)  # nullable, no default content


@pytest.mark.asyncio
async def test_the_503_and_429_answers_are_never_cached_either(monkeypatch):
    monkeypatch.setenv(portal_extras_sql.PUBLIC_READ_SETTING, "1")
    app_module._public_limiter_cache.clear()
    await call(profile())
    with pytest.raises(HTTPException) as e:
        await call(profile())
    assert e.value.status_code == 429 and (e.value.headers or {}).get("Cache-Control") == "no-store"
    monkeypatch.delenv(portal_extras_sql.PUBLIC_READ_SETTING)
    app_module._public_limiter_cache.clear()
    with pytest.raises(HTTPException) as e:
        await call(profile())
    assert e.value.status_code == 503 and (e.value.headers or {}).get("Cache-Control") == "no-store" and "limit" not in str(e.value.detail)  # no word about how the server is configured


def test_the_limiter_forgets_addresses_that_stopped_asking_so_memory_cannot_grow_without_bound():
    now = [0.0]
    limiter = portal_extras_sql.HourlyLimiter(5, clock=lambda: now[0], window=60.0)
    for i in range(1000):
        limiter.allow(f"addr-{i}")
    now[0] = 61.0
    limiter.allow("fresh")
    assert len(limiter._events) <= 2  # the stale addresses are swept, only live ones remain
