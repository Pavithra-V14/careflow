"""scripts/seed_demo_family_rules.py: its REFUSALS and its labelling, offline. The seeding itself writes to the dev database and is run by the owner by hand; nothing here connects to a database.

The numbers it enters are NOT counsel's; the label says so, and the script writes nothing to a migration or a repository default.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "seed_demo_family_rules.py"
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
    results = [
        run(tmp_path, CAREFLOW_ENV="ci"),
        run(tmp_path, CAREFLOW_PROFILE="clinical"),
        run(tmp_path, CAREFLOW_DATA_POLICY=None),
        run(tmp_path, DATABASE_URL_MIGRATOR=DB.replace("abcdefgh", "zzzzzzzz")),
        run(tmp_path, stdin="wrong.host\n"),
        run(tmp_path, stdin=""),
    ]
    assert all(r.returncode == 1 for r in results)
    assert "dev project only" in results[0].stdout and "demo profile only" in results[1].stdout and "synthetic_only" in results[2].stdout
    assert f"target project host: {HOST}" in results[4].stdout and "not the project host shown" in results[4].stdout
    assert "no host was typed" in results[5].stdout
    for r in results:
        assert "not-a-real-password" not in r.stdout + r.stderr  # no secret is ever printed
        assert "Traceback" not in r.stderr


def test_the_rules_are_labelled_as_demo_placeholders_and_nothing_real_is_touched_or_written_to_the_repository():
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'LABEL = "DEMO PLACEHOLDER, not counsel\'s values"' in source
    code = source.lower().split('"""', 2)[2]
    assert code.count("insert into") == 1 and "on conflict (clinic_id, link_type) do nothing" in code
    assert "provenance = :l" in code and "provenance is distinct from :l" in code  # only labelled rows are removed; an unlabelled rule makes it refuse
    assert "origin != \"test\"" in code  # a test clinic only
    assert "db/migrations" not in code and "write_text" not in code and "open(" not in code  # nothing is written to a file
