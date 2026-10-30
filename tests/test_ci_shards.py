"""CI sharding gates (docs/design/ci-sharding.md): the live tests run in parallel shards against ONE shared CI database, so (1) no live test file
may be left out or run twice, (2) the workflow must keep the properties that make parallel shards safe, and (3) no live test may count a whole
table that another file commits to. Offline: it reads the workflow file and the test sources.
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
LIVE_FILES = sorted([*(f"tests/{p.name}" for p in (ROOT / "tests").glob("*live*.py")), "tests/test_api_role_matrix.py"])


def shards() -> list[dict]:
    return WORKFLOW["jobs"]["live"]["strategy"]["matrix"]["include"]


def shard_files(shard: dict) -> list[str]:
    return shard["files"].split()


# ---- (1) every live test file is in exactly one shard ----------------------------------------------------------------------------------------


MATRIX = "tests/test_api_role_matrix.py"


def test_every_live_test_file_is_in_exactly_one_shard_except_the_split_matrix_which_is_in_complete_parts():
    listed = [f for shard in shards() for f in shard_files(shard) if f != MATRIX]
    missing = sorted(set(LIVE_FILES) - set(listed) - {MATRIX})
    twice = sorted({f for f in listed if listed.count(f) > 1})
    unknown = sorted(set(listed) - set(LIVE_FILES))
    assert not missing, f"live test files that no shard runs (they would never run in CI): {missing}"
    assert not twice, f"live test files listed in more than one shard: {twice}"
    assert not unknown, f"shard entries that are not live test files (or do not exist): {unknown}"
    assert all((ROOT / f).is_file() for f in listed)
    # The matrix: in every shard that names it a `matrix_part` "i/n", with the same n, and the parts 1..n each exactly once (so every matrix test runs exactly once).
    parts = [shard["matrix_part"] for shard in shards() if MATRIX in shard_files(shard)]
    assert parts, "the role matrix is in no shard"
    n = {int(p.split("/")[1]) for p in parts}
    assert len(n) == 1 and sorted(int(p.split("/")[0]) for p in parts) == list(range(1, n.pop() + 1)), f"matrix parts must be 1..n exactly once, got {parts}"
    assert all(not shard.get("matrix_part") for shard in shards() if MATRIX not in shard_files(shard)), "a shard without the matrix must not set matrix_part"


def test_the_matrix_part_is_passed_to_pytest_in_every_shard():
    step = next(s for s in WORKFLOW["jobs"]["live"]["steps"] if "Live tests" in s.get("name", ""))
    assert step["env"]["CAREFLOW_MATRIX_PART"] == "${{ matrix.matrix_part }}"


def test_the_root_conftest_partitions_the_matrix_into_disjoint_complete_stable_parts():
    import importlib.util

    spec = importlib.util.spec_from_file_location("root_conftest", ROOT / "conftest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ids = [f"tests/test_api_role_matrix.py::test_role_matrix[{m}-/v1/staff/x{i}]" for i, m in enumerate(["GET", "POST"] * 150)]
    for total in (2, 3):
        owner = {i: module.matrix_part_of(i, total) for i in ids}
        assert set(owner.values()) == set(range(1, total + 1)) and all(module.matrix_part_of(i, total) == owner[i] for i in ids)  # complete, stable
        sizes = [list(owner.values()).count(p) for p in range(1, total + 1)]
        assert max(sizes) - min(sizes) < len(ids) * 0.25, sizes  # roughly even
    assert module.parse_matrix_part("") is None and module.parse_matrix_part("2/3") == (2, 3)
    for bad in ("0/2", "3/2", "x", "1/", "1/2/3"):
        try:
            module.parse_matrix_part(bad)
        except ValueError:
            continue
        raise AssertionError(bad)


def test_the_offline_job_runs_everything_the_shards_do_not():
    steps = " ".join(step.get("run", "") for step in WORKFLOW["jobs"]["offline"]["steps"])
    assert "--ignore-glob='*live*'" in steps and "--ignore=tests/test_api_role_matrix.py" in steps


# ---- (2) the workflow properties that make parallel shards safe ----------------------------------------------------------------------------------


def test_each_shard_has_its_own_non_cancelling_group_a_sixty_minute_limit_and_waits_for_the_migrations():
    live = WORKFLOW["jobs"]["live"]
    assert live["needs"] == "migrate" or live["needs"] == ["migrate"]
    assert live["timeout-minutes"] == 60
    group = live["concurrency"]
    assert "${{ matrix.shard }}" in group["group"], "shards must not share a group: a third waiting job would displace the second"
    assert group["cancel-in-progress"] is False, "a database-touching group must queue, not cancel"
    assert live["strategy"]["fail-fast"] is False


def test_migrations_run_once_in_their_own_non_cancelling_group():
    migrate = WORKFLOW["jobs"]["migrate"]
    assert migrate["concurrency"]["cancel-in-progress"] is False
    assert any("careflow.migrate" in step.get("run", "") for step in migrate["steps"])
    assert not any("careflow.migrate" in step.get("run", "") for step in WORKFLOW["jobs"]["live"]["steps"])


def test_a_newer_commit_cancels_its_own_older_pull_request_run_but_a_run_triggered_by_a_push_to_main_is_never_cancelled():
    """A cancelled live job skips its teardown and leaves committed rows in the shared CI database (how PR #3's main run was killed). So the expression must be true ONLY for
    pull requests: cancel-in-progress is exactly `github.event_name == 'pull_request'`, and the workflow still triggers on pushes to main."""
    concurrency = WORKFLOW["concurrency"]
    assert "github.ref" in concurrency["group"]
    assert concurrency["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"
    assert WORKFLOW[True]["push"]["branches"] == ["main"] and "pull_request" in WORKFLOW[True]


def test_the_required_check_named_test_passes_only_if_every_shard_did():
    test = WORKFLOW["jobs"]["test"]
    # The offline job runs lint, the OpenAPI snapshot and the structural no-side-effect-across-a-pause gate: the one required check must depend on it too.
    assert set(test["needs"]) == {"secret-scan", "offline", "migrate", "live"} and test["if"] == "always()"
    script = " ".join(step.get("run", "") for step in test["steps"])
    for job in ("secret-scan", "offline", "migrate", "live"):
        assert f"needs.{job}.result" in script
    assert '!= "success"' in script  # cancelled and skipped count as failure


def test_every_shard_keeps_verbose_durations_the_zero_skip_check_and_the_connection_sampler():
    run = " ".join(step.get("run", "") for step in WORKFLOW["jobs"]["live"]["steps"])
    for needle in ("-v", "--durations=25", "SKIPPED", "scripts/ci_peak_connections.py", "pytest", "--junitxml=shard-times.xml", "scripts/ci_shard_times.py"):
        assert needle in run, needle


# ---- (3) no unscoped whole-table count of a table that another file commits ----------------------------------------------------------------------

# Tables whose rows NO test commits (everything is rolled back, or the table is append-only and no test writes it for real): a whole-table count
# of them cannot see another session's data.
NEVER_COMMITTED = {"msg.messages", "msg.threads", "msg.screenings", "kb.red_flag_rules"}
# Tables committed by exactly ONE file, which therefore is the only file that may count them whole (it runs in one shard).
SINGLE_OWNER = {
    ("ops.queue", "tests/test_queue_live.py"),
    ("ops.outbox", "tests/test_queue_live.py"),
    ("ops.key_rotation_batches", "tests/test_rotation_live.py"),
}
# Whole-table counts that are run AS THE APPLICATION ROLE, where row-level security scopes them to the caller's clinic.
RLS_SCOPED_HELPERS = {("tests/test_identity_live.py", "mpi.merges"), ("tests/test_rules_live.py", "agents.review_tasks")}
_WHOLE_TABLE_COUNT = re.compile(r"count\(\*\) from ([a-z_]+\.[a-z_]+)\s*[\"')]")


def test_no_live_test_counts_a_whole_table_that_another_file_commits_to():
    offenders = []
    for rel in LIVE_FILES:
        for number, line in enumerate((ROOT / rel).read_text(encoding="utf-8").splitlines(), start=1):
            for table in _WHOLE_TABLE_COUNT.findall(line):
                if table in NEVER_COMMITTED or (table, rel) in SINGLE_OWNER:
                    continue
                if (rel, table) in RLS_SCOPED_HELPERS and line.lstrip().startswith("return (await conn.execute(text("):
                    continue
                offenders.append(f"{rel}:{number} counts all of {table}")
    assert not offenders, (
        "a whole-table count sees rows that OTHER shards commit and would fail under parallel shards; scope it to the test's own clinic "
        "(`where clinic_id = :c`) or, if the table really is committed by one file only, list it in SINGLE_OWNER:\n  " + "\n  ".join(offenders)
    )


def test_the_unscoped_count_guard_really_catches_one():
    assert _WHOLE_TABLE_COUNT.findall('await one(db, "select count(*) from sched.holds") == 1') == ["sched.holds"]
    assert _WHOLE_TABLE_COUNT.findall('await one(db, "select count(*) from sched.holds where clinic_id = :c", c=1) == 1') == []
