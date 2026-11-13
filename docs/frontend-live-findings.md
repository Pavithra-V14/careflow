# Live pass findings (2026-11-13)

Backend: DEV project, demo profile, http://127.0.0.1:8010 through the dev proxy. Headed browser; the user typed every password and authenticator code. No code, token or credential is recorded here.

Skipped by decision: the audit reader (no auditor account) and the patient portal (phone sign-in is not set up).

## MFA

Later statement from the user (13 Nov 2026): the dev accounts have no authenticator and the backend on 8010 runs with CAREFLOW_REQUIRE_AAL2=false; screens the backend lists as refusing without a second factor (visit readiness, results and notes, care plans, care-gap set-up, autonomy, break-glass, grievances admin, staff recovery, erasure, staff accounts, portal claims, admin voice transcripts) are recorded as "not tested live: needs second factor", not as failures.
Console behaviour: a password-only sign-in lands on the authenticator ENROLMENT screen (`mfa-enrol`), because the console requires an aal2 session before it shows anything (`AuthProvider.settle`). It has no password-only path.

Earlier decision (user): MFA stays on; the sign-ins earlier in this pass used a typed password and authenticator code.
- Sign-in with a second factor: verified live (front desk signed in several times; `/v1/iam/me` answered 200 each time).
- The step-up prompt (401 with `WWW-Authenticate: step-up`, "recent authenticator code" dialog): NOT verified live yet. No write so far answered 401 step-up, and the erasure write buttons are off in this build. The statement "step-up verified live" is not made here until a real 401 step-up has been seen.

## Front desk (pavithravenkatesh341@gmail.com), read-only walk

Result: 50 calls, 0 schema mismatches, 0 notes. Report: `frontend/live-report/live-report-front_desk.md` (git-ignored).

Shell showed: role "Active role: Front desk", environment badge "TEST", operating profile Demo.

| Check | Expected | Seen | Verdict |
|---|---|---|---|
| Referrals list | 4 rows | 4 | pass |
| Possible duplicates | 2 pairs | 2 (0 resolved) | pass |
| Merges list | not stated | 1 | note |
| Slots list | 8 seeded slots | 12 rows returned | note: count differs from the seed description; may include past or other-status slots |
| Holds | 4 seeded | released 1, expired 3, booked 2 (separate status filters) | note: no 'active' filter was walked, so the 4 cannot be matched |
| Waitlist, bookings, offers, alerts, review tasks | empty (nothing seeded) | 0 rows | pass |
| Dashboard, care plans, care gaps, accounts, autonomy history/proposals, reviewer stats, cost canvas, voice sessions, recovery, grievances | not for front desk | 403 | pass (role-based) |

Coverage gap: the empty lists mean row shapes for review tasks, holds (active), offers, waitlist, bookings and alerts were not checked in this walk. They get checked when the write steps below create rows.

## Front desk, writes (recorded by hand): 225 calls, 0 schema mismatches

Every write carried an idempotency key. Row shapes are now checked for waitlist, bookings, holds, offers (`message_id` present) and messages.

| Step | Calls seen | Verdict |
|---|---|---|
| Register a patient | POST `/v1/staff/patients` 200, then GET record and GET `/view` 200 | pass |
| Join the waitlist, two patients | POST `/waitlist/join` 200, 200 | pass |
| Join again, same patient and visit type | POST `/waitlist/join` 409 | pass (409 expected; screen text to confirm) |
| Hold a slot | POST `/slots/{id}/hold` 200; Holds list then 1 row | pass |
| Book a slot | POST `/slots/{id}/book` 200; slots 12 -> 11; bookings 1 | pass |
| Cancel a booking | POST `/bookings/{id}/cancel` 200; waitlist for the visit type then 2 | pass |
| Prepare offer | GET `/backfills/{slot}` 404 (no run yet), POST `/backfills` 200 | pass (the 404 is the "no run yet" probe) |
| Choose candidate | POST `/backfills/{slot}/decide` 200 | pass |
| Type the patient's reply | POST `/offers/replies` 201, then POST `/backfills/{slot}/reply` 200 | pass |
| Reply entered a second time | POST `/backfills/{slot}/reply` 409, twice (after each cycle) | EXPLAINED by the backend (careflow-cb): a reply the rules cannot assess ends the run as "needs human" with a nurse task; a later reply then gets 409 with detail "this run is not waiting for that". Intended. The screen text the user saw was never supplied, so the wording on screen is unchecked. |
| Offers list and copy offer text | GET `/offers` 2 rows; GET `/messages/{id}` 200 | pass |
| Mark delivered | no POST `/offers/{hold}/mark-delivered` recorded in either recording (10 calls in the second, none a write) | NOT TESTED. Screenshot of Offers (All): 2 offers for one patient, "Expired" (held until 13 Nov 2026, 1:12 pm) and "Released" (12:59 pm); none "Awaiting delivery", so there was nothing to mark. The hold ran out or was released before delivery. Needs one fresh cycle done without pausing. |

Cycle was run twice (book, cancel, prepare, decide, reply).

Notes (not defects): most pages re-read providers, locations and visit types on every navigation (many repeated GETs). This is wasteful but correct; no change planned unless you want one.

## Front desk, scripted cycle (Prepare offer, Send, Mark delivered; synthetic data; the script is temporary and not committed)

| Step | Seen | Verdict |
|---|---|---|
| Prepare offer | run created (status "drafted", waiting for "Your decision to send, skip or stop", candidate rank 1); call took 8 s once and over 15 s once | pass, but SLOW (backend) |
| Send | offer appeared in Offers as "Awaiting delivery"; the call was still unanswered after 15 s | pass, but SLOW (backend) |
| Offers list | the new offer "Awaiting delivery" next to the older "Expired" and "Released" ones | pass |
| Mark delivered | dialog "Mark this offer delivered? Record that you delivered this offer yourself. The system sent nothing to the patient. This cannot be undone." stayed on "Working…" 5 s after the click; the outcome was not read | CONFIRMED LATER: the offer shows Delivered (see the timed cycle below) |

Earlier finished run: the first slot's offer run ended "needs human", Finished "Yes", with a review task raised, after a typed reply. Later reply calls returned 409. Asked the backend (careflow-cb) to confirm whether that is intended and what the 409 detail is.

Backend answer on the slow calls: no ERROR lines, tracebacks or warnings; the access log has no timestamps or durations; the later POST /backfills, /decide and /mark-delivered for the scripted slot are NOT in the log (uvicorn logs a request only when it finishes), so they either did not finish or the client's disconnect cut them off. /readyz answers in about 6 ms. Likely cause (unproven): many database round trips to the hosted dev project. Offered to add per-request timing to the access log and re-run. Mark delivered never appears in the log at all, so it probably did not complete when my script ended.

Backend defect candidate: write calls (backfills, decide/send, mark-delivered) take 8 to over 15 seconds. Reported to careflow-cb with method, path and timings.

## Front desk, timed cycle with the dev-only password-only sign-in (13 Nov 2026, UTC)

Signed in with the password only at 10:12:00Z (banner shown, no authenticator screen). Patient: Demo Charlie Sample (fictional seed). All calls answered 200 and carried a key.

| Response time (UTC) | Call | Browser-side duration |
|---|---|---|
| 10:12:09 | POST `/patients/search` | 6.1 s |
| 10:12:16 | POST `/slots/{id}/hold` | 6.6 s |
| 10:12:34 | POST `/patients/search` | 7.3 s |
| 10:12:43 | POST `/slots/{id}/book` | 8.1 s |
| 10:13:00 | POST `/bookings/{id}/cancel` | 5.0 s |
| 10:14:08 | POST `/backfills` (Prepare offer) | 50.7 s |
| 10:15:08 | POST `/backfills/{id}/decide` (Send) | 51.1 s |
| 10:15:34 | POST `/offers/{id}/mark-delivered` | 8.5 s |

Sent to careflow-cb to match against its timing lines. Result still to come from them.

Mark delivered: PASS. Screen after it: "The offer is marked delivered."; the offer shows "Delivered", Channel "manual", with a delivered time. The earlier attempt's Mark delivered (the one cut off after 5 s) had also completed: its offer shows Delivered at 2:11 pm (the clock in the screenshot is local).

Hold dialog note (frontend, not a defect): "Minutes to hold" has no default, so the **Hold** button stays disabled until a number is typed. The checklist said "the default duration"; the checklist wording is wrong or the field should have a default. Needs your decision (I will not invent a duration).

## Not tested live: needs second factor

Recorded per the BACKEND window's list, not as failures: visit readiness, results and notes, care plans, care-gap set-up, autonomy, break-glass, grievances admin, staff recovery, erasure, staff accounts, portal claims, admin voice transcripts.

## Frontend defects found so far

None.

## Backend defects found so far

None.

## Write steps still to do (record mode, user clicks)

Front desk: add two patients to the waitlist, Hold, Book, cancel, Prepare offer, Send, mark delivered, typing a patient's reply. Then nurse, doctor, admin.
