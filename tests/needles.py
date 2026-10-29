"""The strings that must never appear in a checkpoint, a repr or a stored row, and one search that proves it can find them.

WHY FULL VALUES. The old checks searched for short needles ("1980", "98765"). A serialized checkpoint holds random ids and timestamps, so a short digit run can appear by chance and fail
a run that leaked nothing (found by a shuffled offline run, 2026-10-28). These are the DISTINCTIVE values from the synthetic letter (tests/test_referral.py LETTER): every date form,
every phone form, the name and the letter's sentinel. A chance match of any of them in random bytes is not a practical concern.

EVERY SEARCH HAS A POSITIVE CONTROL. `assert_not_leaked` first plants each needle in a copy of the haystack and requires the search to find it (so a typo or a type mismatch makes the
test fail, not pass vacuously), and only then asserts that the real haystack holds none.
"""

NEEDLES = (
    "SENTINEL-LETTER",
    "Asha",
    "Rao",
    "5 March 1980",
    "05/03/1980",
    "1980-03-05",
    "98765 43210",
    "9876543210",
)


def _as(haystack, needle: str):
    return needle.encode() if isinstance(haystack, bytes) else needle


def found(haystack, needles=NEEDLES) -> set[str]:
    """The needles (always returned as str) present in `haystack`, which may be str or bytes."""
    return {n for n in needles if _as(haystack, n) in haystack}


def assert_not_leaked(haystack, needles=NEEDLES, *, label: str = "") -> None:
    separator = b" " if isinstance(haystack, bytes) else " "
    planted = haystack + separator + separator.join(_as(haystack, n) for n in needles)
    assert found(planted, needles) == set(needles), "positive control failed: the search cannot find a planted needle"
    assert found(haystack, needles) == set(), f"{label} leaked: {sorted(found(haystack, needles))}"
