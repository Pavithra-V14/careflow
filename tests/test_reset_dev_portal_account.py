"""scripts/reset_dev_portal_account.py: refusals before any connection, the pure decision rules, and what it never touches. Offline."""

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "reset_dev_portal_account.py"
spec = importlib.util.spec_from_file_location("reset_dev_portal_account", SCRIPT)
mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
sys.modules["reset_dev_portal_account"] = mod
spec.loader.exec_module(mod)  # type: ignore[union-attr]

ACCOUNT = "6f1c7c2e-8f0a-4a52-9d6e-2b1d0c3a9e11"
GOOD = {"CAREFLOW_ENV": "dev", "CAREFLOW_PROFILE": "demo", "CAREFLOW_DATA_POLICY": "synthetic_only"}
URL = "https://abcdefgh.supabase.co"
DB = "postgresql://postgres.abcdefgh:not-a-real-password@aws-0-example.pooler.supabase.com:5432/postgres"
HOST = "abcdefgh.supabase.co"


def run(tmp_path, stdin="", **over):
    base = {**GOOD, "SUPABASE_URL": URL, "DATABASE_URL_MIGRATOR": DB, "CAREFLOW_DEV_PROJECT_REF": "abcdefgh"}
    base.update(over)
    env_file = tmp_path / "reset.env"
    env_file.write_text("\n".join(f"{k}={v}" for k, v in base.items() if v is not None) + "\n", encoding="utf-8")
    return subprocess.run([sys.executable, "-I", str(SCRIPT), "--account-id", ACCOUNT, "--env-file", str(env_file)], input=stdin, capture_output=True, text=True, timeout=60, check=False)


def test_it_refuses_before_any_connection_outside_dev_demo_synthetic_and_without_the_typed_host(tmp_path):
    results = [run(tmp_path, CAREFLOW_ENV="ci"), run(tmp_path, CAREFLOW_PROFILE="clinical"), run(tmp_path, CAREFLOW_DATA_POLICY=None), run(tmp_path, DATABASE_URL_MIGRATOR=DB.replace("abcdefgh", "zzzzzzzz")),
               run(tmp_path, stdin="wrong.host\n"), run(tmp_path, stdin="")]
    assert all(r.returncode == 1 for r in results)
    assert f"target project host: {HOST}" in results[4].stdout and "no host was typed" in results[5].stdout
    for r in results:
        assert "not-a-real-password" not in r.stdout + r.stderr and "Traceback" not in r.stderr


def test_the_decision_rules():
    patient = {"account_type": "patient", "status": "active", "origin": "live"}
    assert mod.decide(patient, 0, 0, 0)[0] == "fix"
    assert mod.decide({**patient, "origin": "test"}, 0, 0, 0)[0] == "nothing"
    for case in (mod.decide(None, 0, 0, 0), mod.decide({**patient, "account_type": "staff"}, 0, 0, 0), mod.decide(patient, 1, 0, 0), mod.decide(patient, 0, 1, 0), mod.decide(patient, 0, 0, 1)):
        assert case[0] == "refuse" and case[1].startswith("refusing:")


def test_it_changes_one_column_of_one_account_and_touches_nothing_append_only_or_clinical():
    code = SCRIPT.read_text(encoding="utf-8").split('"""', 2)[2].lower()
    statements = re.findall(r"text\(\"([^\"]+)\"", code)
    writes = [s for s in statements if re.match(r"\s*(insert|update|delete|truncate|alter|drop)", s)]
    assert len(writes) == 1 and writes[0].startswith("update iam.accounts set origin = :o where id = :a and account_type = 'patient'")
    for forbidden in ("audit.", "consent_events", "mpi.patients set", "delete from", "insert into", "claim_requests set", "email", "password"):
        assert forbidden not in " ".join(writes), forbidden
