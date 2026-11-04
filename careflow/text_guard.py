"""Heuristic guards on a letter's text before any model sees it: an instruction-injection scan and PII masking (BUILD-SPEC.md 11.1, 11.3, 14.3 hard gate "no injected text changes a priority or an
extracted value undetected"; CLAUDE.md rules 2, 3 and 7). PLAIN CODE: no model, no service, no network, no new dependency.

THESE ARE HEURISTICS, NOT A GUARANTEE. They are word-and-shape patterns written without clinical judgement and tested on INVENTED text only. They will miss attacks in other words, other languages (the
patterns are English; Tamil and transliterated text are not covered), or split across lines in unusual ways, and they will sometimes hold an innocent letter. That trade is deliberate and it points one way:

  * WHEN UNSURE, HOLD. A suspect, unscannable or oversized text is never passed on; the Front Door holds it for a nurse (status `held_suspect_text`). Nothing here ever turns a hold into a pass, and
    nothing here can lower a priority or clear a red flag (the deterministic rules run before and independently; this guard sits after them and can only add a hold).
  * The hard gate in 14.3 is NOT closed by this module: it needs a clinician-authored adversarial gold set and its measured result (docs/design/pre-launch-checklist.md item 41).

THE INJECTION SCAN (`scan_text`) normalises the text (NFKC, invisible and bidirectional controls counted and removed, case folded, whitespace collapsed) and reports CLOSED reason words, never the matched
text. THE MASK (`mask_pii`) replaces phone-like and identifier-like number runs, e-mail addresses, full dates, labelled dates of birth and labelled or titled names with fixed tokens, plus any names the caller
already knows. It is the version of the letter that later steps (retrieval, embedding) receive; the extractor, which must read the identity, still gets the original, as the spec says. Masking is also
heuristic: an unlabelled name in running prose is not found.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Final

MAX_CHARS: Final = 200_000  # a letter longer than this is not scanned: unsure means hold
INVISIBLE_LIMIT: Final = 3  # this many invisible or bidirectional control characters in one letter is itself a signal (hidden text)

# closed reason words (the only thing that leaves this module about a suspect text)
REASONS: Final = (
    "override_instruction", "addresses_the_system", "role_marker", "priority_steering", "suppress_escalation", "hidden_text", "encoded_blob", "markup", "tool_call_shape", "unscannable",
)

_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = tuple(
    (name, re.compile(pattern, re.IGNORECASE | re.DOTALL))
    for name, pattern in (
        ("override_instruction", r"\b(ignore|disregard|forget|override|bypass|skip)\b.{0,40}\b(previous|prior|above|earlier|all|any|these|those|the|your|every)\b.{0,40}\b(instructions?|rules?|prompts?|guidelines?|polic(?:y|ies)|checks?|filters?)\b"),
        ("addresses_the_system", r"\b(system prompt|system message|developer message|developer mode|as an ai\b|language model|new instructions?|updated instructions?|jailbreak|you are (?:now )?(?:a|an|the)\b|act as\b|pretend (?:to be|you)|from now on\b)"),
        ("role_marker", r"(?:^|\n)\s*(?:system|assistant|user|human|ai)\s*:|<\s*/?\s*(?:system|assistant|instructions?|prompt)\s*>|\[/?inst\]|<\|[a-z_]+\|>|#{2,}\s*(?:system|instruction)"),
        ("priority_steering", r"\b(mark|set|classify|triage|rate|label|treat|score|prioriti[sz]e|categori[sz]e)\b.{0,40}\b(non[- ]?urgent|not urgent|routine|low priority|lowest priority|highest priority|top priority|urgent|immediate)\b"),
        ("suppress_escalation", r"\b(do not|don't|dont|never|no need to|must not|should not)\b.{0,20}\b(escalat\w*|flag\w*|alert\w*|refer\w*|notify|tell|report|review\w*|check\w*|mention)\b"),
        ("markup", r"<\s*script\b|javascript:|data:text/|<\s*iframe\b|<\s*img\b[^>]*\bon\w+\s*="),
        ("tool_call_shape", r"\b(call|invoke|run|execute|use)\b.{0,20}\b(function|tool|command|api|endpoint|script)\b|```"),
    )
)
_BLOB: Final = re.compile(r"[A-Za-z0-9+/=_-]{60,}")
_INVISIBLE_CATEGORIES: Final = {"Cf", "Cc", "Co", "Cs"}
_KEEP: Final = {"\n", "\t", "\r"}


@dataclass(frozen=True)
class Scan:
    suspect: bool
    reasons: tuple[str, ...]  # closed words from REASONS, never text


def _normalise(text: str) -> tuple[str, int]:
    folded = unicodedata.normalize("NFKC", text)
    invisible = sum(1 for ch in folded if ch not in _KEEP and unicodedata.category(ch) in _INVISIBLE_CATEGORIES)
    cleaned = "".join(ch for ch in folded if ch in _KEEP or unicodedata.category(ch) not in _INVISIBLE_CATEGORIES)
    return re.sub(r"[ \t]+", " ", cleaned), invisible


def scan_text(text: object) -> Scan:
    """Suspect (with closed reasons) or clear. Anything that is not a non-empty string of reasonable size is suspect (`unscannable`): unknown is not clear."""
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_CHARS:
        return Scan(True, ("unscannable",))
    cleaned, invisible = _normalise(text)
    found: list[str] = []
    if invisible >= INVISIBLE_LIMIT:
        found.append("hidden_text")
    for name, pattern in _PATTERNS:
        if pattern.search(cleaned):
            found.append(name)
    if _BLOB.search(cleaned):
        found.append("encoded_blob")
    return Scan(bool(found), tuple(dict.fromkeys(found)))


# ---- masking --------------------------------------------------------------------------------------------------------------------------------

_EMAIL: Final = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_NUMBER_RUN: Final = re.compile(r"(?<![\w])\+?\d[\d\s().-]{6,}\d(?![\w])")
_MONTHS: Final = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_DATES: Final = (
    re.compile(r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b"),
    re.compile(r"\b\d{4}[/.-]\d{1,2}[/.-]\d{1,2}\b"),
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTHS}\.?,?\s+\d{{2,4}}\b", re.IGNORECASE),
    re.compile(rf"\b{_MONTHS}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{2,4}}\b", re.IGNORECASE),
)
_DOB_YEAR: Final = re.compile(r"\b(date of birth|d\.?o\.?b\.?|born(?: on| in)?)\b\s*[:\-]?\s*(\d{4})\b", re.IGNORECASE)  # a bare year after a birth label (full dates are masked as dates)
_NAME_LABEL: Final = re.compile(
    r"\b((?i:patient(?: name)?|name|mr|mrs|ms|miss|dr|shri|smt|sri))\b\.?\s*[:\-]?\s*([A-Z][\w'.-]*(?:\s+[A-Z][\w'.-]*){0,3})"
)


@dataclass(frozen=True)
class Masked:
    text: str
    counts: dict[str, int]  # how many of each kind were masked (numbers only, for tests and logs)


def mask_pii(text: str, *, known_names: tuple[str, ...] = ()) -> Masked:
    """The letter with phone/identifier-like runs, e-mails, full dates, labelled dates of birth, labelled or titled names and `known_names` replaced by fixed tokens."""
    counts = {"phone": 0, "email": 0, "date": 0, "dob": 0, "name": 0}
    out = text

    def swap(pattern: re.Pattern[str], token: str, kind: str, source: str, *, group: int | None = None, digits: int = 0) -> str:
        def repl(m: re.Match[str]) -> str:
            if digits and sum(ch.isdigit() for ch in m.group(0)) < digits:
                return m.group(0)
            counts[kind] += 1
            if group is None:
                return token
            start, end = m.span(group)
            base = m.start()
            return m.group(0)[: start - base] + token + m.group(0)[end - base :]

        return pattern.sub(repl, source)

    out = swap(_DOB_YEAR, "[DOB]", "dob", out, group=2)
    out = swap(_EMAIL, "[EMAIL]", "email", out)
    for pattern in _DATES:
        out = swap(pattern, "[DATE]", "date", out)
    out = swap(_NUMBER_RUN, "[PHONE]", "phone", out, digits=8)
    out = swap(_NAME_LABEL, "[NAME]", "name", out, group=2)
    for name in known_names:
        if isinstance(name, str) and len(name.strip()) >= 3:
            pattern = re.compile(re.escape(name.strip()), re.IGNORECASE)
            out, n = pattern.subn("[NAME]", out)
            counts["name"] += n
    return Masked(out, counts)
