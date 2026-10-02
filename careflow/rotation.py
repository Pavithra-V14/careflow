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

Role: the engine runs on whatever connection it is given. Under the restricted app role with a front-desk identity
it rotates that clinic's rows (RLS decides); the maintenance script gives it the migrator connection to cover every
clinic. Plaintext exists only in this process's memory and is never logged or put in an error message.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from careflow.crypto import CryptoError, DemographicVault, DecryptionError, UnknownKeyVersionError
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
