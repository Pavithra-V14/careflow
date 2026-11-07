"""The Auth admin port: creating the SIGN-IN for an invited staff member (docs/design/staff-account-management.md).

The database records the account and its grant (migration 0034). The sign-in itself lives in Supabase Auth and is created by its admin API, which needs the SECRET key. That key must not sit in the
web process by default, so it is behind this port and nothing wires a real adapter unless the owner asks:

  * `FakeAuthAdmin` (tests and the demo): an in-memory directory; the same e-mail always maps to the same account id, so a retried invitation is idempotent. Nothing leaves the process.
  * `SupabaseAuthAdmin`: the REAL adapter, written against Supabase's documented invite endpoint and NOT verified by a real call (Appendix B; CLAUDE.md verification discipline). It refuses to run unless
    `CAREFLOW_AUTH_ADMIN_VERIFIED` is a date that is not in the future (the day someone confirmed the call against the dev project) AND a secret key is configured. It is not referenced by the
    application's start-up: wiring it is an explicit later step.

The e-mail address is personal data: it goes to Auth (an existing, approved service) and nowhere else. It is never stored in our tables, never logged, never in an audit row, and never returned.
"""

import datetime as dt
import uuid
from collections.abc import Mapping
from typing import Final, Protocol

import httpx

NAMESPACE: Final = uuid.UUID("5f0c8a1e-3b7d-4c52-9a4e-2d6b1f8e7c90")  # fixed: the fake derives an account id from the address


class AuthAdminError(Exception):
    """Fixed words only; never an address, a key or a response body."""


class AuthAdminPort(Protocol):
    async def invite(self, email: str) -> uuid.UUID:
        """Create (or find) the sign-in for this address and send the invitation. Returns the account id Auth gave it. Idempotent for the same address."""
        ...

    async def reset_factors(self, account_id: uuid.UUID) -> None:
        """Remove the account's enrolled second factors so the person can enrol again (staff recovery, migration 0060). Idempotent."""
        ...


class FakeAuthAdmin:
    def __init__(self) -> None:
        self.invited: dict[str, uuid.UUID] = {}
        self.reset: set[uuid.UUID] = set()
        self.calls = 0

    async def invite(self, email: str) -> uuid.UUID:
        self.calls += 1
        normalised = email.strip().lower()
        if "@" not in normalised or len(normalised) > 254:
            raise AuthAdminError("the address is not valid")
        return self.invited.setdefault(normalised, uuid.uuid5(NAMESPACE, normalised))

    async def reset_factors(self, account_id: uuid.UUID) -> None:
        self.calls += 1
        self.reset.add(account_id)


class SupabaseAuthAdmin:
    """UNVERIFIED against a real call. Refuses to construct unless the verified date and the secret key are configured."""

    def __init__(self, supabase_url: str, secret_key: str, *, verified_on: str | None, today: dt.date | None = None, http: httpx.AsyncClient | None = None) -> None:
        today = today or dt.datetime.now(dt.UTC).date()
        try:
            verified = dt.date.fromisoformat(verified_on or "")
        except ValueError:
            raise AuthAdminError("the Auth admin call has not been verified (CAREFLOW_AUTH_ADMIN_VERIFIED)") from None
        if verified > today:
            raise AuthAdminError("the verified date is in the future")
        if not supabase_url.startswith("https://") or not secret_key:
            raise AuthAdminError("the Auth admin adapter needs the project URL and the secret key")
        self._url, self._key, self._http = supabase_url.rstrip("/"), secret_key, http or httpx.AsyncClient(timeout=15.0)

    async def invite(self, email: str) -> uuid.UUID:
        try:
            response = await self._http.post(
                f"{self._url}/auth/v1/invite", json={"email": email.strip()}, headers={"apikey": self._key, "Authorization": f"Bearer {self._key}"}
            )
            body = response.json()
            return uuid.UUID(str(body["id"]))
        except (httpx.HTTPError, KeyError, ValueError, TypeError):
            raise AuthAdminError("the invitation could not be created") from None  # nothing from the exchange is kept or logged


    async def reset_factors(self, account_id: uuid.UUID) -> None:
        raise AuthAdminError("the factor reset call has not been verified against a real project")  # no HTTP is written for an unverified endpoint (Appendix B; CLAUDE.md verification discipline)


def from_environment(environ: Mapping[str, str], supabase_url: str) -> AuthAdminPort | None:
    """None unless BOTH the verified date and the secret key are set; a wrong combination raises AuthAdminError. Not called by the application's start-up."""
    key, verified = environ.get("SUPABASE_SECRET_KEY"), environ.get("CAREFLOW_AUTH_ADMIN_VERIFIED")
    if not key and not verified:
        return None
    return SupabaseAuthAdmin(supabase_url, key or "", verified_on=verified)


__all__ = ["AuthAdminError", "AuthAdminPort", "FakeAuthAdmin", "SupabaseAuthAdmin", "from_environment"]
