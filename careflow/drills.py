"""Drill records to pre-deployment evidence (BUILD-SPEC.md 14.6, Appendix D Phase 10; docs/runbooks/rollback-drill.md, restore-drill.md). A PURE CHECKER: it executes nothing. A person runs the drill on
real infrastructure and writes down what happened; this turns that record into the evidence claim `careflow/predeploy.py` reads, or says exactly why the record does not count.

FAILS CLOSED:
  * A ROLLBACK DRILL counts only if every required step is recorded, in order, each with a start and an end and marked ok, and the total from the first start to the last end is UNDER 300 seconds (the spec's
    5 minutes). A missing step, an out-of-order step, a step marked not ok, an end before its start, or a total of 300 seconds or more does not count.
  * A RESTORE DRILL counts only if every required CHECK is recorded true on the restored database. `append_only_triggers_fire` is in the list on purpose: those triggers do not fire under
    `session_replication_role = replica` (a restore path), so a restore that silently disables them must be caught (pre-launch checklist item 40).
  * The step and check NAMES below are engineering's starting list for the runbooks; the owner may extend them. They assert nothing clinical.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final

ROLLBACK_SECONDS: Final = 300
ROLLBACK_STEPS: Final = ("flags_off", "previous_version_deployed", "readiness_confirmed", "smoke_checks_passed")
RESTORE_CHECKS: Final = (
    "row_counts_match", "migration_ledger_checksums_match", "append_only_triggers_fire", "forced_rls_on_every_clinic_table", "role_matrix_passes_on_restored_database", "encryption_keys_decrypt_a_sample",
)


@dataclass(frozen=True)
class Step:
    name: str
    started_at: datetime
    ended_at: datetime
    ok: bool


@dataclass(frozen=True)
class RollbackRecord:
    steps: tuple[Step, ...]
    executed_by: str
    evidence: str  # a pointer to the log or ticket
    verified_on: date


@dataclass(frozen=True)
class RestoreRecord:
    checks: dict[str, bool]
    executed_by: str
    evidence: str
    verified_on: date


def rollback_problems(record: RollbackRecord) -> list[str]:
    problems: list[str] = []
    names = [s.name for s in record.steps]
    if names != list(ROLLBACK_STEPS):
        problems.append("steps_missing_extra_or_out_of_order")
    if any(not s.ok for s in record.steps):
        problems.append("a_step_failed")
    if any(s.ended_at < s.started_at for s in record.steps):
        problems.append("a_step_ends_before_it_starts")
    if any(b.started_at < a.ended_at for a, b in zip(record.steps, record.steps[1:], strict=False)):
        problems.append("steps_overlap_or_are_not_in_time_order")
    if not record.executed_by.strip() or not record.evidence.strip():
        problems.append("no_executor_or_evidence")
    if not problems and (record.steps[-1].ended_at - record.steps[0].started_at).total_seconds() >= ROLLBACK_SECONDS:
        problems.append("five_minutes_or_more")
    return problems


def rollback_evidence(record: RollbackRecord) -> tuple[dict[str, Any] | None, list[str]]:
    """(the claim for ops/predeploy-evidence.yaml `rollback_drill`, problems). No claim unless there are no problems."""
    problems = rollback_problems(record)
    if problems:
        return None, problems
    seconds = int((record.steps[-1].ended_at - record.steps[0].started_at).total_seconds())
    return {"end_to_end": True, "seconds": seconds, "evidence": record.evidence, "verified_on": record.verified_on.isoformat()}, []


def restore_problems(record: RestoreRecord) -> list[str]:
    problems = [f"check_missing:{c}" for c in RESTORE_CHECKS if c not in record.checks]
    problems += [f"check_failed:{c}" for c in RESTORE_CHECKS if record.checks.get(c) is False]
    problems += [f"check_not_boolean:{c}" for c in RESTORE_CHECKS if c in record.checks and not isinstance(record.checks[c], bool)]
    problems += [f"unknown_check:{c}" for c in record.checks if c not in RESTORE_CHECKS]
    if not record.executed_by.strip() or not record.evidence.strip():
        problems.append("no_executor_or_evidence")
    return problems


def restore_evidence(record: RestoreRecord) -> tuple[dict[str, Any] | None, list[str]]:
    """(the claim for the rubric item `restore_drilled`, problems)."""
    problems = restore_problems(record)
    if problems:
        return None, problems
    return {"met": True, "evidence": record.evidence, "verified_on": record.verified_on.isoformat()}, []
