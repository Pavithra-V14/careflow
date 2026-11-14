"""The one place that talks HTTP to a hosted model provider (Gemini, OpenRouter, Sarvam). DEMO PROFILE, SYNTHETIC DATA ONLY (docs/design/hosted-providers.md; data-flow-register.yaml).

Rules, the same as `careflow/adapters/storage.py`:
  * an EXPLICIT timeout on every request (connect 5 s; read and write the row's own `timeout_seconds`; pool 5 s), and redirects are never followed (a key header must not travel to another host);
  * the host is fixed per provider in code, never taken from a response or from configuration;
  * a response is read in pieces and stops at a cap, so a hostile or broken answer cannot fill memory;
  * every failure is a FIXED-WORDS `TransientError` (a timeout, a connection fault, 408/429/5xx: retry-worthy) or `PermanentError` (anything else): the message never holds a URL, a key, a header, a
    request body or a response body. This module logs nothing, and keeps the transport's own loggers quiet (httpx logs request URLs at INFO).
"""

import asyncio
import json
import logging
from typing import Any, Final

import httpx

from careflow.ports import PermanentError, TransientError

MAX_RESPONSE_BYTES: Final = 2 * 1024 * 1024
_TRANSIENT_STATUS: Final = frozenset({408, 425, 429, 500, 502, 503, 504})

for _noisy in ("httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


def timeout_for(seconds: float) -> httpx.Timeout:
    if not isinstance(seconds, int | float) or isinstance(seconds, bool) or seconds <= 0:
        raise ValueError("a provider timeout is a positive number of seconds")
    return httpx.Timeout(connect=5.0, read=float(seconds), write=float(seconds), pool=5.0)


def new_client(seconds: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout_for(seconds), follow_redirects=False, trust_env=False)  # trust_env off: a proxy or CA bundle in the host environment must not see a key or a text


def refuse_redirecting_client(http: httpx.AsyncClient) -> None:
    if http.follow_redirects:
        raise ValueError("a provider client must not follow redirects (the credential header would travel to another host)")


DETAIL_MAX: Final = 200


def _with_detail(exc: Exception, chunks: list[bytes]) -> Exception:
    """Attaches the provider's OWN error fields (`error.status` and `error.message` of a non-200 answer) as the attribute `provider_detail`, truncated, for the owner's live check ONLY. It is NOT in str(exc) or
    repr(exc), so nothing that logs or stores an exception message ever carries it; the app never reads it. It describes the failed request (a dummy one in the live check), never a stored patient's data."""
    exc.provider_detail = _error_fields(b"".join(chunks))  # type: ignore[attr-defined]
    return exc


def _error_fields(raw: bytes) -> str:
    try:
        parsed = json.loads(raw)
    except ValueError:
        return ""
    error = parsed.get("error") if isinstance(parsed, dict) else None
    if not isinstance(error, dict):
        return ""
    parts = [f"{name}={error[name]}" for name in ("status", "code", "message") if isinstance(error.get(name), str | int) and not isinstance(error.get(name), bool)]
    return " ".join(" ".join(parts).split())[:DETAIL_MAX]


async def post_json(http: httpx.AsyncClient, url: str, *, headers: dict[str, str], body: dict[str, Any], seconds: float) -> dict[str, Any]:
    """POST `body` as JSON and return the JSON object answered. Fixed-words errors only."""
    try:
        async with asyncio.timeout(seconds):  # a WALL-CLOCK limit for the whole exchange (httpx's read timeout is per chunk, so a slow drip could outlast it)
            async with http.stream("POST", url, headers={**headers, "content-type": "application/json"}, content=json.dumps(body).encode("utf-8"), timeout=timeout_for(seconds)) as response:
                status = response.status_code
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise PermanentError("the provider's answer is larger than the allowed size")
                    chunks.append(chunk)
    except (httpx.TimeoutException, TimeoutError):
        raise TransientError("the provider did not answer in time") from None
    except httpx.TransportError:
        raise TransientError("the provider could not be reached") from None
    except (httpx.HTTPError, UnicodeError):  # a decoding or stream fault, or a header that cannot be sent: a failed route, never a raw exception out of the gateway
        raise PermanentError("the exchange with the provider failed") from None
    if status in _TRANSIENT_STATUS:
        raise _with_detail(TransientError(f"the provider answered with a retry-worthy status ({status})"), chunks)
    if status in (401, 403):
        raise _with_detail(PermanentError("the provider refused the credential"), chunks)
    if status != 200:
        raise _with_detail(PermanentError(f"the provider refused the request (status {status})"), chunks)
    try:
        parsed = json.loads(b"".join(chunks))
    except ValueError:
        raise PermanentError("the provider's answer is not JSON") from None
    if not isinstance(parsed, dict):
        raise PermanentError("the provider's answer is not a JSON object")
    return parsed
