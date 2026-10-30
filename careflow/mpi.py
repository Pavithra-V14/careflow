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
import json
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Final, Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from careflow.crypto import DemographicVault

Origin = Literal["live", "test", "eval", "canary"]
ORIGINS: Final = ("live", "test", "eval", "canary")
Outcome = Literal["matched", "created", "review", "replayed"]

# The ONE rule for "confident match": every token kind listed must agree on the same record.
# PENDING SIGN-OFF: this rule is a patient-identity policy decision. Its owner is Priya, Health-information lead, who
# has not yet signed it off. Keep it in this single constant until she does; do not widen or narrow it without her.
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


def classify(
    hits: dict[uuid.UUID, set[str]], unverified: frozenset[uuid.UUID] | set[uuid.UUID] = frozenset()
) -> tuple[Literal["match", "review", "none"], dict[uuid.UUID, str]]:
    """Decide from the token kinds that agreed per existing record.

    Returns ("match", {id: ...}) for exactly one confident record; ("review", {id: reason}) when any
    record agrees partially or more than one agrees fully; ("none", {}) when nothing agrees.

    An UNVERIFIED record (created by the unattended worker and not yet confirmed by a front-desk person; migration 0023) is never a confident match, however fully it agrees: a
    person decides (it is reported as a near match). This is what stops a planted record, which carries a token for someone not yet registered, from capturing that person's
    real referral silently (pre-launch blocker 10).
    """
    if not hits:
        return "none", {}
    confident = [pid for pid, kinds in hits.items() if STRONG_IDENTIFIERS <= kinds and pid not in unverified]
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


Registrar = Literal["direct", "function"]


class PatientIngestionPipeline:
    def __init__(self, vault: DemographicVault, *, key_version: int, registrar: Registrar = "direct") -> None:
        if key_version not in vault.key_versions:
            raise ValueError(f"key_version {key_version} is not loaded in the vault {vault.key_versions}")
        if registrar not in ("direct", "function"):
            raise ValueError("registrar must be 'direct' or 'function'")
        self._vault = vault
        self._kv = key_version
        # "direct": the front-desk route inserts the three tables under its own row-level-security policies (a person's authenticated request).
        # "function": the unattended queue worker (the Front Door graph) registers through mpi.register_patient_from_referral (migration 0022), the only way it may write
        # them: that function creates one new patient and can attach tokens or candidates to that patient only (pre-launch blocker 1).
        self._registrar = registrar

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
        hits, unverified = await self._find(conn, clinic_id, origin, tokens)
        decision, reasons = classify(hits, unverified)
        if decision == "match":
            return IngestResult("matched", next(iter(reasons)))

        # 5. Create the record, its tokens and any review rows in one savepoint.
        try:
            async with conn.begin_nested():
                if self._registrar == "function":
                    pid = await self._register_via_function(
                        conn, clinic_id=clinic_id, origin=origin, name=name, dob_iso=dob_iso, phone_c=phone_c,
                        idempotency_key=idempotency_key, tokens=tokens, reasons=reasons, hits=hits,
                    )
                else:
                    pid = await self._register_directly(
                        conn, clinic_id=clinic_id, origin=origin, name=name, dob_iso=dob_iso, phone_c=phone_c,
                        idempotency_key=idempotency_key, tokens=tokens, reasons=reasons, hits=hits,
                    )
        except IntegrityError as exc:
            if "idempotency_key" in str(exc.orig):
                raise IdempotencyConflict("this idempotency key is already used by another request") from exc
            raise
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) == "42501":
                raise NotPermitted("this role may not register patients in this clinic") from exc
            raise
        return IngestResult("review" if reasons else "created", pid, tuple(reasons))

    async def _register_directly(self, conn, *, clinic_id, origin, name, dob_iso, phone_c, idempotency_key, tokens, reasons, hits) -> uuid.UUID:
        pid = (
            await conn.execute(
                text(
                    """insert into mpi.patients
                           (clinic_id, name_ct, name_key_version, dob_ct, dob_key_version,
                            phone_ct, phone_key_version, origin, idempotency_key)
                       values (:clinic, :name_ct, :kv, :dob_ct, :kv, :phone_ct, :phone_kv, :origin, :idem)
                       returning id"""
                ),
                self._patient_params(clinic_id, origin, name, dob_iso, phone_c, idempotency_key),
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
        return pid

    async def _register_via_function(self, conn, *, clinic_id, origin, name, dob_iso, phone_c, idempotency_key, tokens, reasons, hits) -> uuid.UUID:
        """One call to mpi.register_patient_from_referral (migration 0022): the same values the direct path inserts, handed over together so the database can bind the tokens and
        the candidates to the one patient it creates."""
        params = self._patient_params(clinic_id, origin, name, dob_iso, phone_c, idempotency_key)
        kinds = list(tokens)
        candidates = [{"patient_id": str(other), "kinds": sorted(hits[other]), "reason": reason} for other, reason in reasons.items()]
        return (
            await conn.execute(
                text(
                    """select mpi.register_patient_from_referral(
                           :clinic, :origin, :name_ct, cast(:kv as smallint), :dob_ct, cast(:kv as smallint), :phone_ct, cast(:phone_kv as smallint),
                           :idem, cast(:kinds as text[]), cast(:toks as bytea[]), cast(:kv as smallint), cast(:cands as jsonb))"""
                ),
                {**params, "kinds": kinds, "toks": [tokens[k] for k in kinds], "cands": json.dumps(candidates)},
            )
        ).scalar_one()

    def _patient_params(self, clinic_id, origin, name, dob_iso, phone_c, idempotency_key) -> dict:
        return {
            "clinic": clinic_id,
            "name_ct": self._vault.encrypt(name, field="name", key_version=self._kv),
            "dob_ct": self._vault.encrypt(dob_iso, field="dob", key_version=self._kv),
            "phone_ct": None if phone_c is None else self._vault.encrypt(phone_c, field="phone", key_version=self._kv),
            "kv": self._kv,
            "phone_kv": None if phone_c is None else self._kv,
            "origin": origin,
            "idem": idempotency_key,
        }

    async def _find(
        self, conn: AsyncConnection, clinic_id: uuid.UUID, origin: str, tokens: dict[str, bytes]
    ) -> tuple[dict[uuid.UUID, set[str]], set[uuid.UUID]]:
        """The existing records whose tokens agree, with the kinds that agreed, and which of them are UNVERIFIED (worker-created and not confirmed by front desk). A subordinate of an
        active merge counts as its master, and so does its verification status."""
        clauses = ["(t.kind = 'name_dob' and t.token = :nd)"]
        params: dict = {"clinic": clinic_id, "kv": self._kv, "origin": origin, "nd": tokens[NAME_DOB]}
        if PHONE in tokens:
            clauses.append("(t.kind = 'phone' and t.token = :ph)")
            params["ph"] = tokens[PHONE]
        rows = await conn.execute(
            text(
                f"""select coalesce(mg.master_patient_id, t.patient_id) as patient_id, array_agg(distinct t.kind) as kinds,
                           bool_or(coalesce(le.kind = 'confirm', pm.registered_via = 'front_desk')) as verified
                    from mpi.identity_tokens t
                    join mpi.patients p on p.id = t.patient_id and p.clinic_id = t.clinic_id
                    left join mpi.merges mg on mg.subordinate_patient_id = t.patient_id and mg.status = 'active'
                    join mpi.patients pm on pm.id = coalesce(mg.master_patient_id, t.patient_id) and pm.clinic_id = t.clinic_id
                    left join lateral (select e.kind from mpi.identity_confirmations e where e.patient_id = pm.id order by e.seq desc limit 1) le on true
                    where t.clinic_id = :clinic and t.key_version = :kv
                      and p.origin = :origin and p.erased_at is null
                      and ({' or '.join(clauses)})
                    group by 1"""
            ),
            params,
        )
        hits: dict[uuid.UUID, set[str]] = {}
        unverified: set[uuid.UUID] = set()
        for pid, kinds, verified in rows:
            hits[pid] = set(kinds)
            if not verified:
                unverified.add(pid)
        return hits, unverified


# ---- front-desk confirmation of a worker-created record (migration 0023; pre-launch blocker 10) ----------------------------------------------


class PatientNotFound(IngestionError):
    """No such live patient is visible to the caller (an invisible one is the same as a missing one)."""


@dataclass(frozen=True)
class ConfirmResult:
    patient_id: uuid.UUID
    replayed: bool


async def _lock_patient_events(conn: AsyncConnection, patient_id: uuid.UUID) -> None:
    """Serialise the confirm and revoke events of ONE patient (so `seq` order is event order and two clicks cannot interleave). Released at commit or rollback."""
    await conn.execute(text("select pg_advisory_xact_lock(hashtextextended('identity-confirmation:' || cast(:p as text), 0))"), {"p": str(patient_id)})


async def _latest_event(conn: AsyncConnection, patient_id: uuid.UUID) -> tuple[str, uuid.UUID, datetime] | None:
    row = (await conn.execute(text("select kind, id, created_at from mpi.identity_confirmations where patient_id = :p order by seq desc limit 1"), {"p": patient_id})).first()
    return None if row is None else (row[0], row[1], row[2])


async def _event_by_key(conn: AsyncConnection, key: str) -> tuple[uuid.UUID, uuid.UUID, str] | None:
    row = (await conn.execute(text("select patient_id, clinic_id, kind from mpi.identity_confirmations where idempotency_key = :k"), {"k": key})).first()
    return None if row is None else (row[0], row[1], row[2])


class RecordIsMerged(IngestionError):
    """The patient is the subordinate of an ACTIVE merge: the matcher takes verification from the master, so an event on this record would change nothing. Act on the master."""


async def _refuse_if_merged(conn: AsyncConnection, patient_id: uuid.UUID) -> None:
    merged = (await conn.execute(text("select 1 from mpi.merges where subordinate_patient_id = :p and status = 'active'"), {"p": patient_id})).first()
    if merged is not None:
        raise RecordIsMerged("this record is merged into another; act on that record")


def _check_write_arguments(origin: Origin, idempotency_key: str) -> None:
    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise MissingRequiredField("an idempotency key is required on every write")
    if origin not in ORIGINS:
        raise ValueError(f"origin must be one of {ORIGINS}, got {origin!r}")


async def confirm_identity(
    conn: AsyncConnection, *, clinic_id: uuid.UUID, origin: Origin, patient_id: uuid.UUID, confirmed_by: uuid.UUID, idempotency_key: str
) -> ConfirmResult:
    """Record that a front-desk person confirmed this patient's identity. After this, a match on the record (if it was created by the unattended worker) is a confident match again.
    Append-only and idempotent: the same key replays; a patient whose LATEST event is already a confirmation answers as a replay (under any key); a key used for a different patient, or
    for a revoke, is an IdempotencyConflict. After a revoke a new confirmation is a new event (migration 0025). Row-level security (front desk of the clinic, confirmed_by = the caller)
    is what permits the insert."""
    _check_write_arguments(origin, idempotency_key)
    await _lock_patient_events(conn, patient_id)
    by_key = await _event_by_key(conn, idempotency_key)
    if by_key is not None:
        if by_key[0] != patient_id or by_key[1] != clinic_id or by_key[2] != "confirm":
            raise IdempotencyConflict("this idempotency key was used for a different request")
        return ConfirmResult(patient_id, True)
    live = (await conn.execute(text("select 1 from mpi.patients where id = :p and clinic_id = :c and erased_at is null"), {"p": patient_id, "c": clinic_id})).first()
    if live is None:
        raise PatientNotFound("not found")
    await _refuse_if_merged(conn, patient_id)
    latest = await _latest_event(conn, patient_id)
    if latest is not None and latest[0] == "confirm":
        return ConfirmResult(patient_id, True)  # already confirmed: a replay in effect
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    """insert into mpi.identity_confirmations (clinic_id, patient_id, confirmed_by, origin, idempotency_key, kind)
                       values (:c, :p, :u, :o, :k, 'confirm')"""
                ),
                {"c": clinic_id, "p": patient_id, "u": confirmed_by, "o": origin, "k": idempotency_key},
            )
    except DBAPIError as exc:
        state = getattr(exc.orig, "sqlstate", None)
        if state == "42501":
            raise NotPermitted("this role may not confirm identities in this clinic") from exc
        if state == "23505":  # the key is held by a row this role cannot see (another clinic's): the same answer as a key used for a different request
            raise IdempotencyConflict("this idempotency key was used for a different request") from exc
        raise
    return ConfirmResult(patient_id, False)


# ---- revoking a mistaken confirmation (migration 0025; docs/design/identity-confirmation-revoke.md) ------------------------------------------------

REVOKE_REASONS: Final = ("wrong_person", "planted_or_suspect", "mistaken_click")  # a CLOSED operational vocabulary (no free text); listed in docs/design/clinician-sheet.md
REVOKE_TASK_KIND: Final = "identity_confirmation_revoked"
TaskStatus = Literal["raised", "not_configured", "on_record"]


class InvalidRevokeReason(IngestionError):
    """The reason is not one of REVOKE_REASONS."""


class RevokeTaskSettings(BaseSettings):
    """The nurse review task a revoke raises. Both values are CLINICIAN-OWNED: `# CLINICAL-PLACEHOLDER: needs clinician sign-off` (docs/design/clinician-sheet.md). None = NOT
    CONFIGURED, there is no default: the revoke itself is still recorded (it only moves the record towards 'a person decides'), the response and the audit row say the task was NOT
    raised, and nobody is told that a re-check was queued when it was not."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    # CLINICAL-PLACEHOLDER: needs clinician sign-off  (the review task's priority: 'immediate' or 'standard')
    priority: Literal["immediate", "standard"] | None = Field(default=None, validation_alias=AliasChoices("CAREFLOW_REVOKE_TASK_PRIORITY", "priority"))
    # CLINICAL-PLACEHOLDER: needs clinician sign-off  (who re-checks what was linked meanwhile: 'nurse' or 'doctor')
    assigned_role: Literal["nurse", "doctor"] | None = Field(default=None, validation_alias=AliasChoices("CAREFLOW_REVOKE_TASK_ROLE", "assigned_role"))

    @property
    def configured(self) -> bool:
        return self.priority is not None and self.assigned_role is not None


@dataclass(frozen=True)
class RevokeResult:
    patient_id: uuid.UUID
    replayed: bool
    review_task: TaskStatus


async def revoke_identity(
    conn: AsyncConnection, *, clinic_id: uuid.UUID, origin: Origin, patient_id: uuid.UUID, revoked_by: uuid.UUID, reason: str, idempotency_key: str,
    task: RevokeTaskSettings | None = None,
) -> RevokeResult:
    """Record that a front-desk person revokes this patient's identity confirmation (or demotes a front-desk-created record): the record is UNVERIFIED again, so a full match on it is a
    review, never an automatic link. Nothing already decided is undone. One person, as themselves, no step-up: a revoke only moves a record towards 'a person decides'.
    Append-only and idempotent: the same key replays; a record that is already unverified (latest event a revoke, or a worker-created record with no confirmation) answers as a replay in
    effect and writes nothing. Raises ONE review task for a nurse or doctor listing the patient and the window, when the task settings are configured (ids and closed words only)."""
    _check_write_arguments(origin, idempotency_key)
    if reason not in REVOKE_REASONS:
        raise InvalidRevokeReason("the reason must be one of the listed words")
    await _lock_patient_events(conn, patient_id)
    by_key = await _event_by_key(conn, idempotency_key)
    if by_key is not None:
        if by_key[0] != patient_id or by_key[1] != clinic_id or by_key[2] != "revoke":
            raise IdempotencyConflict("this idempotency key was used for a different request")
        return RevokeResult(patient_id, True, "on_record")
    row = (await conn.execute(text("select registered_via from mpi.patients where id = :p and clinic_id = :c and erased_at is null"), {"p": patient_id, "c": clinic_id})).first()
    if row is None:
        raise PatientNotFound("not found")
    await _refuse_if_merged(conn, patient_id)
    latest = await _latest_event(conn, patient_id)
    verified = (latest[0] == "confirm") if latest is not None else (row[0] == "front_desk")
    if not verified:
        return RevokeResult(patient_id, True, "on_record")  # already unverified: nothing to revoke
    confirmed_at = latest[2] if latest is not None else None
    event_id = uuid.uuid4()
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    """insert into mpi.identity_confirmations (id, clinic_id, patient_id, confirmed_by, origin, idempotency_key, kind, reason)
                       values (:i, :c, :p, :u, :o, :k, 'revoke', :r)"""
                ),
                {"i": event_id, "c": clinic_id, "p": patient_id, "u": revoked_by, "o": origin, "k": idempotency_key, "r": reason},
            )
    except DBAPIError as exc:
        state = getattr(exc.orig, "sqlstate", None)
        if state == "42501":
            raise NotPermitted("this role may not revoke identities in this clinic") from exc
        if state == "23505":
            raise IdempotencyConflict("this idempotency key was used for a different request") from exc
        raise
    settings = task or RevokeTaskSettings()
    if not settings.configured:
        return RevokeResult(patient_id, False, "not_configured")
    provenance = {
        "patient_id": str(patient_id), "revoke_event_id": str(event_id), "reason": reason,
        "confirmed_at": confirmed_at.isoformat() if confirmed_at is not None else None, "record_created_by": row[0],
    }
    try:
        # NO `returning` and NO `on conflict`: front desk may insert this kind of task but cannot read it, and both of those need the SELECT policy.
        async with conn.begin_nested():
            await conn.execute(
                text(
                    """insert into agents.review_tasks (id, clinic_id, origin, kind, priority, assigned_role, source_message_id, provenance, idempotency_key)
                       values (:i, :c, :o, :kind, :pr, :role, :src, cast(:p as jsonb), :k)"""
                ),
                {
                    "i": uuid.uuid4(), "c": clinic_id, "o": origin, "kind": REVOKE_TASK_KIND, "pr": settings.priority, "role": settings.assigned_role,
                    "src": f"identity-revoke:{event_id}", "p": json.dumps(provenance, sort_keys=True), "k": f"identity-revoke-review:{event_id}",
                },
            )
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) != "23505":  # a duplicate task for this revoke event is a replay; anything else rolls the revoke back
            raise
    return RevokeResult(patient_id, False, "raised")
