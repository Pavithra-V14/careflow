"""FastAPI application: token verification, per-request identity binding, and the first route
(BUILD-SPEC.md §18.1, §18.4, §21; CLAUDE.md rule 8).

Request lifecycle for every authenticated route:

  1. `verified_claims` reads the Bearer token and verifies it with careflow.auth (ES256, kid-selected key,
     issuer, audience, expiry, role). Anything wrong is a 401 with one generic body: the reason goes to
     the log, never to the caller. If the key source is unreachable and no cached key can vouch for the
     token, the answer is a 503, not a guess.
  2. `db_conn` checks a connection out of the pool behind the TRANSACTION-MODE pooler (the restricted
     `careflow_app` role, careflow.db.make_app_engine), opens ONE transaction, and as its first statement
     binds the verified claims with `select set_config('request.jwt.claims', $1, true)` (bound parameter,
     transaction-local). Only then does any route code touch the database, so `auth.uid()` in every RLS
     policy resolves to the verified caller, and the identity dies with the transaction: a pooled
     connection carries nothing into the next request.
  3. The route runs inside that transaction; it commits when the route returns and rolls back on error. The
     dependency is declared with `scope="function"` (`TxConn`), so the commit happens BEFORE the response is
     sent: a failed commit is a 500, never a 200 for a write that was lost.

Row-level security, not application code, enforces clinic isolation. Routes must therefore never open
their own connection or run SQL outside the `db_conn` transaction.

Every route added to this application must add its own case to the role-matrix test
(`tests/test_api_role_matrix.py`) in the same change; `test_every_route_is_in_the_matrix` fails if one is
missing.
"""

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from langgraph.types import Command
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from careflow import identity, mpi
from careflow.auth import (
    AuthError,
    AuthUnavailable,
    JWKSClient,
    step_up_failure,
    verify_token,
)
from careflow.checkpointer import CheckpointerError
from careflow.crypto import CryptoError, DemographicVault
from careflow.db import make_app_engine, set_request_claims
from careflow.dead_letters import raise_dead_letter_tasks
from careflow.front_door import FrontDoorError, run_config
from careflow.front_door_runner import TASK_TYPE as FRONT_DOOR_TASK
from careflow.front_door_runner import (
    FrontDoorComponents,
    front_door_handler,
    graph_for,
    request_session,
    worker_session,
)
from careflow.messages import (
    MessageErased,
    MessageNotFound,
    MessageUnreadable,
    read_stored_message,
    screen_stored_message,
)
from careflow.mpi import PatientIngestionPipeline
from careflow.queue import NotPermitted as QueueNotPermitted
from careflow.queue import PostgresQueue
from careflow.readiness import check_readiness, parse_required_roles
from careflow.registry import RegistryError, load_registry
from careflow.rules import NotPermitted as ScreeningNotPermitted
from careflow.rules import RedFlagEngine, resolve_profile
from careflow.scheduling import (
    BookingNotFound,
    IdempotencyConflict,
    InvalidTransition,
    NotPermitted,
    SchedulingError,
    SlotManager,
    SlotNotFound,
    SlotUnavailable,
    UnknownReference,
)
from careflow.tick import Poker, worker_loop
from careflow.triage_store import DecisionConflict, TriageStoreError
from careflow.triage_store import NotPermitted as TriageNotPermitted

log = logging.getLogger("careflow.app")
_bearer = HTTPBearer(auto_error=False)


def _unauthenticated() -> HTTPException:
    """One generic body for every credential failure; the reason is logged, never returned."""
    return HTTPException(status_code=401, detail="invalid or missing credentials", headers={"WWW-Authenticate": "Bearer"})


class Settings(BaseSettings):
    """Read from the environment (or a local .env). Never logged."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    supabase_url: str
    database_url_app: str
    supabase_jwks_url: str | None = None
    # Which key version NEW demographics are encrypted under (BUILD-SPEC 17.2, docs/runbooks/key_rotation.md). Optional while exactly one
    # version is loaded; REQUIRED once several are (rotation), so a new key is never adopted by accident: the registration route answers
    # a visible 503 until it is set.
    active_key_version: int | None = Field(default=None, validation_alias=AliasChoices("CAREFLOW_ACTIVE_KEY_VERSION", "active_key_version"))

    # How recently a second factor must have been verified for a sensitive action (BUILD-SPEC 18.1 step-up). REQUIRED, no code default:
    # until it is set the merge routes answer a visible 503 (and log an error) instead of guessing a window.
    step_up_max_age_seconds: int | None = Field(
        default=None, validation_alias=AliasChoices("CAREFLOW_STEP_UP_MAX_AGE_SECONDS", "step_up_max_age_seconds")
    )

    # The in-process worker (BUILD-SPEC 7.4): off unless asked for, so tests and tools that start the app never drain the queue. It needs the
    # id of a `queue_worker` account (row-level security decides which clinics' jobs it can see). Both are plain deployment settings.
    run_worker: bool = Field(default=False, validation_alias=AliasChoices("CAREFLOW_RUN_WORKER", "run_worker"))
    worker_account_id: uuid.UUID | None = Field(default=None, validation_alias=AliasChoices("CAREFLOW_WORKER_ACCOUNT_ID", "worker_account_id"))

    # Roles whose absence of a phi_approved route makes a `clinical` deployment report NOT ready (BUILD-SPEC 2). A comma list with no default in
    # code: which roles are launch-blocking is a deployment decision. Unset means "not configured", and a clinical deployment is then not ready.
    required_roles: str | None = Field(default=None, validation_alias=AliasChoices("CAREFLOW_REQUIRED_ROLES", "required_roles"))

    @property
    def issuer(self) -> str:
        return self.supabase_url.rstrip("/") + "/auth/v1"

    @property
    def jwks_url(self) -> str:
        return self.supabase_jwks_url or self.issuer + "/.well-known/jwks.json"


# ---- dependencies ----------------------------------------------------------------------------------


async def verified_claims(
    request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)
) -> dict[str, Any]:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthenticated()
    try:
        return await verify_token(credentials.credentials, request.app.state.jwks, issuer=request.app.state.issuer)
    except AuthError as exc:
        log.info("rejected token: %s", exc)
        raise _unauthenticated() from None
    except AuthUnavailable as exc:
        log.warning("cannot verify tokens right now: %s", exc)
        raise HTTPException(status_code=503, detail="authentication temporarily unavailable") from None


async def db_conn(request: Request, claims: dict[str, Any] = Depends(verified_claims)) -> AsyncIterator[AsyncConnection]:
    engine: AsyncEngine = request.app.state.engine
    async with engine.connect() as conn, conn.begin():
        await set_request_claims(conn, claims)  # first statement of the transaction, before any query
        yield conn


# Commit before the response goes out (see the lifecycle note above).
TxConn = Annotated[AsyncConnection, Depends(db_conn, scope="function")]


async def require_step_up(request: Request, claims: dict[str, Any] = Depends(verified_claims)) -> None:
    """Step-up re-authentication (BUILD-SPEC 18.1): a second factor verified within CAREFLOW_STEP_UP_MAX_AGE_SECONDS. The decision is the pure
    careflow.auth.step_up_failure; this wrapper supplies the configured window and the clock. A refusal is ONE fixed 401 with
    `WWW-Authenticate: step-up` and no reason; the reason code goes to the log, never a claim value. It runs before any database work.
    Authentication still comes first (verified_claims). The claim shape is UNVERIFIED against a real token (docs/design/step-up-spike.md)."""
    max_age = request.app.state.step_up_max_age_seconds
    if not isinstance(max_age, int) or isinstance(max_age, bool) or max_age <= 0:
        log.error("step-up is unavailable: CAREFLOW_STEP_UP_MAX_AGE_SECONDS is not set to a positive number of seconds")
        raise HTTPException(status_code=503, detail="step-up re-authentication is not configured")
    reason = step_up_failure(claims, max_age_seconds=max_age, now=request.app.state.clock())
    if reason is not None:
        log.info("step-up refused: %s", reason)
        raise HTTPException(status_code=401, detail="step-up required", headers={"WWW-Authenticate": "step-up"})

# ---- routes ----------------------------------------------------------------------------------------

router = APIRouter()


class RoleGrant(BaseModel):
    clinic_id: uuid.UUID
    role: str


class Me(BaseModel):
    account_id: uuid.UUID
    account_type: str
    status: str
    roles: list[RoleGrant]


@router.get("/v1/iam/me", response_model=Me)
async def me(conn: TxConn, claims: dict[str, Any] = Depends(verified_claims)) -> Me:
    """The caller's own active profile: account type, status, and live staff role grants.

    A valid token alone is not enough: the caller needs an ACTIVE row in iam.accounts. A person who signed
    up with Supabase Auth but has no account here (or whose account is invited, suspended or closed) gets
    the same 403, and learns nothing about which case it was.
    """
    account_id = uuid.UUID(claims["sub"])
    account = (
        await conn.execute(
            text("select id, account_type, status from iam.accounts where id = :i"), {"i": account_id}
        )
    ).first()
    if account is None or account.status != "active":
        raise HTTPException(status_code=403, detail="no active account")
    grants = await conn.execute(
        text(
            "select clinic_id, role from iam.staff_roles "
            "where account_id = :i and revoked_at is null order by clinic_id, role"
        ),
        {"i": account_id},
    )
    return Me(
        account_id=account.id,
        account_type=account.account_type,
        status=account.status,
        roles=[RoleGrant(clinic_id=g.clinic_id, role=g.role) for g in grants],
    )


# ---- staff scheduling (BUILD-SPEC.md §17.1, §17.5, §18.4; CLAUDE.md rules 5 and 8) --------------------------------
#
# Thin HTTP over `SlotManager`. Nothing here decides who may do what: row-level security does, and a refusal comes
# back as the status below. In practice only front desk of the slot's own clinic can write; every other role,
# another clinic's front desk and any non-staff account gets 403 or 404 and changes nothing. A request names no
# clinic: it is whatever the verified identity can see, so an unknown or foreign id is simply "not found".
#
# Every write needs an `X-Idempotency-Key` header (rule 5). Replaying a key with the same parameters returns the
# original result with `replayed: true`; the same key with different parameters is 409. A missing or blank key is 422.

scheduling = APIRouter(prefix="/v1/staff/scheduling", tags=["staff scheduling"])
_slots = SlotManager()

IdempotencyKey = Annotated[
    str, Header(alias="X-Idempotency-Key", min_length=1, max_length=200, pattern=r"\S", description="Required on every write.")
]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Out(BaseModel):
    """Base for every scheduling response: building one with a field the schema does not name is an error, so a
    query that starts returning a new column cannot leak it to clients by accident."""

    model_config = ConfigDict(extra="forbid")


class HoldIn(_Body):
    # The cap is only a guard against interval overflow in the database, not an operational policy.
    duration_minutes: int = Field(gt=0, le=1_000_000)
    patient_id: uuid.UUID | None = None  # optional: hold the slot for one patient; nobody else can then book it


class HoldOut(_Out):
    hold_id: uuid.UUID
    slot_id: uuid.UUID
    expires_at: datetime
    replayed: bool


class BookIn(_Body):
    patient_id: uuid.UUID


class BookOut(_Out):
    booking_id: uuid.UUID
    slot_id: uuid.UUID
    patient_id: uuid.UUID
    status: str
    replayed: bool


class CandidateOut(_Out):
    waitlist_id: uuid.UUID
    patient_id: uuid.UUID
    priority_code: str
    rank: int
    joined_at: datetime


class CancelOut(_Out):
    booking_id: uuid.UUID
    slot_id: uuid.UUID
    candidates: list[CandidateOut]  # best first; informational only, nobody is held, booked or contacted
    replayed: bool


class WaitlistJoinIn(_Body):
    patient_id: uuid.UUID
    visit_type_id: uuid.UUID
    # A priority level the clinic's clinicians defined (sched.priority_levels). No default, no free text.
    clinical_priority: str = Field(min_length=1, max_length=64)


class WaitlistJoinOut(_Out):
    waitlist_id: uuid.UUID
    patient_id: uuid.UUID
    visit_type_id: uuid.UUID
    clinical_priority: str
    replayed: bool


@contextmanager
def _scheduling_errors() -> Iterator[None]:
    """Turn the scheduling module's refusals into clean client errors. The transaction rolls back with them."""
    try:
        yield
    except (SlotNotFound, BookingNotFound):
        raise HTTPException(status_code=404, detail="not found") from None
    except UnknownReference as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except SlotUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except IdempotencyConflict:
        raise HTTPException(status_code=409, detail="this idempotency key was already used by a different request") from None
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except NotPermitted:
        raise HTTPException(status_code=403, detail="your role may not make this change") from None
    except SchedulingError as exc:
        log.info("scheduling request rejected: %s", exc)  # may carry database text: logged, never returned
        raise HTTPException(status_code=422, detail="request rejected") from None


@scheduling.post("/slots/{slot_id}/hold", response_model=HoldOut)
async def hold_slot(slot_id: uuid.UUID, body: HoldIn, idempotency_key: IdempotencyKey, conn: TxConn) -> HoldOut:
    """Hold an open slot for `duration_minutes`. 409 if it is held, booked or cancelled."""
    with _scheduling_errors():
        r = await _slots.hold_slot(
            conn, slot_id, body.duration_minutes, idempotency_key=idempotency_key, patient_id=body.patient_id
        )
    return HoldOut(hold_id=r.hold_id, slot_id=r.slot_id, expires_at=r.expires_at, replayed=r.replayed)


@scheduling.post("/slots/{slot_id}/book", response_model=BookOut)
async def book_slot(slot_id: uuid.UUID, body: BookIn, idempotency_key: IdempotencyKey, conn: TxConn) -> BookOut:
    """Book an open slot (or one held for this patient) for `patient_id`. 409 if the slot is taken."""
    with _scheduling_errors():
        r = await _slots.book_appointment(conn, slot_id, body.patient_id, idempotency_key)
    return BookOut(booking_id=r.booking_id, slot_id=r.slot_id, patient_id=r.patient_id, status=r.status, replayed=r.replayed)


@scheduling.post("/bookings/{booking_id}/cancel", response_model=CancelOut)
async def cancel_booking(booking_id: uuid.UUID, idempotency_key: IdempotencyKey, conn: TxConn) -> CancelOut:
    """Cancel a booking, free its slot, and return the ranked waitlist candidates. Nobody is contacted or booked."""
    with _scheduling_errors():
        r = await _slots.cancel_appointment(conn, booking_id, idempotency_key=idempotency_key)
    return CancelOut(
        booking_id=r.booking_id,
        slot_id=r.slot_id,
        candidates=[CandidateOut(**vars(c)) for c in r.candidates],
        replayed=r.replayed,
    )


@scheduling.post("/waitlist/join", response_model=WaitlistJoinOut)
async def join_waitlist(body: WaitlistJoinIn, idempotency_key: IdempotencyKey, conn: TxConn) -> WaitlistJoinOut:
    """Queue a patient for a visit type at a clinician-defined priority level. 409 if already waiting."""
    with _scheduling_errors():
        r = await _slots.join_waitlist(
            conn, body.patient_id, body.visit_type_id, body.clinical_priority, idempotency_key=idempotency_key
        )
    return WaitlistJoinOut(
        waitlist_id=r.waitlist_id,
        patient_id=r.patient_id,
        visit_type_id=r.visit_type_id,
        clinical_priority=r.priority_code,
        replayed=r.replayed,
    )


# ---- staff scheduling: reads ---------------------------------------------------------------------------------
#
# Who may read: front desk, nurse and doctor. Row-level security would let an admin read slots and return an empty
# list for everyone else, so on its own it cannot give a clean refusal. These routes therefore add a coarse gate on
# top: the caller must hold one of those three roles in some clinic (403 otherwise), and every query is narrowed to
# the clinics where they hold one. That only ever REMOVES rows: RLS remains the isolation layer underneath. Lists are
# paged by `limit` (default 100, max 500) and say whether more exist; they return identifiers, never patient text.

SCHEDULING_READERS = ("front_desk", "nurse", "doctor")
Limit = Annotated[int, Query(ge=1, le=500)]


async def _readable_clinics(conn: AsyncConnection, claims: dict[str, Any]) -> list[uuid.UUID]:
    rows = await conn.execute(
        text(
            "select distinct sr.clinic_id from iam.staff_roles sr join iam.accounts a on a.id = sr.account_id "
            "where sr.account_id = :u and sr.revoked_at is null and sr.role = any(:roles) "
            "and a.status = 'active' and a.account_type = 'staff'"
        ),
        {"u": uuid.UUID(claims["sub"]), "roles": list(SCHEDULING_READERS)},
    )
    clinics = [r[0] for r in rows]
    if not clinics:
        raise HTTPException(status_code=403, detail="your role may not read this")
    return clinics


class SlotOut(_Out):
    slot_id: uuid.UUID
    clinic_id: uuid.UUID
    provider_id: uuid.UUID
    location_id: uuid.UUID
    visit_type_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    status: Literal["open", "held"]
    hold_expires_at: datetime | None  # set only while a live hold exists


class SlotList(_Out):
    items: list[SlotOut]
    limit: int
    has_more: bool


class BookingOut(_Out):
    booking_id: uuid.UUID
    clinic_id: uuid.UUID
    slot_id: uuid.UUID
    patient_id: uuid.UUID
    provider_id: uuid.UUID
    location_id: uuid.UUID
    visit_type_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    created_at: datetime


class BookingList(_Out):
    items: list[BookingOut]
    limit: int
    has_more: bool


class WaitlistEntryOut(_Out):
    waitlist_id: uuid.UUID
    clinic_id: uuid.UUID
    patient_id: uuid.UUID
    visit_type_id: uuid.UUID
    clinical_priority: str
    rank: int
    joined_at: datetime


class WaitlistList(_Out):
    items: list[WaitlistEntryOut]  # clinical priority first (rank 1 first), then who joined first
    limit: int
    has_more: bool


@scheduling.get("/slots", response_model=SlotList)
async def list_slots(
    conn: TxConn,
    claims: dict[str, Any] = Depends(verified_claims),
    provider_id: uuid.UUID | None = None,
    location_id: uuid.UUID | None = None,
    starts_after: datetime | None = None,
    limit: Limit = 100,
) -> SlotList:
    """Open and held slots, soonest first, optionally for one provider and/or location."""
    clinics = await _readable_clinics(conn, claims)
    rows = await _slots.list_slots(
        conn, clinics, provider_id=provider_id, location_id=location_id, starts_after=starts_after, limit=limit + 1
    )
    items = [
        SlotOut(slot_id=r.id, clinic_id=r.clinic_id, provider_id=r.provider_id, location_id=r.location_id,
                visit_type_id=r.visit_type_id, starts_at=r.starts_at, ends_at=r.ends_at, status=r.status,
                hold_expires_at=r.hold_expires_at)
        for r in rows[:limit]
    ]
    return SlotList(items=items, limit=limit, has_more=len(rows) > limit)


@scheduling.get("/bookings", response_model=BookingList)
async def list_bookings(
    conn: TxConn, claims: dict[str, Any] = Depends(verified_claims), limit: Limit = 100
) -> BookingList:
    """Confirmed bookings, soonest appointment first."""
    clinics = await _readable_clinics(conn, claims)
    rows = await _slots.list_bookings(conn, clinics, limit=limit + 1)
    items = [
        BookingOut(booking_id=r.id, clinic_id=r.clinic_id, slot_id=r.slot_id, patient_id=r.patient_id,
                   provider_id=r.provider_id, location_id=r.location_id, visit_type_id=r.visit_type_id,
                   starts_at=r.starts_at, ends_at=r.ends_at, created_at=r.created_at)
        for r in rows[:limit]
    ]
    return BookingList(items=items, limit=limit, has_more=len(rows) > limit)


@scheduling.get("/waitlist", response_model=WaitlistList)
async def list_waitlist(
    conn: TxConn,
    claims: dict[str, Any] = Depends(verified_claims),
    visit_type_id: uuid.UUID | None = None,
    limit: Limit = 100,
) -> WaitlistList:
    """The waiting queue, clinical priority first and join time second, optionally for one visit type."""
    clinics = await _readable_clinics(conn, claims)
    rows = await _slots.list_waitlist(conn, clinics, visit_type_id=visit_type_id, limit=limit + 1)
    items = [
        WaitlistEntryOut(waitlist_id=r.id, clinic_id=r.clinic_id, patient_id=r.patient_id, visit_type_id=r.visit_type_id,
                         clinical_priority=r.priority_code, rank=r.rank, joined_at=r.joined_at)
        for r in rows[:limit]
    ]
    return WaitlistList(items=items, limit=limit, has_more=len(rows) > limit)


# ---- staff messages: red-flag screening of a stored message (BUILD-SPEC.md §5.2, §12.1; CLAUDE.md rules 2, 3, 5, 7, 8) --------------
#
# `POST /v1/staff/messages/{message_id}/screen` screens the stored (encrypted) body of one message with the deterministic rules, FIRST
# and ALONE (no model), and records the result through msg.record_screening: a red flag, or an unassessable result, raises the nurse
# task. NO emergency reply is sent or written:
#   # CLINICAL-PLACEHOLDER: needs clinician sign-off
# (the patient-facing wording is clinician-authored and does not exist yet; the screening row records patient_reply =
# pending_clinician_template). The nurse task for an UNASSESSABLE result has priority 'standard':
#   # CLINICAL-PLACEHOLDER: needs clinician sign-off  (pending clinical lead confirmation; set in migration 0013, not here)
# The body is decrypted only inside careflow.messages.screen_stored_message and never leaves it.
#
# A body that cannot be read (tampered or mis-bound, or encrypted under a key version this process does not have) is NEVER left
# unscreened: it is recorded UNASSESSABLE, which raises a nurse task, and this layer logs one warning with the message id, the clinic id
# and the error CLASS only (never any text or key material). Only a missing key vault, meaning no encryption secrets are configured at
# all, is a 503: that is a deployment fault, logged loudly, and nothing can be screened.
#
# Who may call it is decided by row-level security and by msg.record_screening, not by this route: nurse and doctor (every topic) and
# front desk (appointments and safety only) can SEE the message, so only they can screen it. Everyone else, an unknown id and a message
# in another clinic all get the same 404, so a guess reveals nothing. A queue_worker has no HTTP role here (it screens in-process).
# Needs `X-Idempotency-Key` (rule 5): replaying it returns the original screening with `replayed: true`.

staff_messages = APIRouter(prefix="/v1/staff/messages", tags=["staff messages"])
_screening_engine = RedFlagEngine()


class ScreenOut(_Out):
    """Ids and enums only: never message text, a rule phrase, a rule code or any patient detail."""

    message_id: uuid.UUID
    screening_id: uuid.UUID
    outcome: Literal["red_flag", "unassessable", "clear"]
    replayed: bool
    patient_reply: Literal["not_required", "pending_clinician_template"] | None


@staff_messages.post("/{message_id}/screen", response_model=ScreenOut)
async def screen_message(message_id: uuid.UUID, idempotency_key: IdempotencyKey, conn: TxConn, request: Request) -> ScreenOut:
    """Screen a stored message for red flags. 404 if you cannot see it; 409 if its body was erased; 503 only if no encryption keys are
    configured at all. An unreadable body is recorded `unassessable` (a nurse task), never left unscreened."""
    vault: DemographicVault | None = request.app.state.vault
    if vault is None:
        log.error("message screening is unavailable: no encryption keys are configured (deployment fault; nothing can be screened)")
        raise HTTPException(status_code=503, detail="message screening is unavailable: encryption keys are not configured")
    try:
        r = await screen_stored_message(conn, vault, _screening_engine, message_id, idempotency_key=idempotency_key)
    except (MessageNotFound, ScreeningNotPermitted):
        raise HTTPException(status_code=404, detail="not found") from None
    except MessageErased:
        raise HTTPException(status_code=409, detail="the message body was erased") from None
    if r.unreadable_reason is not None:
        log.warning(
            "stored message body could not be read; recorded unassessable (message=%s clinic=%s error=%s)",
            r.message_id, r.clinic_id, r.unreadable_reason,
        )
    return ScreenOut(message_id=r.message_id, screening_id=r.screening_id, outcome=r.outcome, replayed=r.replayed, patient_reply=r.patient_reply)  # type: ignore[arg-type]


# ---- staff message read: GET /v1/staff/messages/{message_id} (BUILD-SPEC.md 9.4 audit principle; CLAUDE.md rules 5, 6, 8) -------------------
#
# Reading a decrypted message is a patient-data access. Who may read is decided by row-level security on msg.messages: nurse and doctor read
# every topic, front desk reads appointments and safety, and every other role, every inactive account and every other clinic get the SAME
# fixed 404 as a message that does not exist. Admin, business owner and auditor get no read access to message bodies.
# The body is decrypted ONLY inside careflow.messages.read_stored_message, which also writes the `audit.log` row (ids only, key
# `message-read:<uuid4>`) in this request's transaction BEFORE anything is returned: a body is never handed out without its audit row, and
# commit-before-response (TxConn) makes a 200 mean the row is durable. Refused reads write no audit row; they log one line with ids only
# (denied-access monitoring is a deferred Phase 4 watchdog item). The response is `Cache-Control: no-store`; the body is never logged and
# never in an error. An erased message is 410 for a caller who can see it. A GET carries no X-Idempotency-Key: the audit key is server-made.


class MessageReadOut(_Out):
    """The only response in the API that carries message text: authorised staff only, never cached."""

    message_id: uuid.UUID
    thread_id: uuid.UUID
    topic: Literal["care", "appointments", "safety"]
    sender_kind: str
    body: str


@staff_messages.get("/{message_id}", response_model=MessageReadOut)
async def read_message(
    message_id: uuid.UUID, conn: TxConn, request: Request, response: Response, claims: dict[str, Any] = Depends(verified_claims)
) -> MessageReadOut:
    """Read one message (and audit the read). 404 if you cannot see it, 410 if its body was erased, 503/500 (fixed bodies) if it cannot be
    decrypted. The body goes only to an authorised staff caller and is never cached."""
    vault: DemographicVault | None = request.app.state.vault
    if vault is None:
        log.error("message reading is unavailable: no encryption keys are configured (deployment fault)")
        raise HTTPException(status_code=503, detail="message reading is unavailable: encryption keys are not configured")
    actor = uuid.UUID(claims["sub"])
    try:
        m = await read_stored_message(conn, vault, message_id, actor_id=actor)
    except MessageNotFound:
        log.warning("message read refused (message=%s actor=%s status=404)", message_id, actor)
        raise HTTPException(status_code=404, detail="not found") from None
    except MessageErased:
        log.info("message read refused (message=%s actor=%s status=410)", message_id, actor)
        raise HTTPException(status_code=410, detail="the message body was erased") from None
    except MessageUnreadable as exc:
        missing_key = exc.reason == "UnknownKeyVersionError"
        log.log(
            logging.ERROR if missing_key else logging.WARNING,
            "stored message body could not be read (message=%s clinic=%s actor=%s error=%s)", exc.message_id, exc.clinic_id, actor, exc.reason,
        )
        if missing_key:
            raise HTTPException(status_code=503, detail="this message cannot be read right now") from None
        raise HTTPException(status_code=500, detail="this message cannot be read") from None
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return MessageReadOut(message_id=m.message_id, thread_id=m.thread_id, topic=m.topic, sender_kind=m.sender_kind, body=m.body)  # type: ignore[arg-type]


# ---- staff patients: registration / identity resolution (BUILD-SPEC.md §17.2, §12.1; CLAUDE.md rules 5, 6, 7, 8) ----------------------------
#
# `POST /v1/staff/patients` takes a person's name, date of birth and (optionally) phone, and either links to an existing record, creates
# a new one, or creates a new one AND queues a duplicate review: careflow.mpi.PatientIngestionPipeline. It never merges and never edits.
#
# PRIVACY CONTRACT. The name, date of birth and phone are encrypted before they reach the database and are NEVER returned, logged or put in
# an error: the response is ids and an outcome only; every error body is a fixed string; and FastAPI's default 422 body (which echoes the
# offending input) is replaced app-wide by one that never includes it. Which clinic is part of the request, but the ORIGIN tag is read from
# that clinic's own row, never from the client. A clinic you cannot see and a clinic you can see but are not front desk of are different
# answers on purpose (404 and 403), as for the scheduling writes: nobody can probe clinics they have no relationship with.
# Needs `X-Idempotency-Key` (rule 5): replaying it returns the original result with outcome `replayed`.

staff_patients = APIRouter(prefix="/v1/staff/patients", tags=["staff patients"])


class IngestIn(_Body):
    clinic_id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)
    dob: str = Field(min_length=1, max_length=10, description="YYYY-MM-DD. An age is never a date of birth.")
    phone: str | None = Field(default=None, min_length=1, max_length=40)


class IngestOut(_Out):
    """Ids and an outcome only: never a name, date of birth or phone."""

    patient_id: uuid.UUID
    outcome: Literal["matched", "created", "review", "replayed"]
    candidate_ids: list[uuid.UUID]  # existing records queued for a person to review (outcome `review`)


def _active_key_version(request: Request) -> int | None:
    vault: DemographicVault = request.app.state.vault
    configured: int | None = request.app.state.active_key_version
    if configured is not None:
        return configured if configured in vault.key_versions else None
    versions = vault.key_versions
    return versions[0] if len(versions) == 1 else None


@staff_patients.post("", response_model=IngestOut)
async def register_patient(
    body: IngestIn, idempotency_key: IdempotencyKey, conn: TxConn, request: Request, claims: dict[str, Any] = Depends(verified_claims)
) -> IngestOut:
    """Register or resolve a patient. 404 for a clinic you cannot see, 403 if you are not front desk there, 409 if the key was used for
    another person, 422 for an invalid field (named, never echoed), 503 if the key vault or active key version is not configured."""
    vault: DemographicVault | None = request.app.state.vault
    if vault is None:
        log.error("patient registration is unavailable: no encryption keys are configured (deployment fault)")
        raise HTTPException(status_code=503, detail="patient registration is unavailable: encryption keys are not configured")
    key_version = _active_key_version(request)
    if key_version is None:
        log.error("patient registration is unavailable: CAREFLOW_ACTIVE_KEY_VERSION must name a loaded key version when several are loaded")
        raise HTTPException(status_code=503, detail="patient registration is unavailable: the active encryption key version is not configured")
    clinic = (await conn.execute(text("select origin from iam.clinics where id = :c"), {"c": body.clinic_id})).first()
    if clinic is None:
        raise HTTPException(status_code=404, detail="not found")
    is_front_desk = (
        await conn.execute(
            text(
                "select 1 from iam.staff_roles sr join iam.accounts a on a.id = sr.account_id "
                "where sr.account_id = :u and sr.clinic_id = :c and sr.role = 'front_desk' and sr.revoked_at is null "
                "and a.status = 'active' and a.account_type = 'staff'"
            ),
            {"u": uuid.UUID(claims["sub"]), "c": body.clinic_id},
        )
    ).first()
    if is_front_desk is None:
        raise HTTPException(status_code=403, detail="your role may not register patients")
    try:
        result = await PatientIngestionPipeline(vault, key_version=key_version).ingest(
            conn, clinic_id=body.clinic_id, origin=clinic[0], name=body.name, dob=body.dob, phone=body.phone, idempotency_key=idempotency_key
        )
    except mpi.NotPermitted:
        raise HTTPException(status_code=403, detail="your role may not register patients") from None
    except mpi.IdempotencyConflict:
        raise HTTPException(status_code=409, detail="this idempotency key was already used by a different request") from None
    except mpi.InvalidDateOfBirth:
        raise HTTPException(status_code=422, detail="date of birth must be a real past date in YYYY-MM-DD form") from None
    except mpi.InvalidPhone:
        raise HTTPException(status_code=422, detail="phone must be a valid Indian mobile number") from None
    except mpi.IngestionError:
        raise HTTPException(status_code=422, detail="a required field is missing or blank") from None
    except UnicodeError:
        # e.g. a lone surrogate in the name: it cannot be encrypted or tokenised. The built-in error holds the offending character, so it
        # is dropped here and a fixed message returned.
        raise HTTPException(status_code=422, detail="the name contains characters that cannot be stored") from None
    return IngestOut(patient_id=result.patient_id, outcome=result.outcome, candidate_ids=list(result.candidate_ids))


class ConfirmIdentityOut(_Out):
    """Ids and a flag only."""

    patient_id: uuid.UUID
    confirmed: Literal[True]
    replayed: bool


@staff_patients.post("/{patient_id}/confirm-identity", response_model=ConfirmIdentityOut)
async def confirm_patient_identity(
    patient_id: uuid.UUID, idempotency_key: IdempotencyKey, conn: TxConn, claims: dict[str, Any] = Depends(verified_claims)
) -> ConfirmIdentityOut:
    """A front-desk person confirms a patient's identity. A record the unattended worker created is NOT auto-linked to by later referrals until this is done (a match on it goes
    to a person); confirming it makes it match normally. A triage approval does NOT do this (owner decision). 404 if the patient is not visible to you (or erased), 403 if you
    are not front desk of that clinic, 409 if the key was used for a different patient, 422 for a bad id or key."""
    row = (await conn.execute(text("select clinic_id, origin from mpi.patients where id = :i and erased_at is null"), {"i": patient_id})).first()
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    origin = await _front_desk_gate(conn, claims, row[0])
    try:
        result = await mpi.confirm_identity(
            conn, clinic_id=row[0], origin=origin, patient_id=patient_id, confirmed_by=uuid.UUID(claims["sub"]), idempotency_key=idempotency_key
        )
    except mpi.PatientNotFound:
        raise HTTPException(status_code=404, detail="not found") from None
    except mpi.NotPermitted:
        raise HTTPException(status_code=403, detail="your role may not do this") from None
    except mpi.IdempotencyConflict:
        raise HTTPException(status_code=409, detail="this idempotency key was already used by a different request") from None
    return ConfirmIdentityOut(patient_id=result.patient_id, confirmed=True, replayed=result.replayed)


async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """FastAPI's default 422 body includes the offending INPUT. On a route that takes a name, a date of birth and a phone that would put them
    in an error response (and in anything that records responses), so every validation error is reduced to its type, location and
    message. Pydantic's messages describe the rule that failed, not the value."""
    errors = [{"type": e.get("type"), "loc": list(e.get("loc", ())), "msg": e.get("msg")} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})


# ---- staff merges: propose, approve, unmerge (BUILD-SPEC.md §17.2, §18.1; CLAUDE.md rules 3, 5, 8) ------------------------------------------
#
# STEP-UP IMPLEMENTED, CLAIM SHAPE PENDING LIVE VERIFICATION. Every route here needs a recently verified second factor
# (`require_step_up`). The `amr` entry shape it reads is documented but NOT yet confirmed against a real token (Appendix B spike,
# docs/design/step-up-spike.md): do not treat the step-up check as verified until that is recorded.
#
# STRONG_IDENTIFIERS (careflow/mpi.py) decides which matches are strong enough to avoid a review row, and it is reachable over HTTP through
# POST /v1/staff/patients. It is PENDING SIGN-OFF by Priya (Health-information lead): no real patient data until that is recorded.
#
# A merge is a ledger row, never destructive (careflow.identity). Propose = front desk person 1; approve = a DIFFERENT front desk person 2;
# unmerge = one person, and it raises the nurse review task when the link had been active (none for a never-confirmed proposal).
# Responses are ids and statuses only. The origin is read from the clinic row, never the client. A patient or merge you cannot see
# (including one in another clinic) is the same 404 as one that does not exist. Every refusal from the identity module is one FIXED body.

staff_merges = APIRouter(prefix="/v1/staff/merges", tags=["staff merges"], dependencies=[Depends(require_step_up)])


class ProposeMergeIn(_Body):
    clinic_id: uuid.UUID
    master_patient_id: uuid.UUID
    subordinate_patient_id: uuid.UUID


class MergeOut(_Out):
    """Ids and a status only."""

    merge_id: uuid.UUID
    status: Literal["awaiting_second_approval", "active", "unmerged"]
    master_patient_id: uuid.UUID
    subordinate_patient_id: uuid.UUID
    replayed: bool


class UnmergeOut(_Out):
    merge_id: uuid.UUID
    status: Literal["unmerged"]
    review_task_id: uuid.UUID | None  # None when a never-confirmed proposal was cancelled
    decisions_flagged: int
    replayed: bool


@contextmanager
def _identity_errors() -> Iterator[None]:
    """Every refusal is a fixed body: nothing says which record, or whether one exists elsewhere."""
    try:
        yield
    except (identity.PatientNotFound, identity.MergeNotFound):
        raise HTTPException(status_code=404, detail="not found") from None
    except identity.NotPermitted:
        raise HTTPException(status_code=403, detail="your role may not do this") from None
    except identity.IdempotencyConflict:
        raise HTTPException(status_code=409, detail="this idempotency key was already used by a different request") from None
    except identity.InvalidTransition:
        raise HTTPException(status_code=409, detail="this merge is not in a state that allows that") from None
    except identity.MergeNotAllowed:
        # One body for every rule (same person approving, already linked, no review row, erased...): the reason stays out of the response.
        raise HTTPException(status_code=409, detail="this merge cannot be carried out") from None


async def _front_desk_gate(conn: AsyncConnection, claims: dict[str, Any], clinic_id: uuid.UUID) -> str:
    """The clinic's origin, after checking the caller is active front desk there. 404 if the clinic is invisible, 403 if visible but not front desk."""
    clinic = (await conn.execute(text("select origin from iam.clinics where id = :c"), {"c": clinic_id})).first()
    if clinic is None:
        raise HTTPException(status_code=404, detail="not found")
    is_front_desk = (
        await conn.execute(
            text(
                "select 1 from iam.staff_roles sr join iam.accounts a on a.id = sr.account_id "
                "where sr.account_id = :u and sr.clinic_id = :c and sr.role = 'front_desk' and sr.revoked_at is null "
                "and a.status = 'active' and a.account_type = 'staff'"
            ),
            {"u": uuid.UUID(claims["sub"]), "c": clinic_id},
        )
    ).first()
    if is_front_desk is None:
        raise HTTPException(status_code=403, detail="your role may not do this")
    return clinic[0]


async def _merge_clinic(conn: AsyncConnection, merge_id: uuid.UUID) -> uuid.UUID:
    """The clinic of a merge the caller can see under RLS; invisible and nonexistent are the same 404."""
    row = (await conn.execute(text("select clinic_id from mpi.merges where id = :i"), {"i": merge_id})).first()
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    return row[0]


def _merge_out(r: identity.MergeResult) -> MergeOut:
    return MergeOut(
        merge_id=r.merge_id, status=r.status, master_patient_id=r.master_id, subordinate_patient_id=r.subordinate_id, replayed=r.replayed  # type: ignore[arg-type]
    )


@staff_merges.post("", response_model=MergeOut)
async def propose_merge(
    body: ProposeMergeIn, idempotency_key: IdempotencyKey, conn: TxConn, claims: dict[str, Any] = Depends(verified_claims)
) -> MergeOut:
    """Step 1: propose linking the subordinate to the master (the caller signs). Not effective until a different front desk person approves.
    STEP-UP IMPLEMENTED, CLAIM SHAPE PENDING LIVE VERIFICATION (docs/design/step-up-spike.md).
    401 step-up required, 404 unknown clinic/patient, 403 not front desk there, 409 refused (one fixed body), 422 invalid input."""
    origin = await _front_desk_gate(conn, claims, body.clinic_id)
    with _identity_errors():
        result = await identity.IdentityManager().execute_merge(
            conn, body.clinic_id, origin, body.master_patient_id, body.subordinate_patient_id, uuid.UUID(claims["sub"]), idempotency_key
        )
    return _merge_out(result)


@staff_merges.post("/{merge_id}/approve", response_model=MergeOut)
async def approve_merge(
    merge_id: uuid.UUID, idempotency_key: IdempotencyKey, conn: TxConn, claims: dict[str, Any] = Depends(verified_claims)
) -> MergeOut:
    """Step 2: a DIFFERENT front desk person confirms. The same person approving is refused with the one generic 409.
    STEP-UP IMPLEMENTED, CLAIM SHAPE PENDING LIVE VERIFICATION (docs/design/step-up-spike.md)."""
    clinic_id = await _merge_clinic(conn, merge_id)
    origin = await _front_desk_gate(conn, claims, clinic_id)
    with _identity_errors():
        result = await identity.IdentityManager().approve_merge(conn, clinic_id, origin, merge_id, uuid.UUID(claims["sub"]), idempotency_key)  # type: ignore[arg-type]
    return _merge_out(result)


@staff_merges.post("/{merge_id}/unmerge", response_model=UnmergeOut)
async def unmerge(
    merge_id: uuid.UUID, idempotency_key: IdempotencyKey, conn: TxConn, claims: dict[str, Any] = Depends(verified_claims)
) -> UnmergeOut:
    """Break the link (one person). For a link that had been active this raises the nurse review task; cancelling a never-confirmed proposal raises none.
    STEP-UP IMPLEMENTED, CLAIM SHAPE PENDING LIVE VERIFICATION (docs/design/step-up-spike.md)."""
    clinic_id = await _merge_clinic(conn, merge_id)
    origin = await _front_desk_gate(conn, claims, clinic_id)
    with _identity_errors():
        result = await identity.IdentityManager().execute_unmerge(conn, clinic_id, origin, merge_id, uuid.UUID(claims["sub"]), idempotency_key)  # type: ignore[arg-type]
    return UnmergeOut(
        merge_id=result.merge_id, status="unmerged", review_task_id=result.review_task_id, decisions_flagged=result.decisions_flagged,
        replayed=result.replayed,
    )


# ---- readiness (BUILD-SPEC.md 2) ---------------------------------------------------------------------------------------------------
#
# `GET /readyz` is unauthenticated and outside /v1 (a platform probe, like a load balancer's health check). It answers ONLY {"ready": bool}
# (200 or 503): which role is missing is logged by name for the operator, never returned. In the `demo` profile it is ready. In `clinical`
# (or any unrecognised profile) it is ready only if every required role has an enabled phi_approved route that has an adapter wired
# (careflow.readiness); nothing is wired yet, so a clinical deployment that requires a model role reports not ready, by design.

health = APIRouter(tags=["health"])


@health.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    profile = resolve_profile()
    required = parse_required_roles(request.app.state.required_roles)
    rows = None
    if profile != "demo" and required:
        account = request.app.state.worker_account_id
        if account is None:
            log.warning("readiness: CAREFLOW_WORKER_ACCOUNT_ID is not set, so the model registry cannot be read")
        else:
            try:
                rows = await load_registry(request.app.state.engine, account, origins=("live",))
            except (RegistryError, SQLAlchemyError) as exc:
                log.warning("readiness: the model registry could not be read (error=%s)", type(exc).__name__)
    result = check_readiness(
        profile, rows, required, wired=request.app.state.wired_adapters, malware_scanner_configured=request.app.state.malware_scanner_configured
    )
    if not result.ready:
        log.warning("not ready (profile=%s missing_roles=%s reasons=%s)", result.profile, list(result.missing_roles), list(result.reasons))
    return JSONResponse({"ready": result.ready}, status_code=200 if result.ready else 503)



# ---- Front Door runs: POST /v1/staff/referrals/{document_id}/run and /review (BUILD-SPEC.md 7.2, 12.1; CLAUDE.md rules 2, 4, 5, 8) -----------------------------
#
# /run ENQUEUES a `front_door_run` job for a registered referral document (ids only in the payload) and pokes the worker AFTER the transaction has committed. Front desk, nurse
# and doctor of the clinic may start a run; the document must be visible to the caller (row-level security on docs.documents), so another clinic's staff, admin, business owner,
# auditor, patients and inactive accounts get the fixed 404; a visible caller who may not start a run (the queue worker) gets 403. The WORKER (careflow.front_door_runner) runs the
# graph as the queue_worker system identity and pauses at review.
# /review RESUMES the paused thread as the REVIEWER: nurse or doctor of the clinic. The reviewer is the signed-in account (claims["sub"]), never a body field; the decision row
# is written with decided_by = auth.uid() (migration 0018). Nothing is ever approved by this route's absence or by a timeout: no decision, no resume.
# Both REQUIRE `X-Idempotency-Key` (rule 5). /run uses it (scoped to the document) as the job's key, so a replay returns the same job. /review validates it but does not use it:
# its write is idempotent on the DOCUMENT (the decision key is `review:<document id>`, one decision per proposal): the same decision by the same reviewer again is `replayed: true`,
# a different one is 409.
# Both answer a visible 503 when no Front Door components are configured (this package only ever runs on stand-ins for the external engines).

staff_referrals = APIRouter(prefix="/v1/staff/referrals", tags=["staff referrals"])


class RunOut(_Out):
    job_id: uuid.UUID
    replayed: bool


class ReviewIn(_Body):
    decision: Literal["approve", "edit", "reject"]


class ReviewOut(_Out):
    document_id: uuid.UUID
    decision: Literal["approve", "edit", "reject"]
    replayed: bool


async def _referral_document(conn: AsyncConnection, document_id: uuid.UUID):
    """(clinic_id, origin) of a REFERRAL document the caller can see under RLS; invisible, missing and non-referral are one 404."""
    row = (await conn.execute(text("select clinic_id, origin, kind from docs.documents where id = :d"), {"d": document_id})).first()
    if row is None or row.kind != "referral":
        raise HTTPException(status_code=404, detail="not found")
    return row


async def _holds_role(conn: AsyncConnection, claims: dict[str, Any], clinic_id: uuid.UUID, roles: tuple[str, ...]) -> bool:
    return (
        await conn.execute(
            text(
                "select 1 from iam.staff_roles sr join iam.accounts a on a.id = sr.account_id "
                "where sr.account_id = :u and sr.clinic_id = :c and sr.role = any(cast(:r as text[])) and sr.revoked_at is null "
                "and a.status = 'active' and a.account_type = 'staff'"
            ),
            {"u": uuid.UUID(claims["sub"]), "c": clinic_id, "r": list(roles)},
        )
    ).first() is not None


def _front_door(request: Request) -> FrontDoorComponents:
    components: FrontDoorComponents | None = request.app.state.front_door
    if components is None:
        log.error("front door runs are unavailable: no components are configured (nothing is run; nothing is lost)")
        raise HTTPException(status_code=503, detail="referral runs are unavailable: not configured")
    return components


@staff_referrals.post("/{document_id}/run", response_model=RunOut)
async def start_referral_run(
    document_id: uuid.UUID, idempotency_key: IdempotencyKey, conn: TxConn, request: Request, background: BackgroundTasks,
    claims: dict[str, Any] = Depends(verified_claims),
) -> RunOut:
    """Enqueue a Front Door run for a referral document. 404 if you cannot see it, 403 if you may not start a run, 503 if not configured."""
    _front_door(request)
    doc = await _referral_document(conn, document_id)
    if not await _holds_role(conn, claims, doc.clinic_id, ("front_desk", "nurse", "doctor")):
        raise HTTPException(status_code=403, detail="your role may not start a referral run")
    try:
        queued = await PostgresQueue().enqueue(
            conn, doc.clinic_id, doc.origin, FRONT_DOOR_TASK, {"document_id": str(document_id)}, f"front-door-run:{document_id}:{idempotency_key}"
        )
    except QueueNotPermitted:
        raise HTTPException(status_code=403, detail="your role may not start a referral run") from None
    background.add_task(request.app.state.poker.poke)  # after the response, so after the commit (TxConn commits before the response goes out)
    return RunOut(job_id=queued.job_id, replayed=queued.replayed)


@staff_referrals.post("/{document_id}/review", response_model=ReviewOut)
async def review_referral(
    document_id: uuid.UUID, body: ReviewIn, idempotency_key: IdempotencyKey, conn: TxConn, request: Request,
    claims: dict[str, Any] = Depends(verified_claims),
) -> ReviewOut:
    """Record the reviewer's decision on a paused run and finish it. 404 if you cannot see the document, 403 unless nurse or doctor there, 404 if the document has no
    run, 409 if the run is not waiting for a decision or already has a different one, 503 if not configured."""
    components = _front_door(request)
    doc = await _referral_document(conn, document_id)
    if not await _holds_role(conn, claims, doc.clinic_id, ("nurse", "doctor")):
        raise HTTPException(status_code=403, detail="your role may not review a referral")
    reviewer = claims["sub"]
    graph = graph_for(components, request_session(conn))
    config = run_config(document_id, doc.clinic_id, doc.origin)
    state = await graph.aget_state(config)
    if not state.values:
        raise HTTPException(status_code=404, detail="not found")
    if state.next != ("review",):
        decided = state.values.get("decision") if state.values.get("status") == "reviewed" else None
        if decided and decided["decision"] == body.decision and decided["reviewer_id"] == reviewer:
            return ReviewOut(document_id=document_id, decision=body.decision, replayed=True)
        raise HTTPException(status_code=409, detail="this referral is not waiting for a decision")
    try:
        await graph.ainvoke(Command(resume={"decision": body.decision, "reviewer_id": reviewer}), config)
    except DecisionConflict:
        raise HTTPException(status_code=409, detail="this referral is not waiting for a decision") from None
    except TriageNotPermitted:
        # Either the role (the decision policy) or a review task another reviewer already acknowledged: one fixed answer for both, and the request transaction rolls back.
        raise HTTPException(status_code=403, detail="you cannot record this decision") from None
    except TriageStoreError:
        raise HTTPException(status_code=404, detail="not found") from None  # the proposal is missing or invisible to you: the one fixed 404
    except (FrontDoorError, CheckpointerError):
        # A deployment fault (hosted tracing switched on, a checkpoint without its scope): not a role problem and not the client's. Logged by class only.
        log.error("a referral review failed in the graph or its checkpointer; nothing was recorded")
        raise HTTPException(status_code=503, detail="referral runs are unavailable") from None
    return ReviewOut(document_id=document_id, decision=body.decision, replayed=False)


# ---- application -----------------------------------------------------------------------------------


def _load_vault() -> DemographicVault | None:
    """The key vault from the environment, or None (the screening route then answers 503: degrade visibly, never silently)."""
    try:
        return DemographicVault.from_env()
    except CryptoError as exc:
        log.warning("message encryption keys are not configured; message routes are unavailable (%s)", type(exc).__name__)
        return None


def create_app(
    settings: Settings | None = None, *, jwks: JWKSClient | None = None, engine: AsyncEngine | None = None,
    vault: DemographicVault | None = None, clock: Callable[[], float] = time.time, front_door: FrontDoorComponents | None = None,
    front_door_session_for: Callable[..., Any] = worker_session,
) -> FastAPI:
    """Build the app. `jwks`, `engine`, `vault` and `clock` (for the step-up window) can be injected (tests do); otherwise they come from
    settings / the environment. `front_door` holds the Front Door's stand-in components; with None the run and review routes answer 503 and no handler is registered."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        cfg = settings or Settings()  # type: ignore[call-arg]  # fields come from the environment
        app.state.issuer = cfg.issuer
        app.state.jwks = jwks or JWKSClient(cfg.jwks_url)
        app.state.engine = engine or make_app_engine(cfg.database_url_app, pool_size=5, max_overflow=5, pool_pre_ping=True)
        app.state.vault = vault if vault is not None else _load_vault()
        app.state.active_key_version = cfg.active_key_version
        app.state.step_up_max_age_seconds = cfg.step_up_max_age_seconds
        app.state.clock = clock
        app.state.required_roles = cfg.required_roles
        app.state.worker_account_id = cfg.worker_account_id
        app.state.wired_adapters = frozenset()  # (provider, model_id) pairs with a wired adapter: none yet
        app.state.malware_scanner_configured = False  # nothing sets it: no scanner exists (BUILD-SPEC.md 11.5); clinical stays not ready
        app.state.front_door = front_door
        app.state.poker = Poker()  # routes that enqueue call `app.state.poker.poke()` after their transaction commits (7.4)
        handlers = {FRONT_DOOR_TASK: front_door_handler(front_door, session_for=front_door_session_for)} if front_door is not None else {}
        sweeps = [lambda conn: raise_dead_letter_tasks(conn, front_door.dead_letters)] if front_door is not None else []  # a dead-lettered run is never silent
        worker: asyncio.Task | None = None
        if cfg.run_worker:
            if cfg.worker_account_id is None:
                log.error("CAREFLOW_RUN_WORKER is on but CAREFLOW_WORKER_ACCOUNT_ID is not set: the worker is NOT started (queued jobs wait)")
            else:
                worker = asyncio.create_task(
                    worker_loop(app.state.engine, PostgresQueue(), handlers, app.state.poker,
                                worker_account_id=cfg.worker_account_id, worker_id=f"api-{uuid.uuid4().hex[:8]}", sweeps=sweeps),
                    name="careflow-worker",
                )
        app.state.worker = worker
        try:
            yield
        finally:
            if worker is not None:
                worker.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await worker
            if engine is None:
                await app.state.engine.dispose()
            if jwks is None:
                await app.state.jwks.aclose()

    app = FastAPI(title="CareFlow", lifespan=lifespan)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)  # type: ignore[arg-type]
    app.include_router(router)
    app.include_router(scheduling)
    app.include_router(staff_messages)
    app.include_router(staff_patients)
    app.include_router(staff_merges)
    app.include_router(staff_referrals)
    app.include_router(health)
    return app


app = create_app()  # `uvicorn careflow.app:app`; settings are read at startup, not at import
