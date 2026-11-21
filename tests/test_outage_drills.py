"""The outage drills (careflow/outage_drill.py), OFFLINE: every check passes on the real code, each check can really FAIL (a negative control breaks the code under test and the drill must say FAIL), and the runnable script
refuses outside the synthetic demo. Fakes only: no network, no model, no database, no spend."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from careflow import outage_drill as drill
from careflow import retrieval

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "drill_outages.py"
pytestmark = pytest.mark.asyncio


async def test_every_drill_check_passes_on_the_real_code():
    results = await drill.run_all()
    assert len(results) >= 17
    failed = [c.line() for c in results if not c.passed]
    assert not failed, failed
    ids = {c.id for c in results}
    for needed in ("vision.failure", "decision.failure", "translation.failure", "embedding.failure", "embedding.long_query", "database.refusal", "rules.red_flag_with_models_down", "rules.before_models",
                   "effects.unreadable_page", "retry.restart", "spend.once_and_ceiling"):
        assert needed in ids, needed


async def test_the_lines_are_fixed_words_with_no_exception_text():
    for check in await drill.run_all():
        assert check.line().startswith(("PASS ", "FAIL ")) and "Traceback" not in check.line() and "the database refused the connection" not in check.line()


async def test_a_drill_that_cannot_run_is_a_fail_named_by_class_only(monkeypatch):
    async def boom():
        raise RuntimeError("SECRET detail that must not be printed")

    monkeypatch.setattr(drill, "ALL", (boom,))
    out = await drill.run_all()
    assert len(out) == 1 and not out[0].passed and "RuntimeError" in out[0].says and "SECRET" not in out[0].line()


async def test_a_negative_control_the_drill_fails_when_the_retriever_stops_abstaining(monkeypatch):
    """Break the code under test on purpose: a retriever that lets an embedding failure escape must turn the embedding checks red."""
    original = retrieval.CardRetriever.retrieve

    async def leaky(self, query, *, scope):  # no abstention: the embedder's failure reaches the caller
        await self.embedder.embed([query])
        return await original(self, query, scope=scope)

    monkeypatch.setattr(retrieval.CardRetriever, "retrieve", leaky)
    results = await drill.embedding_outage()
    assert [c.passed for c in results] == [False, False]


async def test_a_negative_control_the_drill_fails_when_a_writer_repeats_an_effect(monkeypatch):
    """A writer that performs its effect on every attempt (no idempotency) must turn the retry checks red."""

    def careless(self, key):
        self.attempts.append(key)
        self.log.append(f"effect:{self.name}")
        self.performed[f"{key}#{len(self.attempts)}"] = len(self.attempts)  # a NEW effect every time

    monkeypatch.setattr(drill._Effects, "_do", careless)
    results = await drill.retries_repeat_nothing()
    assert not all(c.passed for c in results)


def test_the_script_refuses_outside_the_synthetic_demo_and_prints_fixed_lines_inside_it():
    env_base = {k: v for k, v in os.environ.items() if not k.startswith(("CAREFLOW_", "GEMINI_", "OPENROUTER_", "SARVAM_", "DATABASE_"))}  # the machine's own variables, none of ours
    refused = subprocess.run([sys.executable, "-I", str(SCRIPT)], capture_output=True, text=True, timeout=120, check=False, env={**env_base, "CAREFLOW_PROFILE": "clinical"})
    assert refused.returncode == 1 and "demo" in refused.stdout and "PASS" not in refused.stdout
    ok = subprocess.run([sys.executable, "-I", str(SCRIPT)], capture_output=True, text=True, timeout=300, check=False, env={**env_base, "CAREFLOW_PROFILE": "demo", "CAREFLOW_DATA_POLICY": "synthetic_only"})
    assert ok.returncode == 0, ok.stdout + ok.stderr
    lines = [line for line in ok.stdout.splitlines() if line.startswith(("PASS ", "FAIL "))]
    assert len(lines) >= 17 and all(line.startswith("PASS ") for line in lines) and "drill result: PASS" in ok.stdout
    assert "spent nothing" in ok.stdout.lower() and "fakes only" in ok.stdout.lower()


def test_the_script_source_imports_no_provider_client_and_opens_no_connection():
    text = (ROOT / "scripts" / "drill_outages.py").read_text(encoding="utf-8") + (ROOT / "careflow" / "outage_drill.py").read_text(encoding="utf-8")
    for forbidden in ("httpx", "requests", "create_async_engine", "asyncpg", "GEMINI" + "_API_KEY", "OPENROUTER" + "_API_KEY", "SARVAM" + "_API_KEY", "os.environ["):  # (the names are joined so no test file spells a provider key variable)
        assert forbidden not in text, forbidden
    assert asyncio  # the drills are async
