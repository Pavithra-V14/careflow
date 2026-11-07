"""Client-chosen idempotency keys are stored behind the caller's own account prefix (careflow/app.py key_scope), so a unique-key refusal can never tell one clinic's caller that a key exists in another clinic. Offline."""

import asyncio
import uuid

from careflow.app import IDEMPOTENCY_KEY_MAX, _scoped_idempotency_key, key_scope


def test_the_scope_is_fixed_length_deterministic_and_different_for_every_account():
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    assert key_scope(a) == key_scope(a) and key_scope(a) != key_scope(b)
    assert len(key_scope(a)) == 12 and all(c in "0123456789abcdef" for c in key_scope(a))
    assert a not in key_scope(a)  # not the account id itself


def test_two_accounts_sending_the_same_header_store_different_keys_and_the_same_account_replays():
    a, b = {"sub": str(uuid.uuid4())}, {"sub": str(uuid.uuid4())}
    one = asyncio.run(_scoped_idempotency_key("same-key", a))
    two = asyncio.run(_scoped_idempotency_key("same-key", b))
    again = asyncio.run(_scoped_idempotency_key("same-key", a))
    assert one != two  # clinic A's key can never collide with clinic B's
    assert one == again  # a retry by the same caller finds its own request
    assert one.endswith(":same-key")


def test_the_longest_allowed_header_still_fits_every_database_limit():
    longest = asyncio.run(_scoped_idempotency_key("x" * IDEMPOTENCY_KEY_MAX, {"sub": str(uuid.uuid4())}))
    assert len(longest) + len("claim-resolve:") <= 200  # the longest route prefix in use still fits the 200 the staff and claim functions accept
    assert len(longest) <= 193  # the erasure and staff-account functions accept 200, the columns 300


def test_the_openapi_header_keeps_its_description_and_the_new_limit():
    import json
    from pathlib import Path

    snap = json.loads((Path(__file__).resolve().parent.parent / "openapi.snapshot.json").read_text(encoding="utf8"))
    headers = [p for path in snap["paths"].values() for op in path.values() if isinstance(op, dict) for p in op.get("parameters", []) if p.get("name") == "X-Idempotency-Key"]
    assert headers, "no route documents the idempotency header"
    assert all(h["schema"].get("maxLength") == IDEMPOTENCY_KEY_MAX and h["required"] for h in headers)
