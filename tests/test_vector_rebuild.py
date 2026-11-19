"""The nightly vector index rebuild (spec 16.6, owner decision 2026-11-18): DEMO PROFILE ONLY, refused in clinical, once a day, idempotent. Offline: the index itself is `index_sweep` (live-tested with the editor)."""

from datetime import date

import pytest

from careflow import knowledge_editor_sql as k

DAY = date(2026, 11, 19)

pytestmark = pytest.mark.asyncio


async def test_it_is_off_unless_exactly_on():
    assert k.rebuild_enabled({k.REBUILD_SETTING: "on"}) and k.rebuild_enabled({k.REBUILD_SETTING: " ON "})
    for bad in ("", "1", "true", "yes", "off"):
        assert not k.rebuild_enabled({k.REBUILD_SETTING: bad})
    assert not k.rebuild_enabled({})


@pytest.mark.parametrize("profile,policy", [("clinical", "synthetic_only"), ("clinical", None), ("clinical", "phi_approved"), ("demo", None), ("demo", "phi_approved"), ("", "synthetic_only")])
async def test_it_is_refused_outside_demo_with_synthetic_data_before_anything_is_read(profile, policy):
    class Boom:
        def __getattr__(self, name):
            raise AssertionError("nothing may be touched when refused")

    with pytest.raises(k.RebuildRefused):
        await k.rebuild_sweep(Boom(), Boom(), profile=profile, policy=policy, state={}, today=DAY)  # type: ignore[arg-type]


async def test_in_demo_it_runs_once_a_day_and_a_failed_night_is_tried_again(monkeypatch):
    calls = []

    async def fake_sweep(conn, search, *, batches, errors, **kw):
        calls.append(batches)
        if len(calls) == 1:
            errors.append("OperationalError")
            return 0
        return 7

    monkeypatch.setattr(k, "index_sweep", fake_sweep)
    state: dict = {}
    run = lambda day: k.rebuild_sweep(None, object(), profile="demo", policy="synthetic_only", state=state, today=day)  # type: ignore[arg-type]
    assert await run(DAY) == 0 and "last_day" not in state  # a failed pass is not marked done
    assert await run(DAY) == 7 and state["last_day"] == DAY
    assert await run(DAY) == 0 and len(calls) == 2  # already done today: nothing runs
    assert await run(date(2026, 11, 20)) == 7 and len(calls) == 3 and set(calls) == {k.REBUILD_BATCHES}


async def test_the_application_refuses_to_start_with_the_rebuild_on_outside_the_demo_whether_or_not_a_search_route_exists():
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "careflow" / "app.py").read_text(encoding="utf-8")
    refusal = source.index("knowledge_editor_sql.rebuild_enabled() and not demo_allowed(")
    wiring = source.index("knowledge_editor_sql.rebuild_enabled() and app.state.knowledge is not None")
    assert refusal < wiring and "RebuildRefused(" in source[refusal:wiring]  # the refusal does not depend on `app.state.knowledge`
