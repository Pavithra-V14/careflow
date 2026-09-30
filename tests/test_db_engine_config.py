"""The app engine must be safe behind a transaction-mode pooler (BUILD-SPEC.md §18.1)."""

from careflow import db


def test_asyncpg_statement_cache_is_disabled():
    assert db.app_connect_args()["statement_cache_size"] == 0


def test_prepared_statement_names_are_unique():
    fn = db.app_connect_args()["prepared_statement_name_func"]
    assert len({fn() for _ in range(50)}) == 50


def test_sqlalchemy_dialect_cache_disabled_and_driver_normalised():
    url = db.app_engine_url("postgresql://u:p@h:6543/postgres")
    assert url.drivername == "postgresql+asyncpg"
    assert url.query["prepared_statement_cache_size"] == "0"


def test_make_app_engine_applies_both_settings(monkeypatch):
    captured = {}

    def fake_create(url, **kwargs):
        captured["url"], captured["kwargs"] = url, kwargs
        return object()

    monkeypatch.setattr(db, "create_async_engine", fake_create)
    db.make_app_engine("postgresql+asyncpg://u:p@h:6543/postgres")
    assert captured["kwargs"]["connect_args"]["statement_cache_size"] == 0
    assert "prepared_statement_name_func" in captured["kwargs"]["connect_args"]
    assert captured["url"].query["prepared_statement_cache_size"] == "0"


def test_caller_cannot_silently_reenable_the_cache_by_omission(monkeypatch):
    """Extra connect_args are merged in, but the required ones are always present."""
    captured = {}
    monkeypatch.setattr(db, "create_async_engine", lambda url, **kw: captured.update(kw) or object())
    db.make_app_engine("postgresql+asyncpg://u:p@h:6543/postgres", connect_args={"timeout": 5})
    assert captured["connect_args"]["timeout"] == 5
    assert captured["connect_args"]["statement_cache_size"] == 0
