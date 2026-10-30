"""Browser origins allowed to call the API (CORS), read from `CORS_ALLOWED_ORIGINS` (a comma-separated list; .env.example). Fail-closed and exact.

Rules (owner decision 2026-10-28):
  * EXACT origins only: `scheme://host[:port]`, no path, no trailing slash, no userinfo, no wildcard inside an entry (`https://*.example.com` is refused).
  * The bare wildcard `*` is refused unless the profile is `demo` (an unset or unrecognised profile is `clinical`, which does not allow it). Even then credentials stay off.
  * Plain `http://` is accepted only for localhost / 127.0.0.1 (development) or in the demo profile; anything else must be `https://`.
  * Unset or empty means NO origin is allowed (no CORS headers are sent): unknown is not permissive.
  * Credentials (cookies) are OFF: the browser client sends a bearer token in the Authorization header, which needs no credentialed CORS. Methods and headers are the minimum the API uses.

`CorsConfigError` is raised while the app is being built, so a misconfigured deployment REFUSES TO START instead of serving with a permissive policy. The message names the rule and the
offending entry's shape, never a secret.
"""

import re
from collections.abc import Sequence

ORIGIN_RE = re.compile(r"^(https?)://([A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?)(?::([0-9]{1,5}))?$")
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1"})
ALLOW_METHODS = ("GET", "POST", "OPTIONS")
ALLOW_HEADERS = ("Authorization", "Content-Type", "X-Idempotency-Key")
# Response headers a browser script may read: a 401 carries WWW-Authenticate (the step-up and token-expiry signals), which the browser hides from cross-origin code unless it is exposed.
EXPOSE_HEADERS = ("WWW-Authenticate",)
MAX_AGE_SECONDS = 600


class CorsConfigError(Exception):
    """The configured origins break a rule; the application must not start."""


def parse_origins(raw: str | None, profile: str) -> list[str]:
    """The list of allowed origins (possibly empty), or CorsConfigError. `profile` is 'demo' or 'clinical' (careflow.rules.resolve_profile)."""
    if raw is None or not raw.strip():
        return []
    origins: list[str] = []
    for entry in (part.strip() for part in raw.split(",")):
        if not entry:
            continue
        if entry == "*":
            if profile != "demo":
                raise CorsConfigError("a wildcard CORS origin is refused outside the demo profile")
            origins.append(entry)
            continue
        if "*" in entry:
            raise CorsConfigError("a wildcard inside a CORS origin is refused: list exact origins")
        match = ORIGIN_RE.match(entry)
        if match is None:
            raise CorsConfigError("a CORS origin must be exactly scheme://host[:port], with no path, trailing slash or credentials")
        scheme, host, port = match.groups()
        if port is not None and not 1 <= int(port) <= 65535:
            raise CorsConfigError("a CORS origin has an invalid port")
        if scheme == "http" and host.lower() not in LOCAL_HOSTS and profile != "demo":
            raise CorsConfigError("a non-local CORS origin must use https outside the demo profile")
        origins.append(f"{scheme}://{host.lower()}" + (f":{port}" if port else ""))
    return list(dict.fromkeys(origins))


def middleware_kwargs(origins: Sequence[str]) -> dict:
    return {
        "allow_origins": list(origins),
        "allow_credentials": False,
        "allow_methods": list(ALLOW_METHODS),
        "allow_headers": list(ALLOW_HEADERS),
        "expose_headers": list(EXPOSE_HEADERS),
        "max_age": MAX_AGE_SECONDS,
    }
