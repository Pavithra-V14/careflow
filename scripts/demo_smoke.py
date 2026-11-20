"""A read-mostly SMOKE CHECK of the dev demo: is everything in place for the walks in docs/demo-runbook.md? A person runs it by hand; nothing runs it.

    uv run python scripts/demo_smoke.py --clinic-id <uuid> [--env-file .env]

It prints only PASS or FAIL lines with fixed words and counts: never a key, a value, a name, a row or an e-mail address. Environment variables are checked by NAME only. It writes NOTHING (every query is a
read; the one search runs as the queue worker and records nothing) and spends nothing (the demo search uses the local fake embedder; no provider is called).

What it refuses, before any connection is opened (the rules of scripts/seed_dev_demo_data.py): unless the env file says CAREFLOW_ENV=dev, CAREFLOW_PROFILE=demo and CAREFLOW_DATA_POLICY=synthetic_only; unless the env
names the DEV project; unless YOU TYPE THE PROJECT HOST; unless the clinic is a `test` clinic.

The checks, in order: the migrations the code needs are applied; the clinic is the published public clinic; staff roles exist (front desk, nurse, doctor, admin, queue worker); every demo placeholder row carries its
label and none is unlabelled; the six invented demo cards are indexed; a knowledge search returns a card; the provider keys are present (names only, required when CAREFLOW_DEMO_REAL_MODELS is on); the spend ceilings
are set (the same condition); the red-flag rules come before any model (a model-reading step cannot be given a letter the rules did not clear, and an empty rule set clears nothing); and the switches in the env file
are consistent with section 14 of docs/demo-runbook.md. Exit status 0 only if every check passes.
"""

import argparse
import asyncio
import inspect
import math
import sys
import uuid
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import seed_dev_demo_data as seed
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

Refused = seed.Refused
Result = tuple[str, bool, str]

REQUIRED_ROLES = ("front_desk", "nurse", "doctor", "admin", "queue_worker")
LABELLED_TABLES = (
    "iam.break_glass_reasons", "iam.break_glass_settings", "iam.public_grievance_limits", "docs.canary_settings", "ops.spend_limits", "sched.patient_bookable_visit_types", "iam.family_link_rules",
)
KEY_NAMES = ("GEMINI_API_KEY", "OPENROUTER_API_KEY", "SARVAM_API_KEY")
CEILING_NAMES = ("CAREFLOW_SPEND_CEILING_VLM_USD", "CAREFLOW_SPEND_CEILING_DECISION_USD", "CAREFLOW_SPEND_CEILING_TRANSLATION_USD", "CAREFLOW_SPEND_CEILING_EMBED_USD", "CAREFLOW_SPEND_CAP_PER_RUN_USD")
SWITCHES = (
    "CAREFLOW_PATIENT_MESSAGING", "CAREFLOW_PATIENT_BOOKING", "CAREFLOW_PATIENT_VISIT_PREP", "CAREFLOW_FAMILY_LINKS", "CAREFLOW_KNOWLEDGE_EDITOR", "CAREFLOW_KNOWLEDGE_SEARCH", "CAREFLOW_VECTOR_REBUILD",
    "CAREFLOW_DEMO_EMAIL_SIGNIN", "CAREFLOW_DEMO_REAL_MODELS",
)


SCREEN_SWITCHES = ("CAREFLOW_PATIENT_VISIT_PREP", "CAREFLOW_FAMILY_LINKS", "CAREFLOW_KNOWLEDGE_EDITOR")  # section 13 of docs/demo-runbook.md
AAL2_SCREENS = ("CAREFLOW_FAMILY_LINKS", "CAREFLOW_KNOWLEDGE_EDITOR", "CAREFLOW_PATIENT_VISIT_PREP", "CAREFLOW_DEMO_EMAIL_SIGNIN")  # their database functions (or the claim approval) need a real second factor

# One plain-language "what to do" line per check, in fixed words, printed under every FAIL (never a value).
FIXES = {
    "migrations are applied": "run the migrations (python -m careflow.migrate), then run this again",
    "the clinic is published": "publish it (scripts/dev_clinic.py publish --clinic-id <the clinic id>), then run this again",
    "staff roles exist": "create the missing staff accounts with scripts/seed_dev_staff.py (demo runbook section 3), then run this again",
    "demo placeholders carry their labels": "run scripts/seed_demo_placeholders.py (demo runbook section 14); an unlabelled row is a real value or an unlabelled seed: leave it and tell the owner",
    "the six demo cards are indexed": "run scripts/index_demo_cards.py --clinic-id <the clinic id>, then run this again",
    "a knowledge search returns a card": "set CAREFLOW_KNOWLEDGE_SEARCH=on, check the retrieval settings come from the demo pack, and re-index the demo cards without --real",
    "provider keys are present": "run scripts/set_provider_keys.ps1 (it never shows a key), or turn CAREFLOW_DEMO_REAL_MODELS off in .env",
    "spend ceilings are set": "run scripts/set_provider_keys.ps1 to set the ceilings, or turn CAREFLOW_DEMO_REAL_MODELS off in .env",
    "rules run before any model": "stop: this is a code fault, not a setting; tell the engineer and do not run the demo",
    "the .env switches are consistent with section 14": "fix the .env line named above (exactly on or off, profile demo, data policy synthetic_only) and run this again",
    "no backend runs without the demo profile": "stop that backend (Ctrl+C in its window) and start it only with scripts/run_demo_backend.ps1, then run this again",
    "portal accounts have the demo origin": "run scripts/reset_dev_portal_account.py --account-id <the user's UID> --dry-run, then without --dry-run (demo runbook section 15)",
    "the second factor matches the screens switched on": "turn CAREFLOW_REQUIRE_AAL2 back on and enrol the accounts (docs/mfa-live-pass.md), or switch those screens off",
    "the three screen switches are on": "add the switch named above as NAME=on to .env and run this again",
}


def is_on(env: Mapping[str, str | None], name: str) -> bool:
    return (env.get(name) or "").strip().lower() == "on"


# ---- pure judgements (no I/O: these are what the tests exercise) -------------------------------------------------------------------------------------------------------------------


def judge_roles(counts: Mapping[str, int]) -> Result:
    missing = [r for r in REQUIRED_ROLES if counts.get(r, 0) < 1]
    summary = ", ".join(f"{r} {counts.get(r, 0)}" for r in REQUIRED_ROLES)
    return ("staff roles exist", not missing, f"{summary}" if not missing else f"no live grant for: {', '.join(missing)} (run scripts/seed_dev_staff.py); {summary}")


def judge_labels(per_table: Mapping[str, tuple[int, int]]) -> Result:
    labelled = sum(v[0] for v in per_table.values())
    unlabelled = sum(v[1] for v in per_table.values())
    if unlabelled:
        return ("demo placeholders carry their labels", False, f"{unlabelled} row(s) in the owner-managed tables have no demo label (a real value, or an unlabelled seed); {labelled} labelled")
    if not labelled:
        return ("demo placeholders carry their labels", False, "no demo placeholder is entered (run scripts/seed_demo_placeholders.py, section 14 of the runbook)")
    return ("demo placeholders carry their labels", True, f"{labelled} labelled row(s), 0 unlabelled")


def judge_cards(indexed: set[str], expected: set[str], models: set[str]) -> Result:
    if not expected <= indexed:
        return ("the six demo cards are indexed", False, f"{len(indexed & expected)} of {len(expected)} indexed (run scripts/index_demo_cards.py)")
    return ("the six demo cards are indexed", True, f"{len(expected)} of {len(expected)} indexed, {len(models)} embedding model(s)")


def judge_search(found_card: bool, abstained: bool, indexed_with_fake_only: bool) -> Result:
    if not indexed_with_fake_only:
        return ("a knowledge search returns a card", False, "the cards are indexed under a hosted model and this check searches with the local embedder: re-index without --real, or check by hand")
    if abstained:
        return ("a knowledge search returns a card", False, "the search abstained (are the retrieval thresholds set? the demo pack supplies them)")
    return ("a knowledge search returns a card", found_card, "1 card returned" if found_card else "no card was returned")


def judge_keys(env: Mapping[str, str | None]) -> Result:
    if not is_on(env, "CAREFLOW_DEMO_REAL_MODELS"):
        return ("provider keys are present", True, "not required: CAREFLOW_DEMO_REAL_MODELS is off, the local fakes are in use")
    missing = [n for n in KEY_NAMES if not (env.get(n) or "").strip()]
    return ("provider keys are present", not missing, f"{len(KEY_NAMES) - len(missing)} of {len(KEY_NAMES)} present (names only)" + (f"; missing: {', '.join(missing)}" if missing else ""))


def _positive(value: str | None) -> bool:
    try:
        number = Decimal((value or "").strip())
    except InvalidOperation:
        return False
    return number.is_finite() and number > 0 and not math.isnan(float(number))


def judge_ceilings(env: Mapping[str, str | None]) -> Result:
    if not is_on(env, "CAREFLOW_DEMO_REAL_MODELS"):
        return ("spend ceilings are set", True, "not required: CAREFLOW_DEMO_REAL_MODELS is off")
    bad = [n for n in CEILING_NAMES if not _positive(env.get(n))]
    return ("spend ceilings are set", not bad, f"{len(CEILING_NAMES) - len(bad)} of {len(CEILING_NAMES)} set to a positive number" + (f"; not set or not a number: {', '.join(bad)}" if bad else ""))


def judge_switches(env: Mapping[str, str | None]) -> Result:
    problems: list[str] = []
    for name in SWITCHES:
        value = (env.get(name) or "").strip()
        if value and value.lower() != "on" and value.lower() != "off":
            problems.append(f"{name} is not exactly on")
    if (env.get("CAREFLOW_PROFILE") or "").strip().lower() != "demo" or (env.get("CAREFLOW_DATA_POLICY") or "").strip() != "synthetic_only":
        problems.append("profile/data policy are not demo + synthetic_only")
    if is_on(env, "CAREFLOW_VECTOR_REBUILD") and not is_on(env, "CAREFLOW_KNOWLEDGE_EDITOR"):
        problems.append("CAREFLOW_VECTOR_REBUILD needs CAREFLOW_KNOWLEDGE_EDITOR=on")
    if (is_on(env, "CAREFLOW_KNOWLEDGE_EDITOR") or is_on(env, "CAREFLOW_VECTOR_REBUILD")) and not is_on(env, "CAREFLOW_KNOWLEDGE_SEARCH"):
        problems.append("approved cards are indexed only with CAREFLOW_KNOWLEDGE_SEARCH=on")
    if is_on(env, "CAREFLOW_PATIENT_MESSAGING") and (env.get("CAREFLOW_MESSAGE_MAX_PER_MINUTE") or "").strip() and not (env["CAREFLOW_MESSAGE_MAX_PER_MINUTE"].strip().isdigit()):
        problems.append("CAREFLOW_MESSAGE_MAX_PER_MINUTE is not a whole number")
    on = [n for n in SWITCHES if is_on(env, n)]
    return ("the .env switches are consistent with section 14", not problems, f"{len(on)} switch(es) on" if not problems else "; ".join(problems))


def judge_backend(listening: bool, ready: bool | None) -> Result:
    """A backend already listening on the port must be the demo one: `GET /readyz` is ready in the demo profile and NOT ready in the clinical profile, which is what a plain uvicorn start gives (and that start registers
    patient accounts under the wrong origin). Nothing listening is fine: the start script will start it."""
    name = "no backend runs without the demo profile"
    if not listening:
        return (name, True, "no backend is listening yet")
    if ready is None:
        return (name, False, "something is listening on the port but it is not answering as this backend")
    if not ready:
        return (name, False, "a backend is already running and reports NOT ready: it was started without the demo profile (plain uvicorn), so it gives new accounts the wrong origin")
    return (name, True, "a demo backend is already running and ready")


def judge_live_accounts(env: Mapping[str, str | None], live_patient_accounts: int) -> Result:
    name = "portal accounts have the demo origin"
    if not is_on(env, "CAREFLOW_DEMO_EMAIL_SIGNIN"):
        return (name, True, "not applicable: the e-mail sign-in is off")
    if live_patient_accounts:
        return (name, False, f"{live_patient_accounts} patient account(s) have origin live (registered by a backend without the demo profile): they cannot claim a demo record")
    return (name, True, "0 patient accounts have origin live")


def judge_second_factor(env: Mapping[str, str | None]) -> Result:
    name = "the second factor matches the screens switched on"
    if (env.get("CAREFLOW_REQUIRE_AAL2") or "").strip().lower() not in ("false", "0", "off", "no"):
        return (name, True, "the second factor is required")
    needing = [n for n in AAL2_SCREENS if is_on(env, n)]
    if needing:
        return (name, False, f"CAREFLOW_REQUIRE_AAL2 is off but the database still needs a real second factor for: {', '.join(needing)}")
    return (name, True, "the second factor is off and no switched-on screen needs it at the database")


def judge_screen_switches(env: Mapping[str, str | None]) -> Result:
    name = "the three screen switches are on"
    off = [n for n in SCREEN_SWITCHES if not is_on(env, n)]
    return (name, not off, f"{len(SCREEN_SWITCHES) - len(off)} of {len(SCREEN_SWITCHES)} on" + (f"; off: {', '.join(off)}" if off else ""))


def probe_backend(port: int, timeout: float = 2.0) -> tuple[bool, bool | None]:
    """(listening, ready): GET /readyz on 127.0.0.1, no credentials. ready is None when something answers that is not this backend."""
    import json
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/readyz", timeout=timeout) as response:
            body = json.loads(response.read(2000).decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:  # answered, but not 2xx: a backend that is not ready may say 503
        try:
            body = json.loads(exc.read(2000).decode("utf-8", "replace"))
        except ValueError:
            return True, None
    except (urllib.error.URLError, OSError, TimeoutError):
        return False, None
    except ValueError:
        return True, None
    ready = body.get("ready") if isinstance(body, dict) else None
    return True, ready if isinstance(ready, bool) else None


def judge_rules_before_models() -> Result:
    """No database, no model, no spend: the structural facts that make 'rules before models' true on the Front Door path."""
    from careflow import referral, rules

    try:
        referral.Clearance(uuid.uuid4(), "0" * 64, "demo", "0" * 64)
        return ("rules run before any model", False, "a clearance could be built without screening")
    except referral.ReferralError:
        pass
    empty = rules.RuleSet((), "demo", 0, (), rules.EMPTY_RULE_SET_HASH)
    if rules.evaluate_with(empty, "a plain made-up letter with nothing in it").safe_to_treat_as_clear:
        return ("rules run before any model", False, "an empty rule set cleared a letter")
    if "clearance" not in inspect.signature(referral.ReferralExtractor.extract).parameters:
        return ("rules run before any model", False, "the extractor does not require a clearance")
    return ("rules run before any model", True, "a clearance cannot be forged, an empty rule set clears nothing, the extractor requires a clearance")


# ---- reads (every statement is a select) ---------------------------------------------------------------------------------------------------------------------------------------------


async def read_checks(env: Mapping[str, str], clinic: uuid.UUID) -> list[Result]:
    live_accounts = 0
    from careflow import demo_cards, demo_phase4, knowledge, schema_check
    from careflow.db import make_app_engine
    from careflow.embeddings import FAKE_MODEL_ID, HashingEmbedding
    from careflow.front_door_runner import worker_session

    out: list[Result] = []
    admin = create_async_engine(make_url(env["DATABASE_URL_MIGRATOR"].replace("postgresql://", "postgresql+asyncpg://", 1)), connect_args={"statement_cache_size": 0})
    try:
        try:
            await schema_check.check(admin)
            out.append(("migrations are applied", True, f"all {len(schema_check.REQUIRED)} startup-check objects exist"))
        except schema_check.SchemaOutOfDate:
            out.append(("migrations are applied", False, "a migration the code needs is missing (run python -m careflow.migrate)"))
        async with admin.connect() as conn:
            row = (await conn.execute(text("select origin from iam.clinics where id = :c"), {"c": clinic})).first()
            if row is None or row.origin != "test":
                raise Refused("refusing: that clinic does not exist here or is not a test clinic")
            published = (await conn.execute(text("select count(*) from iam.public_clinics where clinic_id = :c"), {"c": clinic})).scalar_one()
            out.append(("the clinic is published", published == 1, "published as the public clinic" if published == 1 else "not the published clinic (scripts/dev_clinic.py publish)"))
            roles = {r.role: r.n for r in (await conn.execute(text("select role, count(*) as n from iam.staff_roles where clinic_id = :c and revoked_at is null group by role"), {"c": clinic})).all()}
            out.append(judge_roles(roles))
            live_accounts = (await conn.execute(text("select count(*) from iam.accounts where account_type = 'patient' and origin = 'live'"))).scalar_one()
            per_table: dict[str, tuple[int, int]] = {}
            for table in LABELLED_TABLES:
                r = (await conn.execute(text(f"select count(*) filter (where provenance like 'DEMO PLACEHOLDER%') as l, count(*) filter (where provenance is null or provenance not like 'DEMO PLACEHOLDER%') as u from {table} where clinic_id = :c"), {"c": clinic})).one()
                per_table[table] = (r.l, r.u)
            out.append(judge_labels(per_table))
            cards, queries = demo_cards.load()
            embedded = (await conn.execute(text("select card_id, model_id from kb.card_embeddings where clinic_id = :c"), {"c": clinic})).all()
            expected = {c.fields.card_id for c in cards}
            out.append(judge_cards({e.card_id for e in embedded}, expected, {e.model_id for e in embedded}))
        fake_only = bool(embedded) and {e.model_id for e in embedded} == {FAKE_MODEL_ID}
        found = abstained = False
        if fake_only and (env.get("DATABASE_URL_APP") or "").strip() and (env.get("CAREFLOW_WORKER_ACCOUNT_ID") or "").strip():
            app_engine = make_app_engine(env["DATABASE_URL_APP"], pool_size=1, max_overflow=1, pool_pre_ping=True)
            try:
                settings = demo_phase4.effective_environ({**env, "CAREFLOW_DEMO_PHASE4": "1"}, "demo")
                search = knowledge.KnowledgeSearch(cards, lambda _c: (HashingEmbedding(), FAKE_MODEL_ID), knowledge.policy_from(settings), budget_tokens=knowledge.budget_from(settings))
                async with worker_session(app_engine, uuid.UUID(env["CAREFLOW_WORKER_ACCOUNT_ID"]))() as conn:
                    outcome = await search.search(conn, clinic, "test", queries[0].text)
                abstained = outcome.retrieval.abstained
                found = (not abstained) and bool(outcome.retrieval.candidates)
            finally:
                await app_engine.dispose()
            out.append(judge_search(found, abstained, True))
        elif fake_only:
            out.append(("a knowledge search returns a card", False, "DATABASE_URL_APP or CAREFLOW_WORKER_ACCOUNT_ID is not set in the env file"))
        else:
            out.append(judge_search(False, False, False))
    finally:
        await admin.dispose()
    out.append(judge_live_accounts(env, live_accounts))
    return out


def render(results: Sequence[Result], say=print) -> int:
    failed = 0
    for name, ok, detail in results:
        say(f"{'PASS' if ok else 'FAIL'} {name}: {detail}")
        if not ok:
            say(f"  what to do: {FIXES.get(name, 'see docs/demo-runbook.md and fix the line above, then run this again')}")
        failed += 0 if ok else 1
    say(f"{len(results) - failed} passed, {failed} failed")
    return 1 if failed else 0


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clinic-id", required=True, type=uuid.UUID)
    ap.add_argument("--env-file", default=".env")
    ap.add_argument("--port", type=int, default=8000, help="the port the demo backend uses (default 8000)")
    args = ap.parse_args()
    env = {k: v for k, v in dotenv_values(Path(args.env_file)).items() if v is not None}
    try:
        seed.refuse_unless_dev_demo_synthetic(env)
        if not (env.get("DATABASE_URL_MIGRATOR") or "").strip():
            raise Refused("refusing: DATABASE_URL_MIGRATOR is not set in the env file")
        host = seed.refuse_unless_same_project(env.get("SUPABASE_URL"), env["DATABASE_URL_MIGRATOR"], env.get("CAREFLOW_DEV_PROJECT_REF"))
        print(f"target project host: {host}")
        try:
            typed = input("Type that host exactly to confirm (anything else stops here): ")
        except EOFError:
            raise Refused("refusing: no host was typed") from None
        seed.confirm_target(typed, host)
        results = [*await read_checks(env, args.clinic_id), judge_keys(env), judge_ceilings(env), judge_rules_before_models(), judge_switches(env), judge_backend(*probe_backend(args.port)), judge_second_factor(env), judge_screen_switches(env)]
        return render(results)
    except Refused as exc:
        print(str(exc))
        return 1
    except Exception as exc:  # noqa: BLE001 - a fault is a FAIL in fixed words, never the exception's text (it could carry a connection string)
        print(f"FAIL the smoke check could not finish ({type(exc).__name__})")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
