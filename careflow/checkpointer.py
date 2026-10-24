"""Our own asyncpg LangGraph checkpointer (BUILD-SPEC.md §7.2; CLAUDE.md rules 5, 6, 8; migration 0021). Package P2-8.

WHY OUR OWN. The official `langgraph-checkpoint-postgres` is built on psycopg 3 and creates its own tables outside our checksummed migration ledger, with no row-level security
and no origin tag. The owner decided (2026-10-24) on a saver over our existing stack (SQLAlchemy async over asyncpg) on tables from migration 0021 (forced RLS, append-only
history). No psycopg.

SEMANTICS FOLLOW `BaseCheckpointSaver` / `InMemorySaver` (langgraph-checkpoint 4.x), checked against the installed source and pinned by a conformance test that runs the same operations
against both and compares the results:
  * `aput`: the checkpoint (minus channel values) and its metadata are stored under `checkpoint_id`; the channel values go to one blob per (channel, new version), `empty` for a
    channel with no value; the parent is the `checkpoint_id` in the incoming config. ONE transaction: the blobs and the checkpoint row land together or not at all.
  * `aput_writes`: a regular write (idx >= 0) is stored once and a replay is a no-op; a special write (error, scheduled, interrupt, resume: negative idx from WRITES_IDX_MAP)
    REPLACES the earlier one.
  * `aget_tuple`: the checkpoint named by `checkpoint_id`, or the latest (the greatest id, compared as text with the "C" collation); channel values rebuilt from the blobs of its
    `channel_versions`; the pending writes in `writes_sort_key` order; the parent config.
  * `alist`: newest first; filters by thread, namespace, `checkpoint_id`, `before`, metadata, and `limit`.
  * `adelete_thread`: REFUSED. The history is append-only (migration 0021); erasure is a separate, audited procedure that does not exist yet.
  * `get_next_version`: the same scheme as InMemorySaver.

SCOPE. Every row carries a clinic and an origin, taken from `config["configurable"]` (`clinic_id`, `origin`): a write without them is refused (rule 8, no origin default). EVERY READ
IS ALSO SCOPED to that clinic (`clinic_id = :c`), because a queue worker holds its role in each clinic it serves and so row-level security alone would let it see two clinics' rows under
the same thread id; a read with no clinic is refused. Row-level security remains the enforcement; the clinic filter makes the saver correct for a multi-clinic caller. The saver runs each operation in its own transaction on a connection from `session`, which must
set the caller's verified claims (the same factory the graph nodes use) and COMMIT on a clean exit; that factory is the production session, which is not wired yet, so commit-visibility
is not proven here (the live test runs inside one rolled-back transaction).

IDEMPOTENCY (rule 5). Every row has a key: `cp:`, `blob:`, `w:` plus a SHA-256 of the natural key's parts (length-unambiguous: a thread id contains ':'). A replayed put is a no-op
(`ON CONFLICT DO NOTHING`).

The store behind it is a small protocol so the same semantics run offline over an in-memory store and live over Postgres.
"""

import hashlib
import json
import random
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
    writes_sort_key,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

ORIGINS: Final = ("live", "test", "eval", "canary")
EMPTY: Final = "empty"


class CheckpointerError(Exception):
    """Fixed words only; never state or a value."""


class HistoryIsAppendOnly(CheckpointerError):
    """`delete_thread` is refused: checkpoint history is append-only (migration 0021)."""


@dataclass(frozen=True)
class Scope:
    clinic_id: uuid.UUID
    origin: str


Typed = tuple[str, bytes]


@dataclass(frozen=True)
class StoredCheckpoint:
    thread_id: str
    checkpoint_ns: str
    checkpoint_id: str
    parent_checkpoint_id: str | None
    checkpoint: Typed
    metadata: Typed


@dataclass(frozen=True)
class StoredWrite:
    task_id: str
    idx: int
    channel: str
    value: Typed
    task_path: str


class CheckpointStore(Protocol):
    async def put_checkpoint(
        self, scope: Scope, thread_id: str, ns: str, checkpoint_id: str, parent_id: str | None, checkpoint: Typed, metadata: Typed,
        blobs: Sequence[tuple[str, str, Typed]],
    ) -> None: ...

    async def put_writes(self, scope: Scope, thread_id: str, ns: str, checkpoint_id: str, task_id: str, task_path: str, rows: Sequence[tuple[int, str, Typed]]) -> None: ...

    async def get_checkpoint(self, clinic_id: uuid.UUID, thread_id: str, ns: str, checkpoint_id: str | None) -> StoredCheckpoint | None: ...

    async def list_checkpoints(
        self, clinic_id: uuid.UUID, thread_id: str | None, ns: str | None, checkpoint_id: str | None, before_id: str | None
    ) -> list[StoredCheckpoint]: ...

    async def get_blobs(self, clinic_id: uuid.UUID, thread_id: str, ns: str, versions: dict[str, Any]) -> dict[str, Typed]: ...

    async def get_writes(self, clinic_id: uuid.UUID, thread_id: str, ns: str, checkpoint_id: str) -> list[StoredWrite]: ...


def _scope(config: RunnableConfig) -> Scope:
    conf = config["configurable"]
    clinic, origin = conf.get("clinic_id"), conf.get("origin")
    if origin not in ORIGINS:
        raise CheckpointerError("a checkpoint write needs an origin (live, test, eval or canary) in the run config")
    try:
        return Scope(uuid.UUID(str(clinic)), origin)
    except (ValueError, TypeError):
        raise CheckpointerError("a checkpoint write needs a clinic_id in the run config") from None


def _read_clinic(config: RunnableConfig | None) -> uuid.UUID:
    """The clinic a READ is for. A read without one is refused: unknown is not a value."""
    clinic = (config or {}).get("configurable", {}).get("clinic_id")
    try:
        return uuid.UUID(str(clinic))
    except (ValueError, TypeError):
        raise CheckpointerError("a checkpoint read needs a clinic_id in the run config") from None


def idempotency_key(prefix: str, *parts: object) -> str:
    """`cp:`, `blob:` or `w:` plus a hash of the parts: unambiguous even though a thread id contains ':' (a plain join of 'a:b' + '' and 'a' + 'b:' would collide)."""
    return f"{prefix}:{hashlib.sha256(json.dumps([str(p) for p in parts]).encode()).hexdigest()[:48]}"


def _next_version(current: Any) -> str:
    if current is None:
        current_v = 0
    elif isinstance(current, int):
        current_v = current
    else:
        current_v = int(str(current).split(".")[0])
    return f"{current_v + 1:032}.{random.random():016}"


class PostgresCheckpointSaver(BaseCheckpointSaver[str]):
    def __init__(self, store: CheckpointStore, *, serde: Any = None) -> None:
        super().__init__(serde=serde)
        self._store = store

    # ---- writes ----------------------------------------------------------------------------------------------------------------------------

    async def aput(self, config: RunnableConfig, checkpoint: Checkpoint, metadata: CheckpointMetadata, new_versions: ChannelVersions) -> RunnableConfig:
        scope = _scope(config)
        c = checkpoint.copy()
        thread_id = config["configurable"]["thread_id"]
        ns = config["configurable"]["checkpoint_ns"]
        values: dict[str, Any] = c.pop("channel_values")  # type: ignore[misc]
        blobs = [
            (channel, str(version), self.serde.dumps_typed(values[channel]) if channel in values else (EMPTY, b""))
            for channel, version in new_versions.items()
        ]
        await self._store.put_checkpoint(
            scope, thread_id, ns, checkpoint["id"], config["configurable"].get("checkpoint_id"), self.serde.dumps_typed(c),
            self.serde.dumps_typed(get_checkpoint_metadata(config, metadata)), blobs,
        )
        return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ns, "checkpoint_id": checkpoint["id"]}}

    async def aput_writes(self, config: RunnableConfig, writes: Sequence[tuple[str, Any]], task_id: str, task_path: str = "") -> None:
        scope = _scope(config)
        conf = config["configurable"]
        rows = [(WRITES_IDX_MAP.get(channel, idx), channel, self.serde.dumps_typed(value)) for idx, (channel, value) in enumerate(writes)]
        await self._store.put_writes(scope, conf["thread_id"], conf.get("checkpoint_ns", ""), conf["checkpoint_id"], task_id, task_path, rows)

    async def adelete_thread(self, thread_id: str) -> None:
        raise HistoryIsAppendOnly("checkpoint history is append-only; erasing a thread is a separate, audited procedure that does not exist yet")

    # ---- reads -----------------------------------------------------------------------------------------------------------------------------

    async def _tuple(self, clinic_id: uuid.UUID, stored: StoredCheckpoint) -> CheckpointTuple:
        checkpoint_: Checkpoint = self.serde.loads_typed(stored.checkpoint)
        blobs = await self._store.get_blobs(clinic_id, stored.thread_id, stored.checkpoint_ns, checkpoint_["channel_versions"])
        values = {channel: self.serde.loads_typed(blob) for channel, blob in blobs.items() if blob[0] != EMPTY}
        writes = await self._store.get_writes(clinic_id, stored.thread_id, stored.checkpoint_ns, stored.checkpoint_id)
        ordered = sorted(writes, key=lambda w: writes_sort_key(w.task_path, w.task_id, w.idx))
        return CheckpointTuple(
            config={"configurable": {"thread_id": stored.thread_id, "checkpoint_ns": stored.checkpoint_ns, "checkpoint_id": stored.checkpoint_id}},
            checkpoint={**checkpoint_, "channel_values": values},
            metadata=self.serde.loads_typed(stored.metadata),
            pending_writes=[(w.task_id, w.channel, self.serde.loads_typed(w.value)) for w in ordered],
            parent_config=(
                {"configurable": {"thread_id": stored.thread_id, "checkpoint_ns": stored.checkpoint_ns, "checkpoint_id": stored.parent_checkpoint_id}}
                if stored.parent_checkpoint_id
                else None
            ),
        )

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        clinic = _read_clinic(config)
        conf = config["configurable"]
        stored = await self._store.get_checkpoint(clinic, conf["thread_id"], conf.get("checkpoint_ns", ""), get_checkpoint_id(config))
        if stored is None:
            return None
        found = await self._tuple(clinic, stored)
        if get_checkpoint_id(config):  # InMemorySaver returns the caller's own config when it named the checkpoint
            return CheckpointTuple(config, found.checkpoint, found.metadata, found.parent_config, found.pending_writes)
        return found

    async def alist(
        self, config: RunnableConfig | None, *, filter: dict[str, Any] | None = None, before: RunnableConfig | None = None, limit: int | None = None
    ) -> AsyncIterator[CheckpointTuple]:
        clinic = _read_clinic(config)  # listing across clinics is refused: a list is always for one clinic
        assert config is not None
        thread_id = config["configurable"].get("thread_id")
        ns = config["configurable"].get("checkpoint_ns")
        before_id = get_checkpoint_id(before) if before else None
        rows = await self._store.list_checkpoints(clinic, thread_id, ns, get_checkpoint_id(config), before_id)
        remaining = limit
        for stored in rows:  # newest first
            metadata = self.serde.loads_typed(stored.metadata)
            if filter and not all(value == metadata.get(key) for key, value in filter.items()):
                continue
            if remaining is not None and remaining <= 0:
                break
            if remaining is not None:
                remaining -= 1
            yield await self._tuple(clinic, stored)

    def get_next_version(self, current: str | None, channel: None) -> str:  # type: ignore[override]
        return _next_version(current)


# ---- the in-memory store (offline tests) -----------------------------------------------------------------------------------------------------


@dataclass
class InMemoryCheckpointStore:
    """The same contract over dicts, with the same append-only and replace-only-special-writes rules as migration 0021. Keys include the clinic, as the primary keys do."""

    checkpoints: dict[tuple[str, str, str, str], tuple[Scope, str | None, Typed, Typed]] = field(default_factory=dict)
    blobs: dict[tuple[str, str, str, str, str], tuple[Scope, Typed]] = field(default_factory=dict)
    writes: dict[tuple[str, str, str, str, str, int], tuple[Scope, str, Typed, str]] = field(default_factory=dict)
    fail_next_put: int = 0
    keys: list[str] = field(default_factory=list)  # every idempotency key written, for tests

    async def put_checkpoint(self, scope, thread_id, ns, checkpoint_id, parent_id, checkpoint, metadata, blobs) -> None:
        if self.fail_next_put:
            self.fail_next_put -= 1
            raise RuntimeError("the database went away mid-put")
        c = str(scope.clinic_id)
        for channel, version, blob in blobs:
            self.blobs.setdefault((c, thread_id, ns, channel, version), (scope, blob))
            self.keys.append(idempotency_key("blob", c, thread_id, ns, channel, version))
        self.checkpoints.setdefault((c, thread_id, ns, checkpoint_id), (scope, parent_id, checkpoint, metadata))
        self.keys.append(idempotency_key("cp", c, thread_id, ns, checkpoint_id))

    async def put_writes(self, scope, thread_id, ns, checkpoint_id, task_id, task_path, rows) -> None:
        c = str(scope.clinic_id)
        for idx, channel, value in rows:
            key = (c, thread_id, ns, checkpoint_id, task_id, idx)
            if idx >= 0 and key in self.writes:
                continue  # a replayed regular write is a no-op
            self.writes[key] = (scope, channel, value, task_path)  # a special write replaces the earlier one
            self.keys.append(idempotency_key("w", *key))

    def _stored(self, key: tuple[str, str, str, str]) -> StoredCheckpoint:
        _, parent, checkpoint, metadata = self.checkpoints[key]
        return StoredCheckpoint(key[1], key[2], key[3], parent, checkpoint, metadata)

    async def get_checkpoint(self, clinic_id, thread_id, ns, checkpoint_id) -> StoredCheckpoint | None:
        c = str(clinic_id)
        if checkpoint_id is not None:
            key = (c, thread_id, ns, checkpoint_id)
            return self._stored(key) if key in self.checkpoints else None
        ids = [k for k in self.checkpoints if k[:3] == (c, thread_id, ns)]
        return self._stored(max(ids, key=lambda k: k[3])) if ids else None

    async def list_checkpoints(self, clinic_id, thread_id, ns, checkpoint_id, before_id) -> list[StoredCheckpoint]:
        c = str(clinic_id)
        keys = [k for k in self.checkpoints if k[0] == c and (thread_id is None or k[1] == thread_id) and (ns is None or k[2] == ns)]
        keys = [k for k in keys if (checkpoint_id is None or k[3] == checkpoint_id) and (before_id is None or k[3] < before_id)]
        by_thread: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
        for k in keys:
            by_thread[k[1]].append(k)
        ordered = [k for t in by_thread for k in sorted(by_thread[t], key=lambda k: (k[2], k[3]), reverse=True)]
        return [self._stored(k) for k in ordered]

    async def get_blobs(self, clinic_id, thread_id, ns, versions) -> dict[str, Typed]:
        out = {}
        for channel, version in versions.items():
            found = self.blobs.get((str(clinic_id), thread_id, ns, channel, str(version)))
            if found is not None:
                out[channel] = found[1]
        return out

    async def get_writes(self, clinic_id, thread_id, ns, checkpoint_id) -> list[StoredWrite]:
        c = str(clinic_id)
        return [
            StoredWrite(k[4], k[5], channel, value, path)
            for k, (_, channel, value, path) in self.writes.items()
            if k[:4] == (c, thread_id, ns, checkpoint_id)
        ]


# ---- the Postgres store ------------------------------------------------------------------------------------------------------------------------


class SqlCheckpointStore:
    """Over `agents.graph_checkpoints`, `graph_checkpoint_blobs` and `graph_checkpoint_writes` (migration 0021). `session` yields a connection inside a transaction with the caller's
    verified claims set, and commits on a clean exit; each operation is its own transaction."""

    def __init__(self, session: Callable[[], AbstractAsyncContextManager[AsyncConnection]]) -> None:
        self._session = session

    async def put_checkpoint(self, scope, thread_id, ns, checkpoint_id, parent_id, checkpoint, metadata, blobs) -> None:
        async with self._session() as conn:
            for channel, version, (blob_type, blob) in blobs:
                await conn.execute(
                    text(
                        "insert into agents.graph_checkpoint_blobs (clinic_id, origin, thread_id, checkpoint_ns, channel, version, blob_type, blob, idempotency_key) "
                        "values (:c, :o, :t, :ns, :ch, :v, :bt, :b, :k) on conflict (clinic_id, thread_id, checkpoint_ns, channel, version) do nothing"
                    ),
                    {"c": scope.clinic_id, "o": scope.origin, "t": thread_id, "ns": ns, "ch": channel, "v": version, "bt": blob_type, "b": blob,
                     "k": idempotency_key("blob", scope.clinic_id, thread_id, ns, channel, version)},
                )
            await conn.execute(
                text(
                    "insert into agents.graph_checkpoints (clinic_id, origin, thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, checkpoint_type, checkpoint, "
                    "metadata_type, metadata, idempotency_key) values (:c, :o, :t, :ns, :id, :pid, :ct, :cp, :mt, :m, :k) "
                    "on conflict (clinic_id, thread_id, checkpoint_ns, checkpoint_id) do nothing"
                ),
                {"c": scope.clinic_id, "o": scope.origin, "t": thread_id, "ns": ns, "id": checkpoint_id, "pid": parent_id, "ct": checkpoint[0], "cp": checkpoint[1],
                 "mt": metadata[0], "m": metadata[1], "k": idempotency_key("cp", scope.clinic_id, thread_id, ns, checkpoint_id)},
            )

    async def put_writes(self, scope, thread_id, ns, checkpoint_id, task_id, task_path, rows) -> None:
        async with self._session() as conn:
            for idx, channel, (write_type, blob) in rows:
                tail = (
                    "do update set channel = excluded.channel, write_type = excluded.write_type, write_blob = excluded.write_blob, task_path = excluded.task_path"
                    if idx < 0
                    else "do nothing"
                )
                await conn.execute(
                    text(
                        "insert into agents.graph_checkpoint_writes (clinic_id, origin, thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, write_type, write_blob, "
                        "task_path, idempotency_key) values (:c, :o, :t, :ns, :id, :task, :idx, :ch, :wt, :wb, :tp, :k) "
                        f"on conflict (clinic_id, thread_id, checkpoint_ns, checkpoint_id, task_id, idx) {tail}"
                    ),
                    {"c": scope.clinic_id, "o": scope.origin, "t": thread_id, "ns": ns, "id": checkpoint_id, "task": task_id, "idx": idx, "ch": channel, "wt": write_type,
                     "wb": blob, "tp": task_path, "k": idempotency_key("w", scope.clinic_id, thread_id, ns, checkpoint_id, task_id, idx)},
                )

    @staticmethod
    def _row(r) -> StoredCheckpoint:
        return StoredCheckpoint(
            r.thread_id, r.checkpoint_ns, r.checkpoint_id, r.parent_checkpoint_id, (r.checkpoint_type, bytes(r.checkpoint)), (r.metadata_type, bytes(r.metadata_blob))
        )

    async def get_checkpoint(self, clinic_id, thread_id, ns, checkpoint_id) -> StoredCheckpoint | None:
        where = "clinic_id = :c and thread_id = :t and checkpoint_ns = :ns" + (" and checkpoint_id = :id" if checkpoint_id is not None else "")
        async with self._session() as conn:
            row = (
                await conn.execute(
                    text(
                        "select thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, checkpoint_type, checkpoint, metadata_type, metadata as metadata_blob "
                        f"from agents.graph_checkpoints where {where} order by checkpoint_id desc limit 1"
                    ),
                    {"c": clinic_id, "t": thread_id, "ns": ns, "id": checkpoint_id},
                )
            ).first()
        return None if row is None else self._row(row)

    async def list_checkpoints(self, clinic_id, thread_id, ns, checkpoint_id, before_id) -> list[StoredCheckpoint]:
        clauses, params = ["clinic_id = :c"], {"c": clinic_id}
        for column, name, value in (("thread_id", "t", thread_id), ("checkpoint_ns", "ns", ns), ("checkpoint_id", "id", checkpoint_id)):
            if value is not None:
                clauses.append(f"{column} = :{name}")
                params[name] = value
        if before_id is not None:
            clauses.append("checkpoint_id < :before")
            params["before"] = before_id
        async with self._session() as conn:
            rows = (
                await conn.execute(
                    text(
                        "select thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, checkpoint_type, checkpoint, metadata_type, metadata as metadata_blob "
                        f"from agents.graph_checkpoints where {' and '.join(clauses)} order by thread_id, checkpoint_ns desc, checkpoint_id desc"
                    ),
                    params,
                )
            ).all()
        return [self._row(r) for r in rows]

    async def get_blobs(self, clinic_id, thread_id, ns, versions) -> dict[str, Typed]:
        if not versions:
            return {}
        async with self._session() as conn:
            rows = (
                await conn.execute(
                    text(
                        "select channel, version, blob_type, blob from agents.graph_checkpoint_blobs where clinic_id = :c and thread_id = :t and checkpoint_ns = :ns "
                        "and channel = any(cast(:chs as text[])) and version = any(cast(:vs as text[]))"
                    ),
                    {"c": clinic_id, "t": thread_id, "ns": ns, "chs": list(versions), "vs": [str(v) for v in versions.values()]},
                )
            ).all()
        wanted = {(channel, str(version)) for channel, version in versions.items()}
        return {r.channel: (r.blob_type, bytes(r.blob)) for r in rows if (r.channel, r.version) in wanted}

    async def get_writes(self, clinic_id, thread_id, ns, checkpoint_id) -> list[StoredWrite]:
        async with self._session() as conn:
            rows = (
                await conn.execute(
                    text(
                        "select task_id, idx, channel, write_type, write_blob, task_path from agents.graph_checkpoint_writes "
                        "where clinic_id = :c and thread_id = :t and checkpoint_ns = :ns and checkpoint_id = :id"
                    ),
                    {"c": clinic_id, "t": thread_id, "ns": ns, "id": checkpoint_id},
                )
            ).all()
        return [StoredWrite(r.task_id, r.idx, r.channel, (r.write_type, bytes(r.write_blob)), r.task_path) for r in rows]
