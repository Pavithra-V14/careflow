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

import logging
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from careflow.auth import AuthError, AuthUnavailable, JWKSClient, verify_token
from careflow.crypto import CryptoError, DemographicVault
from careflow.db import make_app_engine, set_request_claims
from careflow.messages import (
    KeyNotLoaded,
    MessageErased,
    MessageNotFound,
    screen_stored_message,
)
from careflow.rules import NotPermitted as ScreeningNotPermitted
from careflow.rules import RedFlagEngine
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

log = logging.getLogger("careflow.app")
_bearer = HTTPBearer(auto_error=False)


def _unauthenticated() -> HTTPException:
    """One generic body for every credential failure; the reason is logged, never returned."""
    return HTTPException(status_code=401, detail="invalid or missing credentials", headers={"WWW-Authenticate": "Bearer"})


class Settings(BaseSettings):
    """Read from the environment (or a local .env). Never logged."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    supabase_url: str
    database_url_app: str
    supabase_jwks_url: str | None = None

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
    """Screen a stored message for red flags. 404 if you cannot see it; 409 if its body was erased; 503 if the encryption key is unavailable."""
    vault: DemographicVault | None = request.app.state.vault
    if vault is None:
        raise HTTPException(status_code=503, detail="message screening is unavailable: encryption keys are not configured")
    try:
        r = await screen_stored_message(conn, vault, _screening_engine, message_id, idempotency_key=idempotency_key)
    except (MessageNotFound, ScreeningNotPermitted):
        raise HTTPException(status_code=404, detail="not found") from None
    except MessageErased:
        raise HTTPException(status_code=409, detail="the message body was erased") from None
    except KeyNotLoaded:
        raise HTTPException(status_code=503, detail="message screening is unavailable: the encryption key is not loaded") from None
    return ScreenOut(message_id=r.message_id, screening_id=r.screening_id, outcome=r.outcome, replayed=r.replayed, patient_reply=r.patient_reply)  # type: ignore[arg-type]


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
    vault: DemographicVault | None = None,
) -> FastAPI:
    """Build the app. `jwks`, `engine` and `vault` can be injected (tests do); otherwise they come from settings / the environment."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        cfg = settings or Settings()  # type: ignore[call-arg]  # fields come from the environment
        app.state.issuer = cfg.issuer
        app.state.jwks = jwks or JWKSClient(cfg.jwks_url)
        app.state.engine = engine or make_app_engine(cfg.database_url_app, pool_size=5, max_overflow=5, pool_pre_ping=True)
        app.state.vault = vault if vault is not None else _load_vault()
        try:
            yield
        finally:
            if engine is None:
                await app.state.engine.dispose()
            if jwks is None:
                await app.state.jwks.aclose()

    app = FastAPI(title="CareFlow", lifespan=lifespan)
    app.include_router(router)
    app.include_router(scheduling)
    app.include_router(staff_messages)
    return app


app = create_app()  # `uvicorn careflow.app:app`; settings are read at startup, not at import
