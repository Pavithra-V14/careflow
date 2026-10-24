"""Script detection, used to route text BEFORE calling Laya (BUILD-SPEC.md §5.2).

"It cannot detect a script it wasn't trained for and will return a confident wrong answer on it (measured: English checkpoint scored 0.10 on
Hindi and 0.00 on Khmer at >0.9 confidence). Route by detected script before calling, never by confidence after."

So applicability is decided from the TEXT, by a pure function with no model: the Unicode name of each letter gives its script (the first word
of the name: LATIN, TAMIL, DEVANAGARI, ARABIC, CJK...). Text is NFKC-normalised first so full-width and compatibility forms fold to their plain
letters. Only Latin and Tamil are routed to Laya (the two the spec names and the gold set will test); any other letter script, or no letters
at all, is "not applicable", which means the adapter is skipped, not that it is tried and judged by confidence afterwards. Unknown is not a value
(CLAUDE.md rule 3): a character whose script cannot be named is counted as its own script, so it can never pass as Latin.
"""

import unicodedata
from typing import Final

LATIN: Final = "LATIN"
TAMIL: Final = "TAMIL"
SUPPORTED_BY_LAYA: Final = frozenset({LATIN, TAMIL})
ENGLISH_CHECKPOINT: Final = "english"
MULTILINGUAL_CHECKPOINT: Final = "multilingual"


def letter_scripts(text: str) -> frozenset[str]:
    """The set of scripts of the letters in `text`. Digits, punctuation, spaces and combining marks do not count as letters."""
    scripts: set[str] = set()
    for ch in unicodedata.normalize("NFKC", text):
        if ch.isalpha():
            name = unicodedata.name(ch, "")
            scripts.add(name.split(" ", 1)[0] if name else f"UNNAMED-{unicodedata.category(ch)}")
    return frozenset(scripts)


def laya_checkpoint(text: str, language: str) -> str | None:
    """The Laya checkpoint for this text, or None if Laya must not be used for it.

    Latin letters only and an English hint -> the English checkpoint. Latin only with any other hint (romanised Tamil, for instance) -> the
    multilingual one. Any Tamil letter, alone or mixed with Latin -> the multilingual one (the original text is what is sent). Anything
    else -> None."""
    scripts = letter_scripts(text)
    if not scripts or not scripts <= SUPPORTED_BY_LAYA:
        return None
    if TAMIL in scripts:
        return MULTILINGUAL_CHECKPOINT
    hint = (language or "").strip().lower()
    return ENGLISH_CHECKPOINT if hint in {"en", "en-us", "en-gb", "en-in"} else MULTILINGUAL_CHECKPOINT
