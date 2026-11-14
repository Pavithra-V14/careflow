"""Frontend request 32: in PUT /v1/portal/profile a field left OUT is unchanged, an explicit null CLEARS it, and clearing needs no word list. Offline, with the database calls replaced."""

import pytest

from careflow import app as app_module
from careflow import portal_extras_sql


class _Row:
    def __init__(self, sex, language):
        self.sex, self.preferred_language = sex, language


@pytest.fixture
def written(monkeypatch):
    seen: dict = {}

    async def account(conn, claims, statuses=("active",)):
        return "acct", "test"

    async def get(conn, account_id):
        return _Row("word_a", "ta")

    async def put(conn, *, sex, language):
        seen["sex"], seen["language"] = sex, language

    async def view(request, conn, claims):
        return None

    monkeypatch.setattr(app_module, "_patient_account", account)
    monkeypatch.setattr(portal_extras_sql, "get_profile", get)
    monkeypatch.setattr(portal_extras_sql, "set_profile", put)
    monkeypatch.setattr(app_module, "get_profile", view)
    monkeypatch.delenv(portal_extras_sql.SEX_SETTING, raising=False)  # NO word list configured
    return seen


async def _put(**fields):
    await app_module.put_profile(app_module.ProfileIn(**fields), None, "k", None, {})  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_field_left_out_is_unchanged(written):
    await _put(preferred_language="en")
    assert written == {"sex": "word_a", "language": "en"}


@pytest.mark.asyncio
async def test_an_explicit_null_clears_the_field_even_with_no_word_list(written):
    await _put(sex=None)
    assert written == {"sex": None, "language": "ta"}
    await _put(preferred_language=None)
    assert written["language"] is None and written["sex"] == "word_a"
