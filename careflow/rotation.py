"""Manual key rotation for demographics and identity tokens (BUILD-SPEC.md §17.2; CLAUDE.md rules 5, 6, 7, 10).

`rotate_key_version` moves every patient from one `key_version` to another:

    for each (clinic, origin) that still has a column on the old version:
        repeat, one SAVEPOINT per batch:
            lock up to `batch_size` patients that still have a ciphertext on the old version
            for each: decrypt every ciphertext under its own recorded version        (DemographicVault)
                      re-encrypt the old-version columns under the new version, and check it reads back
                      derive the identity tokens under the new version               (mpi.derive_tokens)
            rewrite the ciphertexts and their key_version columns
            upsert the new-version tokens beside the old ones                          (both coexist)
            write one ops.key_rotation_batches row carrying the batch's idempotency_key
        (the caller's transaction is committed between batches when `commit_each_batch` is set)

Why it is safe to stop, crash or run twice
  * The selection predicate IS the progress marker: a patient is picked up only while a ciphertext still carries the
    old key_version, so a re-run resumes exactly where the last one stopped and never touches finished rows.
  * A batch is a savepoint: if one patient fails to decrypt or round-trip, that whole batch rolls back and the run
    raises. Batches already committed stay valid, because old and new versions coexist and both keys are still loaded.
  * Tokens upsert on (patient_id, kind, key_version); the old-version tokens are never deleted, so identity lookups
    keep working under either version for the whole transition.
  * The ledger row's idempotency_key is derived from (versions, clinic, origin, first and last patient id). It is an
    audit record written in the same savepoint as the batch, never what decides whether work is skipped.

Everything is validated before the first database statement: an unknown or equal key version aborts the run with
nothing read and nothing written. Unknown is not a value: a ciphertext that does not authenticate is an error that
stops the run, never skipped, never "assumed fine".

Message bodies (`msg.messages`, migration 0011) rotate through a SEPARATE entry point, `rotate_message_bodies`, and are
counted by `message_version_usage`. Both refuse to run unless the connection bypasses row-level security (the migrator),
because the application role cannot UPDATE messages at all and, under RLS, would see only some topics: a count taken
that way would be silently low, and "unknown is not zero" (rule 3), so a key could be retired while bodies still use
it. A key may be retired only when BOTH `version_usage` (patients) and `message_version_usage` (messages) show zero.

Role: the engine runs on whatever connection it is given. Since migration 0032 the restricted application role can no longer UPDATE a patient's
identity columns or an identity token, so rotation runs ONLY where the maintenance script runs it: on the migrator connection, which covers every
clinic. Plaintext exists only in this process's memory and is never logged or put in an error message.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from careflow.crypto import (
    CryptoError,
    DecryptionError,
    DemographicVault,
    UnknownKeyVersionError,
)
from careflow.mpi import derive_tokens

DEFAULT_BATCH_SIZE: Final = 200
MAX_BATCH_SIZE: Final = 1000
_COLUMNS: Final = ("name", "dob", "phone")


class RotationError(Exception):
    """The run was refused or stopped. Everything in the failed batch was rolled back."""


@dataclass
class RotationReport:
    from_version: int
    to_version: int
    batches: int = 0
    patients_rotated: int = 0
    tokens_written: int = 0
    batch_keys: list[str] = field(default_factory=list)


@dataclass
class VerifyReport:
    to_version: int
    patients_checked: int = 0
    unreadable: list[uuid.UUID] = field(default_factory=list)  # a ciphertext did not authenticate
    token_problems: list[uuid.UUID] = field(default_factory=list)  # new-version token missing or not what the data derives

    @property
    def ok(self) -> bool:
        return not self.unreadable and not self.token_problems


def check_versions(vault: DemographicVault, from_version: int, to_version: int) -> None:
    for label, v in (("from", from_version), ("to", to_version)):
        if not isinstance(v, int) or isinstance(v, bool) or v < 1:
            raise RotationError(f"--{label} must be a positive integer key version, got {v!r}")
        if v not in vault.key_versions:
            raise UnknownKeyVersionError(
                f"key_version {v} has no key loaded (loaded: {vault.key_versions}); set "
                f"CAREFLOW_ENC_SECRET_V{v} and CAREFLOW_HMAC_SECRET_V{v} before rotating"
            )
    if from_version == to_version:
        raise RotationError("from and to are the same key version; nothing to rotate")


def _decrypt_row(vault: DemographicVault, row) -> dict[str, str | None]:
    """Every present column decrypted under the version recorded beside it."""
    plain: dict[str, str | None] = {}
    for col in _COLUMNS:
        blob, kv = getattr(row, f"{col}_ct"), getattr(row, f"{col}_key_version")
        plain[col] = None if blob is None else vault.decrypt(bytes(blob), field=col, key_version=kv)
    return plain


def _batch_key(from_v: int, to_v: int, clinic_id: uuid.UUID, origin: str, first: uuid.UUID, last: uuid.UUID) -> str:
    return f"rotate-keys:v{from_v}->v{to_v}:{clinic_id}:{origin}:{first}:{last}"


_PENDING = """(name_key_version = :f or dob_key_version = :f or phone_key_version = :f)"""


async def rotate_key_version(
    conn: AsyncConnection,
    vault: DemographicVault,
    *,
    from_version: int,
    to_version: int,
    batch_size: int = DEFAULT_BATCH_SIZE,
    clinic_id: uuid.UUID | None = None,
    commit_each_batch: bool = False,
    on_batch: Callable[[RotationReport], Awaitable[None] | None] | None = None,
) -> RotationReport:
    """Re-encrypt every patient still on `from_version` under `to_version`; see the module docstring.

    `commit_each_batch` ends the caller's transaction after every batch so row locks are released and no connection
    sits in one huge transaction; leave it False when the caller owns the transaction (tests, a wrapping rollback)."""
    check_versions(vault, from_version, to_version)
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise RotationError(f"batch_size must be between 1 and {MAX_BATCH_SIZE}")

    report = RotationReport(from_version, to_version)
    scope = "" if clinic_id is None else " and clinic_id = :clinic"
    groups = (
        await conn.execute(
            text(
                f"""select distinct clinic_id, origin from mpi.patients
                    where erased_at is null and {_PENDING}{scope} order by clinic_id, origin"""
            ),
            {"f": from_version, **({} if clinic_id is None else {"clinic": clinic_id})},
        )
    ).all()
    if commit_each_batch:
        await conn.commit()

    for group_clinic, origin in groups:
        while True:
            async with conn.begin_nested():
                rows = (
                    await conn.execute(
                        text(
                            f"""select id, name_ct, name_key_version, dob_ct, dob_key_version, phone_ct, phone_key_version
                                from mpi.patients
                                where clinic_id = :c and origin = :o and erased_at is null and {_PENDING}
                                order by id limit :n for update"""
                        ),
                        {"c": group_clinic, "o": origin, "f": from_version, "n": batch_size},
                    )
                ).all()
                if not rows:
                    break
                tokens_written = await _rotate_batch(conn, vault, rows, group_clinic, origin, from_version, to_version)
                key = _batch_key(from_version, to_version, group_clinic, origin, rows[0].id, rows[-1].id)
                ledgered = await conn.execute(
                    text(
                        """insert into ops.key_rotation_batches
                               (clinic_id, origin, from_version, to_version, patients_rotated, tokens_written,
                                first_patient_id, last_patient_id, idempotency_key)
                           values (:c, :o, :f, :t, :n, :w, :first, :last, :k)
                           on conflict (idempotency_key) do nothing"""
                    ),
                    {"c": group_clinic, "o": origin, "f": from_version, "t": to_version, "n": len(rows),
                     "w": tokens_written, "first": rows[0].id, "last": rows[-1].id, "k": key},
                )
            report.batches += 1
            report.patients_rotated += len(rows)
            report.tokens_written += tokens_written
            if ledgered.rowcount:  # a colliding key (same id range rotated twice) is not reported as a new ledger row
                report.batch_keys.append(key)
            if commit_each_batch:
                await conn.commit()
            if on_batch is not None:
                maybe = on_batch(report)
                if maybe is not None:
                    await maybe
    return report


async def _rotate_batch(conn, vault, rows, clinic_id, origin, from_v, to_v) -> int:
    written = 0
    for row in rows:
        try:
            plain = _decrypt_row(vault, row)
            new_cols: dict[str, tuple[bytes | None, int | None]] = {}
            for col in _COLUMNS:
                blob, kv = getattr(row, f"{col}_ct"), getattr(row, f"{col}_key_version")
                if blob is not None and kv == from_v:
                    fresh = vault.encrypt(plain[col], field=col, key_version=to_v)
                    if vault.decrypt(fresh, field=col, key_version=to_v) != plain[col]:
                        raise RotationError("re-encrypted value did not read back identically")
                    new_cols[col] = (fresh, to_v)
                else:
                    new_cols[col] = (None if blob is None else bytes(blob), kv)
            # A live record always has a name and a date of birth (CHECK on mpi.patients); refuse rather than guess.
            if plain["name"] is None or plain["dob"] is None:
                raise RotationError("a live record without a name or date of birth")
            tokens = derive_tokens(vault, name=plain["name"], dob_iso=plain["dob"], phone_canonical=plain["phone"], key_version=to_v)
        except (CryptoError, RotationError) as exc:
            # The patient id is an identifier, not a demographic; the plaintext is never included.
            raise RotationError(f"patient {row.id}: {type(exc).__name__}: {exc}") from exc

        await conn.execute(
            text(
                """update mpi.patients set
                       name_ct = :name_ct, name_key_version = :name_kv, dob_ct = :dob_ct, dob_key_version = :dob_kv,
                       phone_ct = :phone_ct, phone_key_version = :phone_kv
                   where id = :id"""
            ),
            {
                "id": row.id,
                **{f"{c}_ct": new_cols[c][0] for c in _COLUMNS},
                **{f"{c}_kv": new_cols[c][1] for c in _COLUMNS},
            },
        )
        for kind, token in tokens.items():
            await conn.execute(
                text(
                    """insert into mpi.identity_tokens (patient_id, clinic_id, kind, token, key_version, origin)
                       values (:p, :c, :kind, :tok, :kv, :o)
                       on conflict (patient_id, kind, key_version) do update set token = excluded.token"""
                ),
                {"p": row.id, "c": clinic_id, "kind": kind, "tok": token, "kv": to_v, "o": origin},
            )
            written += 1
    return written


async def verify_rotation(
    conn: AsyncConnection,
    vault: DemographicVault,
    *,
    to_version: int,
    clinic_id: uuid.UUID | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> VerifyReport:
    """Read-only integrity check of everything on `to_version`: every ciphertext of a patient that has one on the
    new version authenticates under its recorded version, and the new-version tokens exist and equal what the
    decrypted data derives. Run it after `rotate_key_version` and before retiring the old key."""
    if to_version not in vault.key_versions:
        raise UnknownKeyVersionError(f"no key loaded for key_version {to_version!r}")
    report = VerifyReport(to_version)
    after = uuid.UUID(int=0)
    scope = "" if clinic_id is None else " and clinic_id = :clinic"
    while True:
        rows = (
            await conn.execute(
                text(
                    f"""select id, name_ct, name_key_version, dob_ct, dob_key_version, phone_ct, phone_key_version
                        from mpi.patients
                        where erased_at is null and id > :after{scope}
                          and (name_key_version = :t or dob_key_version = :t or phone_key_version = :t)
                        order by id limit :n"""
                ),
                {"after": after, "t": to_version, "n": batch_size, **({} if clinic_id is None else {"clinic": clinic_id})},
            )
        ).all()
        if not rows:
            return report
        stored: dict[uuid.UUID, dict[str, bytes]] = {}
        for pid, kind, tok in (
            await conn.execute(
                text("select patient_id, kind, token from mpi.identity_tokens where key_version = :t and patient_id = any(:ids)"),
                {"t": to_version, "ids": [r.id for r in rows]},
            )
        ).all():
            stored.setdefault(pid, {})[kind] = bytes(tok)
        for row in rows:
            report.patients_checked += 1
            try:
                plain = _decrypt_row(vault, row)
            except DecryptionError:
                report.unreadable.append(row.id)
                continue
            if plain["name"] is None or plain["dob"] is None:  # the table's CHECK forbids it; never guess if it happens
                report.unreadable.append(row.id)
                continue
            expected = derive_tokens(vault, name=plain["name"], dob_iso=plain["dob"], phone_canonical=plain["phone"], key_version=to_version)
            if stored.get(row.id, {}) != expected:
                report.token_problems.append(row.id)
        after = rows[-1].id


async def version_usage(conn: AsyncConnection, *, clinic_id: uuid.UUID | None = None) -> dict[int, dict[str, int]]:
    """How many ciphertext columns and tokens still reference each key_version: the retirement checklist.
    A key may be retired only when its `ciphertexts` count is zero."""
    scope = "" if clinic_id is None else " and clinic_id = :clinic"
    params = {} if clinic_id is None else {"clinic": clinic_id}
    usage: dict[int, dict[str, int]] = {}
    for col in _COLUMNS:  # `col` comes from the fixed tuple above, never from input
        for v, n in (
            await conn.execute(
                text(f"select {col}_key_version, count(*) from mpi.patients where {col}_key_version is not null{scope} group by 1"),
                params,
            )
        ).all():
            usage.setdefault(v, {"ciphertexts": 0, "tokens": 0})["ciphertexts"] += n
    for v, n in (
        await conn.execute(text(f"select key_version, count(*) from mpi.identity_tokens where true{scope} group by 1"), params)
    ).all():
        usage.setdefault(v, {"ciphertexts": 0, "tokens": 0})["tokens"] += n
    return dict(sorted(usage.items()))


# ---- message bodies (msg.messages) --------------------------------------------------------------------------------


@dataclass
class MessageRotationReport:
    from_version: int
    to_version: int
    batches: int = 0
    messages_rotated: int = 0
    batch_keys: list[str] = field(default_factory=list)


@dataclass
class MessageVerifyReport:
    to_version: int
    messages_checked: int = 0
    unreadable: list[uuid.UUID] = field(default_factory=list)  # a body did not authenticate

    @property
    def ok(self) -> bool:
        return not self.unreadable


async def messages_table_exists(conn: AsyncConnection) -> bool:
    """False before migration 0011 is applied, so the CLI can say so instead of failing."""
    return bool((await conn.execute(text("select to_regclass('msg.messages') is not null"))).scalar_one())


async def require_rls_bypass(conn: AsyncConnection) -> None:
    """Refuse (loudly, never a silent skip) unless the session's role bypasses row-level security."""
    bypass = (await conn.execute(text("select rolbypassrls from pg_roles where rolname = current_user"))).scalar_one_or_none()
    if bypass is not True:
        raise RotationError(
            "message bodies can only be rotated, verified or counted over a connection that bypasses row-level security "
            "(the migrator credential): the application role cannot update messages, and under RLS it would see only "
            "some topics, giving a count that is silently too low"
        )


def _message_batch_key(from_v: int, to_v: int, clinic_id: uuid.UUID, origin: str, first: uuid.UUID, last: uuid.UUID) -> str:
    return f"rotate-messages:v{from_v}->v{to_v}:{clinic_id}:{origin}:{first}:{last}"


async def rotate_message_bodies(
    conn: AsyncConnection,
    vault: DemographicVault,
    *,
    from_version: int,
    to_version: int,
    batch_size: int = DEFAULT_BATCH_SIZE,
    clinic_id: uuid.UUID | None = None,
    commit_each_batch: bool = False,
    on_batch: Callable[[MessageRotationReport], Awaitable[None] | None] | None = None,
) -> MessageRotationReport:
    """Re-encrypt every non-erased message body still on `from_version` under `to_version`, in batches (one SAVEPOINT
    each). The selection predicate is the progress marker, so a stopped run resumes and a finished row is never touched
    again. A body that does not authenticate stops the run and rolls back that batch; nothing is skipped or assumed fine.
    Each batch writes one `msg.message_rotation_batches` row. Needs the migrator connection (see the module docstring)."""
    check_versions(vault, from_version, to_version)
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise RotationError(f"batch_size must be between 1 and {MAX_BATCH_SIZE}")
    await require_rls_bypass(conn)

    report = MessageRotationReport(from_version, to_version)
    scope = "" if clinic_id is None else " and clinic_id = :clinic"
    groups = (
        await conn.execute(
            text(
                f"""select distinct clinic_id, origin from msg.messages
                    where erased_at is null and body_key_version = :f{scope} order by clinic_id, origin"""
            ),
            {"f": from_version, **({} if clinic_id is None else {"clinic": clinic_id})},
        )
    ).all()
    if commit_each_batch:
        await conn.commit()

    for group_clinic, origin in groups:
        while True:
            async with conn.begin_nested():
                rows = (
                    await conn.execute(
                        text(
                            """select id, thread_id, body_ct from msg.messages
                               where clinic_id = :c and origin = :o and erased_at is null and body_key_version = :f
                               order by id limit :n for update"""
                        ),
                        {"c": group_clinic, "o": origin, "f": from_version, "n": batch_size},
                    )
                ).all()
                if not rows:
                    break
                for row in rows:
                    ids = {"clinic_id": group_clinic, "thread_id": row.thread_id, "message_id": row.id}
                    try:
                        plain = vault.decrypt_message_body(bytes(row.body_ct), key_version=from_version, **ids)
                        fresh = vault.encrypt_message_body(plain, key_version=to_version, **ids)
                        if vault.decrypt_message_body(fresh, key_version=to_version, **ids) != plain:
                            raise RotationError("re-encrypted body did not read back identically")
                    except (CryptoError, RotationError) as exc:
                        # The message id is an identifier; the body is never included.
                        raise RotationError(f"message {row.id}: {type(exc).__name__}: {exc}") from exc
                    await conn.execute(
                        text("update msg.messages set body_ct = :ct, body_key_version = :t where id = :i"),
                        {"ct": fresh, "t": to_version, "i": row.id},
                    )
                key = _message_batch_key(from_version, to_version, group_clinic, origin, rows[0].id, rows[-1].id)
                ledgered = await conn.execute(
                    text(
                        """insert into msg.message_rotation_batches
                               (clinic_id, origin, from_version, to_version, messages_rotated,
                                first_message_id, last_message_id, idempotency_key)
                           values (:c, :o, :f, :t, :n, :first, :last, :k)
                           on conflict (idempotency_key) do nothing"""
                    ),
                    {"c": group_clinic, "o": origin, "f": from_version, "t": to_version, "n": len(rows),
                     "first": rows[0].id, "last": rows[-1].id, "k": key},
                )
            report.batches += 1
            report.messages_rotated += len(rows)
            if ledgered.rowcount:
                report.batch_keys.append(key)
            if commit_each_batch:
                await conn.commit()
            if on_batch is not None:
                maybe = on_batch(report)
                if maybe is not None:
                    await maybe
    return report


async def verify_message_bodies(
    conn: AsyncConnection,
    vault: DemographicVault,
    *,
    to_version: int,
    clinic_id: uuid.UUID | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> MessageVerifyReport:
    """Read-only: every non-erased body on `to_version` authenticates under that version. Needs the migrator
    connection, because a check that sees only some topics would give false assurance."""
    if to_version not in vault.key_versions:
        raise UnknownKeyVersionError(f"no key loaded for key_version {to_version!r}")
    await require_rls_bypass(conn)
    report = MessageVerifyReport(to_version)
    after = uuid.UUID(int=0)
    scope = "" if clinic_id is None else " and clinic_id = :clinic"
    while True:
        rows = (
            await conn.execute(
                text(
                    f"""select id, clinic_id, thread_id, body_ct from msg.messages
                        where erased_at is null and body_key_version = :t and id > :after{scope}
                        order by id limit :n"""
                ),
                {"t": to_version, "after": after, "n": batch_size, **({} if clinic_id is None else {"clinic": clinic_id})},
            )
        ).all()
        if not rows:
            return report
        for row in rows:
            report.messages_checked += 1
            try:
                vault.decrypt_message_body(
                    bytes(row.body_ct), clinic_id=row.clinic_id, thread_id=row.thread_id, message_id=row.id, key_version=to_version
                )
            except DecryptionError:
                report.unreadable.append(row.id)
        after = rows[-1].id


async def message_version_usage(conn: AsyncConnection, *, clinic_id: uuid.UUID | None = None) -> dict[int, int]:
    """Non-erased message bodies still on each key_version: the message half of the retirement checklist. Refuses
    over a connection that does not bypass RLS (an under-count could let a key be retired while bodies use it)."""
    await require_rls_bypass(conn)
    scope = "" if clinic_id is None else " and clinic_id = :clinic"
    rows = await conn.execute(
        text(f"select body_key_version, count(*) from msg.messages where erased_at is null{scope} group by 1 order by 1"),
        {} if clinic_id is None else {"clinic": clinic_id},
    )
    return {v: n for v, n in rows.all()}


# ---- care-plan text (migration 0056) ------------------------------------------------------------------------------------------------------------------------------------------------------------------------
#
# ops.care_plans.plan_ct is encrypted with the clinic and the PLAN id bound in (careflow.messages.encrypt_plan_text), so it rotates exactly like a message body. The guard of that table has ONE transition for it
# (plan_ct and plan_key_version change, the version MUST change, nothing else), reachable only on the migrator connection; each batch writes one ops.plan_rotation_batches row. An ERASED plan (plan_ct null) is
# never selected: erasure is final. Clinical results hold only numbers and have no key.


@dataclass
class PlanRotationReport:
    from_version: int
    to_version: int
    batches: int = 0
    plans_rotated: int = 0
    batch_keys: list[str] = field(default_factory=list)


@dataclass
class PlanVerifyReport:
    to_version: int
    plans_checked: int = 0
    unreadable: list[uuid.UUID] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.unreadable


def _plan_ids(clinic_id: uuid.UUID, plan_id: uuid.UUID) -> dict:
    return {"clinic_id": clinic_id, "thread_id": plan_id, "message_id": uuid.uuid5(plan_id, "care-plan-text")}


def _plan_batch_key(from_v: int, to_v: int, clinic_id: uuid.UUID, origin: str, first: uuid.UUID, last: uuid.UUID) -> str:
    return f"rotate-plans:v{from_v}->v{to_v}:{clinic_id}:{origin}:{first}:{last}"


async def plans_table_has_text(conn: AsyncConnection) -> bool:
    """False before migration 0056 is applied (no erased_at column yet), so the CLI can say so instead of failing."""
    return bool((await conn.execute(text("select exists (select 1 from information_schema.columns where table_schema = 'ops' and table_name = 'care_plans' and column_name = 'erased_at')"))).scalar_one())


async def rotate_plan_texts(
    conn: AsyncConnection,
    vault: DemographicVault,
    *,
    from_version: int,
    to_version: int,
    batch_size: int = DEFAULT_BATCH_SIZE,
    clinic_id: uuid.UUID | None = None,
    commit_each_batch: bool = False,
) -> PlanRotationReport:
    check_versions(vault, from_version, to_version)
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise RotationError(f"batch_size must be between 1 and {MAX_BATCH_SIZE}")
    await require_rls_bypass(conn)
    report = PlanRotationReport(from_version, to_version)
    scope = "" if clinic_id is None else " and clinic_id = :clinic"
    groups = (
        await conn.execute(
            text(f"select distinct clinic_id, origin from ops.care_plans where erased_at is null and plan_key_version = :f{scope} order by clinic_id, origin"),
            {"f": from_version, **({} if clinic_id is None else {"clinic": clinic_id})},
        )
    ).all()
    if commit_each_batch:
        await conn.commit()
    for group_clinic, origin in groups:
        while True:
            async with conn.begin_nested():
                rows = (
                    await conn.execute(
                        text("select id, plan_ct from ops.care_plans where clinic_id = :c and origin = :o and erased_at is null and plan_key_version = :f order by id limit :n for update"),
                        {"c": group_clinic, "o": origin, "f": from_version, "n": batch_size},
                    )
                ).all()
                if not rows:
                    break
                for row in rows:
                    ids = _plan_ids(group_clinic, row.id)
                    try:
                        plain = vault.decrypt_message_body(bytes(row.plan_ct), key_version=from_version, **ids)
                        fresh = vault.encrypt_message_body(plain, key_version=to_version, **ids)
                        if vault.decrypt_message_body(fresh, key_version=to_version, **ids) != plain:
                            raise RotationError("re-encrypted plan text did not read back identically")
                    except (CryptoError, RotationError) as exc:
                        raise RotationError(f"care plan {row.id}: {type(exc).__name__}: {exc}") from exc  # the id only, never the text
                    await conn.execute(text("update ops.care_plans set plan_ct = :ct, plan_key_version = :t where id = :i"), {"ct": fresh, "t": to_version, "i": row.id})
                key = _plan_batch_key(from_version, to_version, group_clinic, origin, rows[0].id, rows[-1].id)
                ledgered = await conn.execute(
                    text(
                        "insert into ops.plan_rotation_batches (clinic_id, origin, from_version, to_version, plans_rotated, first_plan_id, last_plan_id, idempotency_key) "
                        "values (:c, :o, :f, :t, :n, :first, :last, :k) on conflict (idempotency_key) do nothing"
                    ),
                    {"c": group_clinic, "o": origin, "f": from_version, "t": to_version, "n": len(rows), "first": rows[0].id, "last": rows[-1].id, "k": key},
                )
            report.batches += 1
            report.plans_rotated += len(rows)
            if ledgered.rowcount:
                report.batch_keys.append(key)
            if commit_each_batch:
                await conn.commit()
    return report


async def verify_plan_texts(conn: AsyncConnection, vault: DemographicVault, *, to_version: int, clinic_id: uuid.UUID | None = None, batch_size: int = DEFAULT_BATCH_SIZE) -> PlanVerifyReport:
    if to_version not in vault.key_versions:
        raise UnknownKeyVersionError(f"no key loaded for key_version {to_version!r}")
    await require_rls_bypass(conn)
    report = PlanVerifyReport(to_version)
    after = uuid.UUID(int=0)
    scope = "" if clinic_id is None else " and clinic_id = :clinic"
    while True:
        rows = (
            await conn.execute(
                text(f"select id, clinic_id, plan_ct from ops.care_plans where erased_at is null and plan_key_version = :t and id > :after{scope} order by id limit :n"),
                {"t": to_version, "after": after, "n": batch_size, **({} if clinic_id is None else {"clinic": clinic_id})},
            )
        ).all()
        if not rows:
            return report
        for row in rows:
            report.plans_checked += 1
            try:
                vault.decrypt_message_body(bytes(row.plan_ct), key_version=to_version, **_plan_ids(row.clinic_id, row.id))
            except DecryptionError:
                report.unreadable.append(row.id)
        after = rows[-1].id


async def plan_version_usage(conn: AsyncConnection, *, clinic_id: uuid.UUID | None = None) -> dict[int, int]:
    """Non-erased care-plan texts on each key_version: the third half of the retirement checklist (patients, messages, plans). Refuses over a connection that does not bypass RLS."""
    await require_rls_bypass(conn)
    scope = "" if clinic_id is None else " and clinic_id = :clinic"
    rows = await conn.execute(text(f"select plan_key_version, count(*) from ops.care_plans where erased_at is null{scope} group by 1 order by 1"), {} if clinic_id is None else {"clinic": clinic_id})
    return {v: n for v, n in rows.all()}


# ---- grievance texts (migration 0059) ------------------------------------------------------------------------------------------------------------------------------------------------------------------------
#
# iam.grievances.body_ct is encrypted with the clinic and the GRIEVANCE id bound in (careflow.messages.encrypt_grievance_text), so it rotates exactly like a message body. The guard of that table has ONE transition for it
# (body_ct and body_key_version change, the version MUST change, nothing else), reachable only on the migrator connection; each batch writes one iam.grievance_rotation_batches row. An ERASED grievance (body_ct null) is
# never selected: erasure is final.


@dataclass
class GrievanceRotationReport:
    from_version: int
    to_version: int
    batches: int = 0
    texts_rotated: int = 0
    batch_keys: list[str] = field(default_factory=list)


@dataclass
class GrievanceVerifyReport:
    to_version: int
    texts_checked: int = 0
    unreadable: list[uuid.UUID] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.unreadable


def _grievance_ids(clinic_id: uuid.UUID, grievance_id: uuid.UUID) -> dict:
    return {"clinic_id": clinic_id, "thread_id": grievance_id, "message_id": uuid.uuid5(grievance_id, "grievance-text")}


def _grievance_batch_key(from_v: int, to_v: int, clinic_id: uuid.UUID, origin: str, first: uuid.UUID, last: uuid.UUID) -> str:
    return f"rotate-grievances:v{from_v}->v{to_v}:{clinic_id}:{origin}:{first}:{last}"


async def grievances_table_exists(conn: AsyncConnection) -> bool:
    """False before migration 0059 is applied (no erased_at column yet), so the CLI can say so instead of failing."""
    return bool((await conn.execute(text("select exists (select 1 from information_schema.columns where table_schema = 'iam' and table_name = 'grievances' and column_name = 'erased_at')"))).scalar_one())


async def rotate_grievance_texts(
    conn: AsyncConnection,
    vault: DemographicVault,
    *,
    from_version: int,
    to_version: int,
    batch_size: int = DEFAULT_BATCH_SIZE,
    clinic_id: uuid.UUID | None = None,
    commit_each_batch: bool = False,
) -> GrievanceRotationReport:
    check_versions(vault, from_version, to_version)
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise RotationError(f"batch_size must be between 1 and {MAX_BATCH_SIZE}")
    await require_rls_bypass(conn)
    report = GrievanceRotationReport(from_version, to_version)
    scope = "" if clinic_id is None else " and clinic_id = :clinic"
    groups = (
        await conn.execute(
            text(f"select distinct clinic_id, origin from iam.grievances where erased_at is null and body_key_version = :f{scope} order by clinic_id, origin"),
            {"f": from_version, **({} if clinic_id is None else {"clinic": clinic_id})},
        )
    ).all()
    if commit_each_batch:
        await conn.commit()
    for group_clinic, origin in groups:
        while True:
            async with conn.begin_nested():
                rows = (
                    await conn.execute(
                        text("select id, body_ct from iam.grievances where clinic_id = :c and origin = :o and erased_at is null and body_key_version = :f order by id limit :n for update"),
                        {"c": group_clinic, "o": origin, "f": from_version, "n": batch_size},
                    )
                ).all()
                if not rows:
                    break
                for row in rows:
                    ids = _grievance_ids(group_clinic, row.id)
                    try:
                        plain = vault.decrypt_message_body(bytes(row.body_ct), key_version=from_version, **ids)
                        fresh = vault.encrypt_message_body(plain, key_version=to_version, **ids)
                        if vault.decrypt_message_body(fresh, key_version=to_version, **ids) != plain:
                            raise RotationError("re-encrypted plan text did not read back identically")
                    except (CryptoError, RotationError) as exc:
                        raise RotationError(f"grievance {row.id}: {type(exc).__name__}: {exc}") from exc  # the id only, never the text
                    await conn.execute(text("update iam.grievances set body_ct = :ct, body_key_version = :t where id = :i"), {"ct": fresh, "t": to_version, "i": row.id})
                key = _grievance_batch_key(from_version, to_version, group_clinic, origin, rows[0].id, rows[-1].id)
                ledgered = await conn.execute(
                    text(
                        "insert into iam.grievance_rotation_batches (clinic_id, origin, from_version, to_version, texts_rotated, first_id, last_id, idempotency_key) "
                        "values (:c, :o, :f, :t, :n, :first, :last, :k) on conflict (idempotency_key) do nothing"
                    ),
                    {"c": group_clinic, "o": origin, "f": from_version, "t": to_version, "n": len(rows), "first": rows[0].id, "last": rows[-1].id, "k": key},
                )
            report.batches += 1
            report.texts_rotated += len(rows)
            if ledgered.rowcount:
                report.batch_keys.append(key)
            if commit_each_batch:
                await conn.commit()
    return report


async def verify_grievance_texts(conn: AsyncConnection, vault: DemographicVault, *, to_version: int, clinic_id: uuid.UUID | None = None, batch_size: int = DEFAULT_BATCH_SIZE) -> GrievanceVerifyReport:
    if to_version not in vault.key_versions:
        raise UnknownKeyVersionError(f"no key loaded for key_version {to_version!r}")
    await require_rls_bypass(conn)
    report = GrievanceVerifyReport(to_version)
    after = uuid.UUID(int=0)
    scope = "" if clinic_id is None else " and clinic_id = :clinic"
    while True:
        rows = (
            await conn.execute(
                text(f"select id, clinic_id, body_ct from iam.grievances where erased_at is null and body_key_version = :t and id > :after{scope} order by id limit :n"),
                {"t": to_version, "after": after, "n": batch_size, **({} if clinic_id is None else {"clinic": clinic_id})},
            )
        ).all()
        if not rows:
            return report
        for row in rows:
            report.texts_checked += 1
            try:
                vault.decrypt_message_body(bytes(row.body_ct), key_version=to_version, **_grievance_ids(row.clinic_id, row.id))
            except DecryptionError:
                report.unreadable.append(row.id)
        after = rows[-1].id


async def grievance_version_usage(conn: AsyncConnection, *, clinic_id: uuid.UUID | None = None) -> dict[int, int]:
    """Non-erased care-plan texts on each key_version: the third half of the retirement checklist (patients, messages, plans). Refuses over a connection that does not bypass RLS."""
    await require_rls_bypass(conn)
    scope = "" if clinic_id is None else " and clinic_id = :clinic"
    rows = await conn.execute(text(f"select body_key_version, count(*) from iam.grievances where erased_at is null{scope} group by 1 order by 1"), {} if clinic_id is None else {"clinic": clinic_id})
    return {v: n for v, n in rows.all()}


# ---- note texts (migration 0061) ------------------------------------------------------------------------------------------------------------------------------------------------------------------------
#
# clinical.notes.body_ct is encrypted with the clinic and the NOTE id bound in (careflow.messages.encrypt_note_text), so it rotates exactly like a message body. The guard of that table has ONE transition for it
# (body_ct and body_key_version change, the version MUST change, nothing else), reachable only on the migrator connection; each batch writes one clinical.note_rotation_batches row. An ERASED note (body_ct null) is
# never selected: erasure is final.


@dataclass
class NoteRotationReport:
    from_version: int
    to_version: int
    batches: int = 0
    texts_rotated: int = 0
    batch_keys: list[str] = field(default_factory=list)


@dataclass
class NoteVerifyReport:
    to_version: int
    texts_checked: int = 0
    unreadable: list[uuid.UUID] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.unreadable


def _note_ids(clinic_id: uuid.UUID, note_id: uuid.UUID) -> dict:
    return {"clinic_id": clinic_id, "thread_id": note_id, "message_id": uuid.uuid5(note_id, "note-text")}


def _note_batch_key(from_v: int, to_v: int, clinic_id: uuid.UUID, origin: str, first: uuid.UUID, last: uuid.UUID) -> str:
    return f"rotate-notes:v{from_v}->v{to_v}:{clinic_id}:{origin}:{first}:{last}"


async def notes_table_exists(conn: AsyncConnection) -> bool:
    """False before migration 0061 is applied (no erased_at column yet), so the CLI can say so instead of failing."""
    return bool((await conn.execute(text("select exists (select 1 from information_schema.columns where table_schema = 'clinical' and table_name = 'notes' and column_name = 'erased_at')"))).scalar_one())


async def rotate_note_texts(
    conn: AsyncConnection,
    vault: DemographicVault,
    *,
    from_version: int,
    to_version: int,
    batch_size: int = DEFAULT_BATCH_SIZE,
    clinic_id: uuid.UUID | None = None,
    commit_each_batch: bool = False,
) -> NoteRotationReport:
    check_versions(vault, from_version, to_version)
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise RotationError(f"batch_size must be between 1 and {MAX_BATCH_SIZE}")
    await require_rls_bypass(conn)
    report = NoteRotationReport(from_version, to_version)
    scope = "" if clinic_id is None else " and clinic_id = :clinic"
    groups = (
        await conn.execute(
            text(f"select distinct clinic_id, origin from clinical.notes where erased_at is null and body_key_version = :f{scope} order by clinic_id, origin"),
            {"f": from_version, **({} if clinic_id is None else {"clinic": clinic_id})},
        )
    ).all()
    if commit_each_batch:
        await conn.commit()
    for group_clinic, origin in groups:
        while True:
            async with conn.begin_nested():
                rows = (
                    await conn.execute(
                        text("select id, body_ct from clinical.notes where clinic_id = :c and origin = :o and erased_at is null and body_key_version = :f order by id limit :n for update"),
                        {"c": group_clinic, "o": origin, "f": from_version, "n": batch_size},
                    )
                ).all()
                if not rows:
                    break
                for row in rows:
                    ids = _note_ids(group_clinic, row.id)
                    try:
                        plain = vault.decrypt_message_body(bytes(row.body_ct), key_version=from_version, **ids)
                        fresh = vault.encrypt_message_body(plain, key_version=to_version, **ids)
                        if vault.decrypt_message_body(fresh, key_version=to_version, **ids) != plain:
                            raise RotationError("re-encrypted plan text did not read back identically")
                    except (CryptoError, RotationError) as exc:
                        raise RotationError(f"note {row.id}: {type(exc).__name__}: {exc}") from exc  # the id only, never the text
                    await conn.execute(text("update clinical.notes set body_ct = :ct, body_key_version = :t where id = :i"), {"ct": fresh, "t": to_version, "i": row.id})
                key = _note_batch_key(from_version, to_version, group_clinic, origin, rows[0].id, rows[-1].id)
                ledgered = await conn.execute(
                    text(
                        "insert into clinical.note_rotation_batches (clinic_id, origin, from_version, to_version, texts_rotated, first_id, last_id, idempotency_key) "
                        "values (:c, :o, :f, :t, :n, :first, :last, :k) on conflict (idempotency_key) do nothing"
                    ),
                    {"c": group_clinic, "o": origin, "f": from_version, "t": to_version, "n": len(rows), "first": rows[0].id, "last": rows[-1].id, "k": key},
                )
            report.batches += 1
            report.texts_rotated += len(rows)
            if ledgered.rowcount:
                report.batch_keys.append(key)
            if commit_each_batch:
                await conn.commit()
    return report


async def verify_note_texts(conn: AsyncConnection, vault: DemographicVault, *, to_version: int, clinic_id: uuid.UUID | None = None, batch_size: int = DEFAULT_BATCH_SIZE) -> NoteVerifyReport:
    if to_version not in vault.key_versions:
        raise UnknownKeyVersionError(f"no key loaded for key_version {to_version!r}")
    await require_rls_bypass(conn)
    report = NoteVerifyReport(to_version)
    after = uuid.UUID(int=0)
    scope = "" if clinic_id is None else " and clinic_id = :clinic"
    while True:
        rows = (
            await conn.execute(
                text(f"select id, clinic_id, body_ct from clinical.notes where erased_at is null and body_key_version = :t and id > :after{scope} order by id limit :n"),
                {"t": to_version, "after": after, "n": batch_size, **({} if clinic_id is None else {"clinic": clinic_id})},
            )
        ).all()
        if not rows:
            return report
        for row in rows:
            report.texts_checked += 1
            try:
                vault.decrypt_message_body(bytes(row.body_ct), key_version=to_version, **_note_ids(row.clinic_id, row.id))
            except DecryptionError:
                report.unreadable.append(row.id)
        after = rows[-1].id


async def note_version_usage(conn: AsyncConnection, *, clinic_id: uuid.UUID | None = None) -> dict[int, int]:
    """Non-erased care-plan texts on each key_version: the third half of the retirement checklist (patients, messages, plans). Refuses over a connection that does not bypass RLS."""
    await require_rls_bypass(conn)
    scope = "" if clinic_id is None else " and clinic_id = :clinic"
    rows = await conn.execute(text(f"select body_key_version, count(*) from clinical.notes where erased_at is null{scope} group by 1 order by 1"), {} if clinic_id is None else {"clinic": clinic_id})
    return {v: n for v, n in rows.all()}
