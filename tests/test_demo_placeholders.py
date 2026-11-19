"""The demo placeholder seed and the startup check (owner instruction 2026-11-19), OFFLINE. Nothing here connects to a database.

Every placeholder row carries the label; nothing claims an approval; the values come from the AI-prepared proposals; the script refuses before any connection; and the application refuses to start outside the
synthetic demo while any labelled row exists (or when that cannot be ruled out).
"""

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

from careflow import placeholder_check as pc

pytestmark = pytest.mark.asyncio

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "seed_demo_placeholders.py"
spec = importlib.util.spec_from_file_location("seed_demo_placeholders", SCRIPT)
seed_mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
sys.modules["seed_demo_placeholders"] = seed_mod
spec.loader.exec_module(seed_mod)  # type: ignore[union-attr]

CLINIC = "6f1c7c2e-8f0a-4a52-9d6e-2b1d0c3a9e11"
GOOD = {"CAREFLOW_ENV": "dev", "CAREFLOW_PROFILE": "demo", "CAREFLOW_DATA_POLICY": "synthetic_only"}
URL = "https://abcdefgh.supabase.co"
DB = "postgresql://postgres.abcdefgh:not-a-real-password@aws-0-example.pooler.supabase.com:5432/postgres"
HOST = "abcdefgh.supabase.co"


def run(tmp_path, stdin="", **over):
    base = {**GOOD, "SUPABASE_URL": URL, "DATABASE_URL_MIGRATOR": DB, "CAREFLOW_DEV_PROJECT_REF": "abcdefgh"}
    base.update(over)
    env_file = tmp_path / "seed.env"
    env_file.write_text("\n".join(f"{k}={v}" for k, v in base.items() if v is not None) + "\n", encoding="utf-8")
    return subprocess.run([sys.executable, "-I", str(SCRIPT), "--clinic-id", CLINIC, "--env-file", str(env_file)], input=stdin, capture_output=True, text=True, timeout=60, check=False)


def test_it_refuses_before_any_connection_outside_dev_demo_synthetic_and_without_the_typed_host(tmp_path):
    results = [run(tmp_path, CAREFLOW_ENV="ci"), run(tmp_path, CAREFLOW_PROFILE="clinical"), run(tmp_path, CAREFLOW_DATA_POLICY=None), run(tmp_path, DATABASE_URL_MIGRATOR=DB.replace("abcdefgh", "zzzzzzzz")),
               run(tmp_path, stdin="wrong.host\n"), run(tmp_path, stdin="")]
    assert all(r.returncode == 1 for r in results)
    assert f"target project host: {HOST}" in results[4].stdout and "no host was typed" in results[5].stdout
    for r in results:
        assert "not-a-real-password" not in r.stdout + r.stderr and "Traceback" not in r.stderr


def test_the_label_says_demo_and_not_approved_and_never_claims_an_approval():
    assert seed_mod.LABEL.startswith("DEMO PLACEHOLDER, not approved by ") and seed_mod.INVENTED.startswith(seed_mod.LABEL)
    assert len(seed_mod.INVENTED) <= 200  # the column's limit
    source = SCRIPT.read_text(encoding="utf-8")
    claims = re.findall(r"approved by.{0,40}", source.replace("not approved by", "NOT-A"), re.IGNORECASE)
    assert not claims, claims  # every mention of an approval in the script is a denial


def test_every_insert_in_the_script_writes_the_label_and_nothing_is_written_to_a_migration():
    source = SCRIPT.read_text(encoding="utf-8").split('"""', 2)[2]
    inserts = re.findall(r"insert into [a-z_.]+ \([^)]*\)", source)
    assert len(inserts) == 6 and all("provenance" in i for i in inserts)  # one insert per table, each with the label column
    assert source.count(":l") + source.count(":i") >= 6 and "write_text" not in source and "db/migrations" not in source and "delete from" in source
    assert "provenance like 'DEMO PLACEHOLDER%'" in source  # --remove touches labelled rows only
    for migration in (ROOT / "db" / "migrations").glob("*.sql"):  # and no migration seeds any of these tables
        code = "\n".join(line for line in migration.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("--"))
        for table in seed_mod.TABLES:
            assert f"insert into {table}" not in code, f"{migration.name} seeds {table}"


def test_the_values_are_the_proposals_where_the_proposals_give_one():
    proposals = (ROOT / "docs" / "approval-pack" / "clinic-proposed-values.md").read_text(encoding="utf-8")
    for code in seed_mod.BREAK_GLASS_REASONS:
        assert f"`{code}`" in proposals
    assert f"| {seed_mod.BREAK_GLASS_MINUTES} minutes |" in proposals and f"| {seed_mod.BREAK_GLASS_REOPENS} |" in proposals
    for invented in (seed_mod.PUBLIC_GRIEVANCE_PER_HOUR, seed_mod.CANARY_PER_DAY, seed_mod.BOOKABLE_MAX_FUTURE):
        assert 1 <= invented <= 20  # inside every table's own check; the label says these are invented
    assert 0 < seed_mod.SPEND_USD_PER_CALL <= 1000


def test_no_clinical_content_is_entered():
    source = SCRIPT.read_text(encoding="utf-8").split('"""', 2)[2].lower()
    for word in ("kb.", "red_flag", "card", "dose", "triage", "protocol", "tamil", "reviewed"):
        assert word not in source, word


def test_the_migration_adds_a_label_column_and_a_counts_only_function():
    sql = (ROOT / "db" / "migrations" / "0096_demo_placeholder_labels.sql").read_text(encoding="utf-8")
    code = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    assert code.count("add column provenance text check (provenance is null or length(provenance) <= 200)") == 6
    assert "insert into" not in code and "update " not in code.replace("-- ", "")  # the migration enters no value
    func = code[code.index("create function ops.demo_placeholder_rows") : code.index("revoke all on function")]
    assert "security definer set search_path = ''" in func and "o_rows bigint" in func and "o_source text" in func
    for source in ("iam.break_glass_reasons", "iam.break_glass_settings", "iam.public_grievance_limits", "docs.canary_settings", "ops.spend_limits", "sched.patient_bookable_visit_types", "iam.family_link_rules", "iam.consent_notices"):
        assert source in func, source  # every labelled source is counted
    assert "grant execute on function ops.demo_placeholder_rows() to careflow_app" in code


class Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class Row:
    def __init__(self, source, rows):
        self.o_source, self.o_rows = source, rows


class Engine:
    def __init__(self, rows=None, fail=None):
        self.rows, self.fail, self.queried = rows or [], fail, False

    def connect(self):
        engine = self

        class Ctx:
            async def __aenter__(self_inner):
                if engine.fail:
                    raise engine.fail
                return self_inner

            async def __aexit__(self_inner, *a):
                return False

            async def execute(self_inner, statement):
                engine.queried = True
                assert "ops.demo_placeholder_rows()" in str(statement)
                return Result([Row(s, n) for s, n in engine.rows])

        return Ctx()


async def test_in_the_synthetic_demo_the_check_does_not_even_look():
    engine = Engine(rows=[("iam.break_glass_reasons", 4)])
    await pc.check(engine, demo_allowed=True)  # type: ignore[arg-type]
    assert engine.queried is False


async def test_outside_the_demo_any_placeholder_row_refuses_to_start_naming_sources_and_counts_only():
    engine = Engine(rows=[(source, {"iam.break_glass_reasons": 4, "iam.consent_notices": 2}.get(source, 0)) for source in pc.EXPECTED_SOURCES])
    with pytest.raises(pc.PlaceholdersPresent) as info:
        await pc.check(engine, demo_allowed=False)  # type: ignore[arg-type]
    message = str(info.value)
    assert "iam.break_glass_reasons: 4" in message and "iam.consent_notices: 2" in message and "ops.spend_limits" not in message and "refusing to start" in message


async def test_outside_the_demo_a_clean_database_starts_and_an_unreadable_one_does_not():
    await pc.check(Engine(rows=[(source, 0) for source in pc.EXPECTED_SOURCES]), demo_allowed=False)  # type: ignore[arg-type]
    with pytest.raises(pc.PlaceholdersPresent, match="cannot rule out"):
        await pc.check(Engine(fail=RuntimeError("SECRET detail")), demo_allowed=False)  # type: ignore[arg-type]  # unknown is not clean
    try:
        await pc.check(Engine(fail=RuntimeError("SECRET detail")), demo_allowed=False)  # type: ignore[arg-type]
    except pc.PlaceholdersPresent as exc:
        assert "SECRET" not in str(exc)  # the class name only


async def test_the_application_runs_the_check_with_the_profile_and_policy_on_a_real_engine():
    source = (ROOT / "careflow" / "app.py").read_text(encoding="utf-8")
    assert "await placeholder_check.check(app.state.engine, demo_allowed=demo_allowed(resolve_profile(), _data_policy(os.environ)))" in source
    assert source.index("schema_check.check(app.state.engine)") < source.index("placeholder_check.check(app.state.engine")


async def test_an_empty_or_partial_answer_is_not_clean_because_absence_is_not_a_value():
    every = [(source, 0) for source in pc.EXPECTED_SOURCES]
    for rows in ([], every[:-1], every[1:], [("iam.break_glass_reasons", 0)]):
        with pytest.raises(pc.PlaceholdersPresent, match="incomplete"):
            await pc.check(Engine(rows=rows), demo_allowed=False)  # type: ignore[arg-type]
    await pc.check(Engine(rows=every), demo_allowed=False)  # type: ignore[arg-type]


def test_the_expected_sources_are_exactly_the_ones_the_migration_counts_and_it_guards_against_a_role_that_cannot_see_the_rows():
    sql = (ROOT / "db" / "migrations" / "0096_demo_placeholder_labels.sql").read_text(encoding="utf-8")
    code = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    func = code[code.index("create function ops.demo_placeholder_rows") : code.index("revoke all on function")]
    assert set(re.findall(r"select '([a-z_.]+)'", func)) == set(pc.EXPECTED_SOURCES)
    assert "rolsuper or rolbypassrls" in code  # the function sees every row only if the owner bypasses row-level security
