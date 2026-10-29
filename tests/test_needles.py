"""The shared leak search (tests/needles.py) can fail: it finds planted needles in str and bytes, and it is not fooled by short digit runs inside random-looking ids."""

import pytest

from tests.needles import NEEDLES, assert_not_leaked, found


@pytest.mark.parametrize("needle", NEEDLES)
def test_every_needle_is_found_when_planted_in_text_and_in_bytes(needle):
    assert found(f"id=7f3a-{needle}-tail") == {needle}
    assert found(f"id=7f3a-{needle}-tail".encode()) == {needle}


def test_a_leak_fails_the_assertion_and_names_the_needle():
    with pytest.raises(AssertionError, match="leaked"):
        assert_not_leaked("checkpoint ... Asha ...")
    with pytest.raises(AssertionError, match="leaked"):
        assert_not_leaked(b"\x00\x01 98765 43210 \x02")


def test_random_ids_and_timestamps_with_short_digit_runs_are_not_a_leak():
    haystack = "1ef1980a-7b2c-6a98-8765-0e1980aa9876 2026-10-28T19:80:19+05:30 198 1980 98765 5 March"
    assert_not_leaked(haystack)  # the old short needles ("1980", "98765") would have failed here; the full values do not
    assert_not_leaked(haystack.encode())
