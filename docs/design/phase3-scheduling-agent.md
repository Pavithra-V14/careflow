# Phase 3 — the Scheduling agent over MCP (design only)

Status: **design, nothing built.** Updated 2026-10-27 with the owner's decisions (section 1). Sources: BUILD-SPEC §8 (tools), §7 (graphs, queue), §12.1 (patient messages need a person), §16.2 (degradation), §17.1/§17.5 (scheduling, message topics), Appendix D (phase 3 gate). What exists: `careflow/scheduling.py` (`SlotManager`: hold, book, cancel, join/rank waitlist; deterministic, no model, no message), migrations 0002/0004 (`sched.*`, the gist exclusion constraint, `sched.priority_levels` empty until a doctor authors rows), the `decision` tier (P2-2), the queue and tick, the Front Door graph pattern (P2-8).

Nothing below may invent a clinical value or message wording. Every such value is a **required, undefaulted argument** marked `# CLINICAL-PLACEHOLDER: needs clinician sign-off`, and all of them are listed once in `docs/design/clinician-decisions.md` (items 16-22) for Priya.

## 1. Owner decisions that shape this design (2026-10-27)

| Decision | Consequence here |
|---|---|
| **The front desk presses send.** No worker identity and **no new grants on `sched` or `msg` in Phase 3.** | Nothing in Phase 3 writes a hold, booking, waitlist row or message as the queue worker. Every write is made by a signed-in person's request, under that person's claims, through the MCP `scheduling` server. See section 3 for what this does to the "agent". |
| The official `mcp` SDK is approved: **pinned, in-process only, no network transport.** | Mounted with the SDK's in-memory transport; no HTTP/SSE/stdio transport is created, and a test asserts no socket is opened. A **short spike runs at the START of Phase 3** and is reported before anything is built on it (section 4). No external service. |
| Clinician-owned values stay **named placeholders** (`needs clinician sign-off`). | No values and no message wording are written. |

## 1a. Stated constraint (owner, 2026-10-27; confirmed for the open question in section 9)

**Offers are created and sent under the requesting front-desk user's own session and role.** There is no worker identity in Phase 3 and **no new grants on `sched` or `msg`**. The agent only proposes; a person presses send. Any step that would still need a grant is listed in section 9 as an open question and is NOT given one here. What front desk can already do under existing policies (checked in migrations 0004 and 0011/0013): read and insert/update `sched.holds`, `sched.bookings`, `sched.waitlist`; insert a staff message on an `appointments`-topic thread; read the same topic. It cannot insert or read `agents.review_tasks` (except `unmerge_review`), cannot write graph checkpoints, and cannot write the spend ledger (queue-worker only).

## 2. What the agent does, and what it does not

Four flows, each ending in a person's click:

1. **Offer**: for a triaged, approved referral (or a patient request a person approved), the agent proposes ranked candidate slots and a **draft** offer. A front-desk user reviews it and presses **send**: that one action creates the hold and the `appointments`-topic message.
2. **Reply**: when a patient's reply arrives it is read through the `decision` tier and a **suggested action** (accept / decline / other time / question / unclear) is shown to the front desk, who confirms.
3. **Book / cancel**: confirming an accept converts the hold to a booking; a cancellation frees the slot.
4. **Backfill**: a freed slot produces a ranked list of waiting patients in **clinical-priority order, then wait time**, with a draft offer for the top one; the front desk presses send.

It does **not**: send anything on its own, discuss clinical matters (that is a `care` thread and a nurse), change a priority, decide who is clinically more urgent, or approve its own output.

## 3. What "no worker grants" does to the shape (a consequence to confirm)

The Front Door graph runs in the queue worker, and the LangGraph checkpoint tables are writable only by the worker, nurses and doctors (migration 0021). With no worker grants on `sched`/`msg`, and front desk unable to write checkpoints, a LangGraph scheduling graph that holds and messages is **not possible** in Phase 3. The design therefore does not use one:

- **State is existing rows, not a graph checkpoint and not a new table.** A draft is COMPUTED on demand and never stored. An offer that has been sent is its `sched.holds` row (active, with expiry) plus its `appointments`-topic message; a booking is a `sched.bookings` row. "Waiting for the patient" is simply an active hold that has not expired; there is no pause to resume, so rule 4 (no side effect in a node that pauses) is satisfied by construction. If a graph or a stored draft/offer row is wanted later, that needs a grant and is an open question (section 9, Q4).
- **Each press is one MCP call under the person's claims**: schema validation, row-level authorization, policy, budget, idempotency key (spec §8 order), one transaction.
- **Nothing runs in the background.** A freed slot is noticed **by the front-desk screen**: after a staff cancellation the same request returns the ranked list and a computed draft for the top candidate; any freed slot (including a patient-initiated cancellation) also appears on a front-desk "freed slots" query screen. Nothing is stored and no job runs; the person sees the draft and presses send.
- **Reply interpretation is run when a person opens the reply** (or on a front-desk request), through the `DecisionPort`, as a request under the front desk's claims.

*Settled by the owner on 2026-10-27: this shape applies in Phase 3.*

## 4. Two MCP servers, mounted in-process (spec §8)

| Server | Tools | Database role | Why |
|---|---|---|---|
| `scheduling` (read + write) | `find_slots`, `hold_slot`, `book_appointment`, `cancel_appointment`, `join_waitlist`, `rank_waitlist`, `release_hold`, `propose_offer` (read-only: returns ranked candidates and a draft, writes nothing), `send_offer` (one transaction: hold + message) | the app role with the **acting person's claims** | the only place a booking can change |
| `clinical` (read-only) | `read_priority_context` (the waitlist entry's `priority_code`; nothing else of the chart) | a **separate read-only role / connection** with SELECT only on the columns the tool returns | a read tool must be physically unable to write: a server split, not a flag |

Spec §8's write order is kept: schema validation, row-level authorization, autonomy-mode policy, budget, idempotency key. Results are `ok | empty | degraded | unavailable`, and callers branch (an unreachable source is never `empty`). `find_slots` is load-bearing for an offer: `unavailable` means no draft and a visible message to the user, never a guessed slot. **Clinic isolation stays in the database**: the servers carry no clinic check of their own.

### SDK spike: DONE and accepted (2026-10-27): GO, pin `mcp==2.3.0` (v1.30.0 the documented fallback)
The spike lives on the local, unmerged branch `phase3-mcp-spike` (`docs/design/phase3-mcp-spike.md` there); the main lockfile is not touched until Phase 3 code starts. **Conditions the Phase 3 code must meet:**
1. Only `async def` tools are registered; a registration helper refuses a sync function and a test fails if one is registered (a sync tool would run in a worker thread).
2. Production code raises **fixed-word exceptions only** (no patient text, ids or SQL in any message), and a logging filter on the SDK's records withholds tracebacks and exception text; a test proves a planted sentinel never reaches a log record (with a negative control showing the test can fail).
3. Every tool call on the `clinical` server writes an **audit record**: who, role, tool, outcome; never arguments or results (they could hold patient text). A wrapper we own, because the SDK has no hook. (Whether the first cut has a `clinical` server at all is item 1 below.)
4. `pywin32` is a Windows-only marker dependency; confirmed absent from a Linux resolution, so it does not matter on Linux CI.

### (historical) the spike plan
Pin `mcp` (v1.30.0 or v2.3.0, chosen by the spike). Verify: (1) the in-memory client (`Client(server)` in v2, `create_connected_server_and_client_session` in v1.x) lists tools, validates arguments against their JSON schema before the handler runs, and returns structured results with the four statuses; (2) **no socket, thread or subprocess is opened** (test); (3) tool handlers can receive the caller's claims and a transaction; (4) the added packages (`httpx-sse`/`httpx2`, `mcp-types`, `jsonschema` with `referencing`/`rpds-py`/`attrs`, `python-multipart`, `sse-starlette`, `pywin32` on Windows) import cleanly and the data-flow register gets a "no network in-process" line and test. If the spike fails, fall back to a hand-written in-process registry behind the same interface (and tell the owner).

## 5. Reply interpretation through the DecisionPort

`interpret_reply` asks `decision` one closed-option question: `{accept, decline, other_time, question, unclear}`.

- The deterministic red-flag rules run on the reply text **first, on every path**: a hit raises the existing immediate nurse task and the offer is marked blocked; the model is never the first reader (rule 2).
- The result is a **suggestion** shown to the front desk with the patient's own words; a person confirms. Nothing books on a model's say-so alone in Phase 3. (Whether a high-confidence accept may ever book without a click is a clinician-owned threshold and an autonomy-mode decision, `needs clinician sign-off`, not built.)
- `unclear`, `question`, `other_time`, an out-of-vocabulary answer, an adapter outage, a language the tier cannot score: shown to a person with the reply text visible. No answer is never read as consent.
- A lost race (the slot was taken) is a drafted "that time is gone" reply for the front desk to send, and the next candidate; `book_appointment` already re-checks under the slot lock.
- Replies are matched to the offer by thread and offer id, never by claims in free text.

## 6. Cancellation backfill, ranked by clinical priority

Trigger: a staff `cancel_appointment` (section 3). `rank_waitlist` (exists) orders by **`sched.priority_levels.rank` ascending** (clinician-authored, strict, no ties), then `joined_at`. A longer-waiting lower-priority patient never precedes a higher-priority one. Eligibility filters are mechanical (same visit type, the entry's own stated window, no overlapping booking); the agent never reorders by anything but rank and join time. If the ranking is empty, or priority levels are not configured for the clinic, **nothing is drafted** and the screen shows "slot freed, no ranked candidates" (unknown is not a value; no default ranking exists). Each offer, skip and book records ids and the rule that fired (`rank`, `joined_at`) so "why was this patient offered the slot" is answerable.

**Phase-3 gate test (Appendix D):** a higher-priority patient who joined *later* is offered the freed slot ahead of a lower-priority patient who has waited longer, against the real restricted role and RLS, with fake decision and message adapters and no hosted service.

## 7. Side effects, idempotency, degradation

| Step | Effect | Key |
|---|---|---|
| propose | none (read-only) | n/a |
| send (front desk) | hold + `msg` message, one transaction | `offer-send:<slot>:<patient>` |
| book | booking, hold converted | `book:<offer>` |
| release | hold released | `release:<offer>` |

Degradation (§16.2): MCP `scheduling` unavailable -> the screen says so and nothing is half-done (one transaction per press); `decision` outage -> every reply is shown with no suggestion; message sending unavailable -> the send fails whole, the hold is not taken. No path silently drops an offer. Expired holds are released by the existing soft filter in `SlotManager`, not by a job.

## 8. Tests that gate the phase

Role-matrix cases for every new route in the same change as the route (rule 8); the backfill-priority end-to-end; a "read tool cannot write" test (the `clinical` server's role is refused an INSERT/UPDATE by the database); an MCP-boundary test (a schema violation is rejected before any write); replay tests per write; a missing idempotency key refused on every write tool; **a test that Phase 3 adds no grant on `sched` or `msg` (the grants of migrations 0004/0011/0013 are unchanged) and that the queue worker never writes there**; a test that no scheduling code path sends a message without a front-desk action; `decision` outage; red-flag reply before any model.

## 9. Proposals and questions for the owner

Items 1, 3 and 4 below are **PROPOSED, needs user sign-off** (item 2 is decided). None adds a grant, a table, a role or code; where a proposal depends on something that would, that is stated.

1. **PROPOSED, needs user sign-off: the `clinical` read-only server.** No new database role at first. Reads go through the requesting front-desk user's own session, so RLS decides. Revisit only if a read cannot be done that way. (Today the only field such a server would serve, `priority_code`, is already on `sched.waitlist`, which front desk can read, so the first cut may mount no `clinical` server at all; the audit-wrapper condition applies the day one is mounted.)
2. **DECIDED (owner, 2026-10-27): spend ledger for the reply-interpretation call = the IN-MEMORY ledger for the first cut, with NO new insert grant on the spend ledger.** The call uses its own ordinary (budget-limited) class and ceiling; the safety class stays never budget-limited. An in-memory stop must still become a **visible hold for a person**, never a silent skip. Because the in-memory ledger is per process and lost on restart, the daily ceiling cannot be enforced across processes or restarts.

   > **HARD BLOCKER: no real model call for reply interpretation until a durable ledger is decided and its grant is reviewed by the user.** Until then the `decision` adapter for this call is a fake (as everywhere else); a real engine behind it is not wired.

   Tests (both required; the gateway halves exist today against `InMemorySpendLedger`): (a) an ordinary call at its ceiling is a visible stop, never a silent skip: `tests/test_gateway.py::test_only_a_paid_row_at_the_ceiling_is_a_visible_spend_error_and_another_clinic_is_unaffected` and `::test_an_ordinary_call_skips_a_paid_row_at_the_daily_ceiling_and_a_free_row_still_answers`; (b) the safety class is never budget-limited: `::test_the_safety_class_ignores_the_spend_ceilings_and_is_not_refused_by_an_exhausted_budget` and `tests/test_budget.py::test_safety_calls_are_never_refused_after_every_ordinary_ceiling_is_exhausted`. **Phase 3 adds the consumer-side test:** when the reply-interpretation call hits its ceiling, the front-desk screen shows the reply with "no suggestion: needs a person" (a visible hold), and a red-flag hit on the same reply is still raised (the rules and the safety class are not budget-limited).
3. **PROPOSED, needs user sign-off: stored drafts or front-desk-visible tasks.** None in the first Phase 3 cut. Drafts are computed on demand; the freed-slots list is a query.
4. **PROPOSED, needs user sign-off: a patient's reply.** The deterministic red-flag rules run on it first, before any model. A hit or an unassessable reply creates a nurse review task. The agent never auto-replies to a reply that was not screened. (Confirm the existing `msg.record_screening` path is what raises that nurse task when the reply arrives, since a front-desk session cannot insert the task itself.)
5. (Settled 2026-10-27) Offers are request-driven under the front-desk user's own session; see section 1a.
6. Everything clinician-owned is in `docs/design/clinician-decisions.md` items 16-22; the list for Priya is in the next section. Nothing here supplies a value.
7. Patient-initiated reschedule is out of scope (the spec's "Scheduling conflict" hand-off); confirm it is a later phase.

## 10. What I need from the clinicians before Phase 3 code

One list, to be handed to Priya. **Every item is a required, undefaulted input marked `# CLINICAL-PLACEHOLDER: needs clinician sign-off`; this document and the code supply no value and no wording.**

1. The clinic's **priority levels and their ranks** (codes, labels, a strict order, 1 first), authored and approved by a named doctor in `sched.priority_levels`.
2. The **minimum confidence** at which the system may SUGGEST "accept" to the front desk (per clinic); and whether an accept may ever book without a click (not built, a person confirms today).
3. **Offer expiry**: how long an offer stays open.
4. **Hold lifetime**: how long a slot is held for an offer.
5. **Backfill count**: how many next-ranked patients are drafted for before a person is told "no one could be offered".
6. **Message templates**: the offer, the confirmation and the "that time is gone" wording, and the languages.
7. Whether any autonomy mode may send an offer **without** the front desk pressing send (current decision: no).
8. The **priority and assigned role** of any scheduling task (only relevant if tasks are later added; none in the first cut).
