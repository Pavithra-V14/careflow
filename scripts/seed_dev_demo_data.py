"""Seed SYNTHETIC DEMO DATA into the DEV project so the staff console has something to show. A person runs it by hand; nothing runs it.

    uv run python scripts/seed_dev_demo_data.py --clinic-id <uuid> [--env-file .env] [--dry-run]

What it refuses (owner decision 2026-10-31), before any connection is opened:
  * unless the env file says CAREFLOW_ENV=dev AND CAREFLOW_PROFILE=demo AND CAREFLOW_DATA_POLICY=synthetic_only (never ci, never clinical, never unset);
  * unless the env file names the DEV project's ref in CAREFLOW_DEV_PROJECT_REF and the migrator URL (user `postgres.<ref>`) and SUPABASE_URL both carry that same ref: a CI env file cannot pass by editing
    CAREFLOW_ENV alone;
  * unless YOU TYPE THE PROJECT HOST (printed first) exactly;
  * unless the migrator role bypasses row-level security (the seed writes as the owner, like the migrations), the clinic exists, its origin is `test`, and the clinic already has an active front desk account
    (run scripts/seed_dev_staff.py first);
  * if the clinic already has priority levels that this script did not write (clinician-authored levels are never mixed with demo ones).
A refusal prints fixed words; no secret is ever printed.

What it writes, in ONE transaction (a failure leaves nothing; `--dry-run` runs everything and rolls it back): FICTIONAL patients (names that say "Demo ... Sample", dummy phone numbers), reference lists for
scheduling (locations, providers, visit types, the three DEMO-LEVEL priority levels from config/demo-phase3.yaml, slots), and four referral documents: one waiting to run, two with an OPEN triage proposal and
review task, one held with an open hold task. Every priority, role, rationale, mode and card id is an INVENTED DEMO VALUE labelled "DEMO ONLY - NOT A CLINICAL DECISION" (inside provenance where a column cannot
hold the label); no clinical content is authored here (no thresholds, red-flag rules, protocol card text, wording).

It writes NO human decision: no agents.review_decisions row, no acknowledged or resolved task, nothing attributed to a real person (a review decision is the one thing the project allows to become a training label,
and those tables are append-only). The documents' `registered_by` and the priority levels' `approved_by` are a placeholder account "demo seed (not a clinician)": status closed, no roles, cannot sign in.

Safe to re-run: every row has a fixed idempotency key that INCLUDES THE CLINIC and is inserted `on conflict (idempotency_key)` (or the table's named unique) `do nothing`; nothing is deleted or updated (the
tables are append-only). A surprise constraint violation stops the run and rolls everything back instead of being skipped. Slots are added only when no future demo slot exists, so a re-run once the demo slots
have passed adds a fresh set. Rows carry origin `test`. It NEVER writes msg.messages. Limits, stated: the seeded documents have no stored file behind them (the list and detail show; opening bytes would not
work), a seeded proposal has no graph checkpoint (it can be LISTED, not reviewed through the review route), and the console may not show the demo label (the task columns cannot hold it).
"""

import argparse
import asyncio
import hashlib
import json
import sys
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from careflow.crypto import DemographicVault
from careflow.mpi import PatientIngestionPipeline

DEMO_LABEL = "DEMO ONLY - NOT A CLINICAL DECISION"
IST = timezone(timedelta(hours=5, minutes=30))
TABLES = {"location": "locations", "provider": "providers", "visit-type": "visit_types"}  # fixed names, never user input
SEED = "demo-seed-v1"
NAMESPACE = uuid.UUID("5f1c7c2e-8f0a-4a52-9d6e-2b1d0c3a9e11")
ROOT = Path(__file__).resolve().parent.parent

# Fictional people: obviously made-up names, dummy numbers (9000000xxx), dates of birth spread over decades.
PATIENTS = (
    ("Demo Alpha Sample", "1958-03-14", "+919000000001"),
    ("Demo Bravo Sample", "1971-11-02", "+919000000002"),
    ("Demo Charlie Sample", "1984-07-27", "+919000000003"),
    ("Demo Delta Sample", "1993-01-09", "+919000000004"),
    ("Demo Echo Sample", "2001-09-30", None),
)


class Refused(Exception):
    """A rule of this script in plain words. Never carries a secret."""


def refuse_unless_dev_demo_synthetic(env: Mapping[str, str | None]) -> None:
    if env.get("CAREFLOW_ENV") != "dev":
        raise Refused(f"refusing: CAREFLOW_ENV is {env.get('CAREFLOW_ENV')!r}; this script runs against the dev project only")
    if env.get("CAREFLOW_PROFILE") != "demo":
        raise Refused(f"refusing: CAREFLOW_PROFILE is {env.get('CAREFLOW_PROFILE')!r}; this script runs in the demo profile only")
    if (env.get("CAREFLOW_DATA_POLICY") or "").strip().lower() != "synthetic_only":
        raise Refused("refusing: CAREFLOW_DATA_POLICY must be synthetic_only")


def project_host(supabase_url: str | None) -> str:
    host = urlsplit(supabase_url or "").hostname or ""
    if not host:
        raise Refused("refusing: SUPABASE_URL is not set to a project URL")
    return host


def migrator_project_ref(database_url: str | None) -> str | None:
    """The project ref carried by a Supabase pooler user name (`postgres.<ref>`), or None when the URL does not carry one."""
    try:
        user = unquote(make_url((database_url or "").replace("postgresql+asyncpg://", "postgresql://", 1)).username or "")
    except Exception:  # noqa: BLE001 - any parse fault is a refusal, in fixed words, never an echo of the URL
        raise Refused("refusing: DATABASE_URL_MIGRATOR could not be read") from None
    return user.split(".", 1)[1] if "." in user else None


def refuse_unless_same_project(supabase_url: str | None, database_url: str | None, dev_ref: str | None = None) -> str:
    """The project host, once SUPABASE_URL, the migrator URL and CAREFLOW_DEV_PROJECT_REF (the owner's statement of which project is DEV) all name the same project ref."""
    host = project_host(supabase_url)
    ref = migrator_project_ref(database_url)
    if ref is None or host.split(".", 1)[0] != ref:
        raise Refused("refusing: the migrator URL does not point at the project named by SUPABASE_URL (or carries no project ref)")
    if not dev_ref or dev_ref.strip() != ref:
        raise Refused("refusing: CAREFLOW_DEV_PROJECT_REF must be set in the env file to the DEV project's ref, and it is not the project this env file points at")
    return host


def confirm_target(typed: str, host: str) -> None:
    if typed.strip() != host:
        raise Refused("refusing: the host you typed is not the project host shown")


def det(*parts: object) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, "|".join(str(p) for p in parts))


async def seed(conn: AsyncConnection, clinic: uuid.UUID, vault: DemographicVault, key_version: int, say=print) -> None:
    bypass = (await conn.execute(text("select rolbypassrls from pg_roles where rolname = current_user"))).scalar()
    if not bypass:
        raise Refused("refusing: the migrator role does not bypass row-level security, which this seed relies on (as the migrations do)")
    clinic_row = (await conn.execute(text("select origin from iam.clinics where id = :c"), {"c": clinic})).first()
    if clinic_row is None:
        raise Refused("refusing: that clinic does not exist in this project")
    if clinic_row.origin != "test":
        raise Refused("refusing: that clinic's origin is not 'test'; demo data goes into a test clinic only")
    origin = "test"
    k = f"{SEED}:{clinic}"  # every idempotency key carries the clinic: keys are unique across clinics for most of these tables

    front_desk = (
        await conn.execute(
            text("select sr.account_id from iam.staff_roles sr join iam.accounts a on a.id = sr.account_id where sr.clinic_id = :c and sr.role = 'front_desk' and sr.revoked_at is null and a.status = 'active' limit 1"),
            {"c": clinic},
        )
    ).first()
    if front_desk is None:
        raise Refused("refusing: the clinic has no active front desk account; run scripts/seed_dev_staff.py first")

    # ---- refuse to mix with clinician-authored priority levels -------------------------------------------------------------------------------------------------------------
    foreign = (await conn.execute(text("select count(*) from sched.priority_levels where clinic_id = :c and idempotency_key not like :p"), {"c": clinic, "p": f"{k}:%"})).scalar_one()
    if foreign:
        raise Refused("refusing: this clinic already has priority levels that this script did not write; demo levels are never mixed with real ones")

    # ---- the placeholder account (no roles, closed: it cannot sign in) ---------------------------------------------------------------------------------------------------------
    placeholder = det(SEED, "placeholder-account")
    await conn.execute(text("insert into iam.accounts (id, account_type, status, origin) values (:i, 'staff', 'closed', 'test') on conflict (id) do nothing"), {"i": placeholder})

    # ---- patients (real encryption with the vault from the env file, so search and the view work) ----------------------------------------------------------------------------
    pipeline = PatientIngestionPipeline(vault, key_version=key_version, registrar="direct")
    patient_ids = []
    for i, (name, dob, phone) in enumerate(PATIENTS, start=1):
        result = await pipeline.ingest(conn, clinic_id=clinic, origin=origin, name=name, dob=dob, phone=phone, idempotency_key=f"{k}:patient:{i}")
        patient_ids.append(result.patient_id)
    say(f"patients: {len(patient_ids)} (fictional)")

    # ---- scheduling reference lists --------------------------------------------------------------------------------------------------------------------------------------
    for i, name in enumerate(("Demo Location North", "Demo Location South"), start=1):
        await conn.execute(text("insert into sched.locations (clinic_id, name, origin, idempotency_key) values (:c, :n, 'test', :k) on conflict (idempotency_key) do nothing"), {"c": clinic, "n": name, "k": f"{k}:location:{i}"})
    for i, name in enumerate(("Demo Provider One", "Demo Provider Two"), start=1):
        await conn.execute(text("insert into sched.providers (clinic_id, display_name, origin, idempotency_key) values (:c, :n, 'test', :k) on conflict (idempotency_key) do nothing"), {"c": clinic, "n": name, "k": f"{k}:provider:{i}"})
    for i, (name, minutes) in enumerate((("Demo Visit Type A (30 min, invented)", 30), ("Demo Visit Type B (45 min, invented)", 45)), start=1):
        await conn.execute(text("insert into sched.visit_types (clinic_id, name, duration_minutes, origin, idempotency_key) values (:c, :n, :d, 'test', :k) on conflict (idempotency_key) do nothing"), {"c": clinic, "n": name, "d": minutes, "k": f"{k}:visit-type:{i}"})
    for rank, letter in enumerate("ABC", start=1):  # the DEMO-LEVEL names of config/demo-phase3.yaml; approved_by is the placeholder account, not a person
        await conn.execute(
            text("insert into sched.priority_levels (clinic_id, code, label, rank, approved_by, origin, idempotency_key) values (:c, :code, :label, :r, :a, 'test', :k) on conflict (idempotency_key) do nothing"),
            {"c": clinic, "code": f"demo_level_{letter.lower()}", "label": f"DEMO-LEVEL-{letter}", "r": rank, "a": placeholder, "k": f"{k}:priority:{letter}"},
        )
    levels = (await conn.execute(text("select count(*) from sched.priority_levels where clinic_id = :c and idempotency_key like :p"), {"c": clinic, "p": f"{k}:priority:%"})).scalar_one()
    if levels != 3:
        raise Refused("refusing: the three demo priority levels are not all present after the insert (a code or rank collision)")
    say("reference lists: 2 locations, 2 providers, 2 visit types, 3 priority levels (DEMO-LEVEL-A..C)")

    ids = {}
    for kind, table in TABLES.items():
        ids[kind] = [r[0] for r in (await conn.execute(text(f"select id from sched.{table} where clinic_id = :c and idempotency_key like :p order by idempotency_key"), {"c": clinic, "p": f"{k}:{kind}:%"})).all()]
    if not all(ids.values()):
        raise Refused("refusing: the demo locations, providers or visit types are missing after the insert")
    future = (await conn.execute(text("select count(*) from sched.slots where clinic_id = :c and idempotency_key like :p and starts_at > now()"), {"c": clinic, "p": f"{k}:slot:%"})).scalar_one()
    if future == 0:
        day = (datetime.now(IST) + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        n = 0
        for d, provider in enumerate(ids["provider"]):
            for s in range(4):
                start = (day + timedelta(days=d, minutes=30 * s)).astimezone(UTC)
                done = await conn.execute(
                    text("insert into sched.slots (clinic_id, provider_id, location_id, visit_type_id, starts_at, ends_at, origin, idempotency_key) values (:c, :p, :l, :v, :s, :e, 'test', :k) on conflict (idempotency_key) do nothing"),
                    {"c": clinic, "p": provider, "l": ids["location"][d % len(ids["location"])], "v": ids["visit-type"][0], "s": start, "e": start + timedelta(minutes=30), "k": f"{k}:slot:{start:%Y%m%d%H%M}:{d}"},
                )
                n += done.rowcount or 0
        say(f"slots: {n} added (30 minutes each, tomorrow and the day after)")
    else:
        say("slots: future demo slots already exist (nothing added)")

    # ---- referral documents, OPEN triage proposals and review tasks (no human decision is invented) ------------------------------------------------------------------------
    documents = []
    for i in range(1, 5):
        key = f"{k}:referral:{i}"
        doc = det(clinic, key)
        sha = hashlib.sha256(f"{SEED} synthetic document {i}".encode()).hexdigest()
        await conn.execute(
            text("insert into docs.documents (id, clinic_id, origin, kind, storage_path, content_type, size_bytes, sha256, registered_by, idempotency_key) values (:i, :c, 'test', 'referral', :p, 'application/pdf', :sz, :h, :u, :k) on conflict (clinic_id, idempotency_key) do nothing"),
            {"i": doc, "c": clinic, "p": f"clinic/{clinic}/doc/{doc}", "sz": 2048 * i, "h": sha, "u": placeholder, "k": key},
        )
        documents.append(doc)
    demo = {"demo": DEMO_LABEL}

    async def task(kind: str, role: str, doc: uuid.UUID, provenance: dict, key: str) -> uuid.UUID:
        await conn.execute(
            text("insert into agents.review_tasks (clinic_id, origin, kind, priority, assigned_role, source_message_id, provenance, idempotency_key) values (:c, 'test', :k, 'standard', :r, :s, cast(:p as jsonb), :ik) on conflict (idempotency_key) do nothing"),
            {"c": clinic, "k": kind, "r": role, "s": f"document:{doc}", "p": json.dumps(provenance), "ik": key},
        )
        return (await conn.execute(text("select id from agents.review_tasks where idempotency_key = :ik and clinic_id = :c"), {"ik": key, "c": clinic})).scalar_one()

    # document 1 only registered (nothing has run); documents 2 and 3 have an OPEN proposal; document 4 is held. The priority 'standard', the role 'nurse' and the mode 'assisted' are invented demo values.
    for index, patient in ((1, patient_ids[0]), (2, patient_ids[1])):
        proposal = det(clinic, f"{SEED}:proposal:{index}")
        review_task = await task("triage_proposal", "nurse", documents[index], {"proposal_id": str(proposal), **demo}, f"{k}:triage-task:{index}")
        await conn.execute(
            text("insert into agents.triage_proposals (id, clinic_id, origin, document_id, patient_id, candidates, abstained, mode, review_task_id, idempotency_key) values (:i, :c, 'test', :d, :p, cast(:cand as jsonb), false, 'assisted', :t, :k) on conflict (clinic_id, idempotency_key) do nothing"),
            {"i": proposal, "c": clinic, "d": documents[index], "p": patient, "cand": json.dumps([{"card_id": "demo-synthetic-card-A", "version": 1}, {"card_id": "demo-synthetic-card-B", "version": 1}]), "t": review_task, "k": f"{k}:proposal-key:{index}"},
        )
    await task("referral_hold", "nurse", documents[3], {"status": "held_unassessable", "reasons": ["screening_unassessable"], **demo}, f"{k}:hold-task:4")
    say("referrals: 4 documents (1 waiting to run, 2 with an OPEN proposal and review task, 1 held with an open hold task); no decision is recorded")
    say("every priority, role, mode and card id above is an INVENTED DEMO VALUE: " + DEMO_LABEL)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clinic-id", required=True, type=uuid.UUID)
    ap.add_argument("--env-file", default=".env")
    ap.add_argument("--dry-run", action="store_true", help="do everything, then roll it back: nothing is kept")
    args = ap.parse_args()
    env = dict(dotenv_values(Path(args.env_file)))
    try:
        refuse_unless_dev_demo_synthetic(env)
        migrator = (env.get("DATABASE_URL_MIGRATOR") or "").strip()
        if not migrator:
            raise Refused("refusing: DATABASE_URL_MIGRATOR is not set in the env file")
        host = refuse_unless_same_project(env.get("SUPABASE_URL"), migrator, env.get("CAREFLOW_DEV_PROJECT_REF"))
        print(f"target project host: {host}")
        try:
            typed = input("Type that host exactly to confirm (anything else stops here): ")
        except EOFError:
            raise Refused("refusing: no host was typed") from None
        confirm_target(typed, host)
        try:
            vault = DemographicVault.from_env({k: v for k, v in env.items() if k and v and k.startswith("CAREFLOW_") and "_SECRET_V" in k})
            key_version = int(env.get("CAREFLOW_ACTIVE_KEY_VERSION") or max(vault.key_versions))
        except Exception:  # noqa: BLE001 - fixed words, never the key material
            raise Refused("refusing: the encryption keys (CAREFLOW_ENC_SECRET_V1 / CAREFLOW_HMAC_SECRET_V1) or CAREFLOW_ACTIVE_KEY_VERSION are missing or malformed in the env file") from None
        engine = create_async_engine(make_url(migrator.replace("postgresql://", "postgresql+asyncpg://", 1)), connect_args={"statement_cache_size": 0})
        try:
            async with engine.connect() as conn:
                tx = await conn.begin()
                try:
                    await seed(conn, args.clinic_id, vault, key_version)
                    if args.dry_run:
                        await tx.rollback()
                        print("dry run: everything was rolled back, nothing was kept")
                    else:
                        await tx.commit()
                        print("done: committed")
                except BaseException:
                    if tx.is_active:
                        await tx.rollback()
                    raise
        finally:
            await engine.dispose()
    except Refused as exc:
        print(str(exc))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
