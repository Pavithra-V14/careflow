"""A guard against the incident of PR #9 (2026-10-30): a live test COMMITTED an encrypted message on key version 1 into the shared CI database. Messages are append-only for every role, so it could not
be deleted, and the key-rotation tests assert that NO committed message exists on version 1, so all of them failed until a person erased it by hand.

The guard has two halves, both cheap and honest about what they cannot see:
1. STATIC (this file): a live test file that writes `msg.messages` directly must be one of the files known to run inside a NEVER-COMMITTED transaction (an outer transaction rolled back at the end,
   or the rotation tests' own rolled-back transactions), and must still contain that rollback. A new file that writes messages is not on the list and fails here, with the reason. This sees direct
   SQL only; it cannot see a message written by calling a route.
2. RUNTIME (tests/test_api_role_matrix.py, the `ids` fixture): the committed-fixture matrix drives routes, so it checks at teardown that no message exists in its clinics and fails loudly, naming
   this incident, if one does. That catches the route-driven case (a matrix case that reaches a message-writing route) in the run that causes it, instead of in the rotation tests of a later run.
Anything that commits messages some other way (a script, a new harness) is outside both; the rotation tests' own precondition is the last line of defence.
"""

import re
from pathlib import Path

TESTS = Path(__file__).resolve().parent
WRITES_MESSAGES = re.compile(r"insert\s+into\s+msg\.messages", re.IGNORECASE)
LIVE_MARKER = re.compile(r"DATABASE_URL")  # a file that can reach a real database at all (offline fakes only mention the SQL as text)

# Files that write msg.messages and are KNOWN to do it only inside a transaction that is never committed. Adding a file here is a review decision: say why.
NEVER_COMMITTED = {
    "test_api_screen_message_live.py": "one shared connection in an outer transaction, rolled back at the end",
    "test_rls_msg_live.py": "the `db` fixture: every test in a rolled-back transaction",
    "test_rls_screenings_live.py": "the `db` fixture: every test in a rolled-back transaction",
    "test_rules_live.py": "the `db` fixture: every test in a rolled-back transaction",
    "test_rotation_live.py": "rolled-back transactions; its precondition requires an empty database of committed version-1 messages",
}


# Routes that write a message. A file that calls one must be a never-committed harness, or the committed-fixture matrix (whose teardown guard fails if a message was committed).
MESSAGE_WRITING_ROUTES = re.compile(r"offers/send|OFFER_SEND")
ROUTE_DRIVERS = {
    "test_api_offers_live.py": "an outer transaction, rolled back at the end",
    "test_api_role_matrix.py": "its cases reach the send route only while the clinician settings are unset (503, no write); the `ids` teardown fails if any message was committed",
}


def test_every_live_file_that_calls_a_message_writing_route_is_a_known_harness_and_the_matrix_keeps_its_teardown_guard():
    callers = {p.name for p in TESTS.glob("test_*.py") if p.name != Path(__file__).name and MESSAGE_WRITING_ROUTES.search(p.read_text(encoding="utf-8"))}
    unknown = callers - set(ROUTE_DRIVERS)
    assert not unknown, f"{sorted(unknown)} call a message-writing route: run them in a never-committed harness and list them in ROUTE_DRIVERS (see this file's docstring)"
    assert "rollback()" in (TESTS / "test_api_offers_live.py").read_text(encoding="utf-8")
    assert "_assert_no_committed_messages" in (TESTS / "test_api_role_matrix.py").read_text(encoding="utf-8")  # the runtime half


def test_every_live_file_that_writes_messages_directly_is_a_known_never_committed_harness_that_still_rolls_back():
    writers = {p.name for p in TESTS.glob("test_*.py") if p.name != Path(__file__).name and WRITES_MESSAGES.search(p.read_text(encoding="utf-8")) and LIVE_MARKER.search(p.read_text(encoding="utf-8"))}
    unknown = writers - set(NEVER_COMMITTED)
    assert not unknown, (
        f"{sorted(unknown)} write msg.messages from a live test but are not a known never-committed harness. msg.messages is append-only, so a committed row can never be deleted, and it breaks the "
        "key-rotation tests' precondition for the whole shared database. Use an outer transaction that is rolled back (see tests/test_api_screen_message_live.py) and list the file in NEVER_COMMITTED."
    )
    for name in NEVER_COMMITTED:
        source = (TESTS / name).read_text(encoding="utf-8")
        assert "rollback()" in source, f"{name} is listed as never-committed but no longer rolls anything back"
    stale = set(NEVER_COMMITTED) - writers
    assert not stale, f"{sorted(stale)} are listed but no longer write messages directly: remove them from NEVER_COMMITTED"
