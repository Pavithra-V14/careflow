"""Family links (migration 0091), OFFLINE: the switch, the age arithmetic, the fixed deny-by-default wording, the route wiring (a patient's identity before the setting, front desk only for staff, the age never taken from the
caller) and a static read of the migration (the rules table ships EMPTY, no default, no seed). The database half is tests/test_family_links_db_live.py and the role matrix. Synthetic dates only."""

import re
from datetime import date
from pathlib import Path

import pytest

from careflow import family_links_sql

ROOT = Path(__file__).resolve().parent.parent


def test_the_feature_is_off_unless_exactly_on():
    assert family_links_sql.enabled({family_links_sql.SETTING: "on"})
    for value in ("", "1", "true", "yes", "off"):
        assert not family_links_sql.enabled({family_links_sql.SETTING: value}), value
    assert not family_links_sql.enabled({})


def test_the_age_is_whole_years_and_a_birthday_not_yet_reached_does_not_count():
    today = date(2026, 11, 14)
    assert family_links_sql.age_years("2026-11-14", today) == 0
    assert family_links_sql.age_years("2020-11-14", today) == 6 and family_links_sql.age_years("2020-11-15", today) == 5  # the day before the birthday
    assert family_links_sql.age_years("2000-02-29", date(2026, 2, 28)) == 25 and family_links_sql.age_years("2000-02-29", date(2026, 3, 1)) == 26
    for bad in ("", "not a date", "2026-13-40", "2027-01-01", "20200101"[:5]):
        with pytest.raises(family_links_sql.CannotAge):
            family_links_sql.age_years(bad, today)


def test_the_deny_by_default_wording_is_fixed():
    assert family_links_sql.NOT_CONFIGURED == "family links are not set up: the clinic has not entered its rules"


def _route(name: str) -> str:
    source = (ROOT / "careflow" / "app.py").read_text(encoding="utf-8")
    start = source.index(f"async def {name}")
    return source[start : source.index("\n\n\n", start)]


def test_the_routes_check_identity_before_the_setting_and_the_age_never_comes_from_the_caller():
    for name in ("request_family_link", "list_my_family_links", "revoke_family_link"):
        body = _route(name)
        assert body.index("_patient_account(conn, claims)") < body.index("_family_links_on()"), name
    for name in ("list_staff_family_links", "decide_family_link"):
        body = _route(name)
        assert body.index("_console_clinic(") < body.index("_family_links_on()"), name
    source = (ROOT / "careflow" / "app.py").read_text(encoding="utf-8")
    assert 'FAMILY_VERIFIERS = ("front_desk",)' in source
    assert "family_links_sql.dependant_age(" in _route("decide_family_link") and "age_years" not in source[source.index("class FamilyLinkDecisionIn") : source.index("class FamilyLinkDecisionOut")]  # the age is computed here from the stored date of birth
    for name in ("request_family_link", "revoke_family_link", "decide_family_link"):
        assert "IdempotencyKey" in source[source.index(f"async def {name}") : source.index(f"async def {name}") + 300], name


def test_the_migration_ships_the_rules_empty_denies_by_default_and_forces_rls_with_no_grant_no_policy():
    sql = (ROOT / "db" / "migrations" / "0091_family_links.sql").read_text(encoding="utf-8")
    code = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    assert not re.search(r"insert\s+into\s+iam\.family_link_rules", code)  # counsel's values are not ours: nothing is seeded
    assert not [l for l in code.splitlines() if re.search(r"(min_dependant_age_years|max_dependant_age_years|requires_dependant_consent)\s+(smallint|boolean)", l) and "default" in l]  # no default for any value
    assert "force row level security" in code and "create policy" not in code and not re.search(r"grant\s+(select|insert|update|delete|all)[^;]*iam\.family_link", code)
    assert "'not_configured'" in code and code.count("iam.aal2_session()") >= 2
    for name in ("family_link_request", "patient_family_links", "family_link_revoke", "staff_family_links", "family_link_decide"):
        block = code[code.index(f"create function iam.{name}") :]
        block = block[: block.index("\n$$;")]
        assert "security definer set search_path = ''" in block and "auth.uid()" in block and "42501" in block, name
    for name in ("staff_family_links", "family_link_decide"):
        block = code[code.index(f"create function iam.{name}") :]
        assert "iam.aal2_session()" in block[: block.index("\n$$;")] and "array['front_desk']" in block[: block.index("\n$$;")], name
    assert "enable always trigger family_links_guard_update" in code and "'sys:family-link:' || gen_random_uuid()::text" in code


def test_the_new_live_file_is_in_exactly_one_ci_shard_and_the_schema_check_knows_the_migration():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert ci.count("tests/test_family_links_db_live.py") == 1
    assert "0091" in (ROOT / "careflow" / "schema_check.py").read_text(encoding="utf-8")


def test_migration_0093_rechecks_the_requester_adds_the_staff_revoke_ties_keys_to_the_actor_and_uses_past_tense_audit_names():
    sql = (ROOT / "db" / "migrations" / "0093_family_and_knowledge_hardening.sql").read_text(encoding="utf-8")
    code = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    assert "'requester_not_active'" in code and "pt.erased_at is null" in code[code.index("requester_not_active") - 900 : code.index("requester_not_active")]
    assert "iam.family_link.verified" in code and "iam.family_link.rejected" in code and "'iam.family_link.' ||" not in code
    for prefix in ("'knowledge-edit:' || auth.uid()::text", "'knowledge-submit:' || auth.uid()::text", "'knowledge-decide:' || auth.uid()::text", "'family-decide:' || auth.uid()::text", "'family-staff-revoke:' || auth.uid()::text"):
        assert prefix in code, prefix  # every replay key is tied to the acting account
    block = code[code.index("create function iam.family_link_staff_revoke") :]
    block = block[: block.index("\n$$;")]
    assert "security definer set search_path = ''" in block and "iam.aal2_session()" in block and "array['front_desk']" in block and "42501" in block and "p_reason !~" in block
    assert "grant execute on function iam.family_link_staff_revoke" in code
