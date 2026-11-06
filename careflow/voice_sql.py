"""The SQL halves of the voice channel (migration 0045), on the CALLER's own transaction under row-level security. A patient account's own preference and sessions; the transcript sink inserts ciphertext only.

All inserts are PLAIN inserts (no RETURNING on the chunk table, no ON CONFLICT anywhere): the chunk table has no SELECT right for the application role and PostgreSQL also checks SELECT policies for an
INSERT with ON CONFLICT (see careflow/phase4_sql.py). A repeat of a key is a unique violation, caught and treated as a replay.
"""

import uuid

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from careflow.care_gap_sql import sqlstate
from careflow.crypto import DemographicVault
from careflow.messages import encrypt_body


async def get_preference(conn: AsyncConnection, account_id: uuid.UUID) -> bool:
    """The account's own voice_enabled; False when no row exists (off by default, unknown is not on)."""
    row = (await conn.execute(text("select voice_enabled from msg.voice_preferences where account_id = :a"), {"a": account_id})).first()
    return bool(row[0]) if row is not None else False


async def set_preference(conn: AsyncConnection, account_id: uuid.UUID, origin: str, enabled: bool) -> None:
    exists = (await conn.execute(text("select 1 from msg.voice_preferences where account_id = :a"), {"a": account_id})).first()
    if exists is None:
        await conn.execute(text("insert into msg.voice_preferences (account_id, origin, voice_enabled) values (:a, :o, :e)"), {"a": account_id, "o": origin, "e": enabled})
    else:
        await conn.execute(text("update msg.voice_preferences set voice_enabled = :e where account_id = :a"), {"a": account_id, "e": enabled})


async def open_session(
    conn: AsyncConnection, *, clinic_id: uuid.UUID, account_id: uuid.UUID, origin: str, workflow: str, retention_days: int, idempotency_key: str
) -> tuple[uuid.UUID, bool]:
    """(session id, created). A repeat of the key returns the first session. The database refuses (42501) unless the account's own preference is on, the account is an active patient account and it is bound to a
    patient record of this clinic."""
    try:
        async with conn.begin_nested():
            row = (
                await conn.execute(
                    text(
                        "insert into msg.voice_sessions (clinic_id, account_id, origin, workflow, transcript_retention_days, idempotency_key) "
                        "values (:c, :a, :o, :w, :d, :k) returning id"
                    ),
                    {"c": clinic_id, "a": account_id, "o": origin, "w": workflow, "d": retention_days, "k": idempotency_key},
                )
            ).one()
        return row.id, True
    except IntegrityError as exc:
        if sqlstate(exc) != "23505":
            raise
    existing = (await conn.execute(text("select id from msg.voice_sessions where clinic_id = :c and idempotency_key = :k and account_id = :a"), {"c": clinic_id, "k": idempotency_key, "a": account_id})).first()
    if existing is None:
        raise LookupError("the key is held by a session that is not yours")
    return existing.id, False


class SqlTranscriptSink:
    """Implements careflow.voice.TranscriptSink: each chunk is encrypted (the table never sees plaintext) and inserted with a per-chunk key. A replayed chunk is a no-op."""

    def __init__(self, conn: AsyncConnection, vault: DemographicVault, *, clinic_id: uuid.UUID, key_version: int) -> None:
        self._conn, self._vault, self._clinic, self._key_version = conn, vault, clinic_id, key_version
        self._seq: dict[uuid.UUID, int] = {}

    async def append(self, session_id: uuid.UUID, chunk: str) -> None:
        seq = self._seq.get(session_id, 0)
        body = encrypt_body(
            self._vault, chunk, clinic_id=self._clinic, thread_id=session_id, key_version=self._key_version, message_id=uuid.uuid5(session_id, f"chunk:{seq}")
        )
        try:
            async with self._conn.begin_nested():
                await self._conn.execute(
                    text(
                        "insert into msg.voice_transcript_chunks (session_id, clinic_id, seq, body_ct, body_key_version, idempotency_key) values (:s, :c, :q, :b, :v, :k)"
                    ),
                    {"s": session_id, "c": self._clinic, "q": seq, "b": body.body_ct, "v": body.body_key_version, "k": f"voice-chunk:{session_id}:{seq}"},
                )
        except IntegrityError as exc:
            if sqlstate(exc) != "23505":
                raise  # a repeated (session, seq) or key is a replay
        self._seq[session_id] = seq + 1
