# Live integration checklist (the first live run)

Every call each console screen makes, and what to check against a RUNNING backend. Until now everything was proven against
mocks written from `openapi.snapshot.json`; this list is how we find where the backend differs from them.

**State (2026-11-10).** The read-only walk has been run against the dev backend three times (front desk 24 calls, nurse 26, doctor 26, no
findings). Everything added since (alerts, dashboard, care gaps, care plans, results, accounts, autonomy, reviewer statistics, cost canvas,
voice, readiness, claims, break-glass, recovery, notes, grievances, the patient portal) and every write has NOT been run against a live
backend. The walk now visits every new read-only list; the writes are done by hand, in the order below.

## The first live run, in order

1. **Seed** (see "Data the pass needs"). Without rows, a clean report proves nothing about row shapes: the report lists every empty list as
   "no rows to check".
2. **Read-only walk, once per role** (front desk, nurse, doctor, admin): `LIVE_LABEL=<role> npm run e2e:live`. You sign in yourself. Read the
   four reports: any finding (unknown route, unexpected status, schema error, 5xx) is a mismatch to report to the backend session.
3. **Writes by hand with the recorder** (`npm run e2e:live -- --record`), one role at a time, in this order. Each write changes dev data, so
   this step is your call. Stop at the first surprise and report it before going on.
   1. Front desk: add a provider, location, visit type, slot; hold, book, cancel a booking; join the waitlist; mark an offer delivered.
   2. Nurse / doctor: record a barrier, start an episode, resolve a barrier, re-check a sample (as a different person from the decider),
      record a result (and one that corrects another), write a note and an addendum, relay a readiness reply.
   3. Doctor: write a care plan, approve it; propose switching to autonomous (expect the 503 state until the switch is enabled).
   4. Admin: invite (expect the 503 state if no sign-in administration is configured), grant and revoke a role, suspend and reinstate,
      request a recovery and approve it as a second admin, acknowledge and resolve a grievance, read and purge a voice transcript,
      review a break-glass grant as an admin who did not open it.
   5. Front desk: approve and reject a portal claim.
   6. Break-glass: open a grant with a reason code from the clinic's list and read under it (each read is an audit row).
4. **The patient portal** (needs Supabase phone sign-in configured and a test patient number you can receive codes on): sign in, create the
   account, read the notice and compare it character by character with what the API sends, grant, activate, claim by phone + name +
   date of birth + the clinic id, file a grievance (a category from the clinic's list), download the data file.
5. **Only after one live approve with step-up works:** ask for the merge approve flag to be turned on.

## Patient sign-in for the demo: Supabase phone sign-in with test numbers (VERIFY IN THE DASHBOARD)

The patient portal signs in with a one-time code sent to a mobile number. For the demo this needs no SMS provider and no external service:
Supabase can be given test phone numbers with fixed codes, and a test number never receives a text. These steps are from memory of the
dashboard and **must be verified in the dashboard** before relying on them; the labels and where they sit may differ.

1. Open the dev Supabase project's dashboard, then **Authentication**, then the sign-in **Providers** list, then **Phone**.
2. Turn the **Phone** provider on. Check whether the dashboard insists on choosing an SMS provider before it lets you save; if it does, that
   is a blocker to report (do not add one: no SMS or other service is to be added). Leave "confirm phone" (the one-time code) on.
3. Find the setting for **test phone numbers** (named something like "Test Phone Numbers and OTPs"). Add one or two numbers with a fixed
   six-digit code each, in the format the dashboard shows (the number without or with the plus sign: check), and set how long the fixed
   codes stay valid. A number listed here is sent no text; the portal's code box accepts its fixed code.
4. In the portal (`/patient/sign-in/`) type the test number WITH a plus sign and country code, ask for a code, and enter the fixed code.
   If the dashboard stores the number without a plus sign, the portal sends the plus form; check the two match, or the code is refused.
5. Use only test numbers on the demo project. A real number needs an SMS provider, which is a separate decision and is not part of this
   run. The fixed codes are demo values: write them down only in the dashboard, never in this repository.
6. Rate limits and CAPTCHA on repeated failures are Supabase settings on the same screens: check they are on before a demo others can reach.

What to confirm after setting it up: the code request returns without an error, a wrong code is refused with the one fixed message, the
right fixed code opens a session, `GET /v1/portal/me` then answers 403 (no patient account yet) and the page offers "Create my account".

## How to run it (by hand, never in CI)

1. Start the backend, then the dev server (`npm run dev` in `frontend/`; it proxies `/v1` and `/readyz` to `CAREFLOW_API_BASE_URL`).
2. `npm run e2e:live`: opens a browser window. **You** sign in (email, password, authenticator code); nothing is typed for you and no
   credential reaches the script. It then walks every screen read-only and writes `frontend/live-report/live-report.md`.
3. `npm run e2e:live -- --record`: opens the console and records while you use it yourself, for the writes the walk never makes
   (search a patient, acknowledge, release a hold, record a decision, revoke). Press Resume in the inspector when finished.
4. The report lists, with no values (paths have ids removed, only field paths and type words): unknown routes, statuses the schema
   does not list, 5xx, writes without an idempotency key, schema errors per response, and the operations the run never made.
   Nothing in `live-report/` is committed (git-ignored).

The run refuses to start if the dev server or the backend does not answer. The isolation tests (`tests/live-tools.test.ts`) pin that
no workflow can start it.

## Calls per screen

"Handled" is what the screen does for each status. Every refusal shows a fixed message; the backend's text is never shown.

| Screen | Call | Sends | Handled | Check against the backend |
|---|---|---|---|---|
| Every staff screen after sign-in | `GET /v1/iam/me` | bearer token | 200 account; 403 "cannot use the console"; other 4xx/5xx "could not be loaded" | `roles[]` has `clinic_id`, `role`; are `clinic_name`, `clinic_origin` (`live`/`test`) and `operating_profile` present yet? (requests item 2) |
| Referrals list | `GET /v1/staff/referrals` | no query | 200; 403 not available; 5xx fixed message | `run_state` words seen; `proposal_visible`/`decided` for front desk vs nurse; `has_more` |
| Referral detail | `GET /v1/staff/referrals/{document_id}` | id | 200; 404; 403 | `holds[]`, `proposal` shape, `mode` words, `decided` null vs false |
| Referral decision (nurse, doctor) | `POST /v1/staff/referrals/{document_id}/review` | `X-Idempotency-Key`; body `{decision}` (`approve` or `reject` only) | 200; 409 "not waiting for a decision"; 403; 503 | replay (`replayed: true`) on a retried key; 409 when already decided |
| Upload step 1 | `POST /v1/staff/documents/uploads` | key; `{clinic_id, content_type, kind, size_hint}` | 200; 403; 422; 503 | `upload_url` host is the Supabase project origin; 503 text when storage is off |
| Upload step 2 | `PUT <upload_url>` (storage host, no bearer) | file, `Content-Type` | 2xx; else retry with same key | the storage accepts a raw PUT with this content type |
| Upload step 3 | `POST /v1/staff/documents/register` | **same key**; `{clinic_id, kind}` | 200 shows `scan`; 422 "not accepted"; 409; 503 | `scan` words: only `clean` shown as clean; what a real scanner reports |
| Task inbox | `GET /v1/staff/review-tasks?status=` | `status` (open, acknowledged, resolved) | 200; 403 | task `kind` words seen; ordering (immediate first) |
| Task detail | `GET /v1/staff/review-tasks/{task_id}` | id | 200; 404; 403 | `provenance` keys per kind (only plain values are shown; `document_id` is linked) |
| Acknowledge | `POST .../{task_id}/acknowledge` | key, no body | 200; 409; 403 | replay for the same person |
| Resolve | `POST .../{task_id}/resolve` | key, no body | 200; 409 (kinds that do not close here); 403 | which kinds really close; `red_flag` gives 409 |
| Patient search (front desk) | `POST /v1/staff/patients/search` | `{clinic_id, name, dob, phone?}`, **no key** | 200; 403; 422; 429; 503 | **do by hand** (it needs a real person's details): `label`, `labels_withheld`, `truncated` exist? (requests item 5); 429 behaviour |
| Patient record | `GET /v1/staff/patients/{patient_id}` | id | 200; 404; 403; 429; 503 | `latest_identity_event` words; `merged_into` |
| Confirm identity | `POST .../{patient_id}/confirm-identity` | key, no body | 200; 409; 403 | no step-up demanded |
| Revoke identity | `POST .../{patient_id}/revoke-identity` | key; `{reason}` | 200 shows `review_task`; 409; 403 | `review_task` words (`on_record` = nothing new recorded, confirmed); reasons accepted |
| Scheduling lists | `GET /v1/staff/scheduling/providers`, `/locations`, `/visit-types`, `/priority-levels` | `include_inactive` only when ticked | 200; 403 | fields present; inactive filter |
| Holds | `GET /v1/staff/scheduling/holds?status=` | `status` (default active), optional `clinic_id` | 200; 403; 404 clinic not readable | status words |
| Release a hold | `POST /v1/staff/scheduling/holds/{hold_id}/release` | key, no body | 200; 409 (booked or expired, shown calmly); 403 | 409 wording |
| Slots | `GET /v1/staff/scheduling/slots` | optional `clinic_id`, `provider_id`, `location_id` | 200; 404 clinic not readable; 403/5xx fixed messages | `status` words (open, held, ...); `hold_expires_at` null when open |
| Hold a slot | `POST /v1/staff/scheduling/slots/{slot_id}/hold` | key, `duration_minutes`, optional `patient_id` | 200; 409 "changed in the meantime" | not run live yet: a write |
| Book a slot | `POST /v1/staff/scheduling/slots/{slot_id}/book` | key, `patient_id` | 200; 409 | not run live yet: a write |
| Add a slot | `POST /v1/staff/scheduling/slots` | key, ids, ISO times | 200; 409 overlap; 422 bad range | not run live yet: a write |
| Bookings | `GET /v1/staff/scheduling/bookings` | optional `clinic_id` | 200; 404 clinic not readable | rows, `has_more` |
| Cancel a booking | `POST /v1/staff/scheduling/bookings/{booking_id}/cancel` | key | 200 shows the ranked waitlist; nobody is contacted | not run live yet: a write |
| Waitlist | `GET /v1/staff/scheduling/waitlist` | optional `clinic_id`, `visit_type_id` | 200; 404 clinic not readable | `clinical_priority` words, `rank` order |
| Join the waitlist | `POST /v1/staff/scheduling/waitlist/join` | key, patient, visit type, priority code | 200; 409 already waiting | not run live yet: a write |
| Offers | `GET /v1/staff/scheduling/offers` | optional `clinic_id`, `state` | 200; 404 clinic not readable | the five `state` words |
| Mark an offer delivered | `POST /v1/staff/scheduling/offers/{hold_id}/mark-delivered` | key | 200; 409 no longer live | not run live yet: a write |
| Start a backfill run | `POST /v1/staff/scheduling/backfills` | key; `clinic_id`, `slot_id`, `visit_type_id` | 200 (the run stops at its first pause); 409; 503 not configured | writes nothing but the run's checkpoint and one audit row; ranks the waitlist only |
| Backfill: send decision | `POST /v1/staff/scheduling/backfills/{slot_id}/decide` | key; body `{action}` = `send`, `skip` or `stop` | 200; 409 "not waiting for that"; 404; 403; 503 | `send` HOLDS the slot for the current candidate for the clinic's hold length, and stores the offer text (the approved template, verbatim, encrypted) as a STAFF message in the patient's `appointments` thread (created if missing). The offer then shows in Offers as awaiting delivery. NOTHING is sent to the patient: the front desk delivers it by hand and presses mark-delivered. The run then waits for the reply (`reply_entry`). `skip` moves to the next candidate (no hold, no message); `stop` ends the run |
| Record a reply the patient gave to the front desk | `POST /v1/staff/scheduling/offers/replies` | key; `{clinic_id, patient_id, text}` | 201 `{message_id, thread_id, created, screening_id, outcome}`; 404 not a patient of the clinic; 409 key reused for a different reply; 422; 503 no keys | the text is stored encrypted as a PATIENT message in the patient's appointments thread and screened by the red-flag rules in the same call (a flag raises the nurse task); the response never carries the text. Then use `message_id` in the backfill reply and in interpret-reply |
| Backfill: enter the reply | `POST /v1/staff/scheduling/backfills/{slot_id}/reply` | key; `{outcome: replied, message_id}` or `{outcome: timed_out}` | 200; 409; 422 | the red-flag rules screen the reply FIRST; a flag or an unassessable reply raises a nurse task and ends the run |
| Add a location, provider or visit type | `POST /v1/staff/scheduling/locations`, `/providers`, `/visit-types` | key, clinic id, name (and length) | 200; 409 | not run live yet: a write |
| Copy an offer's text | `GET /v1/staff/messages/{message_id}` | the offer row's `message_id` | the read is audited; no id means nothing to copy | that every offer has a `message_id`; 410 erased; the text is never stored in the page |
| Reviewer view | `GET /v1/staff/referrals/{document_id}/review-view` | no query | 200; 404 no proposal; 403 | `proposal`, `evidence`, `alternatives` key names; null `confidence` and `probability` |
| Reason codes | `GET /v1/staff/console/reason-codes` | no query | 200 `configured` + `codes` | whether the clinic has set the list |
| Alerts | `GET /v1/staff/console/alerts` | `clinic_id`, optional `include_acknowledged` | 200; 403/404 fixed messages | `alert_class` words seen; `audience_role` as the escalation state; `ref` null or an id |
| Acknowledge an alert | `POST /v1/staff/console/alerts/{alert_id}/acknowledge` | key | 200; 409 already acknowledged | not run live yet: a write |
| Dashboard | `GET /v1/staff/console/dashboard` | `clinic_id` | 200 metrics + `not_measured`; 403 unless admin | `metric_id` list; `unreadable` with null value |
| Care gaps | `GET /v1/staff/care-gaps` | `clinic_id` | 200 summaries, rows, `unreadable`; 403 unless nurse, doctor or admin | `source` words; null counts shown as not readable |
| Care plans | `GET /v1/staff/care-plans`, `POST /v1/staff/care-plans`, `POST /v1/staff/care-plans/{plan_id}/confirm` | `clinic_id`; key + patient + text; key + action | list never has plan text; 503 not configured; 409 not waiting | `status` words (the console offers approve/reject on `confirmation_requested`); `waiting_for` |
| Record a result | `POST /v1/staff/results` | key, patient, code, measures, times, verified | 201; 422 malformed measure | not run live yet: a write |
| Care-gap writes | `POST /v1/staff/care-gaps/barriers`, `.../barriers/{barrier_id}/resolve`, `.../episodes`, `.../episode-rules/{episode_rule_id}/done`, `.../samples/{sample_id}/recheck` | key + the typed fields | 201/200; 409 on a re-check by the original decider, on resolving twice, on a date moving backwards or into the future; 503 episode rules not set | not run live yet: writes; the `source` words `unresolved_barrier`, `after_the_fact_review`, `overdue_recurrence` |
| Staff accounts | `GET /v1/staff/accounts`, `POST /v1/staff/accounts/invite`, `.../{account_id}/suspend`, `.../reinstate`, `.../roles`, `.../roles/revoke` | `clinic_id`; key + fields | list is ids, status and role words; invite 503 when no sign-in administration is set; step-up on the writes | not run live yet: writes (each needs a fresh code) |
| Autonomy | `GET /v1/staff/console/autonomy`, `POST /v1/staff/console/autonomy`, `GET /v1/staff/console/autonomy/proposals`, `POST .../proposals`, `POST .../proposals/{proposal_id}/resolve` | `clinic_id`; key + mode / proposer role / decision | mode `shadow` when never set; propose 503 until the switch is enabled; resolve `result` words | not run live yet: writes |
| Autonomy history | `GET /v1/staff/console/autonomy/history` | `clinic_id` | 200; each read is audited; 403 unless admin or doctor | `kind`, `outcome` words; null mode or outcome shown as a dash |
| Reviewer statistics | `GET /v1/staff/console/reviewer-stats` | `clinic_id`, optional `days` | 200; 403 unless admin | null rate and median shown as not recorded; `verdict` words |
| Cost canvas | `GET /v1/staff/console/cost-canvas` | `clinic_id`, optional `days` | 200; null column or term stays null | `state` and `reasons` words |
| Voice transcripts | `GET /v1/staff/voice/sessions`, `GET /v1/staff/voice/sessions/{session_id}/transcript`, `POST /v1/staff/voice/purge` | `clinic_id`; key + optional `limit` | read is audited; 503 keys not configured; purge deletes expired only | `workflow` and `status` words; not run live yet: the read and purge |
| Visit readiness | `GET /v1/staff/visits/{visit_id}/readiness`, `POST .../readiness/start`, `POST .../readiness/reply` | key; outcome and the reply text | 404 no run; 409 no content for the visit type; 503 not configured; 403 unless nurse or doctor | `status` words, `waiting_for`, `current_item`; not run live yet: writes |
| Portal claims | `GET /v1/staff/portal-claims`, `POST /v1/staff/portal-claims/{request_id}/resolve` | `clinic_id`; key + decision + patient id | list is an id and a time; the six `result` words | not run live yet: the resolve |
| Break-glass | `GET /v1/staff/break-glass`, `POST /v1/staff/break-glass`, `GET .../break-glass/{grant_id}/messages`, `POST .../break-glass/{grant_id}/review` | key + patient + reason code; grant id | 422 for a code not on the clinic's list; 503 not configured; each read audited; review by an admin who did not open it | not run live yet: writes and the read; the route to list reason codes does not exist |
| Staff recovery | `GET /v1/staff/recovery`, `POST /v1/staff/recovery`, `POST .../recovery/{request_id}/approve`, `.../cancel`, `.../complete` | `clinic_id`; key + account | `result` words; complete 503 when no sign-in administration is set | not run live yet: writes |
| Notes | `GET /v1/staff/notes`, `POST /v1/staff/notes` | `clinic_id`, `patient_id`; key + text + optional parent | 503 keys not configured; erased or null text says so | not run live yet: the write |
| Grievances | `GET /v1/staff/grievances`, `POST /v1/staff/grievances/{grievance_id}/status` | `clinic_id`; key + status | `result` words recorded, replay, invalid_transition | `category` words; not run live yet: the write |
| Patient portal | `GET /v1/portal/me`, `POST /v1/portal/register`, `GET /v1/portal/consents`, `POST /v1/portal/consents`, `POST /v1/portal/activate`, `POST /v1/portal/claims`, `GET /v1/portal/grievances`, `POST /v1/portal/grievances`, `GET /v1/portal/my-data` | patient session only; key + the typed fields | me 403 = no patient account yet (the page offers to create it); consent 409 = notice changed; activate / claim `outcome` words; grievance 422 category not on the list, 503 not set up; my-data is audited | the sign-in itself (Supabase phone code) is not run live yet; the clinic id the patient types (no route gives it); the grievance category list (no route gives it) |
| Offer draft | `GET /v1/staff/scheduling/offers/draft` | `clinic_id`, `visit_type_id` | 200 shows only `status`; 403; 5xx | `status` words; `reason` and `data` shapes (requests item 6) |
| Possible duplicates | `GET /v1/staff/identity/possible-duplicates?status=` | `status` (default pending) | 200; 403 (non front desk) | `reason`, `matched_kinds` words |
| Merges | `GET /v1/staff/identity/merges?status=` | `status` only when filtered | 200; 403 | status words |
| Merge detail | `GET /v1/staff/identity/merges/{merge_id}` | id | 200; 404; 403 | `proposed_by`, `second_approved_by` ids |
| Merge approve / unmerge (**flag OFF**) | `POST /v1/staff/merges/{merge_id}/approve`, `.../unmerge` | key | 200; 401 step-up; 409; 403 | only after the owner turns the flag on: step-up challenge with a real token, one retry, same key |

Operations in the client that no screen calls: `GET /readyz` (used only by the live-run guard through the proxy) and
`POST /v1/staff/merges` (propose; not built). The snapshot has more routes (messages, bookings, waitlist, slots); no console screen
uses them yet.

## Also verify by hand (the walk cannot)

- The step-up challenge on a merge route with a real token carries `WWW-Authenticate: step-up` and the browser can read it (through
  the dev proxy it can; in production CORS must expose it).
- A 429 on patient search: does the backend send `Retry-After`? (The console shows a fixed message and ignores it.)
- An expired session in the middle of a screen sends the person to sign-in with "Please sign in again".
- The `X-CareFlow-Active-Role` header is NOT sent (flag off), and the backend ignores it if it were.
- `GET /readyz` reports the operating profile; the console shows "Operating profile unknown" until `/me` or `/readyz` carries it.

## Data the pass needs in the dev project (ask the backend session for a seed)

Beyond the list below, for the F7 screens: a slot (open) and a held slot, a booking, a waitlist entry, an offer in each state, an alert
(open and acknowledged), a care gap row from each of the three sources, a care plan awaiting confirmation, a pending portal claim, a
staff account in each status, a recovery request, a break-glass grant to review, a grievance in each status, a voice session with expired
and unexpired chunks, a visit with a readiness run, a note with an addendum, a result-correction review task.

At least one of each, in a single clinic the test account can read: a referral document (and one with an open hold), a review task
that is open, one acknowledged and one resolved, a `referral_hold` task, a `red_flag` task (nurse or doctor account), a patient
record that is verified and one that is not, a pending possible duplicate, a merge awaiting second approval proposed by ANOTHER
account, an active merge, a visit type, a provider, a location, a priority level and an active hold. A nurse or doctor account
and a front-desk account, to see both sides of every role-dependent screen.

## What I need to start

1. The backend running, and `CAREFLOW_API_BASE_URL` in `frontend/.env.local` pointing at it (the dev proxy uses it).
2. A dev staff account for you to sign in with; I will not see credentials. A second account with another role is better.
3. The seed above (or tell me which rows exist).
4. Your OK to open the headed browser window on your machine, and to leave the dev server running.
5. For the portal: the project's Supabase phone sign-in configured, and a test number that can receive a code. The code is yours to type; it
   never reaches a script or a log.
