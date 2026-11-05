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
import json
import logging
import os
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, date, datetime
from typing import Annotated, Any, Final, Literal

from dotenv import dotenv_values
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
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from langgraph.types import Command
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from careflow import (
    active_role,
    demo_front_door,
    demo_phase3,
    identity,
    mpi,
    offers,
    openapi_errors,
    patient_view,
    reply_interpretation,
    review_tasks,
)
from careflow.adapters.storage import DOCUMENT_BUCKET, SupabaseUploadAdapter
from careflow.audit import write_audit
from careflow.auth import (
    AuthError,
    AuthUnavailable,
    JWKSClient,
    mfa_failure,
    step_up_failure,
    verify_token,
)
from careflow.checkpointer import CheckpointerError
from careflow.cors import middleware_kwargs, parse_origins
from careflow.crypto import CryptoError, DemographicVault
from careflow.db import make_app_engine, set_request_claims
from careflow.dead_letters import raise_dead_letter_tasks
from careflow.decision import DecisionService
from careflow.demo_phase3 import data_policy as _data_policy
from careflow.demo_phase3 import (
    demo_allowed,
    refuse_demo_outside_demo,
    scheduling_settings_for,
)
from careflow.documents import (
    DocumentIntake,
    UploadRejected,
    UploadsDisabled,
    UploadUnavailable,
)
from careflow.documents import IdempotencyConflict as DocumentIdempotencyConflict
from careflow.documents import NotPermitted as DocumentNotPermitted
from careflow.front_door import FrontDoorError, run_config
from careflow.front_door_runner import TASK_TYPE as FRONT_DOOR_TASK
from careflow.front_door_runner import (
    FrontDoorComponents,
    front_door_handler,
    graph_for,
    request_session,
    worker_session,
)
from careflow.mcp_kit import Envelope
from careflow.mcp_kit import invoke as mcp_invoke
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
    HoldNotFound,
    IdempotencyConflict,
    InvalidTransition,
    NotPermitted,
    SchedulingError,
    SlotManager,
    SlotNotFound,
    SlotUnavailable,
    UnknownReference,
    release_expired_holds_sweep,
)
from careflow.scheduling_agent import SchedulingSettings, build_scheduling_server
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
    # EVERY staff route needs a verified second factor (aal2 + a well-formed amr with TOTP), ON by default everywhere. It can be turned off ONLY in the demo profile with synthetic data, by this explicit
    # setting; create_app refuses to build with it off anywhere else (MfaSettingRefused). Step-up (recent second factor) on the merge routes is separate and unchanged.
    # The CLOSED list of reasons a patient may be erased for (comma separated words). A privacy decision with NO default: unset means the erase route answers 503.
    erase_reasons: str | None = Field(default=None, validation_alias=AliasChoices("CAREFLOW_ERASE_REASONS", "erase_reasons"))
    require_aal2: bool = Field(default=True, validation_alias=AliasChoices("CAREFLOW_REQUIRE_AAL2", "require_aal2"))
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

    # Browser origins allowed to call the API (careflow/cors.py): a comma-separated list of EXACT origins. Unset means none.
    cors_allowed_origins: str | None = Field(default=None, validation_alias=AliasChoices("CORS_ALLOWED_ORIGINS", "cors_allowed_origins"))

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


async def active_role_request(request: Request, claims: dict[str, Any] = Depends(verified_claims)) -> active_role.ActiveRole | None:
    """The optional X-CareFlow-Active-Role header, parsed strictly with NO database work (docs/design/active-role.md). Authentication comes first (401), then this syntax check (the one fixed 403), then
    the step-up check, then the database. Absent header: None (every live grant counts, as before)."""
    try:
        return active_role.parse(request.headers.getlist(active_role.HEADER))
    except active_role.ActiveRoleRefused as exc:
        log.info("active role refused: %s", exc.reason)  # a reason CODE; never the header value
        raise _active_role_refusal() from None


def _active_role_refusal() -> HTTPException:
    return HTTPException(status_code=active_role.REFUSAL_STATUS, detail=active_role.REFUSAL_BODY["detail"])


async def _begin_request(conn: AsyncConnection, claims: dict[str, Any], active: active_role.ActiveRole | None) -> None:
    """First statement(s) of the request's transaction. Any `careflow_active` in the token is dropped. With an accepted header, ONE statement sets the claims and asks the database whether the caller holds
    that grant LIVE (iam.active_grant_ok; its argument is the set_config call, so the claims are in force before the check runs): no extra round trip. A 'no' is the one fixed 403 and the transaction rolls back."""
    scoped = active_role.claims_with_active(claims, active)
    if active is None:
        await set_request_claims(conn, scoped)
        return
    ok = (await conn.execute(text("select iam.active_grant_ok(set_config('request.jwt.claims', :c, true))"), {"c": json.dumps(scoped)})).scalar()
    if ok is not True:
        log.info("active role refused: not_held")
        raise _active_role_refusal()


class MfaSettingRefused(Exception):
    """CAREFLOW_REQUIRE_AAL2 is off outside profile=demo with data_policy=synthetic_only. Fixed words."""


def _require_mfa(request: Request, claims: dict[str, Any]) -> None:
    """Every staff route: a verified second factor, before any database work. ONE fixed 401, `WWW-Authenticate: mfa`, no reason in the body (the code goes to the log). Off only where create_app
    allowed it (demo + synthetic_only). Unknown state counts as ON."""
    if getattr(request.app.state, "require_aal2", True) is False:
        return
    reason = mfa_failure(claims, now=request.app.state.clock())
    if reason is not None:
        log.info("multi-factor sign-in required: %s", reason)
        raise HTTPException(status_code=401, detail="multi-factor sign-in required", headers={"WWW-Authenticate": "mfa"})


async def db_conn(
    request: Request, claims: dict[str, Any] = Depends(verified_claims), active: active_role.ActiveRole | None = Depends(active_role_request)
) -> AsyncIterator[AsyncConnection]:
    _require_mfa(request, claims)  # /v1/iam/me alone is exempt (db_conn_all_grants): the console needs it to find out what to enrol
    engine: AsyncEngine = request.app.state.engine
    async with engine.connect() as conn, conn.begin():
        await _begin_request(conn, claims, active)  # before any query
        yield conn


async def db_conn_all_grants(request: Request, claims: dict[str, Any] = Depends(verified_claims)) -> AsyncIterator[AsyncConnection]:
    """Like db_conn but IGNORING the active-role header (GET /v1/iam/me only: the console needs every grant to offer its picker, and /me must keep working when its remembered role was revoked)."""
    engine: AsyncEngine = request.app.state.engine
    async with engine.connect() as conn, conn.begin():
        await _begin_request(conn, claims, None)
        yield conn


# Commit before the response goes out (see the lifecycle note above).
TxConn = Annotated[AsyncConnection, Depends(db_conn, scope="function")]
TxConnAllGrants = Annotated[AsyncConnection, Depends(db_conn_all_grants, scope="function")]


async def require_step_up(
    request: Request, claims: dict[str, Any] = Depends(verified_claims), _active: active_role.ActiveRole | None = Depends(active_role_request)
) -> None:
    """Step-up re-authentication (BUILD-SPEC 18.1): a second factor verified within CAREFLOW_STEP_UP_MAX_AGE_SECONDS. The decision is the pure
    careflow.auth.step_up_failure; this wrapper supplies the configured window and the clock. A refusal is ONE fixed 401 with
    `WWW-Authenticate: step-up` and no reason; the reason code goes to the log, never a claim value. It runs before any database work.
    Authentication still comes first (verified_claims). The claim shape was verified on a real dev token on 2026-10-30 (docs/design/step-up-spike.md)."""
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
    clinic_name: str | None  # None only if the clinic row is not visible to the caller (never expected for one's own grant)
    clinic_origin: str | None


OPERATING_PROFILES: Final = ("demo", "clinical")


def operating_profile() -> Literal["demo", "clinical"] | None:
    """The deployment's CAREFLOW_PROFILE, RESTRICTED to the two known values (compared the way `resolve_profile` does: trimmed, case-insensitive). Unset, empty or anything else is None, never a guess:
    the console shows a demo banner for "demo" and says nothing for None. It names the profile only; no setting, key or other configuration value ever appears here."""
    raw = (os.environ.get("CAREFLOW_PROFILE") or "").strip().lower()
    return raw if raw in OPERATING_PROFILES else None  # type: ignore[return-value]


class Me(BaseModel):
    account_id: uuid.UUID
    account_type: str
    status: str
    roles: list[RoleGrant]
    operating_profile: Literal["demo", "clinical"] | None  # the deployment's profile (demo or clinical); null when unset or unrecognised


@router.get("/v1/iam/me", response_model=Me)
async def me(conn: TxConnAllGrants, claims: dict[str, Any] = Depends(verified_claims)) -> Me:
    # EVERY live grant, whatever X-CareFlow-Active-Role says: the header is ignored here on purpose (TxConnAllGrants). Kept out of the docstring so the OpenAPI snapshot is unchanged.
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
            "select sr.clinic_id, sr.role, c.name as clinic_name, c.origin as clinic_origin from iam.staff_roles sr "
            "left join iam.clinics c on c.id = sr.clinic_id "
            "where sr.account_id = :i and sr.revoked_at is null order by sr.clinic_id, sr.role"
        ),
        {"i": account_id},
    )
    return Me(
        account_id=account.id,
        account_type=account.account_type,
        status=account.status,
        roles=[RoleGrant(clinic_id=g.clinic_id, role=g.role, clinic_name=g.clinic_name, clinic_origin=g.clinic_origin) for g in grants],
        operating_profile=operating_profile(),
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
    except (SlotNotFound, BookingNotFound, HoldNotFound):
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


async def _clinic_role_held(conn: AsyncConnection, clinic_id: uuid.UUID, roles: tuple[str, ...]) -> bool:
    """Does the caller hold one of `roles` at this clinic NOW, counting only the ACTIVE role when the request carried one? One definition of "effective role", in SQL (iam.has_clinic_role, migration 0028)."""
    return bool((await conn.execute(text("select iam.has_clinic_role(:c, cast(:r as text[]))"), {"c": clinic_id, "r": list(roles)})).scalar())


async def _any_role_held(conn: AsyncConnection, roles: tuple[str, ...]) -> bool:
    """Does the caller hold one of `roles` at ANY clinic now (the active role only, when the request carried one)? (iam.has_any_role, migration 0028)."""
    return bool((await conn.execute(text("select iam.has_any_role(cast(:r as text[]))"), {"r": list(roles)})).scalar())


async def _readable_clinics(conn: AsyncConnection, claims: dict[str, Any]) -> list[uuid.UUID]:
    # Through the RLS helper (migration 0028), so the active-role header narrows the list to the active clinic. A clinic row is visible to its own staff (clinics_select_own_staff).
    rows = await conn.execute(text("select c.id from iam.clinics c where iam.has_clinic_role(c.id, cast(:roles as text[])) order by c.id"), {"roles": list(SCHEDULING_READERS)})
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


# ---- scheduling reference lists: providers, locations, visit types, priority levels (the booking forms' dropdowns) -------------------------------------------------
#
# Reads only, under the existing `select` policies and grants; the same readers as the other scheduling reads (front desk, nurse, doctor). Names are clinic setup data (a provider's display name,
# a room, a visit type), never patient data. `active` is returned so a screen can hide retired entries; inactive ones are listed only with `include_inactive=true`.


class ProviderOut(_Out):
    provider_id: uuid.UUID
    clinic_id: uuid.UUID
    display_name: str
    active: bool


class LocationOut(_Out):
    location_id: uuid.UUID
    clinic_id: uuid.UUID
    name: str
    active: bool


class VisitTypeOut(_Out):
    visit_type_id: uuid.UUID
    clinic_id: uuid.UUID
    name: str
    duration_minutes: int
    active: bool


class PriorityLevelOut(_Out):
    clinic_id: uuid.UUID
    code: str
    label: str
    rank: int  # 1 is served first
    active: bool


class ProviderList(_Out):
    items: list[ProviderOut]
    limit: int
    has_more: bool


class LocationList(_Out):
    items: list[LocationOut]
    limit: int
    has_more: bool


class VisitTypeList(_Out):
    items: list[VisitTypeOut]
    limit: int
    has_more: bool


class PriorityLevelList(_Out):
    items: list[PriorityLevelOut]
    limit: int
    has_more: bool


async def _reference_rows(conn: AsyncConnection, claims: dict[str, Any], sql: str, include_inactive: bool, limit: int):
    clinics = await _readable_clinics(conn, claims)
    return (await conn.execute(text(sql.format(active="" if include_inactive else " and active")), {"c": clinics, "n": limit + 1})).all()


@scheduling.get("/providers", response_model=ProviderList)
async def list_providers(conn: TxConn, claims: dict[str, Any] = Depends(verified_claims), include_inactive: bool = False, limit: Limit = 100) -> ProviderList:
    """Providers of the clinics you may read, by display name."""
    rows = await _reference_rows(
        conn, claims, "select id, clinic_id, display_name, active from sched.providers where clinic_id = any(cast(:c as uuid[])){active} order by display_name, id limit :n", include_inactive, limit
    )
    return ProviderList(items=[ProviderOut(provider_id=r.id, clinic_id=r.clinic_id, display_name=r.display_name, active=r.active) for r in rows[:limit]], limit=limit, has_more=len(rows) > limit)


@scheduling.get("/locations", response_model=LocationList)
async def list_locations(conn: TxConn, claims: dict[str, Any] = Depends(verified_claims), include_inactive: bool = False, limit: Limit = 100) -> LocationList:
    """Locations (rooms, sites) of the clinics you may read, by name."""
    rows = await _reference_rows(conn, claims, "select id, clinic_id, name, active from sched.locations where clinic_id = any(cast(:c as uuid[])){active} order by name, id limit :n", include_inactive, limit)
    return LocationList(items=[LocationOut(location_id=r.id, clinic_id=r.clinic_id, name=r.name, active=r.active) for r in rows[:limit]], limit=limit, has_more=len(rows) > limit)


@scheduling.get("/visit-types", response_model=VisitTypeList)
async def list_visit_types(conn: TxConn, claims: dict[str, Any] = Depends(verified_claims), include_inactive: bool = False, limit: Limit = 100) -> VisitTypeList:
    """Visit types of the clinics you may read, by name, with their scheduling length."""
    rows = await _reference_rows(
        conn, claims, "select id, clinic_id, name, duration_minutes, active from sched.visit_types where clinic_id = any(cast(:c as uuid[])){active} order by name, id limit :n", include_inactive, limit
    )
    return VisitTypeList(
        items=[VisitTypeOut(visit_type_id=r.id, clinic_id=r.clinic_id, name=r.name, duration_minutes=r.duration_minutes, active=r.active) for r in rows[:limit]], limit=limit, has_more=len(rows) > limit
    )


@scheduling.get("/priority-levels", response_model=PriorityLevelList)
async def list_priority_levels(conn: TxConn, claims: dict[str, Any] = Depends(verified_claims), include_inactive: bool = False, limit: Limit = 100) -> PriorityLevelList:
    """The clinic's clinical priority levels, rank 1 first. The levels themselves are clinician-approved data (sched.priority_levels); this only reads them."""
    rows = await _reference_rows(
        conn, claims, "select clinic_id, code, label, rank, active from sched.priority_levels where clinic_id = any(cast(:c as uuid[])){active} order by clinic_id, rank, code limit :n", include_inactive, limit
    )
    return PriorityLevelList(items=[PriorityLevelOut(clinic_id=r.clinic_id, code=r.code, label=r.label, rank=r.rank, active=r.active) for r in rows[:limit]], limit=limit, has_more=len(rows) > limit)


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


# ---- staff message lists: threads, and the HEADERS of one thread's messages (no bodies) --------------------------------------------------------------
#
# `GET /v1/staff/messages/threads` and `GET /v1/staff/messages/threads/{thread_id}/messages` give the console a conversation list. They return ids, the topic, the sender KIND, times, counts and an
# erased flag: never a body, never ciphertext, never the key version (the SQL names its columns, so a body cannot be selected by accident). Who sees what is decided by row-level security on
# msg.threads and msg.messages, exactly as for reading a body: nurse and doctor see every topic, front desk sees appointments and safety, and a thread a caller cannot see is absent from the list and
# a 404 when opened by id. Admin, business owner, auditor, workers and patients get 403. A header carries no message text, so reading headers writes no audit row (reading a BODY does). Newest first;
# `before` is an exclusive time cursor for the next page. Declared BEFORE `/{message_id}` so the literal path `/threads` is never taken for a message id.

MESSAGE_LIST_READERS: Final = ("front_desk", "nurse", "doctor")
Topic = Literal["care", "appointments", "safety"]


class ThreadOut(_Out):
    thread_id: uuid.UUID
    clinic_id: uuid.UUID
    patient_id: uuid.UUID
    topic: Topic
    status: Literal["open", "closed"]
    created_at: datetime
    message_count: int
    last_message_at: datetime | None


class ThreadList(_Out):
    items: list[ThreadOut]
    limit: int
    has_more: bool


class MessageHeaderOut(_Out):
    message_id: uuid.UUID
    thread_id: uuid.UUID
    topic: Topic
    sender_kind: Literal["patient", "staff", "system"]
    created_at: datetime
    erased: bool


class MessageHeaderList(_Out):
    items: list[MessageHeaderOut]
    limit: int
    has_more: bool


@staff_messages.get("/threads", response_model=ThreadList)
async def list_threads(
    conn: TxConn, claims: dict[str, Any] = Depends(verified_claims), patient_id: uuid.UUID | None = None, topic: Topic | None = None, before: datetime | None = None, limit: Limit = 100
) -> ThreadList:
    """Message threads you may see, newest first (`patient_id`, `topic` filter; `before` pages). 403 unless you hold front desk, nurse or doctor somewhere. No message text."""
    await _staff_reader(conn, claims, MESSAGE_LIST_READERS)
    rows = (
        await conn.execute(
            text(
                "select t.id, t.clinic_id, t.patient_id, t.topic, t.status, t.created_at, "
                "(select count(*) from msg.messages m where m.thread_id = t.id) as message_count, "
                "(select max(m.created_at) from msg.messages m where m.thread_id = t.id) as last_message_at "
                "from msg.threads t where (cast(:p as uuid) is null or t.patient_id = :p) and (cast(:t as text) is null or t.topic = :t) "
                "and (cast(:b as timestamptz) is null or t.created_at < :b) order by t.created_at desc, t.id limit :n"
            ),
            {"p": patient_id, "t": topic, "b": before, "n": limit + 1},
        )
    ).all()
    items = [
        ThreadOut(
            thread_id=r.id, clinic_id=r.clinic_id, patient_id=r.patient_id, topic=r.topic, status=r.status, created_at=r.created_at, message_count=r.message_count,
            last_message_at=r.last_message_at,
        )
        for r in rows[:limit]
    ]
    return ThreadList(items=items, limit=limit, has_more=len(rows) > limit)


@staff_messages.get("/threads/{thread_id}/messages", response_model=MessageHeaderList)
async def list_thread_messages(
    thread_id: uuid.UUID, conn: TxConn, claims: dict[str, Any] = Depends(verified_claims), before: datetime | None = None, limit: Limit = 100
) -> MessageHeaderList:
    """The message HEADERS of one thread, newest first (ids, topic, sender kind, time, erased; never a body). 403 unless you hold front desk, nurse or doctor somewhere; 404 if you cannot see the thread."""
    await _staff_reader(conn, claims, MESSAGE_LIST_READERS)
    if (await conn.execute(text("select 1 from msg.threads where id = :i"), {"i": thread_id})).first() is None:
        raise HTTPException(status_code=404, detail="not found")
    rows = (
        await conn.execute(
            text(
                "select id, thread_id, topic, sender_kind, created_at, (erased_at is not null) as erased from msg.messages "
                "where thread_id = :i and (cast(:b as timestamptz) is null or created_at < :b) order by created_at desc, id limit :n"
            ),
            {"i": thread_id, "b": before, "n": limit + 1},
        )
    ).all()
    items = [MessageHeaderOut(message_id=r.id, thread_id=r.thread_id, topic=r.topic, sender_kind=r.sender_kind, created_at=r.created_at, erased=r.erased) for r in rows[:limit]]
    return MessageHeaderList(items=items, limit=limit, has_more=len(rows) > limit)


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


def _choose_key_version(vault: Any, configured: int | None) -> int | None:
    """The key version new demographics are encrypted under: the configured one if it is loaded, else the only loaded one, else None. ONE definition, used by the routes and by the demo wiring."""
    versions = tuple(getattr(vault, "key_versions", ()) or ())
    if configured is not None:
        return configured if configured in versions else None
    return versions[0] if len(versions) == 1 else None


def _active_key_version(request: Request) -> int | None:
    return _choose_key_version(request.app.state.vault, request.app.state.active_key_version)


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
    if not await _clinic_role_held(conn, body.clinic_id, ("front_desk",)):
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
    except mpi.RecordIsMerged:
        raise HTTPException(status_code=409, detail="this record is merged into another record; act on that record") from None
    await write_audit(
        conn, clinic_id=row[0], origin=origin, actor_id=uuid.UUID(claims["sub"]), actor_role="front_desk", action="patient.confirm_identity", resource_type="patient",
        resource_id=patient_id, details={"replayed": result.replayed}, idempotency_key=f"audit:confirm-identity:{patient_id}:{idempotency_key}",
    )
    return ConfirmIdentityOut(patient_id=result.patient_id, confirmed=True, replayed=result.replayed)


class RevokeIdentityIn(BaseModel):
    """A closed reason word, nothing else (no free text)."""

    model_config = ConfigDict(extra="forbid")
    reason: Literal["wrong_person", "planted_or_suspect", "mistaken_click"]


class RevokeIdentityOut(_Out):
    """Ids, a flag and a closed word. `review_task`: `raised` (a nurse or doctor will re-check what was linked), `not_configured` (the revoke is recorded but NO task was raised, because the
    clinician-owned task settings are unset) or `on_record` (nothing new was written by this call)."""

    patient_id: uuid.UUID
    confirmed: Literal[False]
    replayed: bool
    review_task: Literal["raised", "not_configured", "on_record"]


def revoke_task_settings() -> mpi.RevokeTaskSettings:
    return mpi.RevokeTaskSettings()


@staff_patients.post("/{patient_id}/revoke-identity", response_model=RevokeIdentityOut)
async def revoke_patient_identity(
    patient_id: uuid.UUID, body: RevokeIdentityIn, idempotency_key: IdempotencyKey, conn: TxConn, claims: dict[str, Any] = Depends(verified_claims),
    task: mpi.RevokeTaskSettings = Depends(revoke_task_settings),
) -> RevokeIdentityOut:
    """A front-desk person revokes a mistaken identity confirmation (or demotes a front-desk-created record): afterwards a full match on the record is a review, never an automatic link.
    Nothing already linked is undone; a nurse or doctor review task lists the patient and the window (when the clinician-owned task settings are configured; the response says if not).
    One person, as themselves, no step-up (a revoke only moves a record towards 'a person decides'). 404 if the patient is not visible to you (or erased), 403 if you are not front desk
    of that clinic, 409 if the key was used for a different request, 422 for a bad id, key or reason."""
    row = (await conn.execute(text("select clinic_id, origin from mpi.patients where id = :i and erased_at is null"), {"i": patient_id})).first()
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    origin = await _front_desk_gate(conn, claims, row[0])
    try:
        result = await mpi.revoke_identity(
            conn, clinic_id=row[0], origin=origin, patient_id=patient_id, revoked_by=uuid.UUID(claims["sub"]), reason=body.reason, idempotency_key=idempotency_key, task=task
        )
    except mpi.PatientNotFound:
        raise HTTPException(status_code=404, detail="not found") from None
    except mpi.NotPermitted:
        raise HTTPException(status_code=403, detail="your role may not do this") from None
    except mpi.IdempotencyConflict:
        raise HTTPException(status_code=409, detail="this idempotency key was already used by a different request") from None
    except mpi.RecordIsMerged:
        raise HTTPException(status_code=409, detail="this record is merged into another record; act on that record") from None
    await write_audit(
        conn, clinic_id=row[0], origin=origin, actor_id=uuid.UUID(claims["sub"]), actor_role="front_desk", action="patient.revoke_identity", resource_type="patient",
        resource_id=patient_id, details={"reason": body.reason, "replayed": result.replayed, "review_task": result.review_task},
        idempotency_key=f"audit:revoke-identity:{patient_id}:{idempotency_key}",
    )
    return RevokeIdentityOut(patient_id=result.patient_id, confirmed=False, replayed=result.replayed, review_task=result.review_task)


# ---- review tasks: list, open, acknowledge, resolve (careflow/review_tasks.py; docs/design/review-task-routes.md) --------------------------------------------
#
# Row-level security decides what a person sees and may change (0007, 0013, 0020); the routes add no grant and no policy. Releasing a referral hold IS resolving its `referral_hold` task.
# Only the kinds in `review_tasks.RESOLVABLE_KINDS` close here; the rest close through their own route or wait for a clinician's decision (409, never closed quietly).

staff_review_tasks = APIRouter(prefix="/v1/staff/review-tasks", tags=["staff review tasks"])
TASK_READERS: Final = ("front_desk", "nurse", "doctor", "admin")


class ReviewTaskOut(_Out):
    """No provenance and no source id in the list; ids, closed words and times only."""

    task_id: uuid.UUID
    clinic_id: uuid.UUID
    kind: str
    priority: Literal["immediate", "standard"]
    assigned_role: Literal["nurse", "doctor", "front_desk"]
    status: Literal["open", "acknowledged", "resolved"]
    created_at: datetime
    acknowledged_by: uuid.UUID | None
    acknowledged_at: datetime | None
    resolved_at: datetime | None
    resolvable: bool  # True when the resolve route may close this kind (review_tasks.RESOLVABLE_KINDS); the console reads this instead of carrying its own copy of the list. The server still decides: a 409 stays a 409.


class ReviewTaskDetailOut(ReviewTaskOut):
    """`provenance` holds rule ids, record ids and closed words only (migration 0007: never the patient's own words)."""

    source_message_id: str
    provenance: dict[str, Any]


class ReviewTaskList(_Out):
    items: list[ReviewTaskOut]
    limit: int
    has_more: bool


class ReviewTaskTransitionOut(_Out):
    task_id: uuid.UUID
    status: Literal["acknowledged", "resolved"]
    replayed: bool


def _task_out(r: review_tasks.TaskRow) -> dict[str, Any]:
    return {
        "task_id": r.id, "clinic_id": r.clinic_id, "kind": r.kind, "priority": r.priority, "assigned_role": r.assigned_role, "status": r.status, "created_at": r.created_at,
        "acknowledged_by": r.acknowledged_by, "acknowledged_at": r.acknowledged_at, "resolved_at": r.resolved_at, "resolvable": r.kind in review_tasks.RESOLVABLE_KINDS,
    }


async def _task_reader(conn: AsyncConnection, claims: dict[str, Any]) -> None:
    """403 unless the caller actively holds a task-reading staff role somewhere (row-level security then narrows what they see)."""
    if not await _any_role_held(conn, TASK_READERS):
        raise HTTPException(status_code=403, detail="your role may not read this")


@staff_review_tasks.get("", response_model=ReviewTaskList)
async def list_review_tasks(
    conn: TxConn, claims: dict[str, Any] = Depends(verified_claims), status: Literal["open", "acknowledged", "resolved"] | None = None,
    kind: Annotated[str | None, Query(pattern=r"^[a-z][a-z0-9_]{0,63}$")] = None, assigned_role: Literal["nurse", "doctor", "front_desk"] | None = None, limit: Limit = 100,
) -> ReviewTaskList:
    """The tasks you may see, immediate before standard, then oldest first. 403 unless you hold a staff role."""
    await _task_reader(conn, claims)
    rows = await review_tasks.list_tasks(conn, status=status, kind=kind, assigned_role=assigned_role, limit=limit + 1)
    return ReviewTaskList(items=[ReviewTaskOut(**_task_out(r)) for r in rows[:limit]], limit=limit, has_more=len(rows) > limit)


@staff_review_tasks.get("/{task_id}", response_model=ReviewTaskDetailOut)
async def open_review_task(task_id: uuid.UUID, conn: TxConn, claims: dict[str, Any] = Depends(verified_claims)) -> ReviewTaskDetailOut:
    """One task with its provenance. 404 if you cannot see it (the same as one that does not exist), 403 unless you hold a staff role."""
    await _task_reader(conn, claims)
    try:
        r = await review_tasks.get_task(conn, task_id)
    except review_tasks.TaskNotFound:
        raise HTTPException(status_code=404, detail="not found") from None
    return ReviewTaskDetailOut(**_task_out(r), source_message_id=r.source_message_id or "", provenance=r.provenance or {})


async def _transition(
    action: Literal["acknowledge", "resolve"], task_id: uuid.UUID, idempotency_key: str, conn: AsyncConnection, claims: dict[str, Any]
) -> ReviewTaskTransitionOut:
    await _task_reader(conn, claims)
    by = uuid.UUID(claims["sub"])
    try:
        done = await (review_tasks.acknowledge if action == "acknowledge" else review_tasks.resolve)(conn, task_id, by)
    except review_tasks.TaskNotFound:
        raise HTTPException(status_code=404, detail="not found") from None
    except review_tasks.NotPermitted:
        raise HTTPException(status_code=403, detail="your role may not do this") from None
    except review_tasks.TaskConflict:
        raise HTTPException(status_code=409, detail="this task is already taken or closed") from None
    except review_tasks.NotResolvableHere:
        raise HTTPException(status_code=409, detail="this kind of task is closed through its own route") from None
    await write_audit(
        conn, clinic_id=done.clinic_id, origin=done.origin, actor_id=by, actor_role=await _acting_role(conn, claims, done.clinic_id, ("nurse", "doctor", "front_desk")),
        action=f"review_task.{action}", resource_type="review-task", resource_id=task_id, details={"kind": done.kind, "replayed": done.replayed},
        idempotency_key=f"audit:review-task-{action}:{task_id}:{idempotency_key}",
    )
    return ReviewTaskTransitionOut(task_id=done.task_id, status=done.status, replayed=done.replayed)  # type: ignore[arg-type]


@staff_review_tasks.post("/{task_id}/acknowledge", response_model=ReviewTaskTransitionOut)
async def acknowledge_review_task(
    task_id: uuid.UUID, idempotency_key: IdempotencyKey, conn: TxConn, claims: dict[str, Any] = Depends(verified_claims)
) -> ReviewTaskTransitionOut:
    """Take a task: open -> acknowledged, by you. The same person again is a replay. 404 if invisible, 403 if you may not change it, 409 if another person holds it or it is closed."""
    return await _transition("acknowledge", task_id, idempotency_key, conn, claims)


@staff_review_tasks.post("/{task_id}/resolve", response_model=ReviewTaskTransitionOut)
async def resolve_review_task(
    task_id: uuid.UUID, idempotency_key: IdempotencyKey, conn: TxConn, claims: dict[str, Any] = Depends(verified_claims)
) -> ReviewTaskTransitionOut:
    """Close a task (releasing a referral hold is resolving its `referral_hold` task). Only the kinds in `RESOLVABLE_KINDS`; others are a 409. A task another person acknowledged is
    a 409. 404 if invisible, 403 if you may not change it."""
    return await _transition("resolve", task_id, idempotency_key, conn, claims)


# ---- offers (Phase 3, slice 2, mechanism only; careflow/offers.py) -----------------------------------------------------------------------------------------
#
# Front desk only, under the person's own session; no worker identity and no new grant. The draft is computed now and stored nowhere; sending holds the slot for the chosen candidate and records the
# offer as a staff message in the patient's `appointments` thread (the clinician-approved template, verbatim). Nothing is delivered to the patient and nothing is interpreted. Every clinician-owned
# setting must exist (demo placeholders only in profile `demo`): otherwise the draft is `unavailable` and the send is a 503 naming the missing settings.


def scheduling_settings() -> SchedulingSettings:
    return scheduling_settings_for(os.environ, resolve_profile())


class SendOfferIn(_Body):
    clinic_id: uuid.UUID
    visit_type_id: uuid.UUID
    slot_id: uuid.UUID
    waitlist_id: uuid.UUID  # which ranked candidate; recomputed and checked server-side, never trusted


class SendOfferOut(_Out):
    """Ids and a time only."""

    hold_id: uuid.UUID
    slot_id: uuid.UUID
    patient_id: uuid.UUID
    thread_id: uuid.UUID
    message_id: uuid.UUID
    hold_expires_at: datetime
    replayed: bool


@scheduling.get("/offers/draft", response_model=Envelope)
async def draft_offer(
    clinic_id: uuid.UUID, visit_type_id: uuid.UUID, conn: TxConn, claims: dict[str, Any] = Depends(verified_claims),
    settings: SchedulingSettings = Depends(scheduling_settings),
) -> Envelope:
    """The ranked candidates for a freed slot and a DRAFT offer for the first, computed now and stored nowhere (`status`: ok, empty, or unavailable with `not_configured:` and the names of the
    missing clinician-owned settings). 404 for a clinic you cannot see, 403 unless you are front desk there."""
    await _front_desk_gate(conn, claims, clinic_id)
    return await mcp_invoke(
        build_scheduling_server(settings), "propose_offer", {"clinic_id": str(clinic_id), "visit_type_id": str(visit_type_id)},
        account_id=uuid.UUID(claims["sub"]), conn=conn, origin="live",
    )


@scheduling.post("/offers/send", response_model=SendOfferOut)
async def send_offer_route(
    body: SendOfferIn, idempotency_key: IdempotencyKey, conn: TxConn, request: Request, claims: dict[str, Any] = Depends(verified_claims),
    settings: SchedulingSettings = Depends(scheduling_settings),
) -> SendOfferOut:
    """Press send: hold the slot for the chosen ranked candidate and record the offer message. 404 for a clinic you cannot see, 403 unless you are front desk there, 409 if the slot is not open, the
    candidate is not among the ranked ones, or the key was used for a different offer, 503 if the clinician-owned settings or the encryption keys are not configured."""
    vault: DemographicVault | None = request.app.state.vault
    key_version = _active_key_version(request) if vault is not None else None
    if vault is None or key_version is None:
        log.error("offers are unavailable: no usable encryption key is configured (deployment fault)")
        raise HTTPException(status_code=503, detail="offers are unavailable: encryption keys are not configured")
    origin = await _front_desk_gate(conn, claims, body.clinic_id)
    actor = uuid.UUID(claims["sub"])
    try:
        with _scheduling_errors():
            sent = await offers.send_offer(
                conn, vault, settings, key_version=key_version, clinic_id=body.clinic_id, origin=origin, visit_type_id=body.visit_type_id, slot_id=body.slot_id,
                waitlist_id=body.waitlist_id, actor_id=actor, idempotency_key=idempotency_key,
            )
    except offers.OfferNotConfigured as exc:
        raise HTTPException(status_code=503, detail="offers are unavailable: not configured: " + ",".join(exc.names)) from None
    except offers.NotACandidate:
        raise HTTPException(status_code=409, detail="the slot or the candidate cannot be offered") from None
    except offers.OfferConflict:
        raise HTTPException(status_code=409, detail="this idempotency key was already used by a different request") from None
    await write_audit(
        conn, clinic_id=body.clinic_id, origin=origin, actor_id=actor, actor_role="front_desk", action="offer.send", resource_type="offer", resource_id=sent.hold_id,
        details={"slot_id": sent.slot_id, "patient_id": sent.patient_id, "replayed": sent.replayed}, idempotency_key=f"audit:offer-send:{sent.hold_id}:{idempotency_key}",
    )
    return SendOfferOut(
        hold_id=sent.hold_id, slot_id=sent.slot_id, patient_id=sent.patient_id, thread_id=sent.thread_id, message_id=sent.message_id,
        hold_expires_at=sent.hold_expires_at, replayed=sent.replayed,
    )


# ---- offer delivery by a person: list the offers, and mark one delivered (docs/design/phase3-manual-delivery.md; migration 0031) --------------------------------------------------------
#
# The system sends NOTHING to a patient. The front desk copies the drafted offer text (the message `send` recorded, read through the audited message read) and sends it from the clinic's own phone, then
# marks the offer delivered: one append-only row (who, when, the single channel `manual`). `GET /offers` is the list the front desk works from (awaiting delivery, delivered, expired, booked, released);
# an offer is never shown as sent unless a person marked it. Front desk, nurse and doctor read; ONLY front desk marks. Offer state is derived from the hold and the delivery row, never stored twice.

OfferState = Literal["awaiting_delivery", "delivered", "expired", "booked", "released"]


class OfferRowOut(_Out):
    hold_id: uuid.UUID
    clinic_id: uuid.UUID
    slot_id: uuid.UUID
    patient_id: uuid.UUID | None
    state: OfferState
    created_at: datetime
    expires_at: datetime
    delivered_at: datetime | None
    channel: Literal["manual"] | None


class OfferRowList(_Out):
    items: list[OfferRowOut]
    limit: int
    has_more: bool


class MarkDeliveredOut(_Out):
    hold_id: uuid.UUID
    channel: Literal["manual"]
    delivered_at: datetime
    replayed: bool


_OFFERS_SQL = (
    "with o as (select h.id, h.clinic_id, h.slot_id, h.patient_id, h.created_at, h.expires_at, d.delivered_at, d.channel, "
    "case when h.status = 'converted' then 'booked' when h.status = 'released' then 'released' "
    "when h.status <> 'active' or h.expires_at <= clock_timestamp() then 'expired' when d.delivered_at is not null then 'delivered' else 'awaiting_delivery' end as state "
    "from sched.holds h left join sched.offer_deliveries d on d.hold_id = h.id "
    "where h.clinic_id = any(cast(:c as uuid[])) and h.idempotency_key like 'offer-hold:%') "
    "select * from o where (cast(:s as text) is null or state = :s) order by created_at desc, id limit :n"
)


@scheduling.get("/offers", response_model=OfferRowList)
async def list_offers(conn: TxConn, claims: dict[str, Any] = Depends(verified_claims), state: OfferState | None = None, limit: Limit = 100) -> OfferRowList:
    """Offers of the clinics you may read, newest first (`state` filters). An offer is `delivered` only if a person marked it; `expired` once its hold ran out. Ids, times and closed words only."""
    clinics = await _readable_clinics(conn, claims)
    rows = (await conn.execute(text(_OFFERS_SQL), {"c": clinics, "s": state, "n": limit + 1})).all()
    items = [
        OfferRowOut(
            hold_id=r.id, clinic_id=r.clinic_id, slot_id=r.slot_id, patient_id=r.patient_id, state=r.state, created_at=r.created_at, expires_at=r.expires_at, delivered_at=r.delivered_at,
            channel=r.channel,
        )
        for r in rows[:limit]
    ]
    return OfferRowList(items=items, limit=limit, has_more=len(rows) > limit)


@scheduling.post("/offers/{hold_id}/mark-delivered", response_model=MarkDeliveredOut)
async def mark_offer_delivered(hold_id: uuid.UUID, idempotency_key: IdempotencyKey, conn: TxConn, claims: dict[str, Any] = Depends(verified_claims)) -> MarkDeliveredOut:
    """Record that YOU delivered this offer by hand (channel `manual`). 404 if you cannot see the offer, 403 unless you are front desk there, 409 if the hold is not an offer or is no longer live (expired, released or booked). A second press is a replay and writes nothing more."""
    hold = (await conn.execute(text("select clinic_id, origin, status, expires_at > clock_timestamp() as live, idempotency_key like 'offer-hold:%' as is_offer from sched.holds where id = :h"), {"h": hold_id})).first()
    if hold is None:
        raise HTTPException(status_code=404, detail="not found")
    if not await _clinic_role_held(conn, hold.clinic_id, ("front_desk",)):
        raise HTTPException(status_code=403, detail="your role may not do this")
    if not hold.is_offer:
        raise HTTPException(status_code=409, detail="this hold is not an offer")
    actor = uuid.UUID(claims["sub"])
    prior = (await conn.execute(text("select delivered_at, channel from sched.offer_deliveries where hold_id = :h"), {"h": hold_id})).first()
    replayed = prior is not None
    if prior is None:
        if hold.status != "active" or not hold.live:
            raise HTTPException(status_code=409, detail="this offer is no longer live")
        try:
            async with conn.begin_nested():
                created = (
                    await conn.execute(
                        text(
                            "insert into sched.offer_deliveries (clinic_id, hold_id, channel, delivered_by, origin, idempotency_key) values (:c, :h, 'manual', :u, :o, :k) "
                            "on conflict (hold_id) do nothing returning delivered_at, channel"
                        ),
                        {"c": hold.clinic_id, "h": hold_id, "u": actor, "o": hold.origin, "k": f"offer-delivery:{hold_id}:{idempotency_key}"},
                    )
                ).first()
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) == "42501":
                raise HTTPException(status_code=403, detail="your role may not do this") from None
            if getattr(exc.orig, "sqlstate", None) == "23505":  # the key is held by another request
                raise HTTPException(status_code=409, detail="this idempotency key was already used by a different request") from None
            raise
        if created is None:  # a concurrent press won: this one is a replay
            created = (await conn.execute(text("select delivered_at, channel from sched.offer_deliveries where hold_id = :h"), {"h": hold_id})).first()
            replayed = True
        prior = created
    if prior is None:
        raise HTTPException(status_code=409, detail="this offer is no longer live")
    await write_audit(
        conn, clinic_id=hold.clinic_id, origin=hold.origin, actor_id=actor, actor_role="front_desk", action="offer.mark_delivered", resource_type="offer", resource_id=hold_id,
        details={"channel": prior.channel, "replayed": replayed}, idempotency_key=f"audit:offer-delivered:{hold_id}:{idempotency_key}",
    )
    return MarkDeliveredOut(hold_id=hold_id, channel=prior.channel, delivered_at=prior.delivered_at, replayed=replayed)


# ---- reply interpretation, FAKES ONLY (careflow/reply_interpretation.py; docs/design/phase3-scheduling-agent.md section 5) ---------------------------------------------------------
#
# `POST /v1/staff/scheduling/offers/interpret-reply`: a front-desk person asks what a patient's reply to an offer means. The deterministic red-flag rules screen the reply FIRST (a red flag or an
# unassessable reply raises the nurse task and NO model is asked); only a clear patient reply on the appointments topic is put to the `decision` tier. The answer is a SUGGESTION with a closed
# reason word when there is none; it never contains the reply, and nothing books or is sent. With no decision service wired (no real engine is approved: the durable spend ledger is a hard blocker
# for a real call) the route answers 503. Front desk of the message's clinic only: another role that can SEE the message gets 403, anyone who cannot gets the one fixed 404.


class InterpretReplyIn(_Body):
    message_id: uuid.UUID


class InterpretReplyOut(_Out):
    """Ids, closed words and numbers only: never the reply or any patient text."""

    message_id: uuid.UUID
    screening_id: uuid.UUID
    outcome: Literal["red_flag", "unassessable", "clear"]
    replayed: bool
    suggestion: Literal["accept", "decline", "other_time", "question", "unclear"] | None
    confidence: float | None  # only a CALIBRATED confidence; None means unknown
    calibrated: bool | None
    engine: str | None
    reason: Literal["red_flag", "unassessable", "not_a_patient_reply", "no_route", "spend_ceiling", "engine_failed"] | None
    needs_person: bool


@scheduling.post("/offers/interpret-reply", response_model=InterpretReplyOut)
async def interpret_reply_route(body: InterpretReplyIn, idempotency_key: IdempotencyKey, conn: TxConn, request: Request, claims: dict[str, Any] = Depends(verified_claims)) -> InterpretReplyOut:
    """Suggest what a patient's reply means. 404 if you cannot see the message, 403 unless you are front desk of its clinic, 409 if the body was erased, 503 if reply interpretation or the encryption keys are
    not configured. The red-flag rules always run first; `suggestion` is null with a `reason` when a person must read the reply."""
    vault: DemographicVault | None = request.app.state.vault
    service = request.app.state.reply_interpreter
    if vault is None or service is None:
        log.error("reply interpretation is unavailable: no decision service or no encryption keys are configured")
        raise HTTPException(status_code=503, detail="reply interpretation is not available")
    row = (await conn.execute(text("select clinic_id, origin from msg.messages where id = :i"), {"i": body.message_id})).first()
    if row is None:
        raise HTTPException(status_code=404, detail="not found")  # missing and invisible are one answer
    if not await _holds_role(conn, claims, row.clinic_id, ("front_desk",)):
        raise HTTPException(status_code=403, detail="your role may not do this")
    actor = uuid.UUID(claims["sub"])
    try:
        result = await reply_interpretation.interpret_reply(conn, vault, _screening_engine, service, body.message_id, actor_id=actor, idempotency_key=idempotency_key)
    except (MessageNotFound, ScreeningNotPermitted):
        raise HTTPException(status_code=404, detail="not found") from None
    except MessageErased:
        raise HTTPException(status_code=409, detail="the message body was erased") from None
    await write_audit(
        conn, clinic_id=row.clinic_id, origin=row.origin, actor_id=actor, actor_role="front_desk", action="offer.interpret_reply", resource_type="message", resource_id=body.message_id,
        details={"outcome": result.outcome, "suggestion": result.suggestion or "none", "reason": result.reason or "none"}, idempotency_key=f"audit:interpret-reply:{body.message_id}:{idempotency_key}",
    )
    return InterpretReplyOut(
        message_id=result.message_id, screening_id=result.screening_id, outcome=result.outcome, replayed=result.replayed, suggestion=result.suggestion,  # type: ignore[arg-type]
        confidence=result.confidence, calibrated=result.calibrated, engine=result.engine, reason=result.reason, needs_person=result.needs_person,
    )


# ---- documents: begin an upload, register it (careflow/documents.py; docs/design/uploads-supabase-storage.md) -------------------------------------------------
#
# Two steps, both by a front-desk person or a nurse of the clinic. BEGIN hands the browser a short-lived signed URL for a path THE SERVER chose (derived from the clinic and the idempotency key).
# The browser PUTs the file. REGISTER (the SAME `X-Idempotency-Key`) makes the server read the stored object back and check its size, type and magic number before one row is written; a row exists
# only for an object the server itself accepted. Uploads are disabled outside profile `demo` until a malware scanner is wired (503); with no storage adapter configured they answer 503 too (no real
# storage adapter exists yet: it needs the bucket policy, an owner decision). The file name, bytes, path and hash never appear in a response error.

staff_documents = APIRouter(prefix="/v1/staff/documents", tags=["staff documents"])
UPLOAD_ROLES: Final = ("front_desk", "nurse")
DocumentKind = Literal["referral", "report", "other"]
UploadContentType = Literal["application/pdf", "image/jpeg", "image/png"]


class BeginUploadIn(_Body):
    clinic_id: uuid.UUID
    kind: DocumentKind
    content_type: UploadContentType
    size_hint: int | None = Field(default=None, gt=0, le=1_000_000_000)  # the browser's own claim; the server re-checks the stored size


class BeginUploadOut(_Out):
    """Where to PUT the file, once. The path is the server's and is not returned."""

    document_id: uuid.UUID
    upload_url: str
    expires_at: datetime
    replayed_ticket: bool = False


class RegisterIn(_Body):
    clinic_id: uuid.UUID
    kind: DocumentKind


class RegisteredOut(_Out):
    document_id: uuid.UUID
    kind: DocumentKind
    content_type: UploadContentType
    size_bytes: int
    replayed: bool
    scan: Literal["clean", "not_scanned", "not_checked"]  # not_scanned: registered but HELD for a person; not_checked: demo, no scanner wired


def _document_intake(request: Request) -> DocumentIntake:
    intake: DocumentIntake | None = request.app.state.document_intake
    if intake is None:
        log.error("uploads are unavailable: no document intake is configured (no storage adapter)")
        raise HTTPException(status_code=503, detail="uploads are unavailable: not configured")
    return intake


async def _upload_gate(conn: AsyncConnection, claims: dict[str, Any], clinic_id: uuid.UUID) -> str:
    """The clinic's origin after checking the caller is active front desk or nurse there: 404 if the clinic is invisible, 403 if visible but not one of those."""
    clinic = (await conn.execute(text("select origin from iam.clinics where id = :c"), {"c": clinic_id})).first()
    if clinic is None:
        raise HTTPException(status_code=404, detail="not found")
    if not await _holds_role(conn, claims, clinic_id, UPLOAD_ROLES):
        raise HTTPException(status_code=403, detail="your role may not upload documents")
    return clinic[0]


@contextmanager
def _document_errors() -> Iterator[None]:
    try:
        yield
    except UploadsDisabled:
        raise HTTPException(status_code=503, detail="uploads are disabled in this profile") from None
    except UploadUnavailable:
        raise HTTPException(status_code=503, detail="storage is unavailable; try again") from None
    except UploadRejected as exc:
        raise HTTPException(status_code=422, detail=exc.code) from None  # a fixed word: invalid_request, type_not_allowed, empty, too_large, not_found, content_mismatch, malware_detected
    except DocumentNotPermitted:
        raise HTTPException(status_code=403, detail="your role may not upload documents") from None
    except DocumentIdempotencyConflict:
        raise HTTPException(status_code=409, detail="this idempotency key was already used by a different request") from None


@staff_documents.post("/uploads", response_model=BeginUploadOut)
async def begin_document_upload(
    body: BeginUploadIn, idempotency_key: IdempotencyKey, conn: TxConn, request: Request, claims: dict[str, Any] = Depends(verified_claims)
) -> BeginUploadOut:
    """Step 1: a signed URL to PUT one file to. 404 for a clinic you cannot see, 403 unless you are front desk or a nurse there, 422 for a type or size not allowed, 503 if uploads are disabled
    or storage is not configured. Writes nothing (no row exists until step 2), so there is no audit row."""
    intake = _document_intake(request)
    await _upload_gate(conn, claims, body.clinic_id)
    with _document_errors():
        ticket = await intake.begin(body.clinic_id, body.kind, body.content_type, idempotency_key, size_hint=body.size_hint)
    return BeginUploadOut(document_id=ticket.document_id, upload_url=ticket.signed.url, expires_at=ticket.signed.expires_at)


@staff_documents.post("/register", response_model=RegisteredOut)
async def register_document(
    body: RegisterIn, idempotency_key: IdempotencyKey, conn: TxConn, request: Request, claims: dict[str, Any] = Depends(verified_claims)
) -> RegisteredOut:
    """Step 2, with the SAME idempotency key as step 1: the server reads the stored object back, verifies it, and writes one row. 422 with a fixed word if the object is missing, empty, too large,
    of a type not allowed, not what its type says, or flagged by the scanner (then it is deleted); 409 if the key was used for a different request; 503 as for step 1."""
    intake = _document_intake(request)
    origin = await _upload_gate(conn, claims, body.clinic_id)
    actor = uuid.UUID(claims["sub"])
    with _document_errors():
        done = await intake.register(conn, body.clinic_id, origin, body.kind, actor, idempotency_key)
    await write_audit(
        conn, clinic_id=body.clinic_id, origin=origin, actor_id=actor, actor_role=await _acting_role(conn, claims, body.clinic_id, UPLOAD_ROLES),
        action="document.register", resource_type="document", resource_id=done.document_id, details={"kind": done.kind, "scan": done.scan, "replayed": done.replayed},
        idempotency_key=f"audit:document-register:{done.document_id}:{idempotency_key}",
    )
    return RegisteredOut(
        document_id=done.document_id, kind=done.kind, content_type=done.content_type, size_bytes=done.size_bytes, replayed=done.replayed, scan=done.scan  # type: ignore[arg-type]
    )


async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """FastAPI's default 422 body includes the offending INPUT. On a route that takes a name, a date of birth and a phone that would put them
    in an error response (and in anything that records responses), so every validation error is reduced to its type, location and
    message. Pydantic's messages describe the rule that failed, not the value."""
    errors = [{"type": e.get("type"), "loc": list(e.get("loc", ())), "msg": e.get("msg")} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})


# ---- staff merges: propose, approve, unmerge (BUILD-SPEC.md §17.2, §18.1; CLAUDE.md rules 3, 5, 8) ------------------------------------------
#
# STEP-UP IMPLEMENTED, CLAIM SHAPE VERIFIED ON A REAL DEV TOKEN (2026-10-30). Every route here needs a recently verified second factor
# (`require_step_up`). What that capture did not cover (legacy string `amr`, a later challenge, an expired window, an end-to-end merge call) is listed in docs/design/step-up-spike.md.
# NOTE: the three route docstrings below still say "PENDING LIVE VERIFICATION": they are the OpenAPI descriptions the frontend's generated types copy, so they change together with the next
# schema regeneration (docs/design/step-up-spike.md).
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


async def _acting_role(conn: AsyncConnection, claims: dict[str, Any], clinic_id: uuid.UUID, roles: tuple[str, ...]) -> str:
    """The first of `roles` the caller actively holds in this clinic (for the audit row). Every caller has already been authorised for one of them; if none is found the audit row is not
    written and the request fails closed (a decision without an audit row must not commit)."""
    row = (
        await conn.execute(text("select r from unnest(iam.effective_roles(:c)) as r where r = any(cast(:r as text[])) order by r limit 1"), {"c": clinic_id, "r": list(roles)})
    ).first()  # the roles that count under the active claim (migration 0028): the audit row names the role actually used
    if row is None:
        raise HTTPException(status_code=403, detail="your role may not do this")
    return row[0]


async def _front_desk_gate(conn: AsyncConnection, claims: dict[str, Any], clinic_id: uuid.UUID) -> str:
    """The clinic's origin, after checking the caller is active front desk there. 404 if the clinic is invisible, 403 if visible but not front desk."""
    clinic = (await conn.execute(text("select origin from iam.clinics where id = :c"), {"c": clinic_id})).first()
    if clinic is None:
        raise HTTPException(status_code=404, detail="not found")
    if not await _clinic_role_held(conn, clinic_id, ("front_desk",)):
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
    await write_audit(
        conn, clinic_id=body.clinic_id, origin=origin, actor_id=uuid.UUID(claims["sub"]), actor_role="front_desk", action="merge.propose", resource_type="merge",
        resource_id=result.merge_id, details={"master_patient_id": result.master_id, "subordinate_patient_id": result.subordinate_id, "replayed": result.replayed},
        idempotency_key=f"audit:merge-propose:{result.merge_id}:{idempotency_key}",
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
    await write_audit(
        conn, clinic_id=clinic_id, origin=origin, actor_id=uuid.UUID(claims["sub"]), actor_role="front_desk", action="merge.approve", resource_type="merge",
        resource_id=merge_id, details={"master_patient_id": result.master_id, "subordinate_patient_id": result.subordinate_id, "replayed": result.replayed},
        idempotency_key=f"audit:merge-approve:{merge_id}:{idempotency_key}",
    )
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
    await write_audit(
        conn, clinic_id=clinic_id, origin=origin, actor_id=uuid.UUID(claims["sub"]), actor_role="front_desk", action="merge.unmerge", resource_type="merge",
        resource_id=merge_id, details={"decisions_flagged": result.decisions_flagged, "replayed": result.replayed},
        idempotency_key=f"audit:merge-unmerge:{merge_id}:{idempotency_key}",
    )
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
    return await _clinic_role_held(conn, clinic_id, roles)


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
    await write_audit(
        conn, clinic_id=doc.clinic_id, origin=doc.origin, actor_id=uuid.UUID(reviewer), actor_role=await _acting_role(conn, claims, doc.clinic_id, ("nurse", "doctor")),
        action="referral.review_decision", resource_type="document", resource_id=document_id, details={"decision": body.decision},
        idempotency_key=f"audit:referral-review:{document_id}:{idempotency_key}",
    )
    return ReviewOut(document_id=document_id, decision=body.decision, replayed=False)


# ---- application -----------------------------------------------------------------------------------


def _load_vault() -> DemographicVault | None:
    """The key vault from the environment, or None (the screening route then answers 503: degrade visibly, never silently)."""
    try:
        return DemographicVault.from_env()
    except CryptoError as exc:
        log.warning("message encryption keys are not configured; message routes are unavailable (%s)", type(exc).__name__)
        return None


# ---- patient lookup and the verified-status read (mpi.lookup_patients, mpi.patient_status) ---------------------------------------------------------------------
#
# SEARCH is an exact, read-only lookup by the same keyed tokens registration uses: the caller types a name and date of birth (and optionally a phone) and gets ids, the kinds of identifier that
# agreed and whether each record is verified. It returns NO name, date of birth or phone (what the caller typed they already know; showing a stored value is a separate decision), writes
# nothing, and writes ONE audit row per search: who, role, the kind of search and the result count, never the query text or the results. Front desk only (they register). STATUS tells a front-desk person, nurse
# or doctor whether one record is verified and why; the confirm and revoke buttons need it.


class PatientSearchIn(_Body):
    clinic_id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)
    dob: str = Field(min_length=1, max_length=40)
    phone: str | None = Field(default=None, max_length=40)


def patient_view_config(request: Request) -> patient_view.PatientViewConfig:
    """The deployment's view configuration (overridable in tests). Read once per app and kept: the limiters live on the app, one per configured limit, so a request cannot reset them. A setting with an
    INVALID value does not break the other routes: it marks the config `misconfigured` (the view routes answer a fixed 503; search still works, without labels)."""
    cache: dict[str, Any] = request.app.state.__dict__.setdefault("patient_view_cache", {})
    if "config" not in cache:
        try:
            settings = patient_view.PatientViewSettings()
            fill = {k: v for k, v in demo_phase3.patient_view_placeholders(os.environ, resolve_profile()).items() if getattr(settings, k) is None}  # an explicit value always wins
            settings, bad = settings.model_copy(update=fill), False
        except ValidationError:
            settings, bad = patient_view.PatientViewSettings.model_construct(max_opens_per_minute=None, max_label_rows_per_minute=None, page_limit=1), True  # inert values (no validation, no env): nothing is served while `misconfigured`
        opens = patient_view.OpenRateLimiter(settings.max_opens_per_minute) if settings.max_opens_per_minute is not None else None
        rows = patient_view.OpenRateLimiter(settings.max_label_rows_per_minute) if settings.max_label_rows_per_minute is not None else None
        cache["config"] = patient_view.PatientViewConfig(patient_view.feature_enabled(resolve_profile(), os.environ), settings, opens, rows, misconfigured=bad)
    return cache["config"]


class PatientLabelOut(_Out):
    """One list row. The year of birth and the last four digits only; the full date and the full number appear only when one record is opened."""

    patient_id: uuid.UUID
    name: str
    birth_year: int
    phone_last4: str | None


class PatientHitOut(_Out):
    patient_id: uuid.UUID
    label: PatientLabelOut | None = None  # the list-row view (name, year of birth, last four digits); None outside the demo profile with synthetic data
    matched_kinds: list[Literal["name_dob", "phone"]]
    verified: bool
    registered_via: Literal["front_desk", "referral_worker"]


class PatientSearchOut(_Out):
    items: list[PatientHitOut]
    truncated: bool = False  # more exact matches than one page holds (the page limit, at most 25); there is no total count
    labels_withheld: bool = False  # the feature is on but the labels were not produced (the per-user row budget is spent or not configured)


class PatientStatusOut(_Out):
    patient_id: uuid.UUID
    clinic_id: uuid.UUID
    registered_via: Literal["front_desk", "referral_worker"]
    verified: bool
    latest_identity_event: Literal["confirm", "revoke"] | None
    latest_identity_event_at: datetime | None
    merged_into: uuid.UUID | None  # set when this record is the subordinate of an ACTIVE merge: act on that record


@staff_patients.post("/search", response_model=PatientSearchOut)
async def search_patients(
    body: PatientSearchIn, response: Response, conn: TxConn, request: Request, claims: dict[str, Any] = Depends(verified_claims),
    config: patient_view.PatientViewConfig = Depends(patient_view_config),
) -> PatientSearchOut:
    """Find existing records for exactly these details. 404 for a clinic you cannot see, 403 unless you are front desk there, 422 for an invalid date of birth or phone (named, never echoed), 503 if
    the encryption keys are not configured. Read-only."""
    vault: DemographicVault | None = request.app.state.vault
    key_version = _active_key_version(request) if vault is not None else None
    if vault is None or key_version is None:
        log.error("patient search is unavailable: no usable encryption key is configured (deployment fault)")
        raise HTTPException(status_code=503, detail="patient search is unavailable: encryption keys are not configured")
    origin = await _front_desk_gate(conn, claims, body.clinic_id)
    try:
        hits = await mpi.lookup_patients(
            PatientIngestionPipeline(vault, key_version=key_version), conn, clinic_id=body.clinic_id, origin=origin, name=body.name, dob=body.dob, phone=body.phone
        )
    except mpi.InvalidDateOfBirth:
        raise HTTPException(status_code=422, detail="date of birth must be a real past date in YYYY-MM-DD form") from None
    except mpi.InvalidPhone:
        raise HTTPException(status_code=422, detail="phone must be a valid Indian mobile number") from None
    except mpi.IngestionError:
        raise HTTPException(status_code=422, detail="a required field is missing or blank") from None
    except UnicodeError:
        raise HTTPException(status_code=422, detail="the name contains characters that cannot be stored") from None
    response.headers["Cache-Control"] = "no-store"
    labels: dict[uuid.UUID, Any] = {}
    page = hits[: config.settings.page_limit]
    # A label (the owner's name) is attached ONLY to a hit that matched on name and date of birth: a phone-only hit would reveal whose number it is. The rows are charged to the per-user row budget.
    wanted = [h.patient_id for h in page if "name_dob" in h.matched_kinds]
    withheld = False
    if config.enabled and wanted:
        ready = not config.misconfigured and config.label_limiter is not None and config.settings.max_label_rows_per_minute is not None
        if ready and config.label_limiter.allow(uuid.UUID(claims["sub"]), len(wanted)):  # type: ignore[union-attr]
            with _view_errors():
                labels = {x.patient_id: x for x in await patient_view.patient_labels(conn, vault, body.clinic_id, wanted, page_limit=config.settings.page_limit)}
        else:
            withheld = True
    # One audit row per search: who, role, the KIND of search and how many results. Never the query text and never the results (owner decision 2026-10-30).
    await write_audit(
        conn, clinic_id=body.clinic_id, origin=origin, actor_id=uuid.UUID(claims["sub"]), actor_role="front_desk", action="patient.search", resource_type="clinic", resource_id=body.clinic_id,
        details={"kind": "name_dob_phone" if body.phone else "name_dob", "results": len(hits), "labels": len(labels)}, idempotency_key=f"audit:patient-search:{uuid.uuid4()}",
    )
    return PatientSearchOut(
        items=[
            PatientHitOut(
                patient_id=h.patient_id, matched_kinds=list(h.matched_kinds), verified=h.verified, registered_via=h.registered_via,  # type: ignore[arg-type]
                label=(PatientLabelOut(patient_id=h.patient_id, name=labels[h.patient_id].name, birth_year=labels[h.patient_id].birth_year, phone_last4=labels[h.patient_id].phone_last4) if h.patient_id in labels else None),
            )
            for h in page
        ],
        truncated=len(hits) > len(page),
        labels_withheld=withheld,
    )


@staff_patients.get("/{patient_id}", response_model=PatientStatusOut)
async def patient_status_route(patient_id: uuid.UUID, conn: TxConn, claims: dict[str, Any] = Depends(verified_claims)) -> PatientStatusOut:
    """Whether this record is verified and why (its source and its latest confirm or revoke). 404 if you cannot see it (the same as a missing or erased one), 403 unless you are front desk,
    nurse or doctor somewhere."""
    await _referral_reader(conn, claims)
    try:
        s = await mpi.patient_status(conn, patient_id)
    except mpi.PatientNotFound:
        raise HTTPException(status_code=404, detail="not found") from None
    return PatientStatusOut(
        patient_id=s.patient_id, clinic_id=s.clinic_id, registered_via=s.registered_via, verified=s.verified,  # type: ignore[arg-type]
        latest_identity_event=s.latest_event, latest_identity_event_at=s.latest_event_at, merged_into=s.merged_into,
    )


# ---- identity lists: possible duplicates and merges (reads only) ---------------------------------------------------------------------------------------------
#
# What the merge screens show before anyone proposes, approves or breaks a link. Reads only, under the existing `select` policies (possible duplicates: front desk; merges: front desk, nurse,
# doctor). They live OUTSIDE `/v1/staff/merges` on purpose: that router requires a second factor for every route, which is right for the three write steps and wrong for reading a list.
# Ids, closed words and times only: no name, date of birth or phone.

staff_identity = APIRouter(prefix="/v1/staff/identity", tags=["staff identity"])
DUPLICATE_READERS: Final = ("front_desk",)
MERGE_READERS: Final = ("front_desk", "nurse", "doctor")


class DuplicateOut(_Out):
    candidate_id: uuid.UUID
    clinic_id: uuid.UUID
    patient_id: uuid.UUID  # the record just created
    candidate_patient_id: uuid.UUID  # the existing record it resembles
    matched_kinds: list[Literal["name_dob", "phone"]]
    reason: Literal["near_match", "ambiguous"]
    status: Literal["pending", "resolved"]
    created_at: datetime
    resolved_at: datetime | None


class DuplicateList(_Out):
    items: list[DuplicateOut]
    limit: int
    has_more: bool


class MergeDetailOut(_Out):
    merge_id: uuid.UUID
    clinic_id: uuid.UUID
    master_patient_id: uuid.UUID
    subordinate_patient_id: uuid.UUID
    candidate_id: uuid.UUID
    status: Literal["awaiting_second_approval", "active", "unmerged"]
    proposed_by: uuid.UUID
    second_approved_by: uuid.UUID | None
    activated_at: datetime | None
    unmerged_at: datetime | None
    unmerged_by: uuid.UUID | None
    created_at: datetime


class MergeList(_Out):
    items: list[MergeDetailOut]
    limit: int
    has_more: bool


async def _staff_reader(conn: AsyncConnection, claims: dict[str, Any], roles: tuple[str, ...]) -> None:
    if not await _any_role_held(conn, roles):
        raise HTTPException(status_code=403, detail="your role may not read this")


def _merge_detail_out(r: Any) -> MergeDetailOut:
    return MergeDetailOut(
        merge_id=r.id, clinic_id=r.clinic_id, master_patient_id=r.master_patient_id, subordinate_patient_id=r.subordinate_patient_id, candidate_id=r.candidate_id, status=r.status,
        proposed_by=r.approved_by, second_approved_by=r.second_approved_by, activated_at=r.activated_at, unmerged_at=r.unmerged_at, unmerged_by=r.unmerged_by, created_at=r.created_at,
    )


_MERGE_COLUMNS = "id, clinic_id, master_patient_id, subordinate_patient_id, candidate_id, status, approved_by, second_approved_by, activated_at, unmerged_at, unmerged_by, created_at"


@staff_identity.get("/possible-duplicates", response_model=DuplicateList)
async def list_possible_duplicates(
    conn: TxConn, claims: dict[str, Any] = Depends(verified_claims), status: Literal["pending", "resolved"] | None = "pending", limit: Limit = 100
) -> DuplicateList:
    """Records the matcher queued for a person to compare (newest first; `pending` by default, `status` filters). Front desk only. 403 otherwise."""
    await _staff_reader(conn, claims, DUPLICATE_READERS)
    rows = (
        await conn.execute(
            text(
                "select id, clinic_id, patient_id, candidate_patient_id, matched_kinds, reason, status, created_at, resolved_at from mpi.match_candidates "
                "where (cast(:s as text) is null or status = :s) order by created_at desc, id limit :n"
            ),
            {"s": status, "n": limit + 1},
        )
    ).all()
    items = [
        DuplicateOut(
            candidate_id=r.id, clinic_id=r.clinic_id, patient_id=r.patient_id, candidate_patient_id=r.candidate_patient_id, matched_kinds=list(r.matched_kinds), reason=r.reason,
            status=r.status, created_at=r.created_at, resolved_at=r.resolved_at,
        )
        for r in rows[:limit]
    ]
    return DuplicateList(items=items, limit=limit, has_more=len(rows) > limit)


@staff_identity.get("/merges", response_model=MergeList)
async def list_merges(
    conn: TxConn, claims: dict[str, Any] = Depends(verified_claims), status: Literal["awaiting_second_approval", "active", "unmerged"] | None = None, limit: Limit = 100
) -> MergeList:
    """Merges of the clinics you may read, newest first. Front desk, nurse and doctor. 403 otherwise."""
    await _staff_reader(conn, claims, MERGE_READERS)
    rows = (
        await conn.execute(
            text(f"select {_MERGE_COLUMNS} from mpi.merges where (cast(:s as text) is null or status = :s) order by created_at desc, id limit :n"), {"s": status, "n": limit + 1}
        )
    ).all()
    return MergeList(items=[_merge_detail_out(r) for r in rows[:limit]], limit=limit, has_more=len(rows) > limit)


@staff_identity.get("/merges/{merge_id}", response_model=MergeDetailOut)
async def open_merge(merge_id: uuid.UUID, conn: TxConn, claims: dict[str, Any] = Depends(verified_claims)) -> MergeDetailOut:
    """One merge. 404 if you cannot see it (the same as a missing one), 403 unless you hold front desk, nurse or doctor somewhere."""
    await _staff_reader(conn, claims, MERGE_READERS)
    r = (await conn.execute(text(f"select {_MERGE_COLUMNS} from mpi.merges where id = :i"), {"i": merge_id})).first()
    if r is None:
        raise HTTPException(status_code=404, detail="not found")
    return _merge_detail_out(r)


# ---- holds: list, and release one on purpose (SlotManager.release_hold) ----------------------------------------------------------------------------------------
#
# A hold otherwise lives until its time runs out. Front desk may give the slot back at once (the existing front-desk update on `sched.holds` and on the slot, no new grant). Listing is for the
# same readers as the other scheduling reads. Releasing needs the idempotency key and writes one audit row; a hold that is already released is a replay in effect; a hold that was booked
# (`converted`) or has `expired` is a 409, never silently reopened.


class HoldRowOut(_Out):
    hold_id: uuid.UUID
    clinic_id: uuid.UUID
    slot_id: uuid.UUID
    patient_id: uuid.UUID | None
    status: Literal["active", "released", "expired", "converted"]
    created_at: datetime
    expires_at: datetime


class HoldRowList(_Out):
    items: list[HoldRowOut]
    limit: int
    has_more: bool


class ReleaseOut(_Out):
    hold_id: uuid.UUID
    slot_id: uuid.UUID
    status: Literal["released"]
    replayed: bool


@scheduling.get("/holds", response_model=HoldRowList)
async def list_holds(
    conn: TxConn, claims: dict[str, Any] = Depends(verified_claims), status: Literal["active", "released", "expired", "converted"] | None = "active", limit: Limit = 100
) -> HoldRowList:
    """Holds of the clinics you may read, soonest to expire first (`active` by default; `status` filters)."""
    clinics = await _readable_clinics(conn, claims)
    rows = (
        await conn.execute(
            text(
                "select id, clinic_id, slot_id, patient_id, status, created_at, expires_at from sched.holds "
                "where clinic_id = any(cast(:c as uuid[])) and (cast(:s as text) is null or status = :s) order by expires_at, id limit :n"
            ),
            {"c": clinics, "s": status, "n": limit + 1},
        )
    ).all()
    items = [
        HoldRowOut(hold_id=r.id, clinic_id=r.clinic_id, slot_id=r.slot_id, patient_id=r.patient_id, status=r.status, created_at=r.created_at, expires_at=r.expires_at)
        for r in rows[:limit]
    ]
    return HoldRowList(items=items, limit=limit, has_more=len(rows) > limit)


@scheduling.post("/holds/{hold_id}/release", response_model=ReleaseOut)
async def release_hold_route(hold_id: uuid.UUID, idempotency_key: IdempotencyKey, conn: TxConn, claims: dict[str, Any] = Depends(verified_claims)) -> ReleaseOut:
    """Give a held slot back now. 404 if you cannot see the hold, 403 unless you are front desk of its clinic, 409 if it was booked or has expired."""
    row = (await conn.execute(text("select clinic_id, origin from sched.holds where id = :h"), {"h": hold_id})).first()
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    if not await _holds_role(conn, claims, row.clinic_id, ("front_desk",)):
        raise HTTPException(status_code=403, detail="your role may not do this")
    with _scheduling_errors():
        released = await _slots.release_hold(conn, hold_id)
    actor = uuid.UUID(claims["sub"])
    await write_audit(
        conn, clinic_id=row.clinic_id, origin=row.origin, actor_id=actor, actor_role="front_desk", action="hold.release", resource_type="hold", resource_id=hold_id,
        details={"slot_id": released.slot_id, "replayed": released.replayed}, idempotency_key=f"audit:hold-release:{hold_id}:{idempotency_key}",
    )
    return ReleaseOut(hold_id=released.hold_id, slot_id=released.slot_id, status="released", replayed=released.replayed)


# ---- audited erasure of a patient's identity (migration 0032; docs/design/patient-erasure-and-update-narrowing.md) ------------------------------------------------------------------------
#
# `POST /v1/staff/patients/{patient_id}/erase`: front desk of the patient's clinic, with a recent second factor (step-up), a reason from the clinic's CLOSED list, and an idempotency key. The work is the database
# function mpi.erase_patient (the only way to erase; the application role can no longer update the identity columns): it clears the encrypted name, date of birth and phone and writes the audit row in the same
# transaction. The list of allowed reasons is a REQUIRED setting with no default (CAREFLOW_ERASE_REASONS, comma separated closed words): it is a legal and privacy decision, not an engineering one, so while it
# is unset the route answers 503. Messages are not erased by this route. A patient in a live merge link is a 409 (break the link first).


class EraseIn(_Body):
    reason: str = Field(min_length=1, max_length=40, pattern=r"^[a-z][a-z0-9_]*$")


class EraseOut(_Out):
    patient_id: uuid.UUID
    erased: bool  # False on a replay: the record was already erased and nothing more was written


@staff_patients.post("/{patient_id}/erase", response_model=EraseOut, dependencies=[Depends(require_step_up)])
async def erase_patient_route(patient_id: uuid.UUID, body: EraseIn, idempotency_key: IdempotencyKey, request: Request, conn: TxConn, claims: dict[str, Any] = Depends(verified_claims)) -> EraseOut:
    """Erase a patient's identity (name, date of birth, phone). 404 if you cannot see the patient, 403 unless you are front desk of the clinic, 409 if the patient is in a live merge link, 422 for a reason not on the clinic's list, 503 while the list of reasons is not configured. STEP-UP REQUIRED."""
    allowed: frozenset[str] | None = request.app.state.erase_reasons
    if not allowed:
        log.error("patient erasure is unavailable: CAREFLOW_ERASE_REASONS is not set (the list of reasons is the clinic's decision)")
        raise HTTPException(status_code=503, detail="erasure is unavailable: not configured")
    if body.reason not in allowed:
        raise HTTPException(status_code=422, detail="request rejected")
    row = (await conn.execute(text("select clinic_id from mpi.patients where id = :i"), {"i": patient_id})).first()
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    if not await _clinic_role_held(conn, row[0], ("front_desk",)):
        raise HTTPException(status_code=403, detail="your role may not do this")
    try:
        async with conn.begin_nested():
            erased = bool((await conn.execute(text("select mpi.erase_patient(:p, :r, :k)"), {"p": patient_id, "r": body.reason, "k": idempotency_key})).scalar())
    except DBAPIError as exc:
        state = getattr(exc.orig, "sqlstate", None)
        if state == "23514":
            raise HTTPException(status_code=409, detail="this patient is in a live merge link") from None
        if state == "42501":
            raise HTTPException(status_code=403, detail="your role may not do this") from None
        raise
    return EraseOut(patient_id=patient_id, erased=erased)


# ---- clinic set-up: create locations, providers, visit types and slots (front desk and admin; docs/design/clinic-setup-routes.md) ----------------------------------------------------------
#
# The tables, policies and permissions already allow front desk and admin to write these (migration 0002); there was just no route, so a clinic could not set itself up without the seed script. No migration,
# no new grant. Every route needs `X-Idempotency-Key` (a replay answers the original; a different request under the same key is a 409), writes one audit row (ids and numbers only), and is limited to
# front desk and admin of the named clinic (404 if the clinic is not visible, 403 if visible but not one of those roles). Priority levels are NOT created here: they are clinician-owned. Nothing is
# ever deleted: a later step deactivates.

SETUP_ROLES: Final = ("front_desk", "admin")
MAX_SLOT_HOURS: Final = 24


class LocationIn(_Body):
    clinic_id: uuid.UUID
    name: str = Field(min_length=1, max_length=120, pattern=r"\S")


class ProviderIn(_Body):
    clinic_id: uuid.UUID
    display_name: str = Field(min_length=1, max_length=120, pattern=r"\S")


class VisitTypeIn(_Body):
    clinic_id: uuid.UUID
    name: str = Field(min_length=1, max_length=120, pattern=r"\S")
    duration_minutes: int = Field(gt=0, le=1440)


class SlotIn(_Body):
    clinic_id: uuid.UUID
    provider_id: uuid.UUID
    location_id: uuid.UUID
    visit_type_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime


class CreatedOut(_Out):
    id: uuid.UUID
    clinic_id: uuid.UUID
    replayed: bool


async def _setup_gate(conn: AsyncConnection, claims: dict[str, Any], clinic_id: uuid.UUID) -> tuple[str, str]:
    """(origin, acting role) after checking the caller holds front desk or admin at this clinic NOW. 404 if the clinic is invisible, 403 if visible but not one of those roles."""
    clinic = (await conn.execute(text("select origin from iam.clinics where id = :c"), {"c": clinic_id})).first()
    if clinic is None:
        raise HTTPException(status_code=404, detail="not found")
    return clinic[0], await _acting_role(conn, claims, clinic_id, SETUP_ROLES)


async def _setup_insert(
    conn: AsyncConnection, *, table: str, clinic_id: uuid.UUID, origin: str, key: str, columns: dict[str, Any], same: Callable[[Any], bool], error_map: dict[str, tuple[int, str]]
) -> tuple[uuid.UUID, bool]:
    """Insert one set-up row under a key made of the table, the clinic and the client's key. Returns (id, replayed). A replay with different content is the fixed 409."""
    names = ", ".join(columns)
    marks = ", ".join(f":{c}" for c in columns)
    full_key = f"setup:{table}:{clinic_id}:{key}"
    try:
        async with conn.begin_nested():
            row = (
                await conn.execute(
                    text(f"insert into sched.{table} (clinic_id, origin, idempotency_key, {names}) values (:clinic_id, :origin, :k, {marks}) on conflict (idempotency_key) do nothing returning id"),
                    {"clinic_id": clinic_id, "origin": origin, "k": full_key, **columns},
                )
            ).first()
    except DBAPIError as exc:
        state = getattr(exc.orig, "sqlstate", None)
        if state in error_map:
            status, detail = error_map[state]
            raise HTTPException(status_code=status, detail=detail) from None
        if state == "42501":
            raise HTTPException(status_code=403, detail="your role may not do this") from None
        raise
    if row is not None:
        return row[0], False
    prior = (await conn.execute(text(f"select * from sched.{table} where idempotency_key = :k"), {"k": full_key})).first()
    if prior is None or not same(prior):
        raise HTTPException(status_code=409, detail="this idempotency key was already used by a different request")
    return prior.id, True


async def _setup_audit(conn: AsyncConnection, *, clinic_id: uuid.UUID, origin: str, claims: dict[str, Any], role: str, action: str, resource_id: uuid.UUID, key: str, replayed: bool) -> None:
    await write_audit(
        conn, clinic_id=clinic_id, origin=origin, actor_id=uuid.UUID(claims["sub"]), actor_role=role, action=action, resource_type=action.split(".")[1].removeprefix("create_"),
        resource_id=resource_id, details={"replayed": replayed}, idempotency_key=f"audit:{action}:{resource_id}:{key}",
    )


@scheduling.post("/locations", response_model=CreatedOut)
async def create_location(body: LocationIn, idempotency_key: IdempotencyKey, conn: TxConn, claims: dict[str, Any] = Depends(verified_claims)) -> CreatedOut:
    """Create a location (room or site). 404 if the clinic is not visible to you, 403 unless you are front desk or admin there, 409 if the key was used for a different request."""
    origin, role = await _setup_gate(conn, claims, body.clinic_id)
    new_id, replayed = await _setup_insert(
        conn, table="locations", clinic_id=body.clinic_id, origin=origin, key=idempotency_key, columns={"name": body.name.strip()}, same=lambda r: r.name == body.name.strip(), error_map={}
    )
    await _setup_audit(conn, clinic_id=body.clinic_id, origin=origin, claims=claims, role=role, action="setup.create_location", resource_id=new_id, key=idempotency_key, replayed=replayed)
    return CreatedOut(id=new_id, clinic_id=body.clinic_id, replayed=replayed)


@scheduling.post("/providers", response_model=CreatedOut)
async def create_provider(body: ProviderIn, idempotency_key: IdempotencyKey, conn: TxConn, claims: dict[str, Any] = Depends(verified_claims)) -> CreatedOut:
    """Create a provider (a display name; no sign-in is linked here). 404 if the clinic is not visible to you, 403 unless you are front desk or admin there, 409 if the key was used for a different request."""
    origin, role = await _setup_gate(conn, claims, body.clinic_id)
    new_id, replayed = await _setup_insert(
        conn, table="providers", clinic_id=body.clinic_id, origin=origin, key=idempotency_key, columns={"display_name": body.display_name.strip()},
        same=lambda r: r.display_name == body.display_name.strip(), error_map={},
    )
    await _setup_audit(conn, clinic_id=body.clinic_id, origin=origin, claims=claims, role=role, action="setup.create_provider", resource_id=new_id, key=idempotency_key, replayed=replayed)
    return CreatedOut(id=new_id, clinic_id=body.clinic_id, replayed=replayed)


@scheduling.post("/visit-types", response_model=CreatedOut)
async def create_visit_type(body: VisitTypeIn, idempotency_key: IdempotencyKey, conn: TxConn, claims: dict[str, Any] = Depends(verified_claims)) -> CreatedOut:
    """Create a visit type with its scheduling length in minutes (entered by the clinic; nothing here guesses a duration). 404 if the clinic is not visible to you, 403 unless you are front desk or admin there, 409 if the key was used for a different request."""
    origin, role = await _setup_gate(conn, claims, body.clinic_id)
    new_id, replayed = await _setup_insert(
        conn, table="visit_types", clinic_id=body.clinic_id, origin=origin, key=idempotency_key, columns={"name": body.name.strip(), "duration_minutes": body.duration_minutes},
        same=lambda r: (r.name, r.duration_minutes) == (body.name.strip(), body.duration_minutes), error_map={},
    )
    await _setup_audit(conn, clinic_id=body.clinic_id, origin=origin, claims=claims, role=role, action="setup.create_visit_type", resource_id=new_id, key=idempotency_key, replayed=replayed)
    return CreatedOut(id=new_id, clinic_id=body.clinic_id, replayed=replayed)


@scheduling.post("/slots", response_model=CreatedOut)
async def create_slot(body: SlotIn, idempotency_key: IdempotencyKey, conn: TxConn, claims: dict[str, Any] = Depends(verified_claims)) -> CreatedOut:
    """Create one open slot. 404 if the clinic, provider, location or visit type is not visible to you in that clinic, 403 unless you are front desk or admin there, 409 if it overlaps another live slot of the same provider or the key was used for a different request, 422 for a bad time range."""
    if body.starts_at.tzinfo is None or body.ends_at.tzinfo is None or body.ends_at <= body.starts_at or (body.ends_at - body.starts_at).total_seconds() > MAX_SLOT_HOURS * 3600:
        raise HTTPException(status_code=422, detail="request rejected")
    origin, role = await _setup_gate(conn, claims, body.clinic_id)
    new_id, replayed = await _setup_insert(
        conn, table="slots", clinic_id=body.clinic_id, origin=origin, key=idempotency_key,
        columns={"provider_id": body.provider_id, "location_id": body.location_id, "visit_type_id": body.visit_type_id, "starts_at": body.starts_at, "ends_at": body.ends_at},
        same=lambda r: (r.provider_id, r.location_id, r.visit_type_id, r.starts_at, r.ends_at) == (body.provider_id, body.location_id, body.visit_type_id, body.starts_at, body.ends_at),
        error_map={"23P01": (409, "that time overlaps another slot of this provider"), "23503": (404, "not found")},
    )
    await _setup_audit(conn, clinic_id=body.clinic_id, origin=origin, claims=claims, role=role, action="setup.create_slot", resource_id=new_id, key=idempotency_key, replayed=replayed)
    return CreatedOut(id=new_id, clinic_id=body.clinic_id, replayed=replayed)


# ---- the audit-log reader (auditor only) -----------------------------------------------------------------------------------------------------------------------
#
# Reads `audit.log` under the existing policy `log_select_auditor` (an auditor of the row's clinic; system rows with no clinic are invisible to the application role). Newest first, keyset paging by
# the row id (`before_id`), filters on the closed columns only. Rows hold ids and closed words by the writer's rule (careflow/audit.py refuses free text); `reason` (the break-glass free-text column)
# is NOT returned. Reading the log is not itself logged (it would be a row per read of the log).

staff_audit = APIRouter(prefix="/v1/staff/audit", tags=["staff audit"])


class AuditRowOut(_Out):
    audit_id: int
    occurred_at: datetime
    origin: Literal["live", "test", "eval", "canary"]
    clinic_id: uuid.UUID | None
    actor_id: uuid.UUID | None
    actor_role: str | None
    action: str
    resource_type: str | None
    resource_id: str | None
    details: dict[str, Any]


class AuditList(_Out):
    items: list[AuditRowOut]
    limit: int
    has_more: bool
    next_before_id: int | None  # pass as `before_id` for the next (older) page


_TOKEN_PATTERN = r"^[a-z0-9_:.\-]{1,64}$"


@staff_audit.get("", response_model=AuditList)
async def read_audit_log(
    conn: TxConn, claims: dict[str, Any] = Depends(verified_claims), action: Annotated[str | None, Query(pattern=_TOKEN_PATTERN)] = None,
    resource_type: Annotated[str | None, Query(pattern=_TOKEN_PATTERN)] = None, resource_id: Annotated[str | None, Query(max_length=64, pattern=r"^[A-Za-z0-9_:.\-]+$")] = None,
    actor_id: uuid.UUID | None = None, since: datetime | None = None, until: datetime | None = None, before_id: Annotated[int | None, Query(ge=1)] = None, limit: Limit = 100,
) -> AuditList:
    """Audit rows of the clinics you audit, newest first. 403 unless you are an auditor of some clinic."""
    await _staff_reader(conn, claims, ("auditor",))
    rows = (
        await conn.execute(
            text(
                "select id, occurred_at, origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details from audit.log "
                "where (cast(:a as text) is null or action = :a) and (cast(:rt as text) is null or resource_type = :rt) and (cast(:ri as text) is null or resource_id = :ri) "
                "and (cast(:u as uuid) is null or actor_id = :u) and (cast(:s as timestamptz) is null or occurred_at >= :s) and (cast(:e as timestamptz) is null or occurred_at < :e) "
                "and (cast(:b as bigint) is null or id < :b) order by id desc limit :n"
            ),
            {"a": action, "rt": resource_type, "ri": resource_id, "u": actor_id, "s": since, "e": until, "b": before_id, "n": limit + 1},
        )
    ).all()
    items = [
        AuditRowOut(
            audit_id=r.id, occurred_at=r.occurred_at, origin=r.origin, clinic_id=r.clinic_id, actor_id=r.actor_id, actor_role=r.actor_role, action=r.action,
            resource_type=r.resource_type, resource_id=r.resource_id, details=dict(r.details),
        )
        for r in rows[:limit]
    ]
    return AuditList(items=items, limit=limit, has_more=len(rows) > limit, next_before_id=items[-1].audit_id if len(rows) > limit and items else None)


# ---- patient view: label rows and the opened record (careflow/patient_view.py; docs/design/patient-view-fields.md) ----------------------------------------------
#
# DEMO PROFILE WITH SYNTHETIC DATA ONLY (503 anywhere else). A list row (`PatientLabelOut`) is the name, the YEAR of birth and the LAST FOUR digits of the phone, at most 25 rows per request, no total
# count, no export. OPENING one record decrypts it fully (name, date of birth, phone) for front desk, nurse and doctor of its clinic, is rate-limited per user (a required setting with no default)
# and writes an audit row holding who, role and the patient id, never the contents. Responses are `Cache-Control: no-store`.

VIEW_ROLES: Final = ("front_desk", "nurse", "doctor")


class LabelsIn(_Body):
    clinic_id: uuid.UUID
    patient_ids: list[uuid.UUID] = Field(min_length=1, max_length=patient_view.HARD_PAGE_CAP)


class LabelsOut(_Out):
    items: list[PatientLabelOut]  # only the records of that clinic you may see; an id you may not see is simply absent


class PatientViewOut(_Out):
    patient_id: uuid.UUID
    clinic_id: uuid.UUID
    name: str
    date_of_birth: str  # ISO date
    phone: str | None


async def _view_gate(conn: AsyncConnection, claims: dict[str, Any], clinic_id: uuid.UUID) -> str:
    """The clinic's origin after checking the caller is active front desk, nurse or doctor there: 404 if the clinic is invisible, 403 if visible but not one of those."""
    clinic = (await conn.execute(text("select origin from iam.clinics where id = :c"), {"c": clinic_id})).first()
    if clinic is None:
        raise HTTPException(status_code=404, detail="not found")
    if not await _holds_role(conn, claims, clinic_id, VIEW_ROLES):
        raise HTTPException(status_code=403, detail="your role may not do this")
    return clinic[0]


@contextmanager
def _view_errors() -> Iterator[None]:
    try:
        yield
    except patient_view.ViewDisabled:
        raise HTTPException(status_code=503, detail="patient view is disabled outside the demo profile with synthetic data") from None
    except patient_view.ViewNotConfigured as exc:
        raise HTTPException(status_code=503, detail=f"patient view is unavailable: {exc}") from None  # the exception's own fixed words: which setting is missing (open limit, list-row limit, or an invalid value)
    except patient_view.CannotShow:
        raise HTTPException(status_code=503, detail="this record cannot be shown") from None
    except patient_view.PatientNotFound:
        raise HTTPException(status_code=404, detail="not found") from None


def _vault_or_503(request: Request) -> DemographicVault:
    vault: DemographicVault | None = request.app.state.vault
    if vault is None:
        log.error("patient view is unavailable: no encryption keys are configured (deployment fault)")
        raise HTTPException(status_code=503, detail="patient view is unavailable: encryption keys are not configured")
    return vault


@staff_patients.post("/labels", response_model=LabelsOut)
async def patient_labels_route(
    body: LabelsIn, response: Response, conn: TxConn, request: Request, claims: dict[str, Any] = Depends(verified_claims),
    config: patient_view.PatientViewConfig = Depends(patient_view_config),
) -> LabelsOut:
    """List rows for up to 25 patients of one clinic: name, year of birth, last four digits of the phone. Read-only; one audit row with the COUNT only. 404 for a clinic you cannot see, 403 unless you
    are front desk, nurse or doctor there, 422 for more than the page limit, 429 over your per-minute row budget, 503 outside the demo profile with synthetic data or while the budget is not configured."""
    response.headers["Cache-Control"] = "no-store"
    with _view_errors():
        patient_view.require_ready(config, needs_label_limit=True)
        vault = _vault_or_503(request)
        origin = await _view_gate(conn, claims, body.clinic_id)
        if len(set(body.patient_ids)) != len(body.patient_ids) or len(body.patient_ids) > config.settings.page_limit:
            raise HTTPException(status_code=422, detail="too many or repeated patient ids for one page")
        if config.label_limiter is not None and not config.label_limiter.allow(uuid.UUID(claims["sub"]), len(body.patient_ids)):
            raise HTTPException(status_code=429, detail="too many list rows read; wait a moment")
        labels = await patient_view.patient_labels(conn, vault, body.clinic_id, body.patient_ids, page_limit=config.settings.page_limit)
    await write_audit(
        conn, clinic_id=body.clinic_id, origin=origin, actor_id=uuid.UUID(claims["sub"]), actor_role=await _acting_role(conn, claims, body.clinic_id, VIEW_ROLES), action="patient.view_labels",
        resource_type="clinic", resource_id=body.clinic_id, details={"count": len(labels)}, idempotency_key=f"audit:patient-view-labels:{uuid.uuid4()}",
    )
    return LabelsOut(items=[PatientLabelOut(patient_id=x.patient_id, name=x.name, birth_year=x.birth_year, phone_last4=x.phone_last4) for x in labels])


@staff_patients.get("/{patient_id}/view", response_model=PatientViewOut)
async def open_patient_record(
    patient_id: uuid.UUID, response: Response, conn: TxConn, request: Request, claims: dict[str, Any] = Depends(verified_claims),
    config: patient_view.PatientViewConfig = Depends(patient_view_config),
) -> PatientViewOut:
    """OPEN one record: full name, date of birth and phone, decrypted for front desk, nurse and doctor of its clinic, one audit row (who, role, patient id; never the contents). 404 if you cannot
    see it (the same as a missing or erased one), 403 unless you are front desk, nurse or doctor of its clinic, 429 over your per-minute limit, 503 outside the demo profile with synthetic data or
    while the limit is not configured."""
    response.headers["Cache-Control"] = "no-store"
    with _view_errors():
        patient_view.require_ready(config, needs_open_limit=True)
        vault = _vault_or_503(request)
        row = (await conn.execute(text("select clinic_id, origin from mpi.patients where id = :i and erased_at is null"), {"i": patient_id})).first()
        if row is None:
            raise HTTPException(status_code=404, detail="not found")
        origin = await _view_gate(conn, claims, row[0])
        actor = uuid.UUID(claims["sub"])
        if config.limiter is not None and not config.limiter.allow(actor):
            raise HTTPException(status_code=429, detail="too many records opened; wait a moment")
        record = await patient_view.open_patient(conn, vault, patient_id)
    await write_audit(
        conn, clinic_id=row[0], origin=origin, actor_id=actor, actor_role=await _acting_role(conn, claims, row[0], VIEW_ROLES), action="patient.view", resource_type="patient",
        resource_id=patient_id, details={"fields": "full"}, idempotency_key=f"audit:patient-view:{uuid.uuid4()}",
    )
    return PatientViewOut(patient_id=record.patient_id, clinic_id=record.clinic_id, name=record.name, date_of_birth=record.date_of_birth, phone=record.phone)


# ---- the referral queue: list and open (what a reviewer sees BEFORE deciding) --------------------------------------------------------------------------
#
# Reads only, under the caller's own row-level security. A role sees only what its policies show: front desk cannot read triage proposals (migration 0018), so for front desk `proposal_visible` is
# FALSE and the proposal fields are null, which means "you cannot see", never "there is none". `run_state` is the latest Front Door job for the document (every reader role can see the queue).
# `holds` are the open `referral_hold` tasks YOU may see (front desk sees its own; a nurse-assigned hold is invisible to front desk).

RunState = Literal["unclaimed", "claimed", "completed", "dead_lettered"]


class ReferralOut(_Out):
    document_id: uuid.UUID
    clinic_id: uuid.UUID
    content_type: UploadContentType
    size_bytes: int
    created_at: datetime
    registered_by: uuid.UUID
    run_state: RunState | None  # None: no run has been started
    open_holds: int  # open referral_hold tasks you may see
    proposal_visible: bool  # False: your role cannot read triage proposals, so the next two fields are unknown, not empty
    proposal_id: uuid.UUID | None
    decided: bool | None


class ReferralHoldOut(_Out):
    task_id: uuid.UUID
    assigned_role: Literal["nurse", "doctor", "front_desk"]
    status: Literal["open", "acknowledged", "resolved"]


class CardCandidateOut(_Out):
    """A protocol card the retrieval proposed: its id and version only."""

    card_id: str
    version: int


class ProposalOut(_Out):
    proposal_id: uuid.UUID
    patient_id: uuid.UUID
    abstained: bool
    mode: Literal["shadow", "assisted", "autonomous"]
    candidates: list[CardCandidateOut]
    review_task_id: uuid.UUID
    created_at: datetime


class ReferralDetailOut(ReferralOut):
    holds: list[ReferralHoldOut]
    proposal: ProposalOut | None  # None when there is none OR when proposal_visible is False


class ReferralList(_Out):
    items: list[ReferralOut]
    limit: int
    has_more: bool


REFERRAL_READERS: Final = ("front_desk", "nurse", "doctor")
_REFERRAL_SELECT = """
    select d.id as document_id, d.clinic_id, d.content_type, d.size_bytes, d.created_at, d.registered_by,
           (select q.state from ops.queue q where q.task_type = :tt and q.payload ->> 'document_id' = d.id::text order by q.created_at desc, q.id limit 1) as run_state,
           (select count(*) from agents.review_tasks t where t.kind = 'referral_hold' and t.source_message_id = 'document:' || d.id::text and t.status <> 'resolved') as open_holds,
           iam.has_clinic_role(d.clinic_id, array['nurse', 'doctor']) as proposal_visible,
           case when iam.has_clinic_role(d.clinic_id, array['nurse', 'doctor'])
                then (select p.id from agents.triage_proposals p where p.document_id = d.id and p.clinic_id = d.clinic_id) end as proposal_id,
           case when iam.has_clinic_role(d.clinic_id, array['nurse', 'doctor'])
                then exists (select 1 from agents.review_decisions rd join agents.triage_proposals p2 on p2.id = rd.proposal_id where p2.document_id = d.id and p2.clinic_id = d.clinic_id) end as decided
    from docs.documents d where d.kind = 'referral'
"""


async def _referral_reader(conn: AsyncConnection, claims: dict[str, Any]) -> None:
    if not await _any_role_held(conn, REFERRAL_READERS):
        raise HTTPException(status_code=403, detail="your role may not read this")


def _referral_fields(r: Any) -> dict[str, Any]:
    return {
        "document_id": r.document_id, "clinic_id": r.clinic_id, "content_type": r.content_type, "size_bytes": r.size_bytes, "created_at": r.created_at, "registered_by": r.registered_by,
        "run_state": r.run_state, "open_holds": r.open_holds, "proposal_visible": r.proposal_visible, "proposal_id": r.proposal_id, "decided": r.decided,
    }


@staff_referrals.get("", response_model=ReferralList)
async def list_referrals(conn: TxConn, claims: dict[str, Any] = Depends(verified_claims), limit: Limit = 100) -> ReferralList:
    """The referral documents you may see, newest first, with the state of their run. 403 unless you hold front desk, nurse or doctor somewhere."""
    await _referral_reader(conn, claims)
    rows = (await conn.execute(text(_REFERRAL_SELECT + " order by d.created_at desc, d.id limit :n"), {"tt": FRONT_DOOR_TASK, "n": limit + 1})).all()
    return ReferralList(items=[ReferralOut(**_referral_fields(r)) for r in rows[:limit]], limit=limit, has_more=len(rows) > limit)


@staff_referrals.get("/{document_id}", response_model=ReferralDetailOut)
async def open_referral(document_id: uuid.UUID, conn: TxConn, claims: dict[str, Any] = Depends(verified_claims)) -> ReferralDetailOut:
    """One referral with the holds you may see and, for a nurse or doctor, the triage proposal to review (card ids and versions only). 404 if you cannot see it, 403 unless you hold a reader role."""
    await _referral_reader(conn, claims)
    r = (await conn.execute(text(_REFERRAL_SELECT + " and d.id = :d"), {"tt": FRONT_DOOR_TASK, "d": document_id})).first()
    if r is None:
        raise HTTPException(status_code=404, detail="not found")
    holds = (
        await conn.execute(
            text(
                "select id, assigned_role, status from agents.review_tasks where kind = 'referral_hold' and source_message_id = :s order by created_at, id"
            ),
            {"s": f"document:{document_id}"},
        )
    ).all()
    proposal = None
    if r.proposal_visible:
        p = (
            await conn.execute(
                text("select id, patient_id, candidates, abstained, mode, review_task_id, created_at from agents.triage_proposals where document_id = :d and clinic_id = :c"),
                {"d": document_id, "c": r.clinic_id},
            )
        ).first()
        if p is not None:
            proposal = ProposalOut(
                proposal_id=p.id, patient_id=p.patient_id, abstained=p.abstained, mode=p.mode, review_task_id=p.review_task_id, created_at=p.created_at,
                candidates=[CardCandidateOut(card_id=str(c["card_id"]), version=int(c["version"])) for c in p.candidates],
            )
    return ReferralDetailOut(**_referral_fields(r), holds=[ReferralHoldOut(task_id=h.id, assigned_role=h.assigned_role, status=h.status) for h in holds], proposal=proposal)


def _intake_from_environment(supabase_url: str) -> tuple[DocumentIntake | None, Any]:
    """The real document intake over Supabase Storage, or (None, None) so the upload routes answer 503. Built ONLY when ALL hold: `CAREFLOW_STORAGE_BUCKET` is set, `CAREFLOW_STORAGE_VERIFIED` names the
    date the owner confirmed the real service with `scripts/storage_spike.py` (Appendix B: an external service's behaviour is confirmed by a real call before code depends on it), the profile is demo and the
    data policy synthetic_only (owner decision 2026-10-30: dev and CI projects, synthetic data only), and the project's secret key is present. Anything missing is a logged reason and a 503, never a guess."""
    bucket = (os.environ.get("CAREFLOW_STORAGE_BUCKET") or "").strip()
    if not bucket:
        return None, None
    if bucket != DOCUMENT_BUCKET:
        log.error("CAREFLOW_STORAGE_BUCKET must be %s: the storage policies protect that bucket only; uploads stay unavailable", DOCUMENT_BUCKET)
        return None, None
    verified = (os.environ.get("CAREFLOW_STORAGE_VERIFIED") or "").strip()
    try:
        recorded = date.fromisoformat(verified)
        if recorded > datetime.now(UTC).date():
            raise ValueError("a spike cannot have been recorded in the future")
    except ValueError:
        log.error("storage is configured but CAREFLOW_STORAGE_VERIFIED is not a date: uploads stay unavailable until the real-service spike is recorded")
        return None, None
    if not demo_phase3.demo_allowed(resolve_profile(), demo_phase3.data_policy(os.environ)):
        log.error("storage is configured outside profile=demo with data_policy=synthetic_only: uploads stay unavailable")
        return None, None
    key = (os.environ.get("SUPABASE_SECRET_KEY") or "").strip()
    if not key:
        log.error("storage is configured but SUPABASE_SECRET_KEY is not set: uploads stay unavailable")
        return None, None
    adapter = SupabaseUploadAdapter(supabase_url, key, bucket)
    return DocumentIntake(adapter, profile=resolve_profile()), adapter


def create_app(
    settings: Settings | None = None, *, jwks: JWKSClient | None = None, engine: AsyncEngine | None = None,
    vault: DemographicVault | None = None, clock: Callable[[], float] = time.time, front_door: FrontDoorComponents | None = None,
    front_door_session_for: Callable[..., Any] = worker_session, document_intake: DocumentIntake | None = None, reply_interpreter: DecisionService | None = None,
) -> FastAPI:
    """Build the app. `jwks`, `engine`, `vault` and `clock` (for the step-up window) can be injected (tests do); otherwise they come from
    settings / the environment. `front_door` holds the Front Door's stand-in components; with None the run and review routes answer 503 and no handler is registered."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        cfg = settings or Settings()  # type: ignore[call-arg]  # fields come from the environment
        if not cfg.require_aal2 and not demo_allowed(resolve_profile(), _data_policy(os.environ)):  # also catches a value that came from .env
            raise MfaSettingRefused("CAREFLOW_REQUIRE_AAL2 may be turned off only in profile=demo with data_policy=synthetic_only: refusing to start")
        app.state.issuer = cfg.issuer
        app.state.jwks = jwks or JWKSClient(cfg.jwks_url)
        app.state.engine = engine or make_app_engine(cfg.database_url_app, pool_size=5, max_overflow=5, pool_pre_ping=True)
        app.state.vault = vault if vault is not None else _load_vault()
        app.state.active_key_version = cfg.active_key_version
        app.state.step_up_max_age_seconds = cfg.step_up_max_age_seconds
        app.state.require_aal2 = cfg.require_aal2
        app.state.erase_reasons = frozenset(w.strip() for w in (cfg.erase_reasons or "").split(",") if w.strip()) or None
        app.state.clock = clock
        app.state.required_roles = cfg.required_roles
        app.state.worker_account_id = cfg.worker_account_id
        app.state.wired_adapters = frozenset()  # (provider, model_id) pairs with a wired adapter: none yet
        app.state.malware_scanner_configured = False  # nothing sets it: no scanner exists (BUILD-SPEC.md 11.5); clinical stays not ready
        components = front_door
        if components is None:  # the DEMO profile (synthetic data, switch on, keys loaded) wires FAKE components; any other profile leaves them unwired and the routes answer 503
            try:
                held = app.state.vault
                components = demo_front_door.components_from_environment(os.environ, resolve_profile(), vault=held, key_version=_choose_key_version(held, cfg.active_key_version))
            except Exception:  # noqa: BLE001 - any fault in the demo configuration leaves the components unwired (the routes answer 503), never a startup crash
                log.error("the demo Front Door components could not be built: the referral run and review routes stay unavailable")
                components = None
            if components is not None:
                log.warning("DEMO PROFILE: the Front Door runs on FAKE letters, extractor and retriever (synthetic data only)")
        app.state.front_door = components
        app.state.reply_interpreter = reply_interpreter  # None unless a decision service is injected (FAKES ONLY today): the route answers 503
        app.state.storage_adapter = None
        intake = document_intake
        if intake is None:
            intake, app.state.storage_adapter = _intake_from_environment(cfg.supabase_url)
        app.state.document_intake = intake  # None unless storage is configured AND verified (below): the upload routes answer 503
        app.state.poker = Poker()  # routes that enqueue call `app.state.poker.poke()` after their transaction commits (7.4)
        handlers = {FRONT_DOOR_TASK: front_door_handler(components, session_for=front_door_session_for)} if components is not None else {}
        sweeps = [release_expired_holds_sweep]  # tidy expired holds on every tick (migration 0033); correctness never depended on it
        if components is not None:
            sweeps.append(lambda conn: raise_dead_letter_tasks(conn, components.dead_letters))  # a dead-lettered run is never silent
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
            if app.state.storage_adapter is not None:
                await app.state.storage_adapter.aclose()
            if engine is None:
                await app.state.engine.dispose()
            if jwks is None:
                await app.state.jwks.aclose()

    app = FastAPI(title="CareFlow", lifespan=lifespan)
    # CORS is decided when the app is BUILT, so a misconfigured deployment refuses to start (careflow/cors.py). Unset means no origin is allowed.
    raw_origins = settings.cors_allowed_origins if settings is not None else (os.environ.get("CORS_ALLOWED_ORIGINS") or dotenv_values(".env").get("CORS_ALLOWED_ORIGINS"))
    origins = parse_origins(raw_origins, resolve_profile())
    if origins:
        app.add_middleware(CORSMiddleware, **middleware_kwargs(origins))
    mfa_off = (not settings.require_aal2) if settings is not None else (os.environ.get("CAREFLOW_REQUIRE_AAL2") or "").strip().lower() in {"0", "false", "no", "off"}
    if mfa_off and not demo_allowed(resolve_profile(), _data_policy(os.environ)):
        raise MfaSettingRefused("CAREFLOW_REQUIRE_AAL2 may be turned off only in profile=demo with data_policy=synthetic_only: refusing to start")
    refuse_demo_outside_demo(os.environ, resolve_profile())  # the demo Phase 3 placeholders may never be present outside profile=demo + data_policy=synthetic_only
    app.add_exception_handler(RequestValidationError, _validation_error_handler)  # type: ignore[arg-type]

    @app.middleware("http")
    async def no_store_for_patient_routes(request: Request, call_next):
        """Every response under /v1/staff/patients (errors and refusals included) is `Cache-Control: no-store`: these routes can carry a person's demographics."""
        response = await call_next(request)
        if request.url.path.startswith("/v1/staff/patients"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(router)
    app.include_router(scheduling)
    app.include_router(staff_messages)
    app.include_router(staff_patients)
    app.include_router(staff_merges)
    app.include_router(staff_referrals)
    app.include_router(staff_review_tasks)
    app.include_router(staff_documents)
    app.include_router(staff_identity)
    app.include_router(staff_audit)
    app.include_router(health)
    openapi_errors.install(app, [router, scheduling, staff_messages, staff_patients, staff_merges, staff_referrals, staff_review_tasks, staff_documents, staff_identity, staff_audit])  # describe the fixed error responses in the OpenAPI document (no behaviour change)
    return app


app = create_app()  # `uvicorn careflow.app:app`; settings are read at startup, not at import
