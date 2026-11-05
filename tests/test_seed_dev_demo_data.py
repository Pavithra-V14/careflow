"""scripts/seed_dev_demo_data.py: its REFUSALS, offline (owner decision 2026-10-31). The seeding itself writes to the dev database and is run by the owner by hand; nothing here connects to a database.

Every refusal happens before a connection is opened: the wrong environment or profile or data policy, a migrator URL for a different project than SUPABASE_URL, a host that was not typed exactly.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "seed_dev_demo_data.py"
spec = importlib.util.spec_from_file_location("seed_dev_demo_data", SCRIPT)
seed = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
sys.modules["seed_dev_demo_data"] = seed
spec.loader.exec_module(seed)  # type: ignore[union-attr]

GOOD = {"CAREFLOW_ENV": "dev", "CAREFLOW_PROFILE": "demo", "CAREFLOW_DATA_POLICY": "synthetic_only"}
URL = "https://abcdefgh.supabase.co"
DB = "postgresql://postgres.abcdefgh:not-a-real-password@aws-0-example.pooler.supabase.com:5432/postgres"
HOST = "abcdefgh.supabase.co"
REF = "abcdefgh"


def test_only_dev_demo_synthetic_only_passes():
    seed.refuse_unless_dev_demo_synthetic(GOOD)
    for bad in (
        {**GOOD, "CAREFLOW_ENV": "ci"},
        {**GOOD, "CAREFLOW_ENV": "live"},
        {k: v for k, v in GOOD.items() if k != "CAREFLOW_ENV"},
        {**GOOD, "CAREFLOW_PROFILE": "clinical"},
        {k: v for k, v in GOOD.items() if k != "CAREFLOW_PROFILE"},
        {**GOOD, "CAREFLOW_DATA_POLICY": "phi_approved"},
        {k: v for k, v in GOOD.items() if k != "CAREFLOW_DATA_POLICY"},
        {**GOOD, "CAREFLOW_DATA_POLICY": ""},
    ):
        with pytest.raises(seed.Refused):
            seed.refuse_unless_dev_demo_synthetic(bad)


def test_the_migrator_url_must_belong_to_the_project_named_by_supabase_url():
    assert seed.refuse_unless_same_project(URL, DB, REF) == HOST
    for url, db in ((URL, DB.replace("abcdefgh", "zzzzzzzz")), (URL, "postgresql://postgres:pw@host/db"), (None, DB), ("", DB), (URL, None), (URL, "not a url at all")):
        with pytest.raises(seed.Refused):
            seed.refuse_unless_same_project(url, db, REF)


def test_the_owner_must_name_the_dev_project_ref_and_it_must_be_the_one_the_env_file_points_at():
    for ref in (None, "", "  ", "zzzzzzzz", "abcdefg", "abcdefghi"):
        with pytest.raises(seed.Refused):
            seed.refuse_unless_same_project(URL, DB, ref)  # a CI env file with CAREFLOW_ENV=dev edited in names the CI ref (or none), which is not what DEV_PROJECT_REF says


def test_the_typed_host_must_match_exactly():
    seed.confirm_target(HOST, HOST)
    seed.confirm_target("  " + HOST + " \n", HOST)  # surrounding whitespace only
    for typed in ("", "abcdefgh", HOST + ".evil.test", "ABCDEFGH.supabase.co", "yes", "y"):
        with pytest.raises(seed.Refused):
            seed.confirm_target(typed, HOST)


def run(tmp_path, env_lines, stdin=""):
    env_file = tmp_path / "seed.env"
    env_file.write_text("\n".join(env_lines) + "\n", encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--clinic-id", "6f1c7c2e-8f0a-4a52-9d6e-2b1d0c3a9e11", "--env-file", str(env_file)],
        input=stdin, capture_output=True, text=True, timeout=60, check=False,
    )


def good_lines(**over):
    base = {**GOOD, "SUPABASE_URL": URL, "DATABASE_URL_MIGRATOR": DB, "CAREFLOW_DEV_PROJECT_REF": REF}
    base.update(over)
    return [f"{k}={v}" for k, v in base.items() if v is not None]


def test_the_script_refuses_before_any_connection_in_each_case(tmp_path):
    ci = run(tmp_path, good_lines(CAREFLOW_ENV="ci"))
    assert ci.returncode == 1 and "dev project only" in ci.stdout
    clinical = run(tmp_path, good_lines(CAREFLOW_PROFILE="clinical"))
    assert clinical.returncode == 1 and "demo profile only" in clinical.stdout
    nopolicy = run(tmp_path, good_lines(CAREFLOW_DATA_POLICY=None))
    assert nopolicy.returncode == 1 and "synthetic_only" in nopolicy.stdout
    other = run(tmp_path, good_lines(DATABASE_URL_MIGRATOR=DB.replace("abcdefgh", "zzzzzzzz")))
    assert other.returncode == 1 and "does not point at the project" in other.stdout
    noref = run(tmp_path, good_lines(CAREFLOW_DEV_PROJECT_REF=None))
    assert noref.returncode == 1 and "CAREFLOW_DEV_PROJECT_REF" in noref.stdout
    wrong = run(tmp_path, good_lines(), stdin="wrong.host\n")
    assert wrong.returncode == 1 and f"target project host: {HOST}" in wrong.stdout and "not the project host shown" in wrong.stdout
    for result in (ci, clinical, nopolicy, other, noref, wrong):
        assert "not-a-real-password" not in result.stdout + result.stderr  # no secret is ever printed


def test_a_correct_host_but_missing_encryption_keys_still_stops_before_the_database(tmp_path):
    result = run(tmp_path, good_lines(), stdin=HOST + "\n")
    assert result.returncode == 1 and "encryption keys" in result.stdout


def test_the_script_never_writes_messages_and_deletes_or_updates_nothing():
    source = SCRIPT.read_text(encoding="utf-8").lower().split('"""', 2)[2]  # the code, not the docstring that describes these very rules
    assert "msg.messages" not in source and "insert into msg." not in source
    assert "delete from" not in source and "update " not in source.replace("# update", "")
    assert source.count("insert into") == source.count("on conflict") >= 9  # every insert is idempotent (the patients go through the ingestion pipeline's own idempotency key)
    assert "insert into agents.review_decisions" not in source  # no human decision is ever invented
    assert "acknowledged_by" not in source and "resolved_at" not in source  # no task is acknowledged or resolved on anyone's behalf
    assert "on conflict do nothing" not in source  # every conflict target is explicit, so a surprise constraint stops the run instead of being skipped
