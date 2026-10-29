# Phase 3 — `mcp` SDK spike (branch `phase3-mcp-spike`, never merged)

Scope (owner, 2026-10-27): pinned, in-process only, no external service, no network transport. No production code, no migration. The spike code is `spikes/mcp_probe.py` and `tests/test_spike_mcp_inprocess.py` on this branch only; the dependency is in THIS branch's `pyproject.toml`/`uv.lock` and in no other branch.

## Decision: **GO, accepted by the owner on 2026-10-27**, pin `mcp==2.3.0` (v1.30.0 stays as the documented fallback)

Go, provided the fit questions in section 5 are answered (none needs a grant). Nothing in the spike contradicted a rule. The conditions that matter most: tools must be `async` (a sync tool starts a worker thread), SDK logging must never print exception text, and the caller's session travels in an explicit contextvar that the code, not the SDK, owns.

## Conditions of the GO (owner, 2026-10-27)

This branch stays LOCAL and unmerged; the main lockfile is not touched until Phase 3 code starts.

1. **Only `async def` tools are registered.** A registration helper refuses a sync function, with a test that fails if one is registered (proved in the spike: `spikes/mcp_guards.py::async_tool`, `test_a_sync_tool_cannot_be_registered_and_an_async_one_can`).
2. **Fixed-word exceptions only in production code** (no patient text, ids or SQL in any exception message), PLUS a logging filter on the SDK's records that withholds the traceback and the exception text. Proved feasible in the spike: `WithholdSdkDetail` on the handler, a planted sentinel never reaches a rendered log record, and a negative control without the filter DOES find the sentinel in the log (so the test can fail). The filter must be attached to handlers, not to the `mcp` logger (a logger's filter does not see its children's records).
3. **Every tool call on the `clinical` server writes an audit record**: who (account id), role, tool name, outcome (`ok/empty/degraded/unavailable/refused`); never the arguments or results (they could hold patient text). The SDK has no per-call hook, so this is a wrapper WE own around tool registration (like `async_tool`), not an SDK feature. Where it is written, and under whose session, is an open question until the `clinical` server itself is decided (Phase 3 design, item 1).
4. **`pywin32` must not matter on Linux CI.** Confirmed: in `uv.lock` the only reference is `{ name = "pywin32", marker = "sys_platform == 'win32'" }` (under `mcp`). A fresh resolution for Linux, `echo mcp==2.3.0 | uv pip compile - --python-platform linux --python-version 3.12`, contains no `pywin32` (it lists `mcp`, `mcp-types`, `jsonschema`, `jsonschema-specifications`, `referencing`, `rpds-py`, `attrs`, `python-multipart`, `sse-starlette` and packages we already have); the same command for Windows adds `pywin32==312`. Note: `mcp` also requires `httpx2`/`httpcore2`/`truststore`; they are ALREADY in our lockfile (pulled by another package), so they are not new.

## 1. Version: v2.3.0 over v1.30.0 (one paragraph)

Both lines were installed in scratch environments and both ran the same in-process probe (v1 via `create_connected_server_and_client_session`, v2 via `Client(server)`); in both, a contextvar set by the caller reaches the tool handler and a tool exception reaches the client as an error. v2.3.0 is chosen because its documented in-process path is the one-line `Client(server)` we want, it brings the same number of new packages (10 each; v2 swaps `httpx-sse` for `mcp-types`) and no extra HTTP-client library in our environment, its client-visible tool error is the generic `Error executing tool <name>` (v1.30 puts the exception's own message in the client text, which would carry whatever our handler wrote into it), and it is the line that will receive fixes. The cost is that v2 is the newer major (first released recently), so it is pinned exactly (`==`), upgraded only deliberately, and covered by the tests below. If the owner prefers the older, more settled line, v1.30.0 works for everything here; the choice is reversible behind our own thin wrapper.

## 2. What it adds

**New packages vs the current lockfile (10):** `mcp 2.3.0`, `mcp-types 2.3.0`, `jsonschema 4.26.0`, `jsonschema-specifications 2025.9.1`, `referencing 0.37.0`, `rpds-py 2026.9.1`, `attrs 26.1.0`, `python-multipart 0.0.32`, `sse-starlette 3.6.0`, `pywin32 312` (**Windows only**, marker `sys_platform == "win32"`). Already present and reused: `anyio`, `httpx`, `pydantic`, `pyjwt`, `starlette`, `uvicorn`, `typing-extensions`, `typing-inspection`, `opentelemetry-api`, `cryptography`.

**Install size on disk (this Windows machine, `site-packages`):** about 18.5 MB for the ten packages, of which about 13 MB is `pywin32`. Without `pywin32` (Linux CI and production) it is about **5 MB** (mcp 2.8 MB, mcp-types 0.6, jsonschema 0.7 + specs 0.1 + referencing 0.2 + rpds 0.55, sse-starlette 0.07, python-multipart 0.18, attrs 0.04). A clean scratch install of v2.3.0 was 43 MB for the whole venv including pydantic; v1.30.0 was 42 MB.

**Network behaviour of the new packages by default:** none opens a listener or makes a call at import or at in-memory use. Measured: importing `mcp`, `mcp.client` and `mcp.server.mcpserver` in a fresh interpreter creates 0 sockets and 0 threads. `sse-starlette`, `python-multipart` and `uvicorn` are used only by the SSE/HTTP transports, which we never construct. `grep` of the SDK source finds no analytics, telemetry-upload, update-check or crash-reporting code; the only "telemetry" is spans through `opentelemetry-api` (an API package with no exporter installed, so spans go nowhere). `pywin32` is a Windows COM/Win32 binding that is pulled in by a dependency marker and not used by our code.

## 3. What the spike proves (all six tests pass; run `uv run pytest tests/test_spike_mcp_inprocess.py`)

- A fake server with one read-only tool (`whoami`) and one write tool (`hold_slot`) is called from a client in the same process over the in-memory transport. A **tripwire** wraps `socket.socket.connect/connect_ex/bind/listen/accept`, `socket.create_connection`, `socket.socketpair`, `subprocess.Popen.__init__`, `threading.Thread.start` and the event loop's `create_server/create_connection/create_datagram_endpoint/create_unix_server/subprocess_*`: **zero events** over listing tools and four calls, and **no new `socket.socket` object** exists while the client is connected.
- **The caller's session reaches the handler** through a contextvar the CALLER sets before `call_tool`. 20 concurrent calls, each with a different session on one shared client, each see their own session. After the call, the variable is reset and a further call has no identity.
- **No session -> the tool fails closed** (no default identity). The client sees only `Error executing tool whoami`.
- **Arguments are schema-validated before the handler runs**: a wrong type or a missing field is an error result and nothing is written.
- **A write tool is idempotent on its key** (the handler's job; the SDK offers none) and two callers' writes stay separate because state lives in the caller's transaction object, not in the server.
- One call over a freshly opened in-process client costs about **5.7 ms** (list nothing, one call), so a client per request is affordable.

## 4. Where it fits our rules

| Rule | Result |
|---|---|
| Caller's own session and role, no worker identity, no new grants | Fits: the tool takes the identity from the caller's contextvar and uses the caller's transaction. No tool in the spike touches a database role of its own. |
| Writes idempotent | Fits only because the handler enforces it; the SDK gives nothing. Every write tool must require a key (a test per tool). |
| Reach the database only through the existing per-request path | Fits by construction: the server object holds no engine. The handler gets the request's connection from the same place as `request_session` (which already serialises savepoints with a lock). |
| Clinic isolation in the database | Unchanged: RLS decides, using the claims set on that transaction. |

## 5. Where it does NOT fit (questions, nothing changed)

1. **Sync tools start a thread.** A `def` tool is run in a worker thread (the tripwire caught `Thread.start`). DB work from a thread would break the per-request connection discipline. Proposal: register only `async def` tools and fail a test if any tool is sync. Agree?
2. **Exception text in server logs.** The client sees a generic error, but the SDK logs "Tool raised an unexpected exception" with the full traceback, which includes our exception messages. Our rule is fixed words only, never patient text. Proposal: raise only fixed-word exceptions from tool handlers AND attach a logging filter that drops `exc_info`/messages from the `mcp` loggers. Is a logging filter in production code acceptable?
3. **The contextvar is implicit global state.** It works and survived 20 concurrent calls, but a handler that runs outside the caller's context sees no identity and fails closed. Proposal: wrap it in one small `session_context()` context manager in our code (the only writer), so tools never read it directly. Agree?
4. **No audit hook.** Spec §8 says the `clinical` server is "every call audited"; the SDK has no per-call hook we can rely on without wrapping each tool. Which audit table, written under whose session? (Front desk cannot write the audit tables today.)
5. **The four statuses (`ok/empty/degraded/unavailable`)** are not an SDK concept: we would return our own structured envelope per tool. Is a shared envelope type acceptable?
6. **The `clinical` read-only server and its separate read-only database role** is still an open question from the Phase 3 design (a role = a grant). Not answered by the SDK.
7. **Spend ledger and the reply-interpretation model call** are still open (only the worker may write the ledger). Not an SDK matter; recorded so it is not lost.
8. **OpenTelemetry spans.** Harmless today (API only, no exporter). If an OTel SDK/exporter is ever added (Langfuse/Grafana in Phase 4), spans must not carry tool arguments or results. Needs a data-flow-register entry then.
9. **Private SDK attribute.** The in-memory transport reads `MCPServer._lowlevel_server` internally (marked TODO in the SDK). It is inside the SDK, not our code, but it is a reason to pin exactly and keep a one-day upgrade test.
10. **`pywin32` appears in the lockfile** because of a Windows marker, though CI and production are Linux. Acceptable, or exclude it with a uv override? (Either is a lockfile decision for the owner.)
11. **Hand-written alternative.** If any of 1-4 is rejected, the same interface can be implemented without the SDK (a registry with JSON-schema validation); the cost is that it would not be MCP.

## 5a. Owner answers to section 5 (2026-10-28)

- **Q2 (logging):** YES to the production logging filter on the SDK's records (handler-attached, withholds tracebacks and exception text).
- **Q5 (envelope):** YES to a small shared result-envelope type for `ok/empty/degraded/unavailable`.
- **Q10 (`pywin32`):** ACCEPTED (platform-only marker; no effect on Linux).
- **Q4 (audit):** the audit record is written by the CALLING user's own session in the same transaction as the tool call. Existing grants checked: `careflow_app` has `INSERT, SELECT` on `audit.log` (migration 0001) with the policy `log_insert_as_self` (`actor_id is not distinct from auth.uid()`), and SELECT only for the auditor role. **No new grant is needed.** Hardening note: the insert policy does not bind `clinic_id` to the actor's clinic, so the wrapper must take `clinic_id` from the verified session, never from tool arguments; `details` must hold ids and outcome only. Every existing writer of `audit.log` (e.g. the message read) already follows that.
- **Q1, Q3, Q6-Q9, Q11:** unchanged (Q6 and Q7 are settled by the Phase 3 design: no `clinical` server in the first cut; in-memory spend ledger with the hard blocker).

## 6. What this branch contains (and must not be merged)

`pyproject.toml` and `uv.lock` (the `mcp` pin), `spikes/` (the throwaway probe), `tests/test_spike_mcp_inprocess.py`, and this document. No production code, no migration, no change to `requirements*.txt`. To adopt: re-create the dependency commit on the integration branch after the owner's decision.
