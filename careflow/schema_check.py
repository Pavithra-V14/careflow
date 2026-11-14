"""A startup check that the database has the migrations this code needs (pre-launch: deploy ordering). The migration ledger lives in a schema the application role cannot read (careflow/migrate.py), so the check
looks for objects the newest code depends on, through the catalog, which every role can read. A database that is behind makes the app REFUSE TO START with a plain message, instead of failing on the first
request (rules.py selects `token_sets`, which exists only from 0076, so every screening would otherwise error). Migrate first, then start: docs/first-run-guide.md.

When a migration adds something the code depends on, add one line to REQUIRED. The test in tests/test_schema_check.py proves each named object is created by the migration it names.
"""

from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

# (migration prefix, catalog name of an object that migration creates, how to look it up: "proc" or "rel")
REQUIRED: Final = (
    ("0072", "kb.decide_red_flag_rule(uuid,text,text)", "proc"),
    ("0074", "clinical.medications", "rel"),
    ("0075", "ops.record_session_spend(uuid,text,text,text,numeric,numeric,text)", "proc"),
    ("0076", "kb.author_red_flag_rule_draft(uuid,text,text[],jsonb,text)", "proc"),
    ("0077", "kb.withdraw_red_flag_draft(uuid,text)", "proc"),
    ("0078", "kb.red_flag_rule_decisions_one_per_person_kind", "rel"),
    ("0080", "iam.file_public_grievance(text,bytea,smallint,uuid,text)", "proc"),
    ("0082", "msg.patient_post_message(uuid,uuid,bytea,smallint,text)", "proc"),
    ("0083", "sched.patient_book_slot(uuid,text)", "proc"),
)


class SchemaOutOfDate(RuntimeError):
    """The database has not applied a migration this code needs."""


async def check(engine: AsyncEngine) -> None:
    """Raise SchemaOutOfDate naming the first missing migration. A connection fault is NOT swallowed: it propagates as itself."""
    async with engine.connect() as conn:
        for prefix, name, kind in REQUIRED:
            sql = "select to_regprocedure(:n) is not null" if kind == "proc" else "select to_regclass(:n) is not null"
            if not (await conn.execute(text(sql), {"n": name})).scalar_one():
                raise SchemaOutOfDate(f"the database has not applied migration {prefix} ({name} is missing): run the migrations (python -m careflow.migrate) before starting the app")
