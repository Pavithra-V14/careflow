# Dev latency: why the console feels slow against the dev project

Status: measured 2026-11-13. Owner decision (2026-11-13): leave the dev project where it is; do not optimise the three slow routes now; do not turn off the connection ping.

## What was seen

Front-desk write steps against a local backend (demo profile) took 8 to over 15 seconds: Prepare offer (`POST /v1/staff/scheduling/backfills`), Send (`POST /backfills/{slot_id}/decide`) and `POST /offers/{hold_id}/mark-delivered`. The server log showed no errors, lock waits or outside calls (demo has none on these paths).

## Measurements

The hosted dev project is in Tokyo; the backend ran in India. Measured from the developer machine, through the app's own engine settings (transaction-mode pooler, no prepared-statement cache, `pool_pre_ping=True`):

| What | Result |
|---|---|
| One plain round trip (`select 1`, bare driver) | about 330 ms (254 to 439) |
| First request on a new connection | 1.3 s (bare driver) to 4.9 s (through the app engine) |
| Fixed cost of any pooled request (ping, begin, set claims, commit) | about 1.6 s |
| Each further statement through the app engine | about 0.55 s |
| Request with claims plus 1, 5, 10 statements | 2.1 s, 4.7 s, 7.6 s |

Reading: request time is roughly 1.6 s plus 0.55 s per statement. A route of 12 to 25 statements takes 8 to 15 s. The distance explains it; nothing in the code is stuck. A real deployment puts the backend in the same region as the database, where a round trip is about a millisecond or two and these routes would take well under a second.

## Options (considered; none taken now)

1. **Turn off the pool's connection ping** (`pool_pre_ping`). Saves one round trip (about 0.33 s) per request. Rejected for now: it removes the protection against a connection the pooler has silently closed, so the first request after an idle spell could fail.
2. **Fewer statements inside the slow routes**, for example one combined database function for `mark-delivered` (hold read, role check, prior-delivery read, insert, audit write). Largest saving per route. Each function is a migration with an RLS review and a grants-ledger row, so it is deliberate work. Not done now.
3. **Move the backend next to the database** (or the dev project nearer to the developer). Cuts every route by a large factor with no code change. For dev, left as is (a dev-only artifact of the hosted region).

Not an option: the claims-setting statement at the start of every request is part of the row-level-security design (rule 8) and is not touched to save time.

## Per-route times and statement counts (recording of 2026-11-13, 10:12 to 10:17Z, front desk, MFA off for the run)

Server times from the timing log; the browser saw the same durations, so all of the time is on the server (database distance), none in the browser or network to the backend. Statement counts are ESTIMATED from the time with (ms - 1600) / 550; they are not counted from the code except where noted.

| Route pattern | Requests | Median ms | Slowest ms | About statements |
|---|---|---|---|---|
| POST /v1/staff/scheduling/backfills/{slot_id}/decide (Send) | 1 | 51128 | 51128 | 90 |
| POST /v1/staff/scheduling/backfills (Prepare offer) | 1 | 50668 | 50668 | 89 |
| GET /v1/staff/scheduling/backfills/{slot_id} | 3 | 8361 | 8760 | 12 |
| POST /v1/staff/scheduling/offers/{hold_id}/mark-delivered | 1 | 8465 | 8465 | 12 |
| POST /v1/staff/scheduling/slots/{slot_id}/book | 1 | 8064 | 8064 | 12 |
| POST /v1/staff/patients/search | 3 | 6452 | 7287 | 9 |
| POST /v1/staff/scheduling/slots/{slot_id}/hold | 1 | 6532 | 6532 | 9 |
| POST /v1/staff/scheduling/bookings/{booking_id}/cancel | 1 | 4982 | 4982 | 6 |
| GET /v1/staff/messages/threads/{thread_id}/messages | 1 | 3779 | 3779 | 4 |
| GET /v1/staff/scheduling/waitlist | 3 | 2535 | 4002 | 2 |
| GET /v1/staff/scheduling/bookings | 2 | 2857 | 3829 | 2 |
| GET /v1/iam/me | 8 | 2902 | 3590 | 2 |
| GET /v1/staff/scheduling/slots, providers, locations, visit-types, offers | 5 to 11 each | 2.6 to 3.0 s | 3.5 s | 2 |

Where the two 50 s calls go: the backfill run is an agent graph with a database-backed checkpointer (careflow/checkpointer.py). Each graph step reads and writes checkpoints, blobs and pending writes, and each of those operations sets the caller's claims and runs several statements. At 0.55 s a statement, about 90 statements is 50 s. With a round trip of 1 to 2 ms (backend beside the database) the same run is well under a second. The 12-statement routes (book, mark-delivered, backfill status) are plain per-route statement chains, about 8 s here.
