"""JWT verification against the Supabase project's asymmetric signing keys (BUILD-SPEC.md §18.1).

The backend verifies every token itself; nothing upstream does it for us because the API talks to
Postgres directly rather than through PostgREST. The rules, each enforced here and tested:

  * Only ES256 is accepted, and only that algorithm is ever passed to the verifier. The token's own
    `alg` header is checked against it first and never trusted to choose the algorithm, so `none` and
    shared-secret (HS256) downgrade tricks fail before any key is touched.
  * The key is chosen by the token header's `kid`, never "the first key in the list": the JWKS can hold a
    current and a previous key during a rotation window. A token with no `kid`, or an unknown one, is
    refused.
  * Required claims: `exp`, `sub` (a UUID), `iss` (this project's Auth URL), `aud` ("authenticated").
    The `role` claim must be "authenticated"; the project's anonymous key (role "anon") is refused.
  * Keys are cached (default 10 minutes, matching the endpoint's own `Cache-Control: max-age=600`). An
    unknown `kid` triggers one refresh, but refreshes are rate-limited so a stream of bogus kids cannot
    turn this service into a request cannon aimed at the Auth endpoint. If the endpoint is down, a key
    that is already cached is still honoured for a bounded time (default 1 hour); beyond that, or for a
    key never seen, verification fails closed.

The JWKS fetch sends no patient data (it is a public GET to the project's own Supabase URL). It is listed
in data-flow-register.yaml (§17.6).
"""

import asyncio
import time
import uuid
from collections.abc import Callable
from typing import Any, Final

import httpx
import jwt
from jwt import PyJWK

ALGORITHM: Final = "ES256"
AUDIENCE: Final = "authenticated"
REQUIRED_CLAIMS: Final = ["exp", "sub", "iss", "aud"]


class AuthError(Exception):
    """The token is not acceptable (401). The message is for logs, never for the client."""


class AuthUnavailable(Exception):
    """Verification could not be completed because the key source is unreachable or unusable (503)."""


class JWKSClient:
    def __init__(
        self,
        jwks_url: str,
        *,
        http: httpx.AsyncClient | None = None,
        ttl: float = 600.0,
        min_refetch: float = 30.0,
        max_stale: float = 3600.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = jwks_url
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(5.0))
        self._ttl, self._min_refetch, self._max_stale, self._clock = ttl, min_refetch, max_stale, clock
        self._keys: dict[str, PyJWK] = {}
        self._fetched_at: float | None = None
        self._attempted_at: float | None = None
        self._refresh_failed = False
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get_key(self, kid: str) -> Any:
        now = self._clock()
        if kid in self._keys and self._fresh(now):
            return self._keys[kid].key
        async with self._lock:
            now = self._clock()
            if kid in self._keys and self._fresh(now):  # another request refreshed while we waited
                return self._keys[kid].key
            stale_or_unknown = kid not in self._keys or not self._fresh(now)
            can_try = self._attempted_at is None or now - self._attempted_at >= self._min_refetch
            if stale_or_unknown and can_try:
                await self._refresh(now)
            now = self._clock()
            if kid in self._keys and (self._fresh(now) or self._within_stale_grace(now)):
                return self._keys[kid].key
        if self._refresh_failed:
            raise AuthUnavailable("signing keys could not be loaded and none are cached for this kid")
        raise AuthError("signing key not found for token kid")

    def _fresh(self, now: float) -> bool:
        return self._fetched_at is not None and now - self._fetched_at < self._ttl

    def _within_stale_grace(self, now: float) -> bool:
        return self._fetched_at is not None and now - self._fetched_at < self._max_stale

    async def _refresh(self, now: float) -> None:
        self._attempted_at = now
        self._refresh_failed = True
        try:
            response = await self._http.get(self._url)
            response.raise_for_status()
            document = response.json()
            raw_keys = document["keys"]
            if not isinstance(raw_keys, list):
                raise TypeError("'keys' is not a list")
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            # Keep whatever is cached; get_key decides whether it may still be used.
            return
        keys: dict[str, PyJWK] = {}
        for raw in raw_keys:
            try:
                if raw.get("kty") != "EC" or raw.get("crv") != "P-256" or raw.get("use", "sig") != "sig":
                    continue
                if raw.get("alg", ALGORITHM) != ALGORITHM or not isinstance(raw.get("kid"), str):
                    continue
                keys[raw["kid"]] = PyJWK(raw, ALGORITHM)
            except (jwt.PyJWTError, AttributeError, TypeError, ValueError):
                continue
        if not keys:
            return  # an empty or unusable document must not wipe a working cache
        self._keys, self._fetched_at = keys, self._clock()
        self._refresh_failed = False


async def verify_token(token: str, keys: JWKSClient, *, issuer: str, leeway: float = 10.0) -> dict[str, Any]:
    """Return the verified claims, or raise AuthError (bad token) / AuthUnavailable (cannot verify now)."""
    if not isinstance(token, str) or not token:
        raise AuthError("missing token")
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        raise AuthError("malformed token") from None
    if header.get("alg") != ALGORITHM:
        raise AuthError("token algorithm is not ES256")
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise AuthError("token has no kid")
    key = await keys.get_key(kid)
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            issuer=issuer,
            leeway=leeway,
            options={"require": REQUIRED_CLAIMS},
        )
    except jwt.PyJWTError as exc:
        raise AuthError(f"token rejected: {type(exc).__name__}") from None
    try:
        uuid.UUID(str(claims["sub"]))
    except ValueError:
        raise AuthError("sub is not a UUID") from None
    if claims.get("role") != "authenticated":
        raise AuthError("token role is not 'authenticated'")
    return claims


# ---- step-up re-authentication (BUILD-SPEC.md §18.1) ---------------------------------------------------------------------------------
#
# Sensitive actions (merge, ...) need a SECOND factor verified RECENTLY, even inside an active session. Supabase Auth records a verified
# TOTP factor in the token as `aal: "aal2"` plus an `amr` entry stamped with the verification time, and a refresh keeps that stamp.
#
# APPENDIX B, UNVERIFIED: the shape below (`amr` = list of {"method": str, "timestamp": int-seconds}) comes from documentation, not from a
# real token. It must be confirmed against a real dev staff token before any non-test code depends on it (docs/design/step-up-spike.md).
# `_totp_verified_at` is the ONE place that knows the shape, so a correction is a one-place change. Anything else fails closed.

STEP_UP_METHOD: Final = "totp"
STEP_UP_AAL: Final = "aal2"


def _totp_verified_at(claims: dict[str, Any]) -> int | str:
    """The newest TOTP verification time (unix seconds) in the claims, or a failure reason code (a str) if the shape is not exactly the
    documented one. Reason codes name the problem, never a claim value."""
    amr = claims.get("amr")
    if not isinstance(amr, list) or not amr:
        return "amr_missing"
    times: list[int] = []
    for entry in amr:
        if not isinstance(entry, dict) or set(entry) != {"method", "timestamp"}:
            return "amr_malformed"
        method, stamp = entry["method"], entry["timestamp"]
        if not isinstance(method, str) or isinstance(stamp, bool) or not isinstance(stamp, int):
            return "amr_malformed"
        if method == STEP_UP_METHOD:
            times.append(stamp)
    return max(times) if times else "no_totp_entry"


def step_up_failure(claims: dict[str, Any], *, max_age_seconds: int, now: float) -> str | None:
    """None if the claims show a second factor verified within `max_age_seconds`; otherwise a reason code (for the log, never the client).
    Pure: the clock is a parameter. A future timestamp is refused (no skew allowance). Missing or malformed anything is NOT stepped up."""
    if claims.get("aal") != STEP_UP_AAL:
        return "aal_not_aal2"
    verified_at = _totp_verified_at(claims)
    if isinstance(verified_at, str):
        return verified_at
    if verified_at > now:
        return "timestamp_future"
    if now - verified_at > max_age_seconds:
        return "timestamp_expired"
    return None
