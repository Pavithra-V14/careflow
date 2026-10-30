"""The pieces every in-process MCP server of this project is built from (BUILD-SPEC.md 8; docs/design/phase3-scheduling-agent.md section 4; docs/design/phase3-mcp-spike.md, the
conditions of the GO, owner-approved 2026-10-27/28). IN-PROCESS ONLY: servers are called through the SDK's in-memory transport (`mcp.client.Client(server)`); nothing here opens a
socket, a listener, a thread or a subprocess (the spike's tripwire test proves it for the SDK; `tests/test_mcp_kit.py` proves it for this kit).

* `async_tool`: registers a tool ONLY if it is `async def` (a sync tool would run in a worker thread, taking database work off the request's task).
* `session_context`: the ONE writer of the caller's session. The tool reads the signed-in person's verified claims and THE per-request transaction from a contextvar; with no session
  it fails closed (no worker identity, no default). The SDK never sees the connection; the server object holds no engine.
* `Envelope`: the four statuses of spec 8 (`ok | empty | degraded | unavailable`), never a bare empty collection. A caller branches on `status`.
* `WithholdSdkDetail`: a logging filter for the SDK's records (a tool's exception text and traceback never reach a log; attach it to HANDLERS).
* `audited`: the audit wrapper the `clinical` read-only server needs (none is mounted in the first cut): who, role, tool, outcome; never arguments or results.
"""

import contextvars
import functools
import inspect
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncConnection

from careflow.audit import write_audit

Status = Literal["ok", "empty", "degraded", "unavailable"]
SDK_LOGGER_PREFIX: Final = "mcp"
FIXED_LOG_MESSAGE: Final = "mcp tool error (details withheld)"


class NoSession(Exception):
    """A tool was called outside `session_context`: there is no signed-in person. Fixed words."""


class NotAsyncTool(TypeError):
    """A sync function was offered as a tool."""


# ---- the caller's session ----------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Session:
    """What a tool may use: the signed-in account, the clinic roles it holds (from the verified claims' account, not from arguments), and the request's transaction."""

    account_id: uuid.UUID
    conn: AsyncConnection
    origin: str


_SESSION: contextvars.ContextVar[Session | None] = contextvars.ContextVar("careflow_mcp_session", default=None)


@asynccontextmanager
async def session_context(account_id: uuid.UUID, conn: AsyncConnection, origin: str) -> AsyncIterator[Session]:
    """Set the caller's session for the duration of the calls made inside it; restored on exit, also on error."""
    token = _SESSION.set(Session(account_id, conn, origin))
    try:
        yield _SESSION.get()  # type: ignore[misc]
    finally:
        _SESSION.reset(token)


def current_session() -> Session:
    session = _SESSION.get()
    if session is None:
        raise NoSession("no signed-in session")
    return session


# ---- registration ------------------------------------------------------------------------------------------------------------------------


def async_tool(server: Any) -> Callable[[Callable[..., Awaitable[Any]]], Any]:
    """Decorator: register `fn` as a tool on `server` only if it is `async def`."""

    def decorate(fn: Callable[..., Awaitable[Any]]) -> Any:
        if not inspect.iscoroutinefunction(fn):
            raise NotAsyncTool(f"tool {getattr(fn, '__name__', fn)!r} must be async def")
        return server.tool()(fn)

    return decorate


# ---- results ----------------------------------------------------------------------------------------------------------------------------


class Envelope(BaseModel):
    """The result of every tool. `data` holds ids, numbers and closed words only; a degraded or unavailable result says so in `status` and `reason` (a fixed word)."""

    model_config = ConfigDict(extra="forbid")
    status: Status
    reason: str | None = None
    data: dict[str, Any] | list[Any] | None = None

    @classmethod
    def ok(cls, data: dict[str, Any] | list[Any]) -> "Envelope":
        return cls(status="ok", data=data)

    @classmethod
    def empty(cls, reason: str) -> "Envelope":
        return cls(status="empty", reason=reason, data=[])

    @classmethod
    def unavailable(cls, reason: str) -> "Envelope":
        return cls(status="unavailable", reason=reason)

    @classmethod
    def degraded(cls, reason: str, data: dict[str, Any] | list[Any] | None = None) -> "Envelope":
        return cls(status="degraded", reason=reason, data=data)


# ---- logging ----------------------------------------------------------------------------------------------------------------------------


class WithholdSdkDetail(logging.Filter):
    """Attach to every HANDLER (a logger's filter does not see its children's records). For records from the SDK it removes the traceback and the exception text and replaces the message
    with a fixed one, so no handler can write what a tool's exception said."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == SDK_LOGGER_PREFIX or record.name.startswith(SDK_LOGGER_PREFIX + "."):
            record.msg, record.args = FIXED_LOG_MESSAGE, ()
            record.exc_info = record.exc_text = record.stack_info = None
        return True


def install_log_filter() -> None:
    """Attach the filter to every handler of the root logger (call once at startup, after logging is configured)."""
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, WithholdSdkDetail) for f in handler.filters):
            handler.addFilter(WithholdSdkDetail())


# ---- the audit wrapper for the clinical read-only server ---------------------------------------------------------------------------------------


def audited(tool_name: str, role: str, clinic_of: Callable[[Session], uuid.UUID]) -> Callable[[Callable[..., Awaitable[Envelope]]], Callable[..., Awaitable[Envelope]]]:
    """Wrap a tool so each call writes one audit row (who, role, tool, outcome) from the CALLER's session in the same transaction. Arguments and results are never recorded. If the audit
    insert fails the call fails (no unaudited read of clinical data)."""

    def decorate(fn: Callable[..., Awaitable[Envelope]]) -> Callable[..., Awaitable[Envelope]]:
        @functools.wraps(fn)  # the SDK builds the tool's argument schema from the signature, which functools.wraps exposes
        async def wrapper(*args: Any, **kwargs: Any) -> Envelope:
            session = current_session()
            outcome = "refused"
            try:
                result = await fn(*args, **kwargs)
                outcome = result.status
                return result
            finally:
                await write_audit(
                    session.conn, clinic_id=clinic_of(session), origin=session.origin, actor_id=session.account_id, actor_role=role, action="clinical.tool_call",
                    resource_type="mcp-tool", resource_id=uuid.uuid5(uuid.NAMESPACE_URL, f"careflow-mcp-tool:{tool_name}"),
                    details={"tool": tool_name, "outcome": outcome}, idempotency_key=f"audit:clinical-tool:{uuid.uuid4()}",
                )

        return wrapper

    return decorate


# ---- calling a server in process ----------------------------------------------------------------------------------------------------------


async def invoke(server: Any, tool: str, arguments: dict[str, Any], *, account_id: uuid.UUID, conn: AsyncConnection, origin: str) -> Envelope:
    """Call one tool of an in-process server as `account_id`, inside the request's transaction. A tool that raises (the SDK reports an error result with a generic message) becomes
    `unavailable` with the fixed reason `tool_error`: the caller always gets an Envelope and never an exception's text."""
    from mcp.client import (
        Client,  # imported here so importing the kit never imports the client machinery
    )

    async with session_context(account_id, conn, origin), Client(server) as client:
        result = await client.call_tool(tool, arguments)
    if result.is_error:
        return Envelope.unavailable("tool_error")
    payload = result.structured_content
    if payload is None:
        return Envelope.unavailable("tool_error")
    return Envelope.model_validate(payload)
