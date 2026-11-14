"""Run ONE synthetic dummy letter through the whole Front Door path with the REAL hosted models, once, and print ONLY metadata. A person runs it by hand; nothing runs it. DEMO PROFILE, SYNTHETIC DATA ONLY.

    uv run python scripts/demo_real_models.py --clinic-id <uuid>                  (a scan-style page: Gemini reads it)
    uv run python scripts/demo_real_models.py --clinic-id <uuid> --kind pdf        (a PDF with a text layer: read locally, no vision call)
    uv run python scripts/demo_real_models.py --clinic-id <uuid> --flagged         (the letter also carries the seeded DEMO rule's invented token: the rules fire and NO other model is asked)

The path (each stage says `real` or why it stayed `fake`; a missing key or gate keeps the fake, never a crash):
  1 text      the dummy page is read: a scan by Gemini through the gateway, a PDF locally. (The one step allowed before the rules: they need text to read.)
  2 rules     the deterministic red-flag rules screen that text BEFORE any other model. If they fire, the run ends there with the nurse task.
  3 identity  an invented token name and date of birth are read by a regular expression (not a model), each with a verified quote.
  4 decision  the decision model (Jev via OpenRouter, through the gateway) picks which fictional demo card fits the masked letter: a SUGGESTION that only reorders the fictional card ids.
  5 proposal  a triage proposal and its review task are written and the run PAUSES for a person. Nothing is approved, nothing is sent to a patient, no training label is made.
  6 draft     Sarvam translates an invented, labelled DEMO DRAFT sentence into Tamil as a suggestion. Nothing is stored or sent.

What it prints: stage, real or fake, status, seconds, cost, whether the rules fired, the order the stages ran in, and the ids of the document and the proposal waiting for review. NEVER the letter's text, a name, a
key, a URL or a model's words. The proposal and its review task are real rows in the demo clinic (the review console shows them; the run is reviewable through the review route because the graph's own checkpoint was
written). The page itself is NOT stored in the storage bucket (only its hash and size are recorded), so the console cannot open its bytes.

What it refuses, before any connection is opened: unless the env file says CAREFLOW_ENV=dev, CAREFLOW_PROFILE=demo and CAREFLOW_DATA_POLICY=synthetic_only; unless the env names the DEV project (the same checks as
scripts/seed_dev_demo_data.py); unless YOU TYPE THE PROJECT HOST; unless CAREFLOW_DEMO_PHASE3 is on (the demo Front Door needs it); unless the clinic is a `test` clinic that scripts/seed_dev_demo_data.py has already
seeded. Safe to re-run: the document, the thread and the proposal have fixed idempotency keys, so a second run reports the existing proposal instead of making another (the model calls are made only on the first run).
"""

import argparse
import asyncio
import dataclasses
import hashlib
import sys
import time
import uuid
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import seed_dev_demo_data as seed
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from careflow import demo_front_door, demo_real_models, provider_setup, synthetic_letter
from careflow.crypto import DemographicVault
from careflow.db import make_app_engine
from careflow.extraction import worker_context
from careflow.front_door import initial_state, run_config
from careflow.front_door_runner import graph_for, worker_session
from careflow.gateway import SpendEntry
from careflow.spend import PostgresSpendLedger

Refused = seed.Refused
EVENTS_OK_ORDER = ("text", "rules", "decision")


class RecordingLedger:
    """Delegates to the durable ledger and remembers what each call cost (numbers only), so the printout can show cost per role."""

    def __init__(self, inner) -> None:
        self._inner, self.entries = inner, []

    async def spent_today(self, clinic_id, origin):
        return await self._inner.spent_today(clinic_id, origin)

    async def record(self, entry: SpendEntry) -> None:
        await self._inner.record(entry)
        self.entries.append(entry)

    def cost(self, role: str) -> tuple[float, float]:
        rows = [e for e in self.entries if e.role == role]
        return float(sum(e.usd_actual for e in rows)), float(sum(e.usd_shadow for e in rows))


class TimedEngine:
    """The real rules engine, with a note when the rules first run (so the order of the stages can be shown and checked) and how long they took."""

    def __init__(self, inner, events: list[str]) -> None:
        self._inner, self._events = inner, events
        self.seconds: float | None = None

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if name not in ("evaluate_text", "evaluate_and_escalate") or not callable(attr):
            return attr

        async def timed(*args, **kwargs):
            if "rules" not in self._events:
                self._events.append("rules")
            started = time.monotonic()
            try:
                return await attr(*args, **kwargs)
            finally:
                self.seconds = (self.seconds or 0.0) + time.monotonic() - started

        return timed


class NoteFirstText:
    """Wraps the text source and notes when the page was first read."""

    def __init__(self, inner, events: list[str]) -> None:
        self._inner, self._events = inner, events

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def text_for(self, document_id):
        return await self._noted(self._inner.text_for(document_id))

    async def text_for_origin(self, document_id, origin):
        return await self._noted(self._inner.text_for_origin(document_id, origin))

    async def _noted(self, call):
        result = await call
        if "text" not in self._events:
            self._events.append("text")
        return result


class NoteDecision:
    """Wraps the retriever and notes when the decision stage ran."""

    def __init__(self, inner, events: list[str]) -> None:
        self._inner, self._events = inner, events

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def retrieve(self, query, *, scope):
        found = await self._inner.retrieve(query, scope=scope)
        if getattr(self._inner, "last", None) is not None and self._inner.last.outcome != "not_asked":
            self._events.append("decision")
        return found


def order_is_safe(events: list[str]) -> bool:
    """Text first (the one carve-out), then the rules, and the decision model never before the rules."""
    if "decision" in events and ("rules" not in events or events.index("rules") > events.index("decision")):
        return False
    return events[:1] in (["text"], ["rules"], [])


def provider_environ(env: Mapping[str, str | None]) -> dict[str, str]:
    return {k: v for k, v in env.items() if k and v}


def line(stage: str, mode: str, status: str, seconds: float | None, cost: str, note: str = "") -> str:
    secs = "-" if seconds is None else f"{seconds:.2f}"
    return f"{stage:<10} {mode:<5} {status:<22} {secs:>7}s  {cost:<30} {note}".rstrip()


def mode_of(label: str) -> str:
    return "real" if label.startswith("real") else "fake"


async def run(env: dict[str, str], clinic: uuid.UUID, *, kind: str, flagged: bool, say=print) -> int:
    migrator = env["DATABASE_URL_MIGRATOR"]
    try:
        vault = DemographicVault.from_env({k: v for k, v in env.items() if k.startswith("CAREFLOW_") and "_SECRET_V" in k})
        key_version = int(env.get("CAREFLOW_ACTIVE_KEY_VERSION") or max(vault.key_versions))
        worker = uuid.UUID(env["CAREFLOW_WORKER_ACCOUNT_ID"])
    except Exception:  # noqa: BLE001 - fixed words, never the key material
        raise Refused("refusing: the encryption keys, CAREFLOW_ACTIVE_KEY_VERSION or CAREFLOW_WORKER_ACCOUNT_ID are missing or malformed in the env file") from None
    page, content_type = (synthetic_letter.letter_pdf if kind == "pdf" else synthetic_letter.letter_scan)(flagged=flagged)
    key = f"real-models-demo:{kind}:{'flagged' if flagged else 'clean'}"
    document = seed.det(clinic, key)
    admin = create_async_engine(make_url(migrator.replace("postgresql://", "postgresql+asyncpg://", 1)), connect_args={"statement_cache_size": 0})
    app_engine = make_app_engine(env["DATABASE_URL_APP"], pool_size=2, max_overflow=2, pool_pre_ping=True)
    try:
        async with admin.begin() as conn:
            row = (await conn.execute(text("select origin from iam.clinics where id = :c"), {"c": clinic})).first()
            if row is None or row.origin != "test":
                raise Refused("refusing: that clinic does not exist here or is not a test clinic")
            placeholder = seed.det(seed.SEED, "placeholder-account")
            if (await conn.execute(text("select 1 from iam.accounts where id = :i"), {"i": placeholder})).first() is None or (await conn.execute(text("select 1 from sched.locations where clinic_id = :c and idempotency_key like :p limit 1"), {"c": clinic, "p": f"{seed.SEED}:%"})).first() is None:
                raise Refused("refusing: this clinic has not been seeded; run scripts/seed_dev_demo_data.py first")
            await conn.execute(
                text("insert into docs.documents (id, clinic_id, origin, kind, storage_path, content_type, size_bytes, sha256, registered_by, idempotency_key) values (:i, :c, 'test', 'referral', :p, :t, :sz, :h, :u, :k) on conflict (clinic_id, idempotency_key) do nothing"),
                {"i": document, "c": clinic, "p": f"clinic/{clinic}/doc/{document}", "t": content_type, "sz": len(page), "h": hashlib.sha256(page).hexdigest(), "u": placeholder, "k": key},
            )

        ledger = RecordingLedger(PostgresSpendLedger(app_engine, worker))
        providers = provider_setup.build_providers(provider_environ(env), profile="demo", ledger=ledger)
        fakes = demo_front_door.components_from_environment(env, "demo", vault=vault, key_version=key_version)
        if fakes is None:
            raise Refused("refusing: the demo Front Door is off: set CAREFLOW_DEMO_PHASE3=1 in the env file (and keep the profile demo with synthetic_only)")

        async def load(document_id: uuid.UUID):
            if document_id != document:
                raise Refused("refusing: this run reads only its own dummy page")
            return page, content_type, clinic

        up = demo_real_models.upgrade_components(fakes, providers, loader=load, environ={**env, demo_real_models.SWITCH: "1"}, profile="demo")
        events: list[str] = []
        engine = TimedEngine(up.components.engine, events)
        texts = up.components.texts
        real_texts = texts if isinstance(texts, demo_real_models.RealTexts) else None
        components = dataclasses.replace(up.components, engine=engine, texts=NoteFirstText(texts, events), retriever=NoteDecision(up.components.retriever, events))

        session = worker_session(app_engine, worker)
        graph = graph_for(components, session)
        config = run_config(document, clinic, "test")
        first_run = not (await graph.aget_state(config)).values
        started = time.monotonic()
        with worker_context():
            if first_run:
                await graph.ainvoke(initial_state(document, clinic, "test"), config)
        total = time.monotonic() - started
        state = (await graph.aget_state(config)).values or {}
        status = state.get("status", "unknown")
        fired = status == "red_flag_escalated"

        say(f"clinic {clinic}  document {document}  page: {kind}{' (flagged token)' if flagged else ''}  first run: {'yes' if first_run else 'no, the earlier result is reported and no model was called'}")
        say(f"{'stage':<10} {'mode':<5} {'status':<22} {'seconds':>8}  {'cost (actual / at scale)':<30} note")
        a, s = ledger.cost("vlm")
        say(line("1 text", mode_of(up.text), ("read" if "text" in events else "not run (earlier run)") if first_run else "not run", real_texts.first_read_seconds if real_texts else None, f"${a:.6f} / ${s:.6f}", up.text))
        say(line("2 rules", "real", "FIRED" if fired else ("clear" if status not in ("held_unassessable",) else "unassessable"), engine.seconds, "$0", "the deterministic rules; no model"))
        trace = up.ranker.last if up.ranker is not None else None
        a, s = ledger.cost("decision")
        say(line("3 decision", mode_of(up.decision), (trace.outcome if trace else "not run"), trace.seconds if trace else None, f"${a:.6f} / ${s:.6f}", up.decision + (f"; suggested card {trace.chosen}, confidence {trace.confidence:.2f} (never calibrated)" if trace and trace.chosen else "")))
        say(line("run", "-", status, total, "", f"rules fired: {'YES' if fired else 'no'}"))
        say(f"order the stages ran in: {' -> '.join(events) or '(none)'}  [{'rules before any decision model: OK' if order_is_safe(events) else 'ORDER VIOLATION: STOP AND REPORT'}]")
        if state.get("proposal_id"):
            say(f"PROPOSAL WAITING FOR REVIEW: {state['proposal_id']}  (a person decides in the review console; nothing was approved or sent)")
        elif fired:
            say("no proposal: the rules fired and the nurse task was raised, as designed")
        else:
            say(f"no proposal; the run ended as {status} (a hold or an alert for a person; see the review console)")
        suggestion = await demo_real_models.suggest_translation(providers.translation, clinic_id=clinic)
        say(line("4 draft", "real" if providers.translation is not None else "fake", suggestion.status, None, f"${ledger.cost('translation')[0]:.6f} recorded in the durable ledger (placeholder price)", f"{suggestion.characters} characters in; a suggestion, not stored, not sent"))
        for provider, why in providers.unavailable.items():
            say(f"{provider}: stayed fake: {why}")
        return 1 if not order_is_safe(events) else 0
    finally:
        await admin.dispose()
        await app_engine.dispose()


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clinic-id", required=True, type=uuid.UUID)
    ap.add_argument("--env-file", default=".env")
    ap.add_argument("--kind", choices=("scan", "pdf"), default="scan")
    ap.add_argument("--flagged", action="store_true", help="add the seeded DEMO rule's invented token to the letter: the rules fire and no other model is asked")
    args = ap.parse_args()
    env = {k: v for k, v in dotenv_values(Path(args.env_file)).items() if v is not None}
    try:
        seed.refuse_unless_dev_demo_synthetic(env)
        for name in ("DATABASE_URL_MIGRATOR", "DATABASE_URL_APP", "CAREFLOW_WORKER_ACCOUNT_ID"):
            if not (env.get(name) or "").strip():
                raise Refused(f"refusing: {name} is not set in the env file")
        host = seed.refuse_unless_same_project(env.get("SUPABASE_URL"), env["DATABASE_URL_MIGRATOR"], env.get("CAREFLOW_DEV_PROJECT_REF"))
        print(f"target project host: {host}")
        try:
            typed = input("Type that host exactly to confirm (anything else stops here): ")
        except EOFError:
            raise Refused("refusing: no host was typed") from None
        seed.confirm_target(typed, host)
        return await run(env, args.clinic_id, kind=args.kind, flagged=args.flagged)
    except Refused as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
