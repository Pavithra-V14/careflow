"""Per-request timing in the access log, DEMO profile only (owner decision 2026-11-13).

One line per request: method, ROUTE PATTERN (`/v1/staff/scheduling/backfills/{slot_id}/decide`, never the raw path), status and milliseconds. Nothing else is read or written: no query string, no
header, no body, no id, no token. The pattern comes from the matched route; a request that matched no route is `<unmatched>` (the raw path of a probe is never logged). A request that raises before a
response is logged with status 500 and the exception is re-raised unchanged. Pure ASGI, so it does not buffer or alter a response.
"""

import logging
import sys
import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

LOGGER_NAME = "careflow.request_timing"
UNMATCHED = "<unmatched>"


def timing_logger() -> logging.Logger:
    """Its own logger with its own stderr handler, so the line shows next to uvicorn's access line whatever the root logging level is."""
    log = logging.getLogger(LOGGER_NAME)
    if not log.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(levelname)s:     %(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        log.propagate = False
    return log


def format_line(method: str, pattern: str, status: int, millis: float) -> str:
    return f"timing {method} {pattern} {status} {millis:.0f}ms"


class RequestTimingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            route = scope.get("route")
            pattern = getattr(route, "path", None) or UNMATCHED
            timing_logger().info(format_line(str(scope.get("method", "?")), pattern, status, (time.perf_counter() - started) * 1000))
