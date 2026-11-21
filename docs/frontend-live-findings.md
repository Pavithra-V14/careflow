# Live pass findings (2026-11-13)

Backend: DEV project, demo profile, http://127.0.0.1:8010 through the dev proxy. Headed browser; the user typed every password and authenticator code. No code, token or credential is recorded here.

Skipped by decision: the audit reader (no auditor account) and the patient portal (phone sign-in is not set up).

## MFA

Later statement from the user (13 Nov 2026): the dev accounts have no authenticator and the backend on 8010 runs with CAREFLOW_REQUIRE_AAL2=false; screens the backend lists as refusing without a second factor (visit readiness, results and notes, care plans, care-gap set-up, autonomy, break-glass, grievances admin, staff recovery, erasure, staff accounts, portal claims, admin voice transcripts) are recorded as "not tested live: needs second factor", not as failures.
Console behaviour: a password-only sign-in lands on the authenticator ENROLMENT screen (`mfa-enrol`), because the console requires an aal2 session before it shows anything (`AuthProvider.settle`). It has no password-only path.

Earlier decision (user): MFA stays on; the sign-ins earlier in this pass used a typed password and authenticator code.
- Sign-in with a second factor: verified live (front desk signed in several times; `/v1/iam/me` answered 200 each time).
- The step-up prompt (401 with `WWW-Authenticate: step-up`, "recent authenticator code" dialog): NOT verified live yet. No write so far answered 401 step-up, and the erasure write buttons are off in this build. The statement "step-up verified live" is not made here until a real 401 step-up has been seen.

## Nurse and admin (pavithra.v2022ece@sece.ac.in)

The nurse sign-in window showed "Set up your authenticator app" instead of skipping it, so the dev-only switch was not active in that window. The switch was built and worked for front desk (banner shown, no enrolment). Likely cause (not proven): my `git checkout`/merge of main at 3:58 pm rewrote `vite.config.ts`, and the dev server restarted itself ("vite.config.ts changed, restarting server") around that time. The user then enrolled this account with their phone and typed codes. Decision (user): from here on the doctor and every other pass use enrolment and typed codes; the dev-only switch stays in the code, off by default, dev-only.

The nurse read-only walk then recorded nothing: the browser window was closed before the sign-in finished (`.session-bar` never appeared). Nurse and admin screens are NOT walked yet. Redo needed (one sign-in as the nurse account, which is now enrolled; pick the nurse role, then the admin role).

## Doctor (pavithravenkatesh1974@gmail.com), read-only walk, password only (dev-only skip switch ON)

Signed in with the password only: the banner showed and no enrolment screen appeared. 52 calls, 0 schema mismatches, 0 notes. Shell: "Active role: Doctor", TEST, Operating profile Demo.

| Check | Expected | Seen | Verdict |
|---|---|---|---|
| Referrals, tasks, scheduling lists, offers (with `message_id`) | 200 | 200 | pass |
| Reviewer view `GET /referrals/{id}/review-view` | 200, or 404 when there is no proposal | 404 for the referral the walk opened | note: documented as a valid answer; the screen text was not read |
| Dashboard, portal claims, accounts, reviewer stats, cost canvas, voice sessions, recovery, grievances, possible duplicates | 403 for a doctor | 403 | pass. The walk tool visits every route directly; the console itself does not offer these to a doctor (Identity shows no duplicates list: `CAN_SEE_DUPLICATES` is front desk only). |
| Empty lists (row shapes unchecked) | | review tasks, holds, bookings, alerts, autonomy history | coverage gap |

Doctor writes (review a referral proposal, acknowledge and resolve a task, record a decision) were not done.

## Nurse and admin read-only walks, redone (password only, dev-only switch ON)

- Nurse (pavithra.v2022ece@sece.ac.in, role "Nurse"): 54 calls, 0 mismatches. 403s only on routes the walk visits directly that a nurse is not offered (portal claims, voice sessions, possible duplicates).
- Admin (same account, role "Admin", a separate sign-in because the role is chosen per sign-in): 50 calls, 0 mismatches. 403s: portal claims, voice sessions (needs second factor per the backend list), possible duplicates.
- Both: empty lists (review tasks, holds, bookings, alerts, autonomy history), so those row shapes are unchecked in these two walks.

## Doctor writes, recorded by hand (13 Nov 2026, about 10:50 to 10:56Z), human decisions clicked by the user

52 calls; every write carried a key; 0 schema mismatches. The writes mostly did NOT succeed:

| Call | Status seen |
|---|---|
| POST `/referrals/{id}/review` | 404 four times, then 409 twice; again 404 four times and 409 twice later |
| POST `/referrals/{id}/run` | 200 twice (between the 404s and the 409s) |
| GET `/referrals/{id}/review-view` | 404 |
| POST `/review-tasks/{id}/acknowledge` | 200 once |
| POST `/review-tasks/{id}/resolve` | 409 eight times |

Not yet explained. Asked careflow-cb for the meaning and the exact `detail` of each, and asked the user for the screen text. No frontend defect is claimed until the screen text is known: the console shows fixed messages per problem kind, never the backend text, so the wording on screen has to be compared with the "You should see" lines. Open.

## Doctor writes, scripted (13 Nov 2026, 11:03 to 11:18Z, password only; the user only signed in)

The decisions followed the user's explicit instruction, on fictional dev data. Any decision row made here must NOT be used as a training label (project rule 9: only real human decisions are labels).

| Step | Screen text | Route, status, backend `detail` | Verdict and cause |
|---|---|---|---|
| Survey of 4 referrals | the referral with no proposal (8524ed0e) showed "There is no proposal to show for this referral." and "The decision is offered only when the proposal can be shown. If this referral was only just run, wait a moment and reload the page." | GET `/review-view` 404, "not found" | Expected (no run yet). The gating added in this pass hid the decision form. |
| 1. Run the waiting referral (8524ed0e) | "The run was queued. Job: 7a784bc6…" | POST `/run` 200, 3.4 s | Pass. The reviewer view became readable about 60 s later (about 20 s in the earlier run). |
| 2a. Approve 404d7f72 (seeded) | confirmation "Record this decision? You are about to record the decision "Approve" for this referral. It is kept under your name.", then "That could not be found." | POST `/review` 404, "not found" | Expected. Backend: the seeded proposals have no graph thread, so review-view reads the proposal (200) while the review route finds no run (404). A seed limitation, not a route defect. Run must not be pressed on seeded referrals (it would fail and dead-letter). |
| 2b. Approve 8524ed0e (clean run) | "The referral is not waiting for a decision, or a different decision is already recorded. Refresh and try again." | POST `/review` 409, "this referral is not waiting for a decision", 9.4 s, response 11:17:40.951Z | BACKEND DEFECT (backend agrees it is one if this answers 409): the form was offered and the review view answered 200, but the referral page showed State "completed" afterwards. 16e5a640 has the same stuck state. Sent to careflow-cb with the exact time. Re-test when they say it is deployed. |
| Reject (16e5a640) | "The decision was recorded." | POST `/review` 200 with the reason "other" | Pass after the backend fix (see the re-test below). |
| 3. Hold task (referral hold a2a7ba07 on 16e5a640) | "Someone else is holding this task. Only the person who acknowledged it can close it." | none (Resolve not offered) | Pass. Acknowledged by account 67625a81…, so the doctor cannot resolve it. Before this pass the console offered Resolve and got 409 eight times (a frontend defect, fixed). |
| Acknowledge another task | acknowledged earlier by "You" (fc27d7e1, 200) | POST `/acknowledge` 200 | Pass. |

Re-test after the backend fix (df04754: "resume a run cut off before its review pause"; cause: on this slow link the worker's time budget cut two runs after the proposal was saved, so the retried job saw "finished"):
- Run pressed again on 8524ed0e (11:23:58Z) and 16e5a640 (11:24:08Z), both 200. Approve on 8524ed0e: POST `/review` 200 at 11:27:02Z (49.7 s), screen "The decision was recorded." and the referral then shows State "completed" (after the decision, which is right). PASS.
- Reject on 16e5a640: an earlier attempt (11:43Z) never found the decision form (cause not found; the backend says every read answered 200 and the thread was healed). A diagnostic run at 11:47Z showed the form fine, and the review-view and referral responses for 8524ed0e and 16e5a640 have the SAME shape (keys and types), so there is no payload difference. Reject needs a reason: the form lists the clinic's reason codes (missing information, other, unsafe, wrong card; "optional for approve"), and the Record decision button stays disabled until one is chosen. With the reason "other" (the neutral one, chosen for this synthetic test): POST `/review` 200 at 11:52:20Z, screen "The decision was recorded." PASS.
- Cross-referral state bug found by this re-test (frontend, fixed, regression test added): moving from one referral to the next kept the first one's "The run was queued" / "The decision was recorded" messages and hid the next one's decision form. The detail pages (referral, task, merge, patient record) are now keyed by id.

Reason codes: CORRECTION: the clinic does have reason codes (4: missing information, other, unsafe, wrong card). The first scripted runs never chose one, which is why the Record button stayed disabled on Reject. Approve sends none (optional).

Frontend defects found and fixed in this pass (tests added):
1. The decision form was offered on a referral with no run, or whose run was not ready (404 and 409 on `/review`). It now appears only when the reviewer view can be read, with a calm note otherwise.
2. Run was offered on every referral, though it does nothing once a run exists. Nurse and doctor now see it only when the reviewer view says there is no proposal (404); front desk cannot read that view, so it still sees Run and the backend decides.
3. Resolve was offered on a task acknowledged by someone else (409 eight times). It is now replaced by a short explanation.

Nurse and admin read-only walks (scripted by the same tool, password only) were already done earlier in this pass: nurse 54 calls and admin 50 calls, both with 0 mismatches.

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

# Second live pass (14 Nov 2026)

Backend: `127.0.0.1:8000` (started by the user from the updated main, all migrations applied). Dev server `127.0.0.1:5173/staff/`, started with `VITE_DEV_SKIP_STAFF_MFA=1` and `CAREFLOW_API_BASE_URL` set in the shell for this run only (no file). The user typed the password; no credential, token or storage was read or recorded.

| Step | Result | Cause |
|---|---|---|
| Dev server against 8000, dev-only switch ON | pass | `GET /readyz` answered 200 (JSON) through the dev proxy. |
| Front desk sign-in (pavithravenkatesh341@gmail.com, password only) | signed in, then every staff call refused | `GET /v1/iam/me` 200, then `GET /referrals`, `/review-tasks` (four times), `/scheduling/providers`, `/scheduling/locations` all 401. The console treats any other 401 as "sign in again", so the walk ended in the sign-in screen. Cause, read from the backend code (`careflow/app.py`, `_require_mfa`): `CAREFLOW_REQUIRE_AAL2` defaults to ON, and with it on EVERY staff route except `/v1/iam/me` answers 401 with `WWW-Authenticate: mfa` to a password-only session. This backend run has it on; the 8010 backend of the first pass had it off (allowed only in profile demo with synthetic data). The dev-only switch only skips the console's enrolment screen; it cannot satisfy the backend. |
| Nurse, admin and doctor sign-ins | not run | Same cause: each would only 401. Not asked to sign in three more times for nothing. |
| Scheduling offer draft, rule list and authoring, medications, patient requests, supervisor, staff views of a patient record, voice sessions (admin), alerts, referral review | not tested live: needs second factor | Every one of them is a staff route behind the check above. The supervisor also has no account in the list given (front desk, nurse and admin, doctor). |
| Referral review: a model-made proposal | checked in code and unit tests, not live | See below. |
| Patient request status buttons from `allowed_next` | nothing new | The snapshot (main at 9d3496c) has no `allowed_next` and no transitions on the patient request rows; the guessed set stays, `invalid_transition` is the fallback. |

## Frontend defects found and fixed in this pass

1. **The live walk reported a pass when the backend refused everything.** The run ended "1 passed" with 8 calls, six of them 401. It now prints a line saying how many calls answered 401 and why, and fails the walk (`e2e-live/live.spec.ts`).
2. **Referral review: no clear "suggestion" label.** The reviewer view and the referral's proposal section showed the engine, confidence, probabilities and mode with no statement that this is a suggestion. Both now say, above the figures: "This is a suggestion made by the system, shown as the system recorded it. It is not a decision: a person decides." (English and Tamil, Tamil unreviewed.)
3. **Referral review: values were reworded or rounded.** The mode word was passed through a translation lookup and probabilities and the confidence were rounded to whole percents. The mode (for example `shadow`) is now shown exactly as the API sends it, and figures keep one decimal place (87.6% is not shown as 88%). A missing engine, confidence, probability, rule or mode is still "Not recorded", never zero.

The API's reviewer view has these fields: `engine` (the label), `confidence`, alternatives with `probability` (null when the engine gave none), `triggering_rule`, and `proposal.mode`. It has no "role" field; if the owner expects one, it is not in the schema. Tests: `tests/console-f7.test.tsx` (suggestion label, mode, engine, one-decimal figures, "Not recorded").

## What is needed to run the pass

Either restart the backend with `CAREFLOW_REQUIRE_AAL2=false` (the 8010 setup; demo profile and synthetic data only), or sign in with real authenticator codes (the dev accounts have no authenticator today). Then the four walks (front desk, nurse, admin, doctor) are redone with the walk now covering: patient requests, red-flag rules and the rule authoring form (opened, never saved), up to six referral details, and the patient record with its medications panel.

# Second live pass, redone (14 Nov 2026, second half)

Backend `127.0.0.1:8000` restarted by the user with `CAREFLOW_REQUIRE_AAL2=false` (demo, synthetic data only). Dev server `127.0.0.1:5173/staff/` with the dev-only skip switch on (set in the shell for the run only). The user typed every password; no credential, token or storage was read. All walks are READ-ONLY (the tool fails if it makes a write): no screen was changed, no rule was drafted or saved, no request status was changed.

| Step | Result | Cause |
|---|---|---|
| Front desk (pavithravenkatesh341@gmail.com) | pass | 58 calls, 0 schema mismatches. Scheduling lists, offers, slots, bookings, waitlist, alerts, patient requests (200), identity merges and one detail, possible duplicates, break-glass, autonomy mode, portal claims, one patient record. 403 only on routes a front desk is not offered (dashboard, care, rules, accounts, reviewer stats, cost, recovery, grievances, autonomy history). |
| Nurse (pavithra.v2022ece@sece.ac.in) | pass | 73 calls, 0 mismatches. Referral details and ten reviewer views (200), review tasks and one task detail, scheduling lists, alerts, care gaps and plans, red-flag rules (read), accounts, autonomy, reviewer stats, cost, recovery, grievances, one patient record. 403: offer draft, patient requests, portal claims, voice sessions, possible duplicates (not the nurse's routes). |
| Admin (same account, Admin role, a separate sign-in) | pass | 62 calls, 0 mismatches. 403: offer draft, patient requests, portal claims, possible duplicates (not the admin's routes). |
| Doctor (pavithravenkatesh1974@gmail.com) | pass | 73 calls, 0 mismatches. Referral details, ten reviewer views, red-flag rules (200), care. 403 only on admin and front-desk routes. |
| Scheduling offer draft | pass (front desk) | The first front desk walk skipped it: the walk tool did not wait for the visit types to load (fixed). A second front desk walk got `GET /scheduling/offers/draft` 200 with 0 schema issues. 403 for nurse, doctor and admin, as the route says. Screen text of the draft was not read. |
| Red-flag rule list | pass | Doctor and admin 200; front desk and nurse-less roles as expected. The rule authoring form was opened by the walk and never saved (nothing is written). |
| Patient requests (front desk) | pass | `GET /patient-requests` 200, 0 schema issues. The status route was not called (a write). |
| Alerts, referral review | pass | Alerts 200 (empty list: row shape unchecked). Referral review: see the next table. |
| Medications | not exercised | The patient record opened 200 in the nurse and doctor walks but `GET /medications` was never called. Cause not yet known: most likely the record opened was a merged-away one (its medications panel is hidden); the walk could not tell. The tool now opens up to six distinct patient records until the panel shows and says what each showed. Needs one more nurse walk. |
| Voice sessions (admin), consent and voice settings for staff | not tested live: needs second factor | `GET /voice/sessions` answered 403 to admin: the route says "403 unless you are an admin there with a verified second factor", a database-level check. Not a failure. |
| Staff views of a patient (profile-related) | pass | `GET /patients/{id}` 200 in every walk. The full-record view and break-glass reads were not exercised (second factor / writes). |
| Front desk supervisor | not run | Waiting for the user to say the role is granted. |
| Patient request status buttons from `allowed_next` | nothing new | No `allowed_next` or transitions in the snapshot (checked at main c75110e). |

## Referral review of a model-made proposal, as shown (doctor walk, dev data)

The doctor walk read what the reviewer view shows on four referrals (file `frontend/live-report/review-observations-doctor.md`, synthetic data): the suggestion note "This is a suggestion made by the system, shown as the system recorded it. It is not a decision: a person decides."; Task kind "Triage proposal"; **Mode: assisted** exactly as sent; Abstained: No; Candidates: 2; **Engine, Confidence and Rule that triggered review: "Not recorded"**; two alternatives (`demo-synthetic-card-A@1`, `demo-synthetic-card-B@1`) each with probability "Not recorded". So on this data the backend sent no engine, no confidence and no probabilities, and the screen said so instead of showing zero. An engine label and real probabilities were NOT seen live because the demo proposals carry none; those cases are covered by the unit tests only.

## Frontend defects found and fixed in this half

1. The live walk's offer-draft step did not wait for the visit types, so it silently skipped the draft (fixed: it waits up to 15 s).
2. The live walk preferred the patient record opened from a merge (possibly merged-away, so no medications panel) and saved the report before the last responses were recorded (fixed: it tries up to six records and waits 2.5 s before saving).
3. The reviewer-view observation is now part of the walk, so "shown exactly as sent" can be read from a real answer.

No defect was found in the screens themselves: 0 schema mismatches in 397 calls across the six walks, and every refusal was a role the walk visits directly or a database-level second-factor check.

## Nurse walk 3 (14 Nov 2026): medications exercised

Run at the user's request. Nurse (pavithra.v2022ece@sece.ac.in), password only, read-only, 83 calls, 0 schema mismatches. The walk now opened the first patient record it had seen on a referral: the medications panel was shown (no merged-away notice) and `GET /v1/staff/medications` answered **200 with 0 schema issues**. Medications: **pass** (read only; recording, correcting and stopping were not exercised, they are writes). This also confirms the earlier "not exercised" cause: the record opened from a merge was a merged-away one, which hides the panel.

Still open: re-run the doctor referral review walk once the backend's decision stage makes demo proposals carry an engine, confidence, rule and probabilities (the user will say when). The front desk supervisor walk waits for the role to be granted. Patient request status buttons stay as they are (no `allowed_next` from the backend).

# Front desk supervisor walk (14 Nov 2026)

Account pavithravenkatesh341@gmail.com, which now holds `front_desk` AND `front_desk_supervisor` in the dev clinic. The user typed the password and picked "Front desk supervisor" in the role picker (shell: "Active role: Front desk supervisor", TEST, Demo). Read-only walk, no writes. 62 calls, **0 schema mismatches**. Backend `127.0.0.1:8000` with `CAREFLOW_REQUIRE_AAL2=false`; dev-only skip switch on.

| Check | Result | Cause |
|---|---|---|
| Routes the supervisor reaches that plain front desk does not | none | Every route answered the SAME as in the plain front desk walk (14 Nov, second walk): all the 200s (referrals, review tasks, scheduling lists, offers and the offer draft, slots, bookings, waitlist, alerts, patient requests, portal claims, identity merges and duplicates, autonomy mode, a merge detail, one patient record) and all the 403s (dashboard, care gaps and plans, red-flag rules, accounts, autonomy history and proposals, reviewer statistics, cost, recovery, grievances, voice sessions). `GET /break-glass/mine` was not called in this walk (the plain front desk walk did). |
| Patient requests and the offer draft for the supervisor | 200, expected | They are front desk routes, and this account also holds `front_desk`. This is NOT something the supervisor role unlocks. |
| 403s | all expected | Identical to plain front desk: admin, doctor and nurse routes, and voice sessions (needs a verified second factor). |
| Anything the supervisor role unlocks | not seen, and not separable here | Two reasons. (1) The account holds both grants, and the console does not send the active-role header (`SEND_ACTIVE_ROLE_HEADER` is off), so the backend authorizes with ALL of the person's grants; picking "Front desk supervisor" in the picker changes what the console shows, not what the backend allows. (2) No route description in the snapshot names the supervisor role. The role appears only in task assignment (`assigned_role`), and the review-task list was empty or not distinguishable by role in this data. |

**What this means for the earlier decision** (the supervisor sees the task inbox only): that is a console convenience, and it holds: the console offers the supervisor only Tasks. The live result shows the backend's own enforcement for a supervisor-only person has NOT been exercised, because every available account that holds the role also holds `front_desk`.

To test the supervisor role on its own, either create an account that holds only `front_desk_supervisor`, or switch on the active-role header (`SEND_ACTIVE_ROLE_HEADER`, which needs the backend's migration 0028 live check). Neither was done: no write, no flag change.

## Supervisor item: closed (decided 14 Nov 2026)

**No supervisor-only person can exist; supervisor = front desk plus a console view.** By design a front desk supervisor always holds `front_desk` too, so the walk of 14 Nov (account holding both grants, merged at 8631640) IS the real supervisor case. The supervisor-only walk is cancelled; no supervisor-only test account and no active-role header switch-on are needed for it.

# Doctor referral review walk with a real engine and probabilities (14 Nov 2026)

Doctor (pavithravenkatesh1974@gmail.com), password only, **read-only: 86 calls, 0 writes, 0 schema mismatches**. Backend `127.0.0.1:8000` with the new review-view fields; dev-only skip switch on. The user typed the password. The first run of this walk (62 calls) opened no referral details because the walk tool read the referral list before its rows had arrived (fixed: it waits up to 15 s for the rows and up to 8 s for each reviewer view); the second run below is the one that counts.

The proposal the user named (id `1939f256-c052-5e1c-8abe-de1bd35eeb9c`; the screen does not show a proposal id, so the referral was identified by its values). Referral detail 1 in the list showed it. What the reviewer view showed, taken from the screen text (`frontend/live-report/review-observations-doctor_review_2.md`), against the numbers the user gave:

| Field | Expected (given) | Screen showed | Match |
|---|---|---|---|
| Engine | `jev` (via openrouter) | **jev** | yes. "via openrouter" is not a field of the API's reviewer view, so the screen cannot show it. |
| Model | `typesafe/jev-router` | **typesafe/jev-router** | yes |
| Confidence | 0.51 | **51%** | yes |
| Decision rule | `chosen_option_ranks_first` | **chosen_option_ranks_first** | yes (shown under "Decision rule", `decision_rule`) |
| Alternative `demo-synthetic-card-A@1` | 0.51 | **51%** | yes |
| Alternative `demo-synthetic-card-B@1` | 0.49 | **49%** | yes |
| Rule that triggered review | not given | **Not recorded** | `triggering_rule` is null for this proposal: the separate "rule that triggered review" field, not the decision rule |
| Mode, abstained, candidates | not given | assisted, No, 2 | as the API sends them |
| Suggestion note | | "This is a suggestion made by the system, shown as the system recorded it. It is not a decision: a person decides." | present |

The other six referrals of the clinic (details 2 and 4 to 7) showed Engine, Model, Decision rule, Confidence and both probabilities as **"Not recorded"** (their proposals predate the new decision stage), so the null case also behaves live: no zero, no blank. Referral detail 3 was read before its reviewer view had loaded, so its observation is empty (a walk timing gap, not a screen result); it is not counted.

Verdict: **pass**. The screen shows engine, model id, confidence, decision rule and each alternative's probability exactly as the API sent them, and "Not recorded" for null. Nothing was written.

# Not tested live (18 Nov 2026)

These screens were built after the second live pass and have only been tested against the console's own fake API (unit tests, and the browser tests with axe at 320, 768 and 1280 px in English and Tamil). They are listed as **not tested live** until the owner says the demo is ready. No walk was run for them, and none will be run until then.

| Screen | Why not live yet |
|---|---|
| Patient visit preparation (patient "What to prepare", staff read-only view) | Off by default on the backend (`CAREFLOW_PATIENT_VISIT_PREP`); every route answers 503 until switched on. The staff view also needs a recent second factor. Waiting for the backend's demo switches. |
| Family links (patient section, front-desk screen) | Off by default (`CAREFLOW_FAMILY_LINKS`) and deny by default: even with the switch on, every route answers 503 "the clinic has not entered its rules" until counsel's rule values are entered (the table ships empty). The staff routes also need a recent second factor. Waiting for the backend's demo switches and the demo family-rule script. |
| Cards and templates editor (`/knowledge`) | Off by default (`CAREFLOW_KNOWLEDGE_EDITOR`); every route answers 503 until switched on, and every write needs a recent second factor. Waiting for the backend's demo switches. |
| Staff message screen (`/messages`) and patient labels ("Show names") | Built after the second live pass. Reading a message and screening it write audit rows, and labels need the demo profile with synthetic data. Not walked yet. |
| Patient portal: appointments, messages, family links, requests, profile, notices | The patient sign-in is not set up for live walks (phone sign-in); the screens were tested against the fake API only. |

When the owner says the switches and the demo family-rule script are ready, the walks to run (read-only, the user types the password) are: nurse (visit preparation staff view, cards and templates as a reader), doctor (cards and templates, including a second person approving), front desk (family links), and the same four screens' 503 states with the switches still off.

## Third live pass (19 Nov 2026): visit preparation, family links, /knowledge, messages, labels, patient portal

Backend `127.0.0.1:8000` with migrations 0089-0093, the three switches on, `CAREFLOW_REQUIRE_AAL2=false`, demo placeholder family-link rules. Dev server with the dev-only skip switch on. The user typed every password. Read-only: no writes, and the only non-GET is the names read (`POST /v1/staff/patients/labels`, after its confirmation, no key by design).

| Walk | Calls | Schema mismatches | Writes |
|---|---|---|---|
| Front desk (pavithravenkatesh341@gmail.com) | 76 | 0 | 0 |
| Nurse, first run | 66 | 0 | 0; **discarded**: from call 55 the backend answered 502 for the rest of the walk (11 calls), so none of the new screens were seen |
| Nurse, second run (pavithra.v2022ece@sece.ac.in) | 98 | 0 | 0 |
| Doctor (pavithravenkatesh1974@gmail.com) | 99 | 0 | 0 |
| Patient portal | 0 | n/a | 0 |

| Screen | Result | Detail |
|---|---|---|
| Messages (list, a conversation's headers, one message read after its confirmation) | pass | Front desk, nurse and doctor: `GET /v1/staff/messages/threads`, `.../threads/{id}/messages`, `/v1/staff/messages/{id}` all 200, 0 mismatches. |
| Patient labels (Show names) | pass | `POST /v1/staff/patients/labels` 200 after the confirmation, in all three walks. |
| Family links, front desk page | not tested live: needs second factor (the database checks for it; password-only dev sessions are refused) | `GET /v1/staff/family-links` answered 403 to the front desk. Recorded by the owner's decision (20 Nov 2026). The walk tool now stores the response's reason sentence for every 403 (report section "Refusals (403) and the reason each gave"), so the cause is on record next time. |
| /knowledge, nurse and doctor | not tested live: needs second factor (the database checks for it; password-only dev sessions are refused) | `GET /v1/staff/knowledge/drafts` and `/items` answered 403 to both the nurse and the doctor (front desk 403 is expected). Recorded by the owner's decision (20 Nov 2026). |
| Visit preparation, staff view | not tested live: needs second factor (the database checks for it; password-only dev sessions are refused) | `GET /v1/staff/visits/upcoming` answered 200 for nurse and doctor (403 for front desk), but no visit was opened, so `GET /v1/staff/visits/{id}/prep` was never called; its write-side checks need the same second factor. Recorded by the owner's decision (20 Nov 2026). |
| Visit preparation, patient view; family section; profile; notices; requests; appointments; messages (portal) | not tested live | Phone sign-in could not send a code (the portal showed its fixed "The code could not be sent" message), so no portal screen was reached and no portal call was made. Likely cause is on the hosted Supabase side (phone sign-in or an SMS provider not set up for the dev project); not confirmed. |

Walk-tool fixes in this pass: the patient-labels read is no longer reported as "write without key" (it takes no key by design); the front desk, nurse and doctor walks open the new screens and wait for them to settle.

To finish: a second-factor-capable pass (or a backend with the second-factor check relaxed for these routes) for family links, /knowledge and the staff prep view, and a patient test number for portal phone sign-in.

### Patient portal walk: waiting on a demo sign-in (20 Nov 2026)

Phone codes need a paid SMS provider that is not approved, so the patient portal walk is waiting for the demo email and password sign-in (build-time switch `VITE_DEV_PATIENT_PASSWORD_LOGIN=1`, dev server only, banner "Demo sign-in"; see the checklist). It runs once the backend has linked a synthetic patient and the user has created the sign-in. Until then every portal screen stays "not tested live: phone sign-in could not send a code".

## Demo link of the synthetic portal patient (20 Nov 2026): in progress, stopped at the stage check

The user types every password and code. The only writes authorised were the demo claim path on the synthetic patient (DEMO Portal Patient, born 1990-01-01), the identity confirmation if one was needed, and the claim. What each screen said is in `live-report/link-*.md` (not committed; they hold screen wording only).

| Step | Result | Exact screen text and calls |
|---|---|---|
| Portal demo sign-in (email and password form) | pass | The form under the "Demo sign-in" banner opened a session on four attempts. Account page: "Where your account is: Created", "Linked to a record: No", one notice "DEMO ONLY - Care · version 1". |
| Care-delivery consent (write 1) | recorded, status code not captured | The run that clicked Grant closed before the answer arrived (my script waited about a second). The next run showed the notice's Grant button disabled, so the decision was recorded. This write was made on the synthetic patient only. |
| Activate account (write 2) | pass: result word `activated` | The screen said "Your account is active." That sentence comes from the result word (`activated` or `already_active`), not from the HTTP status: `consent_required` is also a 200 and has its own sentence. `POST /v1/portal/activate` 200. The screen still read "Where your account is: Created" afterwards, and no fresh read of the account followed. The backend's explanation (20 Nov 2026): "Created" means invited and not yet activated, so the account may not have been active at that moment; "New patient" means active with no claim yet. |
| Front desk sign-in with the authenticator code | pass | Front desk (second factor enrolled): `GET /v1/iam/me` 200. |
| Find the demo patient (front desk) | pass | `POST /v1/staff/patients/search` 200: exactly one record. |
| Identity confirmation | not needed, no write | The record page says "Verified", "Registered via Front desk", "Latest identity event: None". There is no "Confirm identity" button on a verified record, so nothing was confirmed. 3 calls, all 200, no 403. |
| Patient stage on a fresh page load | stopped by my check; the check was wrong | After a fresh sign-in and a second fresh page load the stage read **"New patient"**: "Where your account is: New patient", "Linked to a record: No". `GET /v1/portal/me` 200 and `GET /v1/portal/consents` 200 both times, no refusal. My walk stopped because it expected "Active". The backend's correction (20 Nov 2026): "New patient" is the correct stage to claim from. The walk now claims from "New patient" and stops only on "Created" (or "Waiting to be activated"). The claim was not made in that run. |
| Claim (write 3) | not done | Not attempted: the stage check stopped the run. |
| Read-only portal walk | not tested live | Waits for the stage to read Active and the claim; no portal call beyond the two reads above has been made. |
| Front desk family links page | pass on load; row shape not checked | Second factor enrolled and fresh code: `GET /v1/iam/me` 200, `GET /v1/staff/family-links` 200 (the earlier password-only visit was refused with 403, so the second factor is what opens it). The screen said "There are no family link requests." No schema mismatch, no refusal, no writes. The list was empty, so no row was checked. |

Writes made so far, all on the synthetic patient: (1) the care-delivery consent, (2) `POST /v1/portal/activate` 200. No claim, no identity confirmation, no approval.

Walk-tool fixes in this pass: each write now waits up to 30 seconds for its dialog to close; steps already done are skipped; the claim step checks the stage on a fresh page load first; every 403's reason sentence is stored.

### Status codes of writes (20 Nov 2026)

The care-delivery consent write's status code was **not captured**: the run that clicked Grant closed the browser before the answer arrived, and the walk tool only recorded answered calls. The next run showed the notice's Grant button disabled, so the decision was recorded, but the code is unknown. The walk tool now remembers every write when it is **sent** and lists any whose answer had not arrived (or whose request failed) in the report under "Writes sent with no answer received (status unknown)", and in `unanswered-writes<label>.md`; answered writes already list their status code and, for a 403, the reason sentence.

Also from this round: the backend now has `activation_required` and `identity_not_confirmed` answers on the claim path, and the patient record offers "Confirm identity" on a verified record with no confirmation on record (backend request 44, built).

## Demo link, after the account reset (20 Nov 2026): identity confirmed, claim blocked on the clinic code

Writes this round, both on the synthetic patient: (1) **identity confirmation**: `POST /v1/staff/patients/{patient_id}/confirm-identity` **200** (the front desk record page said "Verified", "Latest identity event: None"; the new rule showed the button anyway); no 403, no unanswered writes. (2) No claim yet.

**Frontend defect found and fixed on the way:** with the stage "New patient" the portal showed only the consent section and "Claiming a record ... become available once your account is active": the claim form (and profile, notices, requests, bookings, messages, family section) was hidden for every stage except `active`. The stages are registered (invited), new_patient (activated, no claim yet), pending_activation (a claim waits for a person) and active (a record is linked), so a new_patient account could never reach the claim form. Fixed (commit 50b1bd6): only `registered` is held back; a pending_activation account sees the claim section with its button off. Unit tests cover all three stages.

**Claim: not made. Stopped by design.** After a fresh sign-in and a fresh page load the stage reads "New patient", and the claim form now shows, but it asks for a **clinic code** (the fallback when the public clinic read answers 404: "the owner has marked no clinic public"). The walk was not given a clinic code, so no claim was made. Needed from the owner: either mark the demo clinic public so `GET /public/clinic` answers it, or give the demo clinic code to type into the form.

Portal screens as seen on that visit (stage "New patient", read-only; 24 calls on the second and third loads, **0 schema mismatches, no 403, no write**):

| Portal screen | Result | Exact screen text and calls |
|---|---|---|
| Home / account | pass | "Where your account is: New patient", "Linked to a record: No". `GET /v1/portal/me` 200 on both loads. |
| Notices and decisions | pass | One notice "DEMO ONLY - Care · version 1" (already granted). `GET /v1/portal/consents` 200. |
| Profile | pass | "Name on your record: Not available"; "The hospital has not set up a list of choices yet." `GET /v1/portal/profile` 200. |
| Notices (notifications) | pass on load | `GET /v1/portal/notifications` 200 (list content not recorded). |
| Requests | pass on load | `GET /v1/portal/requests` 200. |
| Appointments and booking list | not tested live: the switch is off | `GET /v1/portal/booking/bookings` 503. The screen said "Booking online is not turned on at this hospital yet." No 403, so no refusal reason. |
| Messages | not tested live: the switch is off | `GET /v1/portal/messages/threads` 503. The screen said "Messages are not turned on at this hospital yet." |
| Visit preparation | not tested live | No appointment to open it from (the booking list answered 503). |
| Family section | pass on load | `GET /v1/portal/family-links` 200 on the fresh load; no request made. |
| Complaints | pass on load | `GET /v1/portal/grievance-categories` 200, `GET /v1/portal/grievances` 200. The public complaint form's `GET /public/grievance-categories` answered 404. |
| Voice | pass on load | `GET /v1/portal/voice/preference` 200. |
| Claim | not done | See above: blocked on the clinic code. |

The two stage readings: the activation earlier said "Your account is active" for the result word `activated` (not for the HTTP 200; `consent_required` is also a 200).

## Demo link completed (20 Nov 2026): identity confirmed, claim linked directly, portal walk

The user typed every password and code. The demo clinic is public ("Demo Clinic"); the earlier 404 on `GET /public/clinic` was **a frontend dev-server gap, not the backend**: the dev proxy forwarded only `/v1` and `/readyz`, so `/public/*` never reached the backend. `/public` is now proxied in dev (vite.config.ts), and the claim form fills the clinic from the public read (no clinic code was typed).

**The only writes, both on the synthetic patient, each with its status code:**

1. Identity confirmation (front desk, second factor, exactly one record found for DEMO Portal Patient, 1990-01-01): `POST /v1/staff/patients/{patient_id}/confirm-identity` **200**. The record page said "Verified", "Registered via Front desk", "Latest identity event: None" before; no 403, no unanswered write.
2. The claim (patient, one attempt, stage "New patient" on a fresh load): `POST /v1/portal/claims` **200**. The screen said **"Your record is linked."** (the outcome `claimed`: linked directly, not `pending_review`; no 403, no origin-mismatch refusal). The claim button was then off. Afterwards: "Linked to a record: Yes", "Name on your record: DEMO Portal Patient", and a fresh load said "Your account is linked to a record." The stage line still reads "New patient" (a record bound without a front-desk approval stays new_patient: backend pre-launch note 66).

**Portal walk (read-only, after the claim): 49 calls over the whole run, 0 schema mismatches, no 403, no unanswered write.** Sections on the page: consent, claim, profile, notifications, requests, booking, messages, family, grievance, voice, my data.

| Portal screen | Result | Exact screen text and calls |
|---|---|---|
| Home / account | pass | "Where your account is: New patient", "Linked to a record: Yes" after the claim. `GET /v1/portal/me` 200 (read after every write). |
| Notices and decisions | pass | "DEMO ONLY - Care · version 1" already granted. `GET /v1/portal/consents` 200. |
| Profile | pass | "Name on your record: DEMO Portal Patient" (after the claim; "Not available" before); "The hospital has not set up a list of choices yet." `GET /v1/portal/profile` 200. |
| Notifications | pass on load | `GET /v1/portal/notifications` 200 (content not recorded). |
| Requests | pass on load | `GET /v1/portal/requests` 200. |
| Appointments and booking list | not tested live: the switch is still off | `GET /v1/portal/booking/bookings` 503 and `GET /v1/portal/booking/slots` 503; the screen said "Booking online is not turned on at this hospital yet." No 403, so no refusal reason. |
| Messages | not tested live: the switch is still off | `GET /v1/portal/messages/threads` 503; "Messages are not turned on at this hospital yet." |
| Visit preparation | not tested live | No appointment on the page to open it from (the booking list answered 503). |
| Family section | pass on load | `GET /v1/portal/family-links` 200, section on the page; no request made (no write). |
| Complaints | pass on load | `GET /v1/portal/grievance-categories` 200, `GET /v1/portal/grievances` 200. The public form's `GET /public/grievance-categories` answers 503 (not set up: the screen says nothing can be sent). |
| Voice | pass on load | `GET /v1/portal/voice/preference` 200. |
| Claim | pass | As above: one write, 200, "Your record is linked." |

Earlier in this round the front desk family links page passed with the second factor (200, "There are no family link requests.").

### Public complaint form: what the screen shows while its category read answers 503 (20 Nov 2026)

Read on the signed-out portal page against the live backend (a read only; nothing typed, nothing sent). `GET /public/grievance-categories` answers **503**. The "Send a complaint without signing in" section then shows: its heading, "This goes to the hospital without signing in. A person at the hospital reads it.", the Category label, and in place of the category list the fixed sentence **"Complaints without signing in are not set up at this hospital yet. Nothing can be sent."**, followed by the text box and the Send button, which has no category to send. No backend text is shown. Nothing was changed in the frontend; the backend is checking why the read answers 503.

## Booking and messages switched on (20 Nov 2026): no open time offered to the patient, so no booking

Backend restarted through demo_up.ps1 with booking and messaging on, slots and bookable visit types seeded, and the complaint fix. The user typed every password and code. **No new write was made: the one authorised booking was not made, because the patient was offered no open time.** (The writes of this whole demo remain the identity confirmation, 200, and the claim, 200, recorded above.)

**Public complaint form (signed-out page, read only, nothing sent):** `GET /public/grievance-categories` now answers **200**. The Category list shows "Choose a category" and eight categories: access to my data, complaint about care, complaint about staff, complaint about the system, correction of my data, erasure of my data, nominee, other. The Send button is off until a category is chosen and text is typed. Before the fix it answered 503 and the screen said "Complaints without signing in are not set up at this hospital yet. Nothing can be sent."

| Screen | Result | Exact screen text and calls |
|---|---|---|
| Portal: appointments and booking list | pass (empty) | `GET /v1/portal/booking/bookings` **200**, no appointments for DEMO Portal Patient. 0 schema mismatches. |
| Portal: open times to book | **finding, no booking made** | `GET /v1/portal/booking/slots` **200** but the list is **empty**: the response had `$.slots` with no entries, so no "Book" button was offered. The patient screen therefore says there are no open times. Not tested live: booking. The staff side (below) lists 12 slots for the clinic, so the seeded slots exist but are not offered to this patient. The call record keeps no slot statuses, so which filter hides them (patient-bookable visit type, time window, status) is not known from here. For the backend to check. |
| Portal: messages | pass (empty) | `GET /v1/portal/messages/threads` **200**, no conversations for this patient. Not tested live: opening a conversation, sending (no write was authorised). |
| Portal: visit preparation | not tested live | There is no appointment to open it from (no booking was made). Marking items was not attempted. |
| Front desk: scheduling | pass | Second factor: `GET /v1/staff/scheduling/locations` 200 (2), `visit-types` 200 (2), `providers` 200 (2), `slots` 200 (**12 rows**). 0 schema mismatches. |
| Front desk: visit readiness | refused, as expected for the role | `GET /v1/staff/visits/upcoming` **403**, reason given: "your role may not do this". The screen said "This is not available to your role." (a nurse and doctor section). No retry. |
| Front desk: staff view of the visit | not tested live | The upcoming-visits list was refused (403, above), and there is no booking to look at. |
| Front desk: messages | pass | `GET /v1/staff/messages/threads` **200**, 1 conversation listed (none of them opened). |
| Nurse and doctor: visit readiness, visit preparation (staff view), cards and templates | not tested live: needs second factor | Nurse and doctor have no authenticator yet; the database check refuses a password-only session. |

The patient walk of this round: 27 calls, 0 schema mismatches, no 403, no write. The front desk walk: 8 calls, 0 schema mismatches, one 403 (visit readiness, above), no write.

## Booking walk after backend 0099 and 0100 (20 Nov 2026): still no open time offered, reason word shown

The user typed the demo password. Read-only: **no write was made, so no booking** (the one authorised booking needs an open time to book).

| Screen | Result | Exact screen text and calls |
|---|---|---|
| Portal: open times | **finding: none offered, reason word shown** | `GET /v1/portal/booking/slots` **200** with an empty list. The screen (new fixed-wording state) said **"No open times at the moment. Please check again later."** with "Check again" and "Back to the top of the page": the sentence for the reason word `no_open_times` (booking is set up, but no open time exists for this patient right now). Not "Online booking is not set up for this clinic yet." and not "Booking online is not turned on at this hospital yet." Booking is therefore not tested live. |
| Portal: appointments list | pass (empty) | `GET /v1/portal/booking/bookings` 200, no appointments. |
| Portal: messages | pass (empty) | `GET /v1/portal/messages/threads` 200, no conversations. Opening one and sending: not tested live (no write authorised). |
| Portal: visit preparation | not tested live | No appointment to open it from. Nothing was marked. |
| Staff view of the same visit (front desk, second factor) | not tested live | There is no booking, so there is no visit to look at; this step was not run. (The front desk scheduling lists, run earlier, showed 12 slots in the clinic; why none is offered to this patient is the backend's to check.) |

27 calls, 0 schema mismatches, no 403, no write, no unanswered write. The `empty_reason` handling built from backend item 48 is therefore also seen working against the real backend: the screen showed the fixed sentence for the word, never backend text.

## The public home page found broken live, fixed (20 Nov 2026)

The user reloaded the signed-out home page and it still showed every placeholder. Read-only diagnosis: (1) the dev server was serving the latest code; (2) the dev proxy forwards `GET /public/clinic` and `GET /public/clinic/profile` and both answered **200** through port 5173: the clinic read has a name; the profile has text for about, address, opening hours and phone (non-null), two departments, `privacy_promise: null`, `placeholder: true`, features booking, messaging, offers, visit_preparation and family_links **true**, records **false**, languages en and ta. **The backend was not the cause** (the route exists, the demo values are seeded, the switches are on). (3) **Frontend defect:** in the development build the app's API base is an empty string (same origin, through the proxy) and the home page treated an empty base as "not configured", so it never asked, and a failed read fell back to placeholders silently. Fixed test first (commit 41e10fc): an empty base means the page's own origin; a failed read is now said in fixed words, 404 "The hospital's details are not available yet.", anything else "The hospital's details could not be loaded.", never backend text. Seen on the live page afterwards (read-only, headless): header and tab title "Demo Clinic"; the "Demo content" mark; booking, offers, messages and family shown as available and only "See your records after your first visit" marked "Coming soon" (records is false); the supplied texts shown and 3 placeholder boxes left (the privacy promise and two FAQ items); the same in Tamil. No command is needed from the user for this one.

## Booking walk with the user's existing booking (20 Nov 2026): visit preparation read, staff side read

**Write made before this run (not by this walk):** ONE booking for DEMO Portal Patient, made by the user in the portal (Confirmed, Demo Visit Type A, Demo Location North, 21 Nov 2026 9:00-9:30). This walk made **no write**, booked nothing and cancelled nothing.

| Screen | Result | Exact screen text and calls |
|---|---|---|
| Portal: appointments list | **pass** | `GET /v1/portal/booking/bookings` **200**, 1 appointment on the page (not cancelled). |
| Portal: open times | **pass** | `GET /v1/portal/booking/slots` **200**, **9 open times offered** now (the earlier empty list answered "No open times at the moment. Please check again later."). No booking made from them. |
| Portal: visit preparation for the booking | **pass** | "What to prepare" opened; `GET /v1/portal/visits/{booking_id}/prep` **200**; the screen said **"Nothing to prepare for this visit."** (correct: the clinic supplied no checklist items). Nothing was marked. |
| Portal: messages | pass (empty) | `GET /v1/portal/messages/threads` 200, no conversations to open. Sending not tested live (no write authorised). |
| Front desk (second factor): scheduling | **pass** | `locations` 200 (2), `providers` 200 (2), `visit-types` 200 (2), `slots` 200 (**22 rows**). |
| Front desk: the booking as staff see it | **pass** | `GET /v1/staff/scheduling/bookings` **200**, **2 rows** listed in the clinic's bookings. Patient names were not shown (no names read). |
| Front desk: visit readiness | refused, as expected for the role | `GET /v1/staff/visits/upcoming` **403**, reason given: "your role may not do this". The screen said "This is not available to your role." (a nurse and doctor section). No retry. |
| Staff view of the visit's preparation | not tested live: needs a nurse or doctor with a second factor | The front desk is refused on visit readiness, and nurse and doctor have no authenticator yet. |
| Front desk: message screen | **pass** | `GET /v1/staff/messages/threads` **200**, 1 conversation listed, none opened. |

Patient walk: 31 calls, 0 schema mismatches, no 403, no write, no unanswered write. Front desk walk: 12 calls, 0 schema mismatches, one 403 (visit readiness, above), no write, no unanswered write.

## Nurse and doctor second-factor walks (21 Nov 2026)

Both accounts now have an authenticator. The user typed every password and fresh code. Read-only: **no write was made** (no draft was written, because the user did not say so; nothing was approved or rejected).

| Screen | Nurse (pavithra.v2022ece@sece.ac.in, role Nurse) | Doctor (pavithravenkatesh1974@gmail.com, role Doctor) |
|---|---|---|
| Sign-in with the second factor | pass: `GET /v1/iam/me` 200 | pass: `GET /v1/iam/me` 200 |
| Visit readiness: the list of upcoming visits | **pass**: `GET /v1/staff/visits/upcoming` **200** (the front desk is refused here with 403) | **pass**: 200 |
| Visit readiness: the run for a visit | **finding (backend setting)**: `GET /v1/staff/visits/{visit_id}/readiness` **503**; the screen said "Visit readiness is not set up yet. Nothing was written." (a calm fixed state, no 403) | same: 503, same sentence |
| Visit preparation, staff view | **pass**: `GET /v1/staff/visits/{visit_id}/prep` **200**, an empty checklist (the clinic supplied no items) | **pass**: 200, empty |
| Cards and templates: the lists | **pass**: `GET /v1/staff/knowledge/drafts` **200** and `GET /v1/staff/knowledge/items` **200**; the screen said "There are no drafts. Nothing is supplied by the system: every card and template is typed by staff." and "There are no approved cards or templates." | same: 200, 200, same sentences |
| Cards and templates: write a draft, submit, approve or reject | not tested live: no draft was written (it needs the user's go-ahead: `LIVE_DRAFT=yes`) | not tested live: there is no draft to look at, so the Look and the Approve dialog were not reached |

7 calls each, 0 schema mismatches, no 403, no write, no unanswered write. The earlier "not tested live: needs second factor" for these screens is now answered for the reads: the second factor is what opens them (the password-only sessions were refused). **For the backend:** visit readiness answers 503 "not set up" for the demo visit: its content or switch is not set up; that is a setting, not a frontend defect.

## The editor write test: one message template draft, submitted, looked at by a doctor (21 Nov 2026)

The user typed every password and fresh code. Limits as decided: ONE message **template** draft (never a card), its title and its text exactly "DEMO PLACEHOLDER, not a clinical template, not approved" and nothing else; save it, submit it for approval; the doctor opens it, opens the Approve dialog and **cancels** it; nothing approved, nothing rejected.

**The only writes, both by the nurse (pavithra.v2022ece@sece.ac.in, role Nurse, second factor), both with an idempotency key, no 403:**
1. The draft: `POST /v1/staff/knowledge/drafts` **201**. The screen said "The draft was saved."
2. The submit for approval: `POST /v1/staff/knowledge/drafts/{draft_id}/submit` **200**. The screen said "It was recorded."

| Screen | Result | Exact screen text and calls |
|---|---|---|
| Nurse: write a template draft | **pass** | 201, "The draft was saved." (kind Template, code `demo.walk.draft`). Right after the save the list line still said "There are no drafts. Nothing is supplied by the system: every card and template is typed by staff." (it is replaced on the next read; a brief stale line, noted, not a failure of the write). |
| Nurse: submit it | **pass** | 200, "It was recorded." The page then offered **0** Approve buttons to the author: a nurse cannot approve by role. The rule that a doctor who wrote a draft cannot approve it ("You wrote this draft. A different doctor must decide.") was **not tested live** (it needs a doctor to author a draft; none was authorised). |
| Doctor: the drafts list | **pass** | `GET /v1/staff/knowledge/drafts` 200, **1 row** (the nurse's draft); `GET /v1/staff/knowledge/items` 200, "There are no approved cards or templates." |
| Doctor: look at the draft | **pass** | The draft opened (its detail block shows the Synonyms, Phrases patients use and May be read by labels, empty for a template). |
| Doctor: the Approve dialog | **pass, cancelled** | The Approve button was offered to the doctor (not the author) and its dialog opened; it was **cancelled**. **No approve and no reject was made**; the draft is still waiting for approval. |
| Visit readiness (both) | finding, unchanged | The run answers 503 "Visit readiness is not set up yet. Nothing was written." (a backend setting); the upcoming-visits list and the empty preparation answer 200. |

Nurse run: 15 calls; doctor run: 7 calls; 0 schema mismatches, no 403, no unanswered write. **The draft is still on the demo clinic, submitted and undecided.** It is plainly marked as not a clinical template and not approved; it should be rejected or left, by a doctor's decision, not by this walk.

## Combined walk, step 1: the signed-out demo complaint answered 401; the running backend is older than main (21 Nov 2026)

**Stopped at the first unexpected error, as instructed. No retry. Steps 2 and 3 were not started.**

- Frontend (commit ba5731c, test first): the public complaint form sends its own fresh random key (a uuid, 36 characters) once per attempt and the same one when that attempt is retried, and never an Authorization header; a 401 is now said "This cannot be done right now. Nothing was sent." (never "sign in"); a 503 as the not-set-up sentence; a 429 as "Too many requests. Wait a moment and try again." Unit tests pin all of it.
- Live, with no sign-in, ONE demo complaint ("DEMO complaint, not a real complaint", category "other", chosen from the eight the list offered; the list read answered 200): **`POST /public/grievances` → 401.** The screen said, in the new fixed words, **"This cannot be done right now. Nothing was sent."** (not "sign in"). Nothing was filed. **The one write attempted, refused: no complaint exists.** It was not retried.
- **Cause (read-only check, no write):** the running backend is an older build than main. Its own `/openapi.json` documents the complaint's key header as 1 to 173 characters; main's snapshot (after the public-surface review, backend request 50) says 32 to 128. So the process still runs the code that demands a token on this route. **Command for the user:** restart the demo backend from current main, `scripts\demo_up.ps1` (the handover's first command), then tell me; I will re-send the one demo complaint (no sign-in needed) and continue with steps 2 and 3.

| Screen | Result | Exact screen text and calls |
|---|---|---|
| Public complaint form, categories | pass | `GET /public/grievance-categories` 200: access_to_my_data, complaint_about_care, complaint_about_staff, complaint_about_the_system, correction_of_my_data, erasure_of_my_data, nominee, other |
| Public complaint form, send | **finding (backend not restarted on main)** | `POST /public/grievances` **401**; screen "This cannot be done right now. Nothing was sent."; the walk's 30-second wait for the dialog to close ended with the dialog open, as it should after a refusal. |

Writes this combined walk: **one attempted and refused (the complaint, 401); none succeeded.**

## Combined walk (21 Nov 2026): complaint sent, admin read, partial write walk, stopped at the document upload (503)

The backend was restarted on current main (process started 08:36, after the public-surface commit; `/openapi.json` now documents the complaint key header as 32 to 128 characters; `/readyz` ready; the clinic profile answers 200). The user typed every password and code. Every write is on the demo clinic's made-up data, ONE action each, no retries. **Stopped at the first unexpected error, as instructed.**

### Writes made, with status codes (the full list)

| # | Write | Who | Status | Screen text |
|---|---|---|---|---|
| 1 | `POST /public/grievances` (the demo complaint "DEMO complaint, not a real complaint", category "other", signed out) | nobody signed in | **201** | "Your message was sent." (an earlier attempt, before the backend restart, answered 401 and filed nothing) |
| 2 | `POST /v1/portal/requests` (a nominee request: a closed word, no free text) | DEMO Portal Patient | **201** | "Your request was sent." |
| 3 | `POST /v1/staff/patients` (register Demo Zulu Sample, born 1985-04-04, no phone) | front desk | **200** | "A new record was created. Open record 6498999a…" (the registration-outcome wording is still the clinic's placeholder box) |
| 4 | `POST /v1/staff/scheduling/slots/{slot_id}/hold` (one open slot, 10 minutes) | front desk | **200** | "The slot is held." |
| 5 | `POST /v1/staff/scheduling/slots/{slot_id}/book` (that slot, for Demo Zulu Sample) | front desk | **200** | "The slot is booked." |
| 6 | `POST /v1/staff/scheduling/bookings/{booking_id}/cancel` (that booking only; DEMO Portal Patient's 9:00 booking was not touched) | front desk | **200** | "The booking was cancelled and the slot is free." plus the ranked waitlist for the freed slot (2 candidates) and "Prepare offer for this slot" (nothing was prepared or sent) |
| 7 | `POST /v1/staff/documents/uploads` (ONE dummy synthetic referral letter) | front desk | **503, refused: nothing was uploaded** | "This is not available right now. Try again in a moment." |

Not touched: DEMO Portal Patient's booking and consent; anything switched off in the build (erasure, merge approve and unmerge). The earlier hold-then-book run was split in two by my own script's timing (the patient search had not answered yet when the script counted the results, so it stopped before the booking): the second run resumed at the booking on the same still-held slot, so no step was repeated.

### The stop: document upload answers 503

`POST /v1/staff/documents/uploads` → **503** on the first and only attempt (a key was sent; no retry). The screen said "This is not available right now. Try again in a moment." (the fixed wording for a switched-off service, no backend text). **Cause (from the configuration reference, not confirmed by a call):** real intake is built only if `CAREFLOW_STORAGE_BUCKET`, `CAREFLOW_STORAGE_VERIFIED` (a date on which `scripts/storage_spike.py` was run against the project) and a secret key all hold; otherwise the upload routes answer 503. `demo_up.ps1` does not set them. **For the owner:** run the storage spike against the dev project and set those two settings (the secret key is the backend's, never the frontend's); then the upload and the referral run can be walked. Not run, so not tested live: uploading a referral, running it, and everything after it in the sequence.

### Admin read-only walk with the second factor (the nurse/admin login, role Admin): all pass

12 calls, **0 schema mismatches, no 403, no write.** The shell said "Active role: Admin, Clinic: Demo Clinic, TEST, Operating profile: Demo".

| Screen | Result | Exact screen text and calls |
|---|---|---|
| Dashboard | pass | `GET /v1/staff/console/dashboard` 200 |
| Accounts | pass | `GET /v1/staff/accounts` 200, 4 rows |
| Autonomy and its history | pass | `.../autonomy` 200, `.../autonomy/proposals` 200, `.../autonomy/history` 200 (0 rows). "Mode Shadow (never set, so shadow)", "There are no pending proposals.", "There is no history yet." |
| Red-flag rules | pass | `GET /v1/staff/console/red-flag-rules` 200: a demo draft version "Draft, not approved" (phrases zzdemoalpha, zzdemobravo; no patterns, token sets or vital-sign criteria) |
| Reviewer stats | pass | `GET /v1/staff/console/reviewer-stats` 200 |
| Cost | pass | `GET /v1/staff/console/cost-canvas` 200: "State Not enough data", "Amplification over the ceiling No" |
| Voice | pass | `GET /v1/staff/voice/sessions` 200: "There are no voice sessions." |
| Recovery | pass | `GET /v1/staff/recovery` 200: "There are no recovery requests." |
| Grievances | pass | `GET /v1/staff/grievances` 200 (the list loaded; the screen shows its rows as plain text, not recorded here) |
| The clinic public-profile screen | not tested live: no such staff screen exists | The backend asked for none; the admin write is `PUT /v1/staff/clinic/public-profile` |

### Per screen, this combined walk

| Screen | Result |
|---|---|
| Public complaint form (send) | **pass** (201, after the backend restart) |
| Patient: ask the hospital (send a request) | **pass** (201) |
| Front desk: register a patient | **pass** (200) |
| Front desk: hold, book, cancel a slot | **pass** (200, 200, 200) |
| Front desk: upload a referral | **finding (backend storage not set up)**: 503 |
| Front desk: run an uploaded referral, acknowledge and resolve a task, acknowledge an alert, change a patient request's status | **not tested live**: not reached (the walk stopped at the upload) |
| Nurse: screen a message for red flags | **not tested live**: not started (the walk stopped earlier) |
| Doctor: record a referral decision, reject the demo draft | **not tested live**: not started (the walk stopped earlier) |

### Screens still "not tested live", and why (updated)

**Staff, front desk:** upload a referral (503: storage not set up) and run it (needs the upload); acknowledge and resolve a task, acknowledge an alert, change a patient request's status (stopped before them; the nominee request sent by the patient is waiting for it); revoke an identity confirmation; every other scheduling write (waitlist join, mark an offer delivered, add a provider, location, visit type or slot, an offer run); family link verify, reject, revoke (no family request exists); portal claim approval (the demo claim linked directly, none pending); break-glass open and review; messages: reading one and screening one for red flags (not run). Switched off in the build, never touched: erasure ask, approve and cancel; identity merge approve and unmerge.

**Staff, nurse and doctor:** the screen-for-red-flags check; recording a referral decision; rejecting or approving the demo draft; care plans, results, care gaps, notes and medications writes; red-flag rule drafts and approvals; the visit readiness run (503: not set up) and so starting it or relaying a reply; a doctor-authored draft (author cannot approve).

**Staff, admin and auditor:** every admin write (invite, suspend, reinstate, grant, revoke, autonomy change or proposal, voice read and purge, recovery, grievance acknowledge and resolve, break-glass review); the clinic public-profile write (no screen); the auditor's audit log (no auditor walk run).

**Patient portal:** withdrawing consent, cancelling the booking, marking a preparation item done, sending a complaint while signed in, a family-link request or withdrawal, downloading my data, opening a voice session, sending a message or opening a conversation, phone-code sign-in (no approved SMS provider).

**All Tamil wording:** machine-checked only; no human reviewer has approved a string.
