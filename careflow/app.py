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
  3. The route runs inside that transaction; it commits when the route returns and rolls back on error.

Row-level security, not application code, enforces clinic isolation. Routes must therefore never open
their own connection or run SQL outside the `db_conn` transaction.

Every route added to this application must add its own case to the role-matrix test
(`tests/test_api_role_matrix.py`) in the same change; `test_every_route_is_in_the_matrix` fails if one is
missing.
"""

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from careflow.auth import AuthError, AuthUnavailable, JWKSClient, verify_token
from careflow.db import make_app_engine, set_request_claims

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
async def me(claims: dict[str, Any] = Depends(verified_claims), conn: AsyncConnection = Depends(db_conn)) -> Me:
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


# ---- application -----------------------------------------------------------------------------------


def create_app(
    settings: Settings | None = None, *, jwks: JWKSClient | None = None, engine: AsyncEngine | None = None
) -> FastAPI:
    """Build the app. `jwks` and `engine` can be injected (tests do); otherwise they come from settings."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        cfg = settings or Settings()  # type: ignore[call-arg]  # fields come from the environment
        app.state.issuer = cfg.issuer
        app.state.jwks = jwks or JWKSClient(cfg.jwks_url)
        app.state.engine = engine or make_app_engine(cfg.database_url_app, pool_size=5, max_overflow=5, pool_pre_ping=True)
        try:
            yield
        finally:
            if engine is None:
                await app.state.engine.dispose()
            if jwks is None:
                await app.state.jwks.aclose()

    app = FastAPI(title="CareFlow", lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()  # `uvicorn careflow.app:app`; settings are read at startup, not at import
