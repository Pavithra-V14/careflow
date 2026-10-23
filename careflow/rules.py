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
MAX_PROVENANCE_MATCHES: Final = 50  # msg.record_screening keeps at most this many matches (migration 0013)
# The hash of "no rules in force": sha256 of the empty string, which is also what the database computes for the empty set.
EMPTY_RULE_SET_HASH: Final = hashlib.sha256(b"").hexdigest()


class RulesError(Exception):
    """Base class: the request was refused and nothing was changed."""


class NotPermitted(RulesError):
    """Row-level security refused the write: the caller's role may not do this."""


class ClearRefused(RulesError):
    """msg.record_screening refused a `clear` (fail closed). The caller records `unassessable` instead."""


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
    rule_set_hash: str = EMPTY_RULE_SET_HASH  # identity of the rules in force, compiled or not (see rule_set_hash())

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


def rule_set_hash(chosen: "list[Mapping[str, Any]]") -> str:
    """Identity of the rule set in force: sha256 of the lines "rule_id:version_id", sorted as plain strings, joined with a single
    newline and NO trailing newline. msg.record_screening computes the same value in SQL (kb.active_red_flag_rules, lines sorted
    under COLLATE "C"), so a `clear` can be checked against what the database sees. The rows are the CHOSEN ones, before compiling,
    so an uncompilable rule is still part of the identity (and makes a `clear` fail closed: the counts then differ)."""
    lines = sorted(f"{row['id']}:{row['version_id']}" for row in chosen)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


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
    rule_set_hash: str = EMPTY_RULE_SET_HASH

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
    return Evaluation(
        outcome, tuple(matches), ruleset.profile, ruleset.usable_with_criteria, ruleset.drafts_excluded, ruleset.problems,
        ruleset.rule_set_hash,
    )


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


@dataclass(frozen=True)
class ScreeningResult:
    """What msg.record_screening recorded for a STORED message (migration 0013)."""

    screening_id: uuid.UUID
    replayed: bool
    outcome: Outcome  # what was recorded: a refused `clear` is recorded as `unassessable`


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
        return RuleSet(tuple(rules), resolved, drafts_passed, tuple(problems), rule_set_hash(chosen))

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
        """Raise the immediate nurse task for a match (§12.1) for a source that is NOT a stored message (a live voice transcript).
        For a stored message use `record_screening` / `evaluate_and_escalate`. Allowed for nurse, doctor and queue_worker only:
        front desk and admin get NotPermitted (migration 0013 hides message-sourced tasks from them). Idempotent: the key is derived
        from the source id and the rules that fired, so re-escalating the same source returns the same task. Only the provenance
        (ids, versions, positions) is stored, never the patient's words."""
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

    async def record_screening(
        self, conn: AsyncConnection, clinic_id: uuid.UUID, origin: Origin, message_id: uuid.UUID, evaluation: Evaluation,
        *, idempotency_key: str,
    ) -> ScreeningResult:
        """Record the result of screening a STORED message through msg.record_screening (migration 0013): the only way a screening
        row, and for a red flag or an unassessable result its nurse task, comes to exist. Works for every role that may screen the
        message (nurse, doctor, queue_worker; front desk for appointments / safety), because the database function does the writing.

        Only `clear` can be refused by the database (fail closed). A refused clear, or a demo-profile clear on a live message, is
        recorded as `unassessable` instead, which raises the nurse task: unknown is never clear. One warning is logged, with ids only."""
        if origin not in ORIGINS:
            raise ValueError(f"origin must be one of {ORIGINS}, got {origin!r}")
        if not isinstance(message_id, uuid.UUID):
            raise TypeError("message_id must be the uuid of a stored message")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise RulesError("an idempotency key is required")
        matches = [m.as_provenance() for m in evaluation.matches]
        provenance = {
            "outcome": evaluation.outcome, "profile": evaluation.profile,
            "matches": matches[:MAX_PROVENANCE_MATCHES], "matches_truncated": len(matches) > MAX_PROVENANCE_MATCHES,
        }
        if evaluation.outcome == "clear" and evaluation.profile == "demo" and origin == "live":
            log.warning(
                "screening: demo-profile clear on live origin treated as unassessable (clinic=%s message=%s)", clinic_id, message_id
            )
            return await self._call_record_screening(conn, message_id, "unassessable", evaluation, {}, f"{idempotency_key}:unassessable")
        try:
            return await self._call_record_screening(conn, message_id, evaluation.outcome, evaluation, provenance, idempotency_key)
        except ClearRefused:
            log.warning(
                "screening: clear refused by the database, recording unassessable (clinic=%s message=%s profile=%s)",
                clinic_id, message_id, evaluation.profile,
            )
            return await self._call_record_screening(conn, message_id, "unassessable", evaluation, {}, f"{idempotency_key}:unassessable")

    @staticmethod
    async def _call_record_screening(
        conn: AsyncConnection, message_id: uuid.UUID, outcome: str, evaluation: Evaluation, provenance: dict[str, Any], key: str
    ) -> ScreeningResult:
        try:
            async with conn.begin_nested():  # a refusal must not abort the caller's transaction
                row = (
                    await conn.execute(
                        text("select * from msg.record_screening(:m, :o, :p, :n, :h, cast(:prov as jsonb), :k)"),
                        {"m": message_id, "o": outcome, "p": evaluation.profile, "n": evaluation.rules_evaluated,
                         "h": evaluation.rule_set_hash, "prov": json.dumps(provenance, sort_keys=True), "k": key},
                    )
                ).one()
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) == "42501":
                raise NotPermitted("your role may not screen this message") from exc
            if "CLEAR_REFUSED" in str(exc.orig):
                raise ClearRefused("the database refused a clear") from exc
            raise
        return ScreeningResult(row.screening_id, row.replayed, row.recorded_outcome)  # the row's own outcome, not the requested one

    async def evaluate_and_escalate(
        self, conn: AsyncConnection, clinic_id: uuid.UUID, origin: Origin, text_content: str, message_id: uuid.UUID,
        *, profile: Profile | None = None, idempotency_key: str | None = None,
    ) -> tuple[Evaluation, ScreeningResult]:
        """Screen the text of a STORED message and record the result, in the caller's transaction, for every role. The rules run
        first and alone: no model is consulted. A red flag or an unassessable result raises the nurse task (through
        msg.record_screening); a clear is recorded only when the database agrees. For a source that is not a stored message (a live
        voice transcript) use `evaluate_text` / `open_stream` and `escalate_red_flag` instead."""
        evaluation = await self.evaluate_text(conn, clinic_id, origin, text_content, profile=profile)
        result = await self.record_screening(
            conn, clinic_id, origin, message_id, evaluation, idempotency_key=idempotency_key or f"screen-eval:{message_id}:{evaluation.profile}:{evaluation.rule_set_hash}"
        )
        return evaluation, result
