"""Migration 0026 read as TEXT (offline): the front-desk insert of an `unmerge_review` task is bound to a real unmerge, admin loses the insert (not the read), the 0025 revoke clause is kept
exactly, and nothing else (grant, role, other policy) changes. The behaviour, as the restricted role, is in tests/test_rls_screenings_live.py."""

import re
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parent.parent / "db" / "migrations"
FORBIDDEN = re.compile(r"\bgrant\b|\brevoke (all|select|insert|update)\b|\bcreate role\b|\balter role\b|\bsecurity definer\b|\bcreate function\b|\balter table\b")


def code(name: str) -> str:
    sql = (MIGRATIONS / name).read_text(encoding="utf-8")
    return "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--")).lower()


def squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


C26, C25 = code("0026_unmerge_task_bound_to_merge.sql"), code("0025_identity_confirmation_revoke.sql")


def test_it_replaces_exactly_one_policy_and_adds_no_grant_role_or_function():
    assert C26.count("drop policy") == 1 and C26.count("create policy") == 1 and "review_tasks_insert" in C26
    assert not FORBIDDEN.search(C26)


def test_front_desks_unmerge_clause_needs_an_unmerged_link_it_wrote_with_the_key_and_source_the_code_writes():
    clause = squash(C26.split("kind = 'unmerge_review'")[1].split("kind = 'identity_confirmation_revoked'")[0])
    assert "assigned_role='nurse'andstatus='open'andacknowledged_byisnull" in clause
    assert "frommpi.mergesmwherem.status='unmerged'" in clause and "m.unmerged_by=auth.uid()" in clause
    assert "m.activated_atisnotnull" in clause  # the link was active: a cancelled proposal raised no task in the real writer
    assert "m.clinic_id=review_tasks.clinic_idandm.origin=review_tasks.origin" in clause
    assert "review_tasks.idempotency_key='unmerge-review:'||m.id::text" in clause and "review_tasks.source_message_id='merge:'||m.id::text" in clause
    identity = (MIGRATIONS.parent.parent / "careflow" / "identity.py").read_text(encoding="utf-8")
    assert 'f"unmerge-review:{merge.id}"' in identity and 'f"merge:{merge.id}"' in identity and "'unmerge_review', 'immediate', 'nurse'" in identity  # the shapes the writer produces


def test_admin_no_longer_inserts_and_the_other_roles_and_the_revoke_clause_are_unchanged():
    policy = C26.split("create policy review_tasks_insert")[1]
    assert "admin" not in policy  # admin still READS unmerge reviews (review_tasks_select is untouched and not in this file)
    assert "review_tasks_select" not in C26 and "review_tasks_update" not in C26
    assert "iam.has_clinic_role(clinic_id, array['nurse', 'doctor', 'queue_worker'])" in policy
    revoke_26 = squash(C26.split("kind = 'identity_confirmation_revoked'")[1])
    revoke_25 = squash(C25.split("kind = 'identity_confirmation_revoked'")[1])
    assert revoke_26 == revoke_25  # the 0025 clause, reproduced exactly
