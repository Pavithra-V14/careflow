"""The deterministic red-flag rule engine (BUILD-SPEC.md §5.2, §11.6, §12.1, §20.2; CLAUDE.md rules 1, 2, 11).

# CLINICAL-PLACEHOLDER: needs clinician sign-off

This module is MECHANISM ONLY. It contains no red-flag phrase, pattern, threshold or vitals boundary, and it
must never gain one: rules are clinician-authored data in `kb.red_flag_rules`, created `draft_unreviewed` and
usable in the `clinical` profile only once approved under the two-person rule (§20.2).

What it guarantees
  * No model, no network, no third-party service. Matching is string and regex work plus one read of the
    rule table. The safety outcome therefore cannot depend on a model's availability or opinion, and nothing
    here sits behind a model call: callers run this FIRST, on every path (§5.2, rule 2). A model's safety
    opinion may only escalate a case this engine passed clean; it can never undo a flag raised here.
  * Same code for text and voice (rule 11, §11.6). `evaluate_text` screens a complete message;
    `StreamEvaluator.feed` screens a transcript as each utterance arrives, so a phrase split across chunks,
    or across a run of spaces, is still found. In a stream the end of the text counts as a word boundary, so a
    phrase fires the moment it is complete (never later than the full-text scan would; possibly earlier).
  * Unknown is not clear. An outcome of `clear` means rules were actually evaluated and none matched. If the
    profile leaves no usable rule with any criteria, or a usable rule failed to compile, the outcome is
    `unassessable`, which callers must treat as NOT clear (route to a human), never as safe.
  * Provenance without patient text: a match records the rule id, code, version, status, which phrase or
    pattern fired (by index) and the position. It never records the patient's words (rules 6 and 7).

Operational decisions (answers to the scoping questions for this module)

A. Text pre-processing. Input and every rule phrase/pattern go through `normalize_text` before matching:
   Unicode NFKC, removal of invisible format and control characters (so zero-width characters cannot be
   slipped inside a phrase to hide it), case-folding, then collapsing every run of whitespace to one space.
   Phrases match on token boundaries, where a boundary is anything that is not a letter, digit or combining
   mark; the text's start and end count as boundaries. Combining marks are treated as part of a word so Tamil
   and other Indic scripts, whose vowel signs are not `\\w`, are not split mid-word. Known limit: look-alike
   letters from another script (a Cyrillic "а" for a Latin "a") are not mapped to each other.

B. Profile behaviour. The engine runs identically in `demo` and `clinical`: it has no third-party dependency
   to degrade, so it never switches itself off and never gets slower or weaker. The one profile-dependent
   input is WHICH rule statuses count, because §20.2 says a `draft_unreviewed` rule is unusable in `clinical`:
   `clinical` uses approved rules only; `demo` also uses drafts (synthetic data only). Given the same rules
   the two profiles return the same result (tested). A profile that is unset or unrecognised is treated as
   `clinical`, the stricter one.

Known limit, stated rather than hidden: patterns are Python regular expressions with no execution timeout, so a
pathological pattern could be slow. Rules reach this table only through the migrator today; a timeout-capable
engine (RE2 or the `regex` module) must be adopted before a rule-authoring UI exists.
"""

import hashlib
import json
import logging
import os
import re
import unicodedata
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

log = logging.getLogger("careflow.rules")

PLACEHOLDER: Final = "# CLINICAL-PLACEHOLDER: needs clinician sign-off"
Profile = Literal["demo", "clinical"]
Origin = Literal["live", "test", "eval", "canary"]
ORIGINS: Final = ("live", "test", "eval", "canary")
Outcome = Literal["red_flag", "clear", "unassessable"]
USABLE_STATUSES: Final[dict[str, frozenset[str]]] = {
    "clinical": frozenset({"approved"}),
    "demo": frozenset({"approved", "draft_unreviewed"}),
}
MAX_MATCHES_PER_PATTERN: Final = 50
MAX_PATTERN_CHARS: Final = 500


class RulesError(Exception):
    """Base class: the request was refused and nothing was changed."""


class NotPermitted(RulesError):
    """Row-level security refused the write: the caller's role may not do this."""


def resolve_profile(value: str | None = None) -> Profile:
    """`demo` or `clinical`. Unset or unrecognised means `clinical` (unknown is not permissive)."""
    raw = value if value is not None else os.environ.get("CAREFLOW_PROFILE")
    return "demo" if raw is not None and raw.strip().lower() == "demo" else "clinical"


# ---- text normalisation (A) ------------------------------------------------------------------------

_INVISIBLE = {"Cf", "Cc"}


def normalize_text(value: str) -> str:
    """NFKC, strip invisible format/control characters, case-fold, collapse whitespace to single spaces."""
    if not isinstance(value, str):
        raise TypeError("text must be a str")
    folded = unicodedata.normalize("NFKC", value)
    folded = "".join(ch for ch in folded if ch.isspace() or unicodedata.category(ch) not in _INVISIBLE)
    folded = unicodedata.normalize("NFKC", folded.casefold())
    return " ".join(folded.split())


def _is_word_char(ch: str) -> bool:
    return unicodedata.category(ch)[0] in {"L", "N", "M"}


def _on_boundaries(haystack: str, start: int, end: int) -> bool:
    return (start == 0 or not _is_word_char(haystack[start - 1])) and (end == len(haystack) or not _is_word_char(haystack[end]))


# ---- rules and matches -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Rule:
    id: uuid.UUID
    rule_code: str
    version_id: int
    status: str
    phrases: tuple[str, ...]  # already normalised
    patterns: tuple[re.Pattern[str], ...]

    @property
    def has_criteria(self) -> bool:
        return bool(self.phrases or self.patterns)


@dataclass(frozen=True)
class Match:
    rule_id: uuid.UUID
    rule_code: str
    version_id: int
    rule_status: str
    kind: Literal["phrase", "pattern"]
    index: int  # which phrase / pattern of the rule fired
    start: int  # offsets into the NORMALISED text; the text itself is never recorded
    end: int

    def as_provenance(self) -> dict[str, Any]:
        return {
            "rule_id": str(self.rule_id), "rule_code": self.rule_code, "version_id": self.version_id,
            "rule_status": self.rule_status, "kind": self.kind, "index": self.index, "start": self.start, "end": self.end,
        }


@dataclass(frozen=True)
class RuleSet:
    rules: tuple[Rule, ...]
    profile: Profile
    drafts_excluded: int = 0  # unreviewed rules left out because the profile forbids them
    problems: tuple[str, ...] = ()  # rules that could not be compiled; an approved rule failing is never silent

    @property
    def usable_with_criteria(self) -> int:
        return sum(1 for r in self.rules if r.has_criteria)


def compile_rule(row: Mapping[str, Any]) -> tuple[Rule | None, str | None]:
    """Build a Rule from a table row. Returns (rule, None) or (None, problem description without rule text)."""
    try:
        phrases = tuple(p for p in (normalize_text(x) for x in (row["phrases"] or [])) if p)
        compiled = []
        for pattern in row["patterns"] or []:
            if not isinstance(pattern, str) or not pattern.strip() or len(pattern) > MAX_PATTERN_CHARS:
                raise ValueError("pattern is blank or longer than the limit")
            compiled.append(re.compile(pattern, re.IGNORECASE | re.UNICODE))
    except (re.error, ValueError, TypeError):
        return None, f"rule {row['rule_code']} v{row['version_id']} could not be compiled"
    return Rule(row["id"], row["rule_code"], row["version_id"], row["status"], phrases, tuple(compiled)), None


def select_active_versions(rows: list[Mapping[str, Any]], profile: Profile) -> tuple[list[Mapping[str, Any]], int]:
    """Per (clinic scope, rule_code), pick the version that is in force for this profile.

    Walk versions newest first: a retired version withdraws the rule; the first version whose status the profile
    may use is the one in force. So a newer DRAFT never replaces an approved version in `clinical`. Returns the
    chosen rows and how many rules had a draft that was passed over."""
    allowed = USABLE_STATUSES[profile]
    by_key: dict[tuple[Any, str], list[Mapping[str, Any]]] = {}
    for row in rows:
        by_key.setdefault((row["clinic_id"], row["rule_code"]), []).append(row)
    chosen, drafts_passed = [], 0
    for versions in by_key.values():
        for row in sorted(versions, key=lambda r: r["version_id"], reverse=True):
            if row["status"] == "retired":
                break
            if row["status"] in allowed:
                chosen.append(row)
                break
            drafts_passed += 1
    return chosen, drafts_passed


# ---- scanning (pure, shared by text and voice) -----------------------------------------------------


def scan_normalized(rules: tuple[Rule, ...], haystack: str) -> list[Match]:
    """Every match of every rule in already-normalised text, ordered by position."""
    found: list[Match] = []
    for rule in rules:
        for i, phrase in enumerate(rule.phrases):
            at = haystack.find(phrase)
            while at != -1:
                end = at + len(phrase)
                if _on_boundaries(haystack, at, end):
                    found.append(Match(rule.id, rule.rule_code, rule.version_id, rule.status, "phrase", i, at, end))
                at = haystack.find(phrase, at + 1)
        for i, pattern in enumerate(rule.patterns):
            for count, m in enumerate(pattern.finditer(haystack)):
                if count >= MAX_MATCHES_PER_PATTERN:
                    break
                if m.end() > m.start():  # an empty match carries no information
                    found.append(Match(rule.id, rule.rule_code, rule.version_id, rule.status, "pattern", i, m.start(), m.end()))
    return sorted(found, key=lambda x: (x.start, x.end, x.rule_code, x.kind, x.index))


@dataclass(frozen=True)
class Evaluation:
    outcome: Outcome
    matches: tuple[Match, ...]
    profile: Profile
    rules_evaluated: int
    drafts_excluded: int = 0
    problems: tuple[str, ...] = ()

    @property
    def safe_to_treat_as_clear(self) -> bool:
        """True only for `clear`. `unassessable` is NOT safe: it means the net could not vouch for the text."""
        return self.outcome == "clear"

    @property
    def provenance(self) -> dict[str, Any]:
        """Which rules fired and where. Structured ids and offsets only: no patient text."""
        return {
            "outcome": self.outcome, "profile": self.profile, "rules_evaluated": self.rules_evaluated,
            "drafts_excluded": self.drafts_excluded, "matches": [m.as_provenance() for m in self.matches],
        }


def _judge(ruleset: RuleSet, matches: list[Match]) -> Evaluation:
    if matches:
        outcome: Outcome = "red_flag"
    elif ruleset.usable_with_criteria == 0 or ruleset.problems:
        outcome = "unassessable"
    else:
        outcome = "clear"
    return Evaluation(outcome, tuple(matches), ruleset.profile, ruleset.usable_with_criteria, ruleset.drafts_excluded, ruleset.problems)


def evaluate_with(ruleset: RuleSet, text_content: str) -> Evaluation:
    """Screen one complete message against an already-loaded rule set. Pure and synchronous."""
    return _judge(ruleset, scan_normalized(ruleset.rules, normalize_text(text_content)))


class StreamEvaluator:
    """Screens a transcript as it arrives (voice, §11.6). Feed it each transcribed utterance.

    The whole transcript so far is re-normalised and re-scanned on every feed (transcripts are short), so a
    phrase split across two chunks, or across a run of spaces, still matches. Each distinct match is reported
    once, by the feed that completes it. Matching never waits for the end of the stream."""

    def __init__(self, ruleset: RuleSet) -> None:
        self._ruleset = ruleset
        self._raw = ""
        self._reported: set[tuple[uuid.UUID, str, int, int]] = set()
        self.red_flag_seen = False

    def feed(self, chunk: str) -> Evaluation:
        if not isinstance(chunk, str):
            raise TypeError("chunk must be a str")
        self._raw += chunk
        every = scan_normalized(self._ruleset.rules, normalize_text(self._raw))
        fresh = [m for m in every if (m.rule_id, m.kind, m.index, m.start) not in self._reported]
        self._reported.update((m.rule_id, m.kind, m.index, m.start) for m in fresh)
        self.red_flag_seen = self.red_flag_seen or bool(fresh)
        return _judge(self._ruleset, fresh)


# ---- the engine (database-backed) ------------------------------------------------------------------


@dataclass(frozen=True)
class EscalationResult:
    task_id: uuid.UUID
    replayed: bool = False


class RedFlagEngine:
    """Loads clinician-authored rules for a tenant and screens text. Runs as the restricted app role with the
    verified claims set; row-level security decides which rules the caller may read."""

    async def load_rules(self, conn: AsyncConnection, clinic_id: uuid.UUID, origin: Origin, profile: Profile | None = None) -> RuleSet:
        if origin not in ORIGINS:
            raise ValueError(f"origin must be one of {ORIGINS}, got {origin!r}")
        resolved = profile if profile in USABLE_STATUSES else resolve_profile(profile)
        result = await conn.execute(
            text(
                """select id, clinic_id, rule_code, version_id, status, phrases, patterns
                   from kb.red_flag_rules
                   where (clinic_id = :c or clinic_id is null) and origin = :o"""
            ),
            {"c": clinic_id, "o": origin},
        )
        chosen, drafts_passed = select_active_versions([dict(r._mapping) for r in result], resolved)
        rules, problems = [], []
        for row in chosen:
            rule, problem = compile_rule(row)
            if rule is not None:
                rules.append(rule)
            elif problem:
                problems.append(problem)
        return RuleSet(tuple(rules), resolved, drafts_passed, tuple(problems))

    async def evaluate_text(
        self, conn: AsyncConnection, clinic_id: uuid.UUID, origin: Origin, text_content: str, *, profile: Profile | None = None
    ) -> Evaluation:
        """Screen a complete message (text channels). No model is consulted. Logs the provenance of any match."""
        if not isinstance(text_content, str):
            raise TypeError("text_content must be a str")
        evaluation = evaluate_with(await self.load_rules(conn, clinic_id, origin, profile), text_content)
        self._log(clinic_id, evaluation)
        return evaluation

    async def open_stream(
        self, conn: AsyncConnection, clinic_id: uuid.UUID, origin: Origin, *, profile: Profile | None = None
    ) -> StreamEvaluator:
        """Load the rules once and return an evaluator to feed live transcript chunks (voice, §11.6)."""
        return StreamEvaluator(await self.load_rules(conn, clinic_id, origin, profile))

    @staticmethod
    def _log(clinic_id: uuid.UUID, evaluation: Evaluation) -> None:
        if evaluation.outcome == "red_flag":
            log.warning("red flag matched clinic=%s provenance=%s", clinic_id, json.dumps(evaluation.provenance, sort_keys=True))
        elif evaluation.outcome == "unassessable":
            log.warning("red-flag screening unassessable clinic=%s profile=%s problems=%s", clinic_id, evaluation.profile, list(evaluation.problems))

    async def escalate_red_flag(
        self, conn: AsyncConnection, clinic_id: uuid.UUID, origin: Origin, match_provenance: "Evaluation | Mapping[str, Any]",
        source_message_id: str,
    ) -> EscalationResult:
        """Raise the immediate nurse task for a match (§12.1). Idempotent: the key is derived from the source
        message and the rules that fired, so re-escalating the same message returns the same task. Only the
        provenance (ids, versions, positions) is stored, never the patient's words."""
        if origin not in ORIGINS:
            raise ValueError(f"origin must be one of {ORIGINS}, got {origin!r}")
        if not isinstance(source_message_id, str) or not source_message_id.strip() or len(source_message_id) > 200:
            raise RulesError("source_message_id is required (1 to 200 characters)")
        provenance = match_provenance.provenance if isinstance(match_provenance, Evaluation) else dict(match_provenance)
        matches = provenance.get("matches")
        if not isinstance(matches, list) or not matches:
            raise RulesError("there is no match to escalate")
        safe_matches = [
            {k: m[k] for k in ("rule_id", "rule_code", "version_id", "rule_status", "kind", "index", "start", "end")}
            for m in matches
        ]
        stored = {"matches": safe_matches, "profile": provenance.get("profile"), "outcome": "red_flag"}
        fired = sorted({(m["rule_id"], m["version_id"]) for m in safe_matches})
        digest = hashlib.sha256(json.dumps(fired).encode()).hexdigest()[:32]
        key = f"redflag:{source_message_id}:{digest}"
        try:
            async with conn.begin_nested():
                created = (
                    await conn.execute(
                        text(
                            """insert into agents.review_tasks
                                   (clinic_id, origin, kind, priority, assigned_role, source_message_id, provenance, idempotency_key)
                               values (:c, :o, 'red_flag', 'immediate', 'nurse', :m, cast(:p as jsonb), :k)
                               on conflict (idempotency_key) do nothing
                               returning id"""
                        ),
                        {"c": clinic_id, "o": origin, "m": source_message_id, "p": json.dumps(stored, sort_keys=True), "k": key},
                    )
                ).first()
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) == "42501":
                raise NotPermitted("your role may not raise a review task for this clinic") from exc
            raise
        if created is not None:
            return EscalationResult(created[0])
        existing = (await conn.execute(text("select id from agents.review_tasks where idempotency_key = :k"), {"k": key})).first()
        if existing is None:  # the key exists but is not visible to this caller
            raise RulesError("this escalation key is already used by another request")
        return EscalationResult(existing[0], replayed=True)

    async def evaluate_and_escalate(
        self, conn: AsyncConnection, clinic_id: uuid.UUID, origin: Origin, text_content: str, source_message_id: str,
        *, profile: Profile | None = None,
    ) -> tuple[Evaluation, EscalationResult | None]:
        """Screen a message and, if it matches, raise the nurse task in the same transaction."""
        evaluation = await self.evaluate_text(conn, clinic_id, origin, text_content, profile=profile)
        if evaluation.outcome != "red_flag":
            return evaluation, None
        return evaluation, await self.escalate_red_flag(conn, clinic_id, origin, evaluation, source_message_id)
