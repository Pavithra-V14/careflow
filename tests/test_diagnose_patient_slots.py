"""scripts/diagnose_patient_slots.py, offline: it refuses before any connection like the other dev scripts, it checks the SAME conditions as the patient list function (read from migration 0083), it prints counts and fixed words only,
and the opt-in seed fix (--add-bookable-slots) only ever writes open `test` slots in the evening. Nothing here connects to a database."""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "diagnose_patient_slots.py"
sys.path.insert(0, str(ROOT / "scripts"))
import diagnose_patient_slots as diag

CLINIC = "6f1c7c2e-8f0a-4a52-9d6e-2b1d0c3a9e11"
GOOD = {"CAREFLOW_ENV": "dev", "CAREFLOW_PROFILE": "demo", "CAREFLOW_DATA_POLICY": "synthetic_only"}
URL = "https://abcdefgh.supabase.co"
DB = "postgresql://postgres.abcdefgh:not-a-real-password@aws-0-example.pooler.supabase.com:5432/postgres"


def run(tmp_path, stdin="", **over):
    base = {**GOOD, "SUPABASE_URL": URL, "DATABASE_URL_MIGRATOR": DB, "CAREFLOW_DEV_PROJECT_REF": "abcdefgh"}
    base.update(over)
    env_file = tmp_path / "diag.env"
    env_file.write_text("\n".join(f"{k}={v}" for k, v in base.items() if v is not None) + "\n", encoding="utf-8")
    return subprocess.run([sys.executable, "-I", str(SCRIPT), "--clinic-id", CLINIC, "--env-file", str(env_file)], input=stdin, capture_output=True, text=True, timeout=60, check=False)


def test_it_refuses_before_any_connection_outside_dev_demo_synthetic_and_without_the_typed_host(tmp_path):
    results = [run(tmp_path, CAREFLOW_ENV="ci"), run(tmp_path, CAREFLOW_PROFILE="clinical"), run(tmp_path, CAREFLOW_DATA_POLICY=None), run(tmp_path, DATABASE_URL_MIGRATOR=DB.replace("abcdefgh", "zzzzzzzz")), run(tmp_path, stdin="wrong.host\n"), run(tmp_path, stdin="")]
    assert all(r.returncode == 1 for r in results)
    assert "not the project host shown" in results[4].stdout and "no host was typed" in results[5].stdout
    for r in results:
        assert "not-a-real-password" not in r.stdout + r.stderr and "Traceback" not in r.stderr


def test_it_is_read_only_and_only_selects():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "set transaction read only" in source and "rollback" in source
    for sql in (diag.COUNTS_SQL, diag.PATIENT_SQL, diag.BOOKABLE_SQL, diag.ORIGINS_SQL):
        assert sql.lstrip().lower().startswith("select") and not re.search(r"\b(insert|update|delete|truncate|alter|drop)\b", sql, re.IGNORECASE)


def test_the_conditions_are_the_ones_of_the_patient_list_function_in_migration_0083():
    migration = (ROOT / "db" / "migrations" / "0083_patient_booking.sql").read_text(encoding="utf-8")
    function = migration[migration.index("create function sched.patient_open_slots") : migration.index("create function sched.patient_book_slot")]
    for fragment in ("s.status = 'open'", "s.starts_at > clock_timestamp()", "vt.active", "l.active"):
        assert fragment in function and fragment in diag.COUNTS_SQL, fragment
    assert "patient_bookable_visit_types" in function and "patient_bookable_visit_types" in diag.COUNTS_SQL
    assert "s.origin = (select pt.origin from mpi.patients pt" in function and "s.origin = :origin" in diag.COUNTS_SQL


COUNTS = {
    "total": 12, "not_open": 2, "in_the_past": 8, "type_not_listed_bookable": 0, "type_not_active": 0, "location_not_active": 0, "origin_differs": 0, "open_and_future": 0, "open_future_type_blocked": 0,
    "open_future_location_blocked": 0, "open_future_origin_differs": 0, "passes_every_condition": 0, "earliest_open_date": None, "latest_open_date": None,
}


def test_the_report_has_counts_and_fixed_words_only_and_names_the_reason_the_counts_show():
    lines = diag.report(COUNTS, bookable_types=2, patient="active, bound to a live record (origin word: test)", origins=[("test", 12)], with_origin=True)
    text = "\n".join(lines)
    assert "starts in the past" in text and "no open future slot at all" in text
    assert "verdict: no slot is both open and in the future" in lines[-1]
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", text)  # no id of any kind
    # the same counts, but the open future slots are of a type that is not bookable
    blocked = {**COUNTS, "open_and_future": 4, "open_future_type_blocked": 4, "earliest_open_date": "2026-11-21", "latest_open_date": "2026-11-22"}
    out = diag.report(blocked, bookable_types=1, patient="active, bound to a live record (origin word: test)", origins=[("test", 12)], with_origin=True)
    assert "between 2026-11-21 and 2026-11-22" in "\n".join(out) and "not in the bookable list or not active" in out[-1]
    # origin differs, no bookable types, a refused account and a clean list each get their own sentence
    assert "another origin" in diag.verdict({**COUNTS, "open_and_future": 3, "open_future_origin_differs": 3}, bookable_types=1, with_origin=True, patient="active")
    assert "not set up" in diag.verdict({**COUNTS, "open_and_future": 3}, bookable_types=0, with_origin=True, patient="active")
    assert "refuses this account" in diag.verdict(COUNTS, bookable_types=1, with_origin=True, patient="NOT an active patient account")
    assert "would show slots" in diag.verdict({**COUNTS, "passes_every_condition": 3}, bookable_types=1, with_origin=True, patient="active")


def test_without_an_account_the_origin_check_is_reported_per_origin_word_not_applied():
    lines = diag.report(COUNTS, bookable_types=1, patient="not given (--account-id)", origins=[("test", 10), ("live", 2)], with_origin=False)
    assert any("origin check: not applied" in line and "test 10" in line and "live 2" in line for line in lines)


def test_the_opt_in_seed_fix_writes_only_open_test_slots_in_the_evening_for_bookable_types():
    source = (ROOT / "scripts" / "seed_dev_demo_data.py").read_text(encoding="utf-8")
    fn = source[source.index("async def add_bookable_slots") : source.index("async def main")]
    assert "patient_bookable_visit_types" in fn and "vt.active" in fn and "and active" in fn and "'test'" in fn and "status" not in fn.split("insert into sched.slots")[1].split("values")[0]  # status stays the default, open
    assert "hour=16, minute=30" in fn and "--add-bookable-slots" in source and "if args.add_bookable_slots" in source
    assert "seed_demo_placeholders.py first" in fn
