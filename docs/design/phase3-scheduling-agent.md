# Phase 3 — the Scheduling agent over MCP (design only)

Status: **design, nothing built, no dependency added.** Sources: BUILD-SPEC §8 (tools), §7 (graphs, queue), §12.1 (patient messages need a person), §16.2 (degradation), §17.1/§17.5 (scheduling, message topics), Appendix D (phase 3 gate). What exists today: `careflow/scheduling.py` (`SlotManager`: hold, book, cancel, join/rank waitlist; deterministic, no model, no message), migrations 0002/0004 (`sched.*`, the gist exclusion constraint, `sched.priority_levels` empty until a doctor authors rows), the `decision` tier (P2-2), the queue and tick (P1), the Front Door graph pattern (P2-8).

Nothing below may invent a clinical value. Every such value is a **required, undefaulted argument** marked `# CLINICAL-PLACEHOLDER: needs clinician sign-off` and listed in `docs/design/clinician-decisions.md` (new items are collected in section 9).

## 1. What the agent does, and what it does not

Four flows, all of them wrappers around `SlotManager` plus a graph that waits for people:

1. **Offer**: a triaged, approved referral (or a patient request a person approved) needs an appointment. The agent finds candidate slots, holds one, and puts an offer in front of the patient on an `appointments`-topic thread.
2. **Reply**: the patient's answer is read through the `decision` tier (accept / decline / ask for another time / ask something else) and acted on.
3. **Book / cancel**: an accepted offer converts the hold to a booking; a cancellation frees the slot.
4. **Backfill**: a freed slot is offered to the waiting patients in **clinical-priority order**, then by wait time.

It does **not**: discuss clinical matters (that is a `care` thread and a nurse), change a priority, decide who is clinically more urgent, send a free-form message, or approve its own output. A message that is not a clean answer to the offer is handed to a person (section 5).

## 2. Two MCP servers, mounted in-process (spec §8)

| Server | Tools | Database role | Why |
|---|---|---|---|
| `scheduling` (read + write) | `find_slots`, `hold_slot`, `book_appointment`, `cancel_appointment`, `join_waitlist`, `rank_waitlist`, `release_hold` | the app role with the **acting account's claims** | the only place a booking can change |
| `clinical` (read-only) | `read_priority_context` (the single field the agent may use: the waitlist entry's `priority_code`; nothing else of the chart) | a **separate read-only role / connection** with SELECT only on the columns the tool returns | a read tool must be *physically* unable to write: a server split, not a permission flag |

Rules that carry over unchanged from §8: arguments are schema-validated, then **row-level authorization**, then the autonomy-mode policy, then the budget, then the idempotency key, in that order, **before** any write tool runs. Results are `ok | empty | degraded | unavailable` and the calling node must branch; an unreachable source is never `empty`. Load-bearing vs decorative is declared per tool per workflow (find_slots is load-bearing for an offer: `unavailable` means no offer and a task, never a guessed slot).

**Clinic isolation stays in the database.** The servers do not hold clinic checks of their own: each tool call runs inside a transaction with the *caller's* verified claims (the same `request_session`/`worker_session` pattern as the Front Door), and RLS decides. The agent acts as the **queue worker identity** for background steps and as a **staff or patient account** only where a person's click started the flow. The worker identity needs its own narrow grants on `sched.holds`, `sched.bookings`, `sched.waitlist`, `msg.*` (new migration, reviewed by `rls-security-reviewer`; today 0004 gives the worker none of these).

## 3. Is the official MCP Python SDK needed? (Context7 + PyPI metadata checked 2026-10-27)

**Answer: not to run two in-process tool servers, but yes to make them *MCP*.** The spec says the tool boundary is MCP and the Phase-3 end-to-end test drives "MCP servers over HTTP" (§ testing). The SDK is what gives the real protocol layer (tool listing, JSON-schema validation of arguments, structured results, a client that goes through the same code path as a deployed server): "No subprocess, no port, no bytes on a wire... the call still goes through the real protocol layer" (SDK docs, in-memory transport: `Client(server)` in v2, `create_connected_server_and_client_session` in v1.x). Without the SDK we would hand-write a registry that imitates MCP, and the later move to a real MCP boundary would be a rewrite.

What the dependency would add (from PyPI metadata; **nothing installed, nothing downloaded**). Already in our lock: anyio, httpx, pydantic, pydantic-settings, pyjwt, starlette, typing-extensions, typing-inspection, uvicorn, cryptography, opentelemetry-api.

| Candidate | New top-level packages | Notes |
|---|---|---|
| `mcp` 1.30.0 (v1 line) | `httpx-sse`, `jsonschema` (pulls `referencing`, `rpds-py`, `attrs`), `python-multipart`, `sse-starlette`, `pywin32` on Windows only | pure Python except `rpds-py` and `pywin32` wheels |
| `mcp` 2.3.0 (v2 line) | `httpx2`, `mcp-types`, `jsonschema` (same tree), `python-multipart`, `sse-starlette`, `pywin32` on Windows only | newer API (`Client(server)`); a second HTTP client library beside `httpx` |

Network/phone-home: the SDK makes **no outbound call** when used in-process (no transport is opened); `opentelemetry-api` is the API package only (no exporter). Still, a data-flow-register line and a test that in-process use opens no socket would be added with it (rule 7). Version choice (v1.30 vs v2.3) needs a short spike before we pin: confirm the in-memory client, tool-schema validation and structured results behave as documented (Appendix B practice). **Ask before adding: see section 9, question 1.**

## 4. The graph (LangGraph, same discipline as the Front Door)

One thread per *offer* (`scheduling:<waitlist_or_referral_id>:<slot_id>`), ids only in state, our Postgres checkpointer, `sync` durability, tracing refused, structural test extended to this graph (rule 4 is a CI gate).

```
find_slots -> hold_slot -> compose_offer -> [PAUSE: wait_for_reply] -> interpret_reply
    -> book | release_and_next | hand_to_human
```

- `find_slots` (read, no effect) -> `unavailable`/`empty` ends in a **front-desk task** (no slot is invented).
- `hold_slot` (effect: hold, key `hold:<offer>`), the side effect sits in the step *before* the pause, never in the node that waits.
- `compose_offer` (effect: **a draft message** on the `appointments` thread through the approval matrix, key `offer:<offer>`). Whether a draft needs a person's approval before it reaches the patient is the autonomy-mode decision (§12.1); until a clinician-approved template and mode exist the draft goes to the front-desk queue (`CLINICAL-PLACEHOLDER` for the template text).
- `wait_for_reply` is the interrupt, with **two timers owned by the queue, not by the graph**: the hold expires (database clock, `SlotManager` already soft-expires), and the offer expires; expiry resumes the graph with `no_reply`, which releases and moves on. A timeout never books and never approves.
- `book` converts the hold to a booking (key `book:<offer>`); the booking confirmation is a draft message, same rule.

## 5. Reply interpretation through the DecisionPort

`interpret_reply` asks `decision` one closed-option question: `{accept, decline, other_time, question, unclear}`. Rules:

- The deterministic red-flag rules run on the reply text **first, on every path** (a patient replying to a slot offer can still say they are unwell): a hit raises the existing immediate nurse task and the offer thread stops; the model is never the first reader (rule 2).
- Only `accept` with confidence at or above a clinic threshold and a clean, unambiguous reply books. Everything else (`unclear`, low confidence, `question`, `other_time`, an out-of-vocabulary answer, an adapter outage, a reply in a language the tier cannot score) goes to a **human task with the reply text visible**; `decline` releases the hold and moves to the next candidate only after the decline was a person-confirmed or high-confidence one. Unknown is not a value: no answer is never read as consent. **The threshold is a required argument** (`CLINICAL-PLACEHOLDER`).
- An acceptance never books a slot the patient was not offered, and never a slot whose hold has lapsed and been taken: `book_appointment` re-checks under the slot lock (it already does); a lost race is a polite "that time is gone" draft and the next candidate, not an error.
- Replies are matched to the offer by thread and offer id, never by free-text identity claims. Identity of the replying patient is the logged-in account's (the `appointments` topic is open to `new_patient` accounts, §17.5).

## 6. Cancellation backfill, ranked by clinical priority

Trigger: `cancel_appointment` (or an expired unconverted hold) frees a slot -> a queue job `backfill_slot` (key `backfill:<slot_id>:<freed_at>`).

1. `rank_waitlist` (exists): order by **`sched.priority_levels.rank` ascending** (clinician-authored, strict, no ties), then `joined_at` ascending. A longer-waiting lower-priority patient never precedes a higher-priority one; an entry whose `priority_code` is missing from the levels table cannot exist (the FK).
2. Eligibility filters are *mechanical*: same visit type, slot within the entry's own stated window if it has one, not already booked at an overlapping time. **No clinical judgment in code**: the agent never reorders by anything but rank and join time.
3. The top candidate gets an offer (flow section 4). If they decline or do not answer in time, the **next** candidate gets it; each step is its own job with its own key, so a crash replays nothing twice.
4. If the ranking is empty, or priority levels are not configured for the clinic, **nothing is offered** and a front-desk task says "slot freed, no ranked candidates" (unknown is not a value; an unconfigured clinic never gets a default ranking).
5. Auditing: every offer/skip/book records ids and the rule that fired (`rank`, `joined_at`), so "why was this patient offered the slot" is answerable from the record.

**Phase-3 gate test (spec Appendix D):** a higher-priority patient who joined *later* is offered the freed slot ahead of a lower-priority patient who has waited longer. Run against the real restricted role and RLS, with fake decision/vision/mail adapters and no hosted service.

## 7. Side effects, idempotency, degradation

| Step | Effect | Key |
|---|---|---|
| hold | `sched.holds` row | `hold:<offer>` |
| offer draft | `msg` draft + review/send task | `offer:<offer>` |
| book | `sched.bookings` row, hold -> converted | `book:<offer>` |
| release | hold -> released | `release:<offer>` |
| backfill job | next-candidate offer | `backfill:<slot>:<freed_at>`, `offer-next:<offer>` |

Degradation (§16.2): MCP `scheduling` unavailable -> the job retries, then dead-letters into the existing nurse `referral_dead_letter`-style task (new kind `scheduling_dead_letter`, a sibling of the Front Door one); `decision` outage -> every reply goes to a person; message sending unavailable -> the offer stays a draft task, the hold keeps its expiry. No path silently drops an offer.

## 8. Tests that gate the phase

Role matrix cases for every new route in the same change as the route (rule 8); the structural pause/side-effect test for the new graph; the backfill-priority end-to-end above; a "read tool cannot write" test (the `clinical` server's role is refused an INSERT/UPDATE at the database); an MCP-boundary test (schema violation rejected before any write); replay tests per write; a test that a missing idempotency key is refused on every write tool; `decision` adapter outage; red-flag reply before any model.

## 9. Questions for the owner (nothing here is built or added)

1. **Add the `mcp` SDK?** Recommended: yes, pinned, in-process use only; v1.30.0 or v2.3.0 to be settled by a one-day spike (new packages listed in section 3). Alternative: a hand-written in-process registry now and the SDK later (rewrite risk).
2. **Clinician-owned values** (all required arguments, none defaulted): priority levels and ranks per clinic (table is empty today); the acceptance-confidence threshold; offer expiry and hold TTL; number of backfill candidates tried before a human is told; offer and confirmation message templates; whether an offer draft may auto-send in any autonomy mode (default: no, a person sends).
3. **Which account sends offers?** A queue-worker identity needs new grants on `sched`/`msg`; an alternative is that the front desk presses "send" on each draft (slower, simpler grants). Safety-relevant: please decide.
4. **Patient-initiated reschedule** is out of scope here (spec's "Scheduling conflict" hand-off exists for the care plan); confirm it is a later phase.
