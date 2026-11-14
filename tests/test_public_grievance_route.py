"""The public grievance route (POST /public/grievances; migration 0080), offline: it is a door with NO sign-in, so it is never unlimited and never silent. The route function is called directly with a stand-in request; the database
behaviour (the owner-published clinic, the owner-managed hourly cap, replay, the admin view) is in tests/test_portal_extras_db_live.py. Synthetic text only."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from careflow import app as app_module
from careflow import portal_extras_sql

pytestmark = pytest.mark.asyncio


class _NothingPublished:
    """An engine whose public clinic lookup finds nothing: the route then answers 404 (after every limit has been checked)."""

    def connect(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        return SimpleNamespace(first=lambda: None)


_VAULT = object()


def request(categories=frozenset({"demo_category"}), vault=_VAULT, host="203.0.113.7"):
    state = SimpleNamespace(grievance_categories=categories, vault=vault, engine=_NothingPublished(), active_key_version=1)
    return SimpleNamespace(app=SimpleNamespace(state=state), client=SimpleNamespace(host=host))


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    app_module._public_limiter_cache.clear()
    monkeypatch.setattr(app_module, "_active_key_version", lambda r: 1)
    monkeypatch.delenv(portal_extras_sql.PUBLIC_IP_SETTING, raising=False)
    yield
    app_module._public_limiter_cache.clear()


def body(category="demo_category"):
    return app_module.PublicGrievanceIn(category=category, text="DEMO complaint text")


async def call(req, key="k" * 32, **kw):
    return await app_module.file_public_grievance(kw.get("body", body()), req, key)


@pytest.mark.parametrize("raw", [None, "", "0", "-1", "abc", "1001", "2.5"])
async def test_without_a_usable_per_address_limit_the_route_answers_503_never_an_unlimited_door(monkeypatch, raw):
    if raw is not None:
        monkeypatch.setenv(portal_extras_sql.PUBLIC_IP_SETTING, raw)
    with pytest.raises(HTTPException) as e:
        await call(request())
    assert e.value.status_code == 503


async def test_without_the_category_list_or_the_keys_the_route_answers_503(monkeypatch):
    monkeypatch.setenv(portal_extras_sql.PUBLIC_IP_SETTING, "5")
    for req in (request(categories=None), request(vault=None)):
        app_module._public_limiter_cache.clear()
        with pytest.raises(HTTPException) as e:
            await call(req)
        assert e.value.status_code == 503


async def test_a_category_outside_the_clinics_list_is_rejected_before_anything_is_charged_or_written(monkeypatch):
    monkeypatch.setenv(portal_extras_sql.PUBLIC_IP_SETTING, "1")
    with pytest.raises(HTTPException) as e:
        await call(request(), body=body("free_text_category"))
    assert e.value.status_code == 422
    with pytest.raises(HTTPException) as again:  # the rejection did not use up the address's one allowance
        await call(request())
    assert again.value.status_code == 404  # past the limit, and only the missing published clinic stops it


async def test_one_address_is_limited_and_another_is_not(monkeypatch):
    monkeypatch.setenv(portal_extras_sql.PUBLIC_IP_SETTING, "2")
    for _ in range(2):
        with pytest.raises(HTTPException) as e:
            await call(request(host="198.51.100.1"))
        assert e.value.status_code == 404  # reached the clinic lookup: not limited yet
    with pytest.raises(HTTPException) as limited:
        await call(request(host="198.51.100.1"))
    assert limited.value.status_code == 429 and limited.value.detail == "too many requests; wait and try again"
    with pytest.raises(HTTPException) as other:
        await call(request(host="198.51.100.2"))
    assert other.value.status_code == 404


def test_the_hourly_limiter_forgets_after_an_hour():
    now = [0.0]
    limiter = portal_extras_sql.HourlyLimiter(1, clock=lambda: now[0])
    assert limiter.allow("a") and not limiter.allow("a") and limiter.allow("b")
    now[0] = 3601.0
    assert limiter.allow("a")


def test_the_sex_word_list_is_a_required_setting_and_a_bad_word_makes_the_whole_list_unusable(monkeypatch):
    for raw in (None, "", " , "):
        monkeypatch.delenv(portal_extras_sql.SEX_SETTING, raising=False)
        if raw is not None:
            monkeypatch.setenv(portal_extras_sql.SEX_SETTING, raw)
        assert portal_extras_sql.sex_words() is None
    monkeypatch.setenv(portal_extras_sql.SEX_SETTING, "word_a, word_b")
    assert portal_extras_sql.sex_words() == frozenset({"word_a", "word_b"})
    monkeypatch.setenv(portal_extras_sql.SEX_SETTING, "word_a,Bad Word")
    assert portal_extras_sql.sex_words() is None  # never a silent drop of one entry


async def test_a_short_or_guessable_key_is_rejected(monkeypatch):
    monkeypatch.setenv(portal_extras_sql.PUBLIC_IP_SETTING, "5")
    with pytest.raises(HTTPException) as e:
        await call(request(), key="1")
    assert e.value.status_code == 422


# ---- GET /public/grievance-categories: the closed list, no sign-in, never an unlimited door ---------------------------------------------------------------------------------------------------------------------


@pytest.fixture
def _read_fresh(monkeypatch):
    monkeypatch.delenv(portal_extras_sql.PUBLIC_READ_SETTING, raising=False)
    app_module._public_limiter_cache.clear()
    yield
    app_module._public_limiter_cache.clear()


async def read(categories=frozenset({"b_cat", "a_cat"}), host="203.0.113.9"):
    state = SimpleNamespace(grievance_categories=categories)
    req = SimpleNamespace(app=SimpleNamespace(state=state), client=SimpleNamespace(host=host))
    return await app_module.public_grievance_categories(req)  # type: ignore[arg-type]


@pytest.mark.parametrize("raw", [None, "", "0", "-1", "abc", "1001", "2.5"])
async def test_the_category_list_answers_503_without_a_usable_per_address_limit(monkeypatch, _read_fresh, raw):
    if raw is not None:
        monkeypatch.setenv(portal_extras_sql.PUBLIC_READ_SETTING, raw)
    with pytest.raises(HTTPException) as e:
        await read()
    assert e.value.status_code == 503


async def test_the_category_list_answers_503_until_the_list_is_set(monkeypatch, _read_fresh):
    monkeypatch.setenv(portal_extras_sql.PUBLIC_READ_SETTING, "5")
    with pytest.raises(HTTPException) as e:
        await read(categories=None)
    assert e.value.status_code == 503


async def test_the_category_list_is_the_sorted_closed_list_and_one_address_is_limited_per_minute(monkeypatch, _read_fresh):
    monkeypatch.setenv(portal_extras_sql.PUBLIC_READ_SETTING, "2")
    for _ in range(2):
        assert (await read()).categories == ["a_cat", "b_cat"]
    with pytest.raises(HTTPException) as limited:
        await read()
    assert limited.value.status_code == 429
    assert (await read(host="198.51.100.4")).categories == ["a_cat", "b_cat"]  # another address is not limited


def test_the_read_window_is_sixty_seconds_not_an_hour():
    now = [0.0]
    limiter = portal_extras_sql.HourlyLimiter(1, clock=lambda: now[0], window=60.0)
    assert limiter.allow("a") and not limiter.allow("a")
    now[0] = 61.0
    assert limiter.allow("a")


def test_the_route_is_public_and_the_demo_pack_supplies_a_labelled_limit():
    from pathlib import Path

    import yaml

    from careflow import demo_phase4

    routes = {r.path: r for r in app_module.public_config.routes}
    assert "/public/grievance-categories" in routes and not routes["/public/grievance-categories"].dependant.dependencies  # no sign-in dependency
    assert demo_phase4.VARIABLES["public_read_ip_per_minute"] == portal_extras_sql.PUBLIC_READ_SETTING
    pack = yaml.safe_load((Path(__file__).resolve().parent.parent / "config" / "demo-phase4.yaml").read_text(encoding="utf-8"))
    assert pack["settings"]["public_read_ip_per_minute"]["label"] == "DEMO ONLY - NOT A CLINICAL DECISION"
