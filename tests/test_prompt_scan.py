"""The prompt-template scan (BUILD-SPEC.md 6.2): no PHI, names, timestamps or interpolated patient values in a prompt template. Offline."""

from pathlib import Path

import pytest

from careflow.prompt_scan import load_allowlist, scan_directory, scan_text

ROOT = Path(__file__).resolve().parent.parent
SENTINEL = "SENTINEL-TEMPLATE-VALUE-9917"


def codes(text, kind, allowed=frozenset()):
    return [f.code for f in scan_text(text, kind, allowed=allowed)]


# ---- the repository's own templates ----------------------------------------------------------------------------------------------------------


def test_the_repositorys_prompt_templates_are_clean():
    assert scan_directory(ROOT / "prompts") == []


def test_a_missing_prompt_directory_is_a_finding_not_a_pass(tmp_path):
    assert [f.code for f in scan_directory(tmp_path / "nope")] == ["prompt_directory_missing"]


# ---- system prompts: byte-identical, no placeholders -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "template",
    ["Hello {name}", "Hello {{ patient }}", "Hello ${who}", "Hello %(who)s", "Hello <<who>>", "Hello <who>", "Hello {0}", "Hello {{ now | iso }}"],
)
def test_a_system_prompt_may_contain_no_placeholder_of_any_syntax(template):
    assert "placeholder_in_system_prompt" in codes(template, "system")


def test_a_plain_system_prompt_passes():
    text = "You classify the message into one of the listed options. Answer with the option only.\nDo not add anything else.\n"
    assert codes(text, "system") == []


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("Written on 2026-10-24 for you.", "timestamp_literal"),
        ("Valid from 24/10/2026.", "timestamp_literal"),
        ("It is 09:30 now.", "timestamp_literal"),
        ("Answer as of today.", "time_word"),
        ("Use the current date to decide.", "time_word"),
        ("Call 9876543210 for help.", "phone_like"),
        ("Call +91 98765 43210 for help.", "phone_like"),
        ("Write to someone@example.org.", "email_like"),
        ("Id 1234 5678 9012.", "national_id_like"),
    ],
)
def test_literal_timestamps_and_phi_shaped_values_are_refused_in_any_template(text, code):
    for kind in ("system", "user"):
        assert code in codes(text, kind), (kind, code)


def test_a_version_number_is_not_mistaken_for_a_date():
    assert codes("Prompt version 1.2.10 of the classifier.", "system") == []


# ---- user templates: an allowlist, and never identifying fields -------------------------------------------------------------------------------


def test_a_user_template_may_use_only_allowlisted_variables():
    allowed = frozenset({"document_text", "options_block"})
    assert codes("Text:\n{document_text}\nOptions:\n{{ options_block }}\n", "user", allowed) == []
    assert codes("Hello {other_thing}", "user", allowed) == ["variable_not_allowlisted"]


@pytest.mark.parametrize("variable", ["patient_name", "name", "dob", "date_of_birth", "phone_number", "email", "home_address", "aadhaar_no", "mrn",
                                      "patient_id", "guardian", "first_name"])
def test_an_identifying_variable_is_refused_even_if_it_is_allowlisted(variable):
    assert codes("Hello {" + variable + "}", "user", frozenset({variable})) == ["identifying_variable"]


@pytest.mark.parametrize("variable", ["now", "today", "current_date", "timestamp", "utcnow", "request_time"])
def test_a_time_variable_is_refused_even_if_it_is_allowlisted(variable):
    # `today` and `utcnow` are ALSO caught by the time-word literal check, so the set may hold more than one code
    assert "timestamp_variable" in codes("At {" + variable + "}", "user", frozenset({variable}))


def test_a_positional_placeholder_has_no_name_to_allowlist():
    assert codes("Hello {0}", "user", frozenset({"0"})) == ["unnamed_or_positional_placeholder"]


def test_a_finding_never_contains_the_text_that_triggered_it():
    findings = scan_text(f"Call 9876543210 about {{{SENTINEL}}} on 2026-10-24 or {SENTINEL}@example.org", "user")
    assert findings and SENTINEL not in repr(findings) and "9876543210" not in repr(findings)


# ---- the allowlist file and the directory walk -----------------------------------------------------------------------------------------------


def test_the_allowlist_cannot_grant_identifying_or_time_like_names(tmp_path):
    path = tmp_path / "variables.allowlist"
    path.write_text("# comment\ndocument_text  # the referral text\n\npatient_name\ntoday\nnot an identifier\n", encoding="utf-8")
    names, findings = load_allowlist(path)
    assert names == {"document_text"}
    assert [f.code for f in findings] == ["allowlist_entry_is_identifying_or_time_like", "allowlist_entry_is_identifying_or_time_like",
                                          "allowlist_entry_is_not_a_plain_identifier"]


def test_the_directory_scan_applies_each_kind_by_file_name_and_flags_unknown_names(tmp_path):
    (tmp_path / "variables.allowlist").write_text("document_text\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("Example: {name} and 2026-10-24 are described here, not scanned.\n", encoding="utf-8")
    (tmp_path / "classify.system.txt").write_text("Classify the text.\n", encoding="utf-8")
    (tmp_path / "classify.user.txt").write_text("Text:\n{document_text}\n", encoding="utf-8")
    assert scan_directory(tmp_path) == []
    (tmp_path / "bad.system.txt").write_text("Hello {name}\n", encoding="utf-8")
    (tmp_path / "sneaky.prompt").write_text("Hello {name}\n", encoding="utf-8")
    sub = tmp_path / "nested"
    sub.mkdir()
    (sub / "deep.user.txt").write_text("{patient_name}\n", encoding="utf-8")
    got = {(f.file, f.line, f.code) for f in scan_directory(tmp_path)}
    assert got == {
        ("bad.system.txt", 1, "placeholder_in_system_prompt"),
        ("sneaky.prompt", 0, "file_is_not_a_recognised_template_name"),
        ("nested/deep.user.txt", 1, "identifying_variable"),
    }
