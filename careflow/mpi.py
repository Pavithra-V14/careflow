"""Master Patient Index: ingestion and identity resolution (BUILD-SPEC.md §17.2, §12.1).

`PatientIngestionPipeline.ingest` takes raw demographics for one clinic and either links to an existing
patient, creates a new one, or creates a new one AND queues a human review. It never merges two records
and never changes an existing record: a merge is a separate, human-approved act that is not built yet.

Outcomes
    matched     exactly one existing record agrees on EVERY strong identifier -> return it, write nothing
    created     nothing agrees -> new patient, new tokens
    review      something agrees, but not enough to be sure it is the same person, or more than one
                record qualifies -> new patient, new tokens, and a `mpi.match_candidates` row per
                resembling record for a person to decide. Never auto-linked.
    replayed    this idempotency key already created a record in this clinic -> the original result

What counts as a confident match is one deliberately conservative rule, `STRONG_IDENTIFIERS`: the
composite name+date-of-birth token AND the phone token must both agree. Wrongly linking two people puts
one patient's record in front of another, so the cost of a false link is far above the cost of a
duplicate that a person later reviews. Any weaker agreement (phone only, name+DOB only, no phone
supplied) is a near-match. This rule is a policy decision that needs sign-off from whoever owns patient
identity at the clinic; it lives in one constant so changing it is one line and one test.

Safety properties
    * Unknown is not a value: a blank name, DOB or phone is an error, never stored and never matched on.
      An absent phone is None, which can only ever produce a near-match, never a confident one.
    * Matching is scoped to the clinic AND to the origin tag: a test or eval record can never match, or
      be matched by, a live patient.
    * Concurrent ingests that share an identity token are serialised with transaction-scoped advisory
      locks (taken in sorted order, so they cannot deadlock), so two simultaneous identical requests
      cannot both conclude "no match" and create duplicates.
    * Plaintext demographics never reach the database: only ciphertext and keyed-HMAC tokens do.
    * The caller owns the transaction and the identity: `conn` must already be inside a transaction with
      the verified claims set (`careflow.db.set_request_claims`), running as the restricted app role.
      Row-level security, not this module, enforces clinic isolation, so a caller who is not front desk
      of the clinic is refused with `NotPermitted`.
"""

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Final, Literal

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from careflow.crypto import DemographicVault

Origin = Literal["live", "test", "eval", "canary"]
ORIGINS: Final = ("live", "test", "eval", "canary")
Outcome = Literal["matched", "created", "review", "replayed"]

# The ONE rule for "confident match": every token kind listed must agree on the same record.
STRONG_IDENTIFIERS: Final = frozenset({"name_dob", "phone"})
NAME_DOB = "name_dob"
PHONE = "phone"
MIN_BIRTH_YEAR: Final = 1900


class IngestionError(Exception):
    """Base class: the input or request was refused; nothing was written."""


class MissingRequiredField(IngestionError):
    """A required demographic is absent or blank."""


class InvalidDateOfBirth(IngestionError):
    """The date of birth is not a real, past date in YYYY-MM-DD form."""


class InvalidPhone(IngestionError):
    """The phone number cannot be canonicalised to an Indian mobile number."""


class IdempotencyConflict(IngestionError):
    """This idempotency key is already used by a different request."""


class NotPermitted(IngestionError):
    """Row-level security refused the write: the caller is not front desk of this clinic."""


@dataclass(frozen=True)
class IngestResult:
    outcome: Outcome
    patient_id: uuid.UUID
    candidate_ids: tuple[uuid.UUID, ...] = ()  # existing records queued for human review


# ---- canonicalisation (before any cryptographic step) ----------------------------------------------

_SEPARATORS = re.compile(r"[\s\-().]")
_DIGITS = re.compile(r"^[0-9]+$")
_INDIA_MOBILE = re.compile(r"^[6-9][0-9]{9}$")
_ISO_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


def canonicalize_phone(raw: str) -> str:
    """Return `+91` followed by the ten-digit Indian mobile number, or raise InvalidPhone.

    Accepted spellings: `9876543210`, `09876543210`, `919876543210`, `+919876543210`, `0091 9876543210`,
    with spaces, hyphens, dots or brackets between digits. Anything else is refused rather than guessed
    at: a landline, a foreign number, a wrong length, letters, or an obvious placeholder such as ten
    identical digits (which would otherwise make unrelated patients share a phone token).
    """
    if not isinstance(raw, str):
        raise TypeError("phone must be a str")
    text_ = raw.strip()
    if not text_:
        raise InvalidPhone("phone is blank; an absent phone must be None")
    compact = _SEPARATORS.sub("", text_.removeprefix("+"))
    if not _DIGITS.match(compact):
        raise InvalidPhone("phone contains characters other than digits and separators")
    if text_.startswith("+"):
        national = compact[2:] if compact.startswith("91") else ""
    elif compact.startswith("0091"):
        national = compact[4:]
    elif len(compact) == 12 and compact.startswith("91"):
        national = compact[2:]
    elif len(compact) == 11 and compact.startswith("0"):
        national = compact[1:]
    else:
        national = compact
    if not _INDIA_MOBILE.match(national) or len(set(national)) == 1:
        raise InvalidPhone("phone is not a valid Indian mobile number")
    return "+91" + national


def _today_in_india() -> date:
    """Today's date in IST (fixed UTC+05:30, no tz database needed), so a baby born this morning in
    India is never rejected as "in the future" just because UTC has not reached the new day."""
    return datetime.now(timezone(timedelta(hours=5, minutes=30))).date()


def parse_date_of_birth(raw: str, *, today: date | None = None) -> date:
    """A strict YYYY-MM-DD date that is real, not in the future, and not before 1900."""
    if not isinstance(raw, str):
        raise TypeError("date of birth must be a str")
    text_ = raw.strip()
    if not text_:
        raise MissingRequiredField("date of birth is required (§17.2); a record without one routes to the front desk")
    if not _ISO_DATE.match(text_):
        raise InvalidDateOfBirth("date of birth must be YYYY-MM-DD; an age is never a date of birth")
    try:
        parsed = date.fromisoformat(text_)
    except ValueError:
        raise InvalidDateOfBirth("date of birth is not a real calendar date") from None
    if parsed > (today or _today_in_india()):
        raise InvalidDateOfBirth("date of birth is in the future")
    if parsed.year < MIN_BIRTH_YEAR:
        raise InvalidDateOfBirth(f"date of birth is before {MIN_BIRTH_YEAR}")
    return parsed


def classify(hits: dict[uuid.UUID, set[str]]) -> tuple[Literal["match", "review", "none"], dict[uuid.UUID, str]]:
    """Decide from the token kinds that agreed per existing record.

    Returns ("match", {id: ...}) for exactly one confident record; ("review", {id: reason}) when any
    record agrees partially or more than one agrees fully; ("none", {}) when nothing agrees.
    """
    if not hits:
        return "none", {}
    confident = [pid for pid, kinds in hits.items() if STRONG_IDENTIFIERS <= kinds]
    if len(confident) == 1:
        return "match", {confident[0]: "match"}
    reasons = {pid: ("ambiguous" if pid in confident else "near_match") for pid in hits}
    return "review", reasons


def derive_tokens(
    vault: DemographicVault, *, name: str, dob_iso: str, phone_canonical: str | None, key_version: int
) -> dict[str, bytes]:
    """The identity tokens of one person under one key version. Ingestion and key rotation both call this, so a
    token written by a rotation is byte-for-byte what a fresh ingest would have written (one definition, no drift).
    Inputs are already canonical: whitespace-collapsed name, ISO date of birth, `+91` phone or None."""
    tokens = {NAME_DOB: vault.identity_token(NAME_DOB, f"{name} {dob_iso}", key_version=key_version)}
    if phone_canonical is not None:
        tokens[PHONE] = vault.identity_token(PHONE, phone_canonical, key_version=key_version)
    return tokens


def _lock_key(clinic_id: uuid.UUID, kind: str, token: bytes) -> int:
    digest = hashlib.sha256(f"mpi-ingest:{clinic_id}:{kind}:{token.hex()}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


class PatientIngestionPipeline:
    def __init__(self, vault: DemographicVault, *, key_version: int) -> None:
        if key_version not in vault.key_versions:
            raise ValueError(f"key_version {key_version} is not loaded in the vault {vault.key_versions}")
        self._vault = vault
        self._kv = key_version

    async def ingest(
        self,
        conn: AsyncConnection,
        *,
        clinic_id: uuid.UUID,
        origin: Origin,
        name: str,
        dob: str,
        phone: str | None,
        idempotency_key: str,
    ) -> IngestResult:
        # 1. Validate and canonicalise. Pure; nothing has touched the database or the vault yet.
        if origin not in ORIGINS:
            raise ValueError(f"origin must be one of {ORIGINS}, got {origin!r}")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise MissingRequiredField("an idempotency key is required on every write")
        if not isinstance(name, str) or not name.strip():
            raise MissingRequiredField("name is required")
        name = " ".join(name.split())
        dob_iso = parse_date_of_birth(dob).isoformat()
        phone_c = None if phone is None else canonicalize_phone(phone)

        # 2. Tokens (deterministic keyed HMAC), then serialise concurrent ingests that share any of them.
        tokens = derive_tokens(self._vault, name=name, dob_iso=dob_iso, phone_canonical=phone_c, key_version=self._kv)
        for key in sorted(_lock_key(clinic_id, kind, tok) for kind, tok in tokens.items()):
            await conn.execute(text("select pg_advisory_xact_lock(:k)"), {"k": key})

        # 3. Replay: this key already created a record here (re-checked after the lock, so a concurrent
        #    duplicate request sees the first one's committed work).
        done = (
            await conn.execute(
                text("select id from mpi.patients where idempotency_key = :k and clinic_id = :c"),
                {"k": idempotency_key, "c": clinic_id},
            )
        ).first()
        if done:
            pid = done[0]
            stored = await conn.execute(
                text("select kind, token from mpi.identity_tokens where patient_id = :p and key_version = :kv"),
                {"p": pid, "kv": self._kv},
            )
            if {kind: bytes(tok) for kind, tok in stored} != tokens:
                raise IdempotencyConflict("this idempotency key was used for a different person")
            rows = await conn.execute(
                text("select candidate_patient_id from mpi.match_candidates where patient_id = :p order by created_at, id"),
                {"p": pid},
            )
            return IngestResult("replayed", pid, tuple(r[0] for r in rows))

        # 4. Look the tokens up (clinic- and origin-scoped; erased records never match) and decide. A subordinate
        #    of an ACTIVE merge counts as its master, and the identifiers of both pool together, so a merged person
        #    never looks like two confident matches (which would be "ambiguous").
        hits = await self._find(conn, clinic_id, origin, tokens)
        decision, reasons = classify(hits)
        if decision == "match":
            return IngestResult("matched", next(iter(reasons)))

        # 5. Create the record, its tokens and any review rows in one savepoint.
        try:
            async with conn.begin_nested():
                pid = (
                    await conn.execute(
                        text(
                            """insert into mpi.patients
                                   (clinic_id, name_ct, name_key_version, dob_ct, dob_key_version,
                                    phone_ct, phone_key_version, origin, idempotency_key)
                               values (:clinic, :name_ct, :kv, :dob_ct, :kv, :phone_ct, :phone_kv, :origin, :idem)
                               returning id"""
                        ),
                        {
                            "clinic": clinic_id,
                            "name_ct": self._vault.encrypt(name, field="name", key_version=self._kv),
                            "dob_ct": self._vault.encrypt(dob_iso, field="dob", key_version=self._kv),
                            "phone_ct": None
                            if phone_c is None
                            else self._vault.encrypt(phone_c, field="phone", key_version=self._kv),
                            "kv": self._kv,
                            "phone_kv": None if phone_c is None else self._kv,
                            "origin": origin,
                            "idem": idempotency_key,
                        },
                    )
                ).scalar_one()
                for kind, tok in tokens.items():
                    await conn.execute(
                        text(
                            """insert into mpi.identity_tokens (patient_id, clinic_id, kind, token, key_version, origin)
                               values (:p, :c, :kind, :tok, :kv, :origin)"""
                        ),
                        {"p": pid, "c": clinic_id, "kind": kind, "tok": tok, "kv": self._kv, "origin": origin},
                    )
                for other, reason in reasons.items():
                    await conn.execute(
                        text(
                            """insert into mpi.match_candidates
                                   (clinic_id, patient_id, candidate_patient_id, matched_kinds, reason, origin, idempotency_key)
                               values (:c, :p, :other, :kinds, :reason, :origin, :idem)"""
                        ),
                        {
                            "c": clinic_id,
                            "p": pid,
                            "other": other,
                            "kinds": sorted(hits[other]),
                            "reason": reason,
                            "origin": origin,
                            "idem": f"cand:{idempotency_key}:{other}",
                        },
                    )
        except IntegrityError as exc:
            if "idempotency_key" in str(exc.orig):
                raise IdempotencyConflict("this idempotency key is already used by another request") from exc
            raise
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) == "42501":
                raise NotPermitted("only front desk of this clinic may register patients") from exc
            raise
        return IngestResult("review" if reasons else "created", pid, tuple(reasons))

    async def _find(
        self, conn: AsyncConnection, clinic_id: uuid.UUID, origin: str, tokens: dict[str, bytes]
    ) -> dict[uuid.UUID, set[str]]:
        clauses = ["(t.kind = 'name_dob' and t.token = :nd)"]
        params: dict = {"clinic": clinic_id, "kv": self._kv, "origin": origin, "nd": tokens[NAME_DOB]}
        if PHONE in tokens:
            clauses.append("(t.kind = 'phone' and t.token = :ph)")
            params["ph"] = tokens[PHONE]
        rows = await conn.execute(
            text(
                f"""select coalesce(mg.master_patient_id, t.patient_id) as patient_id, array_agg(distinct t.kind) as kinds
                    from mpi.identity_tokens t
                    join mpi.patients p on p.id = t.patient_id and p.clinic_id = t.clinic_id
                    left join mpi.merges mg on mg.subordinate_patient_id = t.patient_id and mg.status = 'active'
                    where t.clinic_id = :clinic and t.key_version = :kv
                      and p.origin = :origin and p.erased_at is null
                      and ({' or '.join(clauses)})
                    group by 1"""
            ),
            params,
        )
        return {r[0]: set(r[1]) for r in rows}
