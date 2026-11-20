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
EMAIL = "demo.patient@example.test"
GOOD = {"CAREFLOW_ENV": "dev", "CAREFLOW_PROFILE": "demo", "CAREFLOW_DATA_POLICY": "synthetic_only"}
URL = "https://abcdefgh.supabase.co"
DB = "postgresql://postgres.abcdefgh:not-a-real-password@aws-0-example.pooler.supabase.com:5432/postgres"
HOST = "abcdefgh.supabase.co"


def run(tmp_path, stdin="", email=EMAIL, **over):
    base = {**GOOD, "SUPABASE_URL": URL, "DATABASE_URL_MIGRATOR": DB, "CAREFLOW_DEV_PROJECT_REF": "abcdefgh"}
    base.update(over)
    env_file = tmp_path / "seed.env"
    env_file.write_text("\n".join(f"{k}={v}" for k, v in base.items() if v is not None) + "\n", encoding="utf-8")
    return subprocess.run([sys.executable, "-I", str(SCRIPT), "--email", email, "--clinic-id", CLINIC, "--env-file", str(env_file)], input=stdin, capture_output=True, text=True, timeout=60, check=False)


def test_it_refuses_before_any_connection_outside_dev_demo_synthetic_without_the_host_and_without_keys(tmp_path):
    results = [run(tmp_path, CAREFLOW_ENV="ci"), run(tmp_path, CAREFLOW_PROFILE="clinical"), run(tmp_path, CAREFLOW_DATA_POLICY=None), run(tmp_path, DATABASE_URL_MIGRATOR=DB.replace("abcdefgh", "zzzzzzzz")),
               run(tmp_path, stdin="wrong.host\n"), run(tmp_path, stdin=""), run(tmp_path, stdin=HOST + "\n"), run(tmp_path, email="not an email")]
    assert all(r.returncode == 1 for r in results)
    assert "encryption keys" in results[6].stdout and "e-mail address" in results[7].stdout and "no host was typed" in results[5].stdout
    for r in results:
        out = r.stdout + r.stderr
        assert "not-a-real-password" not in out and EMAIL not in out and "Traceback" not in r.stderr  # no secret and no address is ever printed


def test_the_address_check_and_the_key_hide_the_address():
    assert mod.check_email("  a@b.test ") == "a@b.test"
    for bad in ("", "a", "@b.test", "a@", "a@@b", "a b@c.test", "x" * 250 + "@b.test"):
        with pytest.raises(mod.Refused):
            mod.check_email(bad)
    key = mod.key_for("Demo.Patient@Example.test", __import__("uuid").UUID(CLINIC))
    assert key == mod.key_for(" demo.patient@example.test ", __import__("uuid").UUID(CLINIC)) and "demo.patient" not in key and "@" not in key


def test_it_binds_nothing_calls_no_service_and_only_prepares_the_synthetic_record():
    code = SCRIPT.read_text(encoding="utf-8").split('"""', 2)[2].lower()
    for forbidden in ("iam.accounts", "consent_events", "claim_requests", "claim_patient_record", "staff_roles", "set account_id", "httpx", "requests.", "/auth/v1", "admin/users", "print(email", "password"):
        assert forbidden not in code, forbidden
    assert code.count("insert into") == 0 and "patientingestionpipeline" in code and "origin=\"test\"" in code and "refuse_unless_dev_demo_synthetic" in code
    assert mod.NAME.startswith("DEMO") and mod.DOB == "1990-01-01"
