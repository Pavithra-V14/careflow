"""The prompt-template scan (BUILD-SPEC.md §6.2, §17.4; CLAUDE.md rules 6 and 7).

§6.2: "Provider prompt-prefix caching: system prompt + tool defs kept byte-identical, variable content last. No PHI, no timestamps, no names
ever interpolated into a system prompt, enforced by a CI test that scans prompt templates." §17.4: prompts live in the repository as files.
This module is that scan. `tests/test_prompt_scan.py` runs it over `prompts/` on every build, so a template that interpolates a patient value
fails CI.

TWO KINDS OF TEMPLATE, told apart by file name (anything else under `prompts/` is itself a finding, so nothing escapes by being named oddly):

  `*.system.txt`  a system prompt. It must be BYTE-IDENTICAL from call to call, so it may contain NO placeholder of any syntax, no timestamp
                  or date, and nothing shaped like a phone number, e-mail address, national id or date of birth.
  `*.user.txt`    a user-turn template. It may interpolate only variables named in `prompts/variables.allowlist` (one name per line,
                  `#` comments), and a variable whose name looks like patient-identifying data (name, dob, phone, e-mail, address, id number...)
                  is refused EVEN IF it is allowlisted: identifying fields are never sent in a template slot. Patient TEXT reaches a model as
                  its own variable content through the gateway's data-class routing (careflow.gateway), not by being named in a template here.
                  Timestamps and literal PHI-shaped values are refused here too.

A finding carries the file, the line number and a short CODE. It never carries the matched text, so the CI log can never become a place a
value from a template is printed. Placeholder syntaxes recognised: `{name}`, `{{ name }}`, `${name}`, `%(name)s`, `<<name>>`, `<name>`-style
angle slots with an identifier, and `{0}`-style positional slots (which are refused outright: a positional slot has no name to allowlist).
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

SYSTEM_SUFFIX: Final = ".system.txt"
USER_SUFFIX: Final = ".user.txt"
ALLOWED_OTHER_FILES: Final = {"README.md", "variables.allowlist", ".gitkeep"}

_PLACEHOLDERS: Final = [
    re.compile(r"\{\{\s*([^{}\s][^{}]*?)\s*\}\}"),  # {{ name }} (also {{ name | filter }})
    re.compile(r"\$\{\s*([^{}]*?)\s*\}"),  # ${name}
    re.compile(r"%\(\s*([^()]*?)\s*\)[a-zA-Z]"),  # %(name)s
    re.compile(r"<<\s*([^<>]*?)\s*>>"),  # <<name>>
    re.compile(r"(?<![\w{$])\{\s*([^{}\s][^{}]*?)\s*\}(?!\})"),  # {name}
    re.compile(r"<\s*([A-Za-z_][A-Za-z0-9_.]*)\s*>"),  # <name>
]
# A variable name that looks like it carries identifying data.
_IDENTIFYING: Final = re.compile(
    r"(name|dob|birth|phone|mobile|msisdn|email|e_mail|address|aadhaar|aadhar|passport|ssn|mrn|nric|insurance|guardian|relative|surname|"
    r"given_?name|first_?name|last_?name|patient_?id|national_?id|id_?number)",
    re.IGNORECASE,
)
_TIMESTAMP_VARIABLE: Final = re.compile(r"(now|today|date|time|timestamp|datetime|utc|clock|day|year|month)", re.IGNORECASE)
# Literal values in the text of a template.
_LITERALS: Final = {
    "timestamp_literal": re.compile(r"\b\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2})?|\b\d{1,2}:\d{2}(:\d{2})?\s?(am|pm)?\b|\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b\d{1,2}\.\d{1,2}\.\d{4}\b",
                                    re.IGNORECASE),
    "time_word": re.compile(r"\b(today|tonight|right now|the current (date|time)|current date|current time|as of now|utcnow)\b", re.IGNORECASE),
    "phone_like": re.compile(r"(?<!\d)(\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)"),
    "email_like": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "national_id_like": re.compile(r"(?<!\d)\d{4}[\s-]?\d{4}[\s-]?\d{4}(?!\d)"),
}


@dataclass(frozen=True)
class Finding:
    file: str
    line: int  # 1-based; 0 for a finding about the file as a whole
    code: str  # never the matched text


def _variable_names(line: str) -> list[str]:
    names: list[str] = []
    for pattern in _PLACEHOLDERS:
        names.extend(m.group(1).split("|")[0].split(".")[0].split(":")[0].strip() for m in pattern.finditer(line))
    return names


def scan_text(text: str, kind: str, *, allowed: frozenset[str] = frozenset(), file: str = "<text>") -> list[Finding]:
    """`kind` is 'system' or 'user'. Returns findings in line order; the text itself is never copied into one."""
    if kind not in {"system", "user"}:
        raise ValueError("kind must be 'system' or 'user'")
    out: list[Finding] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for name in _variable_names(line):
            if kind == "system":
                out.append(Finding(file, number, "placeholder_in_system_prompt"))
            elif not name or name.isdigit():
                out.append(Finding(file, number, "unnamed_or_positional_placeholder"))
            elif _IDENTIFYING.search(name):
                out.append(Finding(file, number, "identifying_variable"))
            elif _TIMESTAMP_VARIABLE.search(name):
                out.append(Finding(file, number, "timestamp_variable"))
            elif name not in allowed:
                out.append(Finding(file, number, "variable_not_allowlisted"))
        for code, pattern in _LITERALS.items():
            if pattern.search(line):
                out.append(Finding(file, number, code))
    return out


def load_allowlist(path: Path) -> tuple[frozenset[str], list[Finding]]:
    """The allowed variable names, plus findings for any entry that is itself identifying or time-like (an allowlist cannot grant those)."""
    names: set[str] = set()
    findings: list[Finding] = []
    if not path.exists():
        return frozenset(), findings
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        entry = raw.split("#", 1)[0].strip()
        if not entry:
            continue
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", entry):
            findings.append(Finding(path.name, number, "allowlist_entry_is_not_a_plain_identifier"))
        elif _IDENTIFYING.search(entry) or _TIMESTAMP_VARIABLE.search(entry):
            findings.append(Finding(path.name, number, "allowlist_entry_is_identifying_or_time_like"))
        else:
            names.add(entry)
    return frozenset(names), findings


def scan_directory(root: Path) -> list[Finding]:
    """Scan every file under `root`. A missing directory is a finding (a scan that silently finds nothing proves nothing)."""
    if not root.is_dir():
        return [Finding(str(root), 0, "prompt_directory_missing")]
    allowed, findings = load_allowlist(root / "variables.allowlist")
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        if path.name in ALLOWED_OTHER_FILES:
            continue
        if path.name.endswith(SYSTEM_SUFFIX):
            kind = "system"
        elif path.name.endswith(USER_SUFFIX):
            kind = "user"
        else:
            findings.append(Finding(rel, 0, "file_is_not_a_recognised_template_name"))
            continue
        findings.extend(scan_text(path.read_text(encoding="utf-8"), kind, allowed=allowed, file=rel))
    return findings
