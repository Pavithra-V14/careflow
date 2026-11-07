"""Regression for CI run 42201883307: the alert sink must not use ON CONFLICT (row-level security then also checks the SELECT policy, which the writing role deliberately fails). A repeat of the same key is
caught from a plain insert and is a no-op; any other integrity error is real. Offline."""

import pytest
from sqlalchemy.exc import IntegrityError

from careflow.phase4_sql import SqlAlertSink

pytestmark = pytest.mark.asyncio
CLINIC = "00000000-0000-0000-0000-000000000001"


class _Orig(Exception):
    def __init__(self, sqlstate):
        super().__init__("x")
        self.sqlstate = sqlstate


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Conn:
    def __init__(self, error=None):
        self.error, self.sql = error, []

    def begin_nested(self):
        return _Nested()

    async def execute(self, statement, params=None):
        self.sql.append(str(statement))
        if self.error:
            raise IntegrityError("stmt", params, self.error)


async def raise_one(conn):
    await SqlAlertSink(conn).raise_alert_fields(clinic_id=CLINIC, origin="test", alert_class="dead_letters", audience_role="admin", count=1, ref="r", idempotency_key="k")


async def test_the_insert_has_no_on_conflict_clause():
    conn = _Conn()
    await raise_one(conn)
    assert len(conn.sql) == 1 and "on conflict" not in conn.sql[0].lower()


async def test_a_repeated_key_is_a_no_op():
    await raise_one(_Conn(_Orig("23505")))


async def test_any_other_integrity_error_is_raised():
    with pytest.raises(IntegrityError):
        await raise_one(_Conn(_Orig("23514")))


def test_no_sql_text_in_the_application_calls_auth_uid_by_name():
    """CI run 42185193684: the restricted role cannot call auth.uid() ("permission denied for schema auth"; BUILD-SPEC.md 18.1). Policies may use it; application SQL must pass the id from the verified claims."""
    import re
    from pathlib import Path

    for path in sorted((Path(__file__).resolve().parent.parent / "careflow").glob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"""text\(\s*f?["'].*auth\.uid\(\)""", line):
                raise AssertionError(f"{path.name}:{number} calls auth.uid() in application SQL")
