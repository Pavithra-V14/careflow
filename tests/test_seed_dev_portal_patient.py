"""scripts/seed_dev_portal_patient.py: its REFUSALS and what it never does, offline. The seeding writes to the dev database and is run by hand; nothing here connects to a database."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "seed_dev_portal_patient.py"
spec = importlib.util.spec_from_file_location("seed_dev_portal_patient", SCRIPT)
mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
sys.modules["seed_dev_portal_patient"] = mod
spec.loader.exec_module(mod)  # type: ignore[union-attr]

CLINIC = "6f1c7c2e-8f0a-4a52-9d6e-2b1d0c3a9e11"
ACCOUNT = "6f1c7c2e-8f0a-4a52-9d6e-2b1d0c3a9e12"
GOOD = {"CAREFLOW_ENV": "dev", "CAREFLOW_PROFILE": "demo", "CAREFLOW_DATA_POLICY": "synthetic_only"}
URL = "https://abcdefgh.supabase.co"
DB = "postgresql://postgres.abcdefgh:not-a-real-password@aws-0-example.pooler.supabase.com:5432/postgres"
HOST = "abcdefgh.supabase.co"


def run(tmp_path, stdin="", account=ACCOUNT, **over):
    base = {**GOOD, "SUPABASE_URL": URL, "DATABASE_URL_MIGRATOR": DB, "CAREFLOW_DEV_PROJECT_REF": "abcdefgh"}
    base.update(over)
    env_file = tmp_path / "seed.env"
    env_file.write_text("\n".join(f"{k}={v}" for k, v in base.items() if v is not None) + "\n", encoding="utf-8")
    return subprocess.run([sys.executable, "-I", str(SCRIPT), "--account-id", account, "--clinic-id", CLINIC, "--env-file", str(env_file)], input=stdin, capture_output=True, text=True, timeout=60, check=False)


def test_it_refuses_before_any_connection_outside_dev_demo_synthetic_without_the_host_and_without_keys(tmp_path):
    results = [run(tmp_path, CAREFLOW_ENV="ci"), run(tmp_path, CAREFLOW_PROFILE="clinical"), run(tmp_path, CAREFLOW_DATA_POLICY=None), run(tmp_path, DATABASE_URL_MIGRATOR=DB.replace("abcdefgh", "zzzzzzzz")),
               run(tmp_path, stdin="wrong.host\n"), run(tmp_path, stdin=""), run(tmp_path, stdin=HOST + "\n"), run(tmp_path, account="not-a-uuid")]
    assert results[7].returncode == 2 and all(r.returncode == 1 for r in results[:7])  # a malformed id is refused by the argument parser
    assert "encryption keys" in results[6].stdout and "--account-id" in results[7].stderr or "invalid" in results[7].stderr and "no host was typed" in results[5].stdout
    for r in results:
        out = r.stdout + r.stderr
        assert "not-a-real-password" not in out and ACCOUNT not in out and "Traceback" not in r.stderr  # no secret and no address is ever printed


def test_the_key_hides_the_account_id_and_the_decisions_are_closed_and_safe():
    import uuid

    clinic = uuid.UUID(CLINIC)
    key = mod.key_for(uuid.UUID(ACCOUNT), clinic)
    assert key == mod.key_for(uuid.UUID(ACCOUNT.upper()), clinic) and ACCOUNT not in key and key.startswith(f"demo-portal-patient:{clinic}:")
    live = {"origin": "test", "erased": False, "bound": False}
    assert mod.decide(None, remove=False)[0] == "create" and mod.decide(live, remove=False)[0] == "exists"
    again = mod.decide({**live, "erased": True}, remove=False)
    assert again[0] == "refuse" and "removed earlier" in again[1]  # a re-seed after a removal says so instead of silently doing nothing
    assert mod.decide(live, remove=True)[0] == "erase" and mod.decide(None, remove=True)[0] == "nothing" and mod.decide({**live, "erased": True}, remove=True)[0] == "nothing"
    for unsafe in ({**live, "bound": True}, {**live, "origin": "live"}):
        assert mod.decide(unsafe, remove=True)[0] == "refuse", unsafe  # never a bound record, never one that is not of origin test


def test_it_binds_nothing_calls_no_service_and_only_prepares_the_synthetic_record():
    code = SCRIPT.read_text(encoding="utf-8").split('"""', 2)[2].lower()
    for forbidden in ("iam.accounts", "consent_events", "claim_requests", "claim_patient_record", "staff_roles", "set account_id", "httpx", "requests.", "/auth/v1", "admin/users", "print(account", "password"):
        assert forbidden not in code, forbidden
    assert code.count("insert into") == 0 and "patientingestionpipeline" in code and "origin=\"test\"" in code and "refuse_unless_dev_demo_synthetic" in code
    assert mod.NAME.startswith("DEMO") and mod.DOB == "1990-01-01"
    assert "where id = :i and origin = 'test' and account_id is null and erased_at is null" in code  # the clean-up touches an unbound record of origin test only


def test_a_second_record_with_the_same_name_and_birth_date_is_refused_in_plain_words_with_both_ways_out():
    assert mod.duplicate_words(0, 0) is None
    for unbound, bound in ((1, 0), (0, 1), (2, 3)):
        words = mod.duplicate_words(unbound, bound)
        assert words and words.startswith("refusing:") and f"{unbound} unbound and {bound} bound" in words
        assert "--remove-legacy" in words and "--name" in words and "search" in words and "ambiguous" in words


def test_the_demo_name_must_say_demo_and_stay_plain():
    assert mod.check_name("  DEMO   Portal   Patient Two ") == "DEMO Portal Patient Two"
    for bad in ("Portal Patient", "DEMO", "DEMO " + "x" * 60, "DEMO O'Brien", "demo lower", "DEMO <script>"):
        with pytest.raises(mod.Refused):
            mod.check_name(bad)
    assert mod.NAME == "DEMO Portal Patient"


def test_remove_legacy_blanks_only_older_unbound_records_this_script_made_and_the_duplicate_check_runs_before_any_record_is_created():
    code = SCRIPT.read_text(encoding="utf-8").split('"""', 2)[2]
    legacy = code[code.index("if remove_legacy:") : code.index("token = vault.identity_token")]
    for clause in ("origin = 'test'", "account_id is null", "erased_at is null", "idempotency_key like :p", "idempotency_key <> :k"):
        assert clause in legacy  # unbound, live, origin test, made by this script (its key prefix), and never the record of this very account
    assert "KEY_PREFIX}{clinic}:%" in legacy
    start = code.index("if remove_legacy:")
    assert start < code.index("raise Refused(words)", start) < code.index("pipeline.ingest(")
    assert 'result.outcome != "created"' in code  # a matched or flagged-as-duplicate registration is never kept
