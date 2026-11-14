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

## Offer run after a cancel (front desk; nothing is sent to a patient)

Why this exists: after a booking is cancelled, an offer only appears in **Offers** once an offer run has been started for the freed slot and
a person has decided **Send**. Cancelling alone starts nothing. Steps, each with what you should see:

1. **Scheduling, Bookings:** cancel a booking (confirm). *You should see:* "The booking was cancelled and the slot is free", the ranked
   waitlist for the freed slot (or "Nobody on the waitlist matches this slot"), and a button **Prepare offer for this slot**.
2. **Prepare offer for this slot, then confirm.** *You should see:* the confirmation says "Nothing is sent to anyone"; after it, "The run is
   started", a status word, **Waiting for: Your decision to send, skip or stop**, the current candidate (a short id and a rank), and the
   ranked waitlist with that candidate marked "Current candidate". If the backend answers 503 you should see "Offer runs are not set up
   yet. Nothing was started or changed", calmly and not as an error.
3. **Skip** (confirm). *You should see:* "The candidate was skipped", and the run moves to the next candidate (a new candidate and rank),
   or finishes with "Finished: Yes" if nobody is left. A 409 reads "The run is not waiting for that".
4. **Send** (confirm). *You should see:* "The offer was recorded for the candidate", a hold id on the run, and **Waiting for: The patient's
   reply to be entered**. Open **Scheduling, Offers**: the offer is there as "Awaiting delivery". The system has sent nothing: you deliver it
   by hand (Copy offer text, then Mark delivered).
5. **Stop** (on another run, confirm). *You should see:* "The run was stopped" and "Finished: Yes".
6. **Type the patient's reply** (a run waiting for the reply): in "Type what the patient told you", type what they said (by phone or in
   person) and press **Save the reply**. *You should see:* a confirmation that says it is stored as a message from the patient, encrypted,
   that the rules read it first, and "Nothing is sent to the patient"; after you confirm, the box is EMPTY again (the text has left the
   page), "The reply was saved and picked below. The rules' reading: clear" (or "a red flag was found; a nurse task was raised" / "it could
   not be assessed; a nurse task was raised", and then a review task in **Tasks**), and the saved message is already picked. A 503 reads
   "Replies cannot be stored yet (encryption is not set up). Nothing was saved", a 422 "The reply is blank or too long", a 409 "A different
   reply was already saved with this request"; each is a calm message, and what you typed stays in the box so you can correct it.
   Saving does NOT yet enter the reply into the run.
6b. **A reply stored earlier** (after a reload, say): under "Or pick a reply that was stored earlier" the stored replies are listed by
   time and sender, never by text. *You should see:* the message is NOT read until you click **Read this message**; then its text appears
   (that read is audited) with a Hide button. If none exists the page says so, and **No reply came** is still available.
7. **Suggest a reading.** *You should see:* a rules screening word (Clear, Red flag, Could not be assessed), a suggested meaning or "No
   suggestion: a person must read the reply" with the reason, "Not recorded" for a missing confidence or engine, and the line that what it
   means is your decision. A red flag shows "The rules did not clear this reply".
8. **Enter this reply** (confirm). *You should see:* "The reply was entered", and, for a red flag or a reply the rules could not assess,
   a nurse review task in **Tasks** and the run finished. **No reply came** (confirm) moves the run on.

## Hold and Book across clinics (item 17)

The scheduling lists show the ACTIVE clinic's rows only (the routes return every clinic the account can read; the console filters by each row's
`clinic_id` until the routes take a `clinic_id` parameter). If the account can read two clinics, check:

1. **Scheduling, Slots**, with the two clinics seeded. *You should see:* only the slots of the clinic in the session bar. The provider and
   visit-type choices in the filters and in "Add a slot" are that clinic's too.
2. **Hold, then Book**, with a patient found by the search. *You should see:* "The slot is held" / "The slot is booked", not "That could not
   be found". If the patient is not registered at the slot's clinic you should see "This patient is not registered at the clinic that owns this
   slot", in plain words, as a calm status and not an alert.
3. **Bookings, Holds, Waitlist, Offers** show the same clinic only. In the browser's network tab a refused Hold or Book answers 404 with
   `{"detail":"unknown patient for this clinic"}`; if it ever reads `{"detail":"not found"}` the slot itself is not visible: tell the backend.

## Audit, register, erasure, Run and the full record (F9; synthetic data only)

Use a demo clinic with synthetic people. The erasure buttons are OFF in a real build, so steps 4 and 5 below only check the list and the note
until the step-up claim spike is recorded.

1. **Audit log** (an auditor account; `GET /v1/staff/audit`). *You should see:* the page opens with nothing read; "Read the log" lists rows
   newest first (when, action, who, record, clinic, origin); "Show details" opens a row's details as plain text; "Older rows" adds the next page and
   keeps the filter; an account with no auditor role sees "not available to your role". Check that your own opens in steps 6 and 7 appear as rows.
2. **Register a patient** (front desk, Patients page; `POST /v1/staff/patients`). Search first. *You should see:* an empty form, a confirmation, then
   one of "A new record was created", "This person matched an existing record", "Possible matches were found. Nothing was linked automatically; a
   person decides" (with links) or "This request had already been saved"; the typed details are gone from the page.
3. **Run the referral** (front desk, nurse or doctor, referral detail; `POST /v1/staff/referrals/{document_id}/run`). *You should see:* a
   confirmation, then "The run was queued. Job: ..." (or "already queued a run" on a replay); the referral's state changes as the run proceeds.
4. **Erasure requests** (front desk; `GET /v1/staff/erasure-requests`). *You should see:* the open requests (who asked, shown as "You" for yours),
   a plain note that asking, approving and cancelling are switched off, and, under "Ask for an erasure", the clinic's reasons from `GET /v1/staff/erasure-requests/reasons` (none chosen), or the calm "not set up yet" state on a 503. The ask, approve and cancel calls (`POST /v1/staff/patients/{patient_id}/erasure-requests`,
   `POST /v1/staff/erasure-requests/{request_id}/approve`, `.../cancel`) are not made until the flag is turned on.
5. **When the flag is on** (only after the claim spike): approve as a DIFFERENT front desk account than the one that asked. *You should see:* the
   confirmation naming what is erased and what is kept, the authenticator prompt, then "Approved. The identity was erased." The asker never sees an
   Approve button on their own request.
6. **Full details** (front desk, nurse or doctor, patient page; `GET /v1/staff/patients/{patient_id}/view`). *You should see:* the "pending the
   privacy owner" notice, nothing read until you click "Open the full details", then the name, date of birth and phone, gone again on "Hide". An
   audit row exists for each open (step 1). Outside the demo profile you should see "cannot be opened in this setup".

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
| Hold a slot | `POST /v1/staff/scheduling/slots/{slot_id}/hold` | key, `duration_minutes`, optional `patient_id` | 200; 409 "changed in the meantime" | run live 2026-11-13 (front desk, synthetic patient, 200). "Minutes to hold" has NO default: type a number, the Hold button stays disabled until you do. |
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
| Red-flag rules (list) | `GET /v1/staff/console/red-flag-rules` | `clinic_id` | 200 `rules` with status, origin, phrases, patterns, `has_vitals_criteria`, `decisions` (who as an id, when); 403 unless doctor or admin | content shown exactly as sent, nothing added; the person's own id shown as "You"; an admin sees no Approve or Reject |
| Red-flag rules (decide) | `POST /v1/staff/console/red-flag-rules/{rule_id}/decision` | key; `{decision}` (approve or reject) | 200 `result` (ten words); 403 for a non-doctor or without a verified second factor; 404 | a confirmation first; the first approval says "not approved yet, a second doctor must"; the same doctor twice says already decided; the author is refused (`author_cannot_decide`); a platform rule is refused; nothing says approved until `approved`. DEMO ONLY with the seeded DRAFT rule and two doctor accounts; never approve a real rule from this checklist |
| Red-flag rules (author) | `POST /v1/staff/console/red-flag-rules` | key; `{clinic_id, rule_code, phrases, token_sets}` (no pattern field) | 201 `{rule_id, status: draft_unreviewed}`; 403 unless a doctor with a verified second factor; 409 key used for a different draft; 422 outside the language (limits, invisible characters, a token set without two different words, no criterion) | the form starts empty with no example; a review step shows exactly what will be saved before Save draft; the new version appears as "Draft, not approved" and is never active; the same code again gets the next version number; the rule list now also shows `token_sets`. DEMO ONLY: use made-up placeholder words, never real clinical wording, until the clinicians own the content |
| Red-flag rules (withdraw) | `POST /v1/staff/console/red-flag-rules/{rule_id}/withdraw` | key; no body | 200 `result` (`withdrawn`, or `not_draft` for anything that is not a draft); 403 unless a doctor of the clinic with a verified second factor; 404 | a confirmation first, showing the content; a withdrawn draft shows as "Withdrawn" (never "Retired"), can never be approved, and has no buttons; replaying the same request says withdrawn. DEMO ONLY |
| Red-flag rules (retire) | `POST /v1/staff/console/red-flag-rules/{rule_id}/decision` with `{decision: "retire"}` | key; `retire` on an APPROVED version | 200 `result` (`recorded`, `retired`, `not_approved`, `withdrawn` on a replay after withdrawal, and the other words); 403; 404 | a confirmation first, showing the content and what retiring means; one retire decision alone retires nothing ("Retire decisions recorded so far: 1"); the second, DIFFERENT doctor's retire makes it "Retired"; a doctor who already recorded one no longer sees the button. The list's `authored_by_you` hides Approve and Reject from the author. DEMO ONLY |
| Front desk supervisor | `GET /v1/staff/review-tasks` (as the supervisor role) | — | only `identity_confirmation_revoked` tasks assigned to the supervisor role; Tasks is the only menu entry | needs the backend setting `CAREFLOW_REVOKE_TASK_ROLE` and a person holding front desk AND the supervisor role |
| Medications (list) | `GET /v1/staff/medications` | `clinic_id`, `patient_id` | 200 `medications` newest first; 403 unless nurse or doctor; 404 | code shown exactly as sent; verified Yes or No; a null end time says "No end recorded" (not "ongoing"); a replaced row is marked and offers no action |
| Medications (record) | `POST /v1/staff/medications` | key; `{clinic_id, patient_id, code, verified, valid_from, valid_to, supersedes_id}` | 201 `medication_id`; 409 replaced already or key clash; 422 code outside the clinic's vocabulary or bad times; 503 until the clinic's vocabulary setting exists | nothing pre-chosen (code, verified, start time); "Correct this entry" and "Stop this medicine" are two separate actions, each with its own confirmation; both send `supersedes_id` and the old row stays, marked replaced. A stop reuses the old row's code and start and asks only for the stop time and the verified answer (stop time required). Use a SYNTHETIC patient and a made-up code only; the clinic's real vocabulary is a clinical decision |
| Revoke result: admin alert | `POST .../{patient_id}/revoke-identity` (the `admin_alert` field) | as above | `admin_alert` is `raised`, `not_for_this_reason`, `not_configured` or `on_record` | a line next to the review-task line; nothing for `on_record`; the alert appears for an admin as "An identity confirmation was revoked as suspect" (needs `CAREFLOW_REVOKE_ALERT_REASONS`) |
| Reviewer statistics | `GET /v1/staff/console/reviewer-stats` | `clinic_id`, optional `days` | 200; 403 unless admin | null rate and median shown as not recorded; `verdict` words |
| Cost canvas | `GET /v1/staff/console/cost-canvas` | `clinic_id`, optional `days` | 200; null column or term stays null | `state` and `reasons` words |
| Voice transcripts | `GET /v1/staff/voice/sessions`, `GET /v1/staff/voice/sessions/{session_id}/transcript`, `POST /v1/staff/voice/purge` | `clinic_id`; key + optional `limit` | read is audited; 503 keys not configured; purge deletes expired only | `workflow` and `status` words; not run live yet: the read and purge |
| Visit readiness | `GET /v1/staff/visits/{visit_id}/readiness`, `POST .../readiness/start`, `POST .../readiness/reply` | key; outcome and the reply text | 404 no run; 409 no content for the visit type; 503 not configured; 403 unless nurse or doctor | `status` words, `waiting_for`, `current_item`; not run live yet: writes |
| Portal claims | `GET /v1/staff/portal-claims`, `POST /v1/staff/portal-claims/{request_id}/resolve` | `clinic_id`; key + decision + patient id | list is an id and a time; the six `result` words | not run live yet: the resolve |
| Break-glass | `GET /v1/staff/break-glass`, `POST /v1/staff/break-glass`, `GET .../break-glass/{grant_id}/messages`, `POST .../break-glass/{grant_id}/review` | key + patient + reason code; grant id | 422 for a code not on the clinic's list; 503 not configured; each read audited; review by an admin who did not open it | not run live yet: writes and the read; the route to list reason codes does not exist |
| Staff recovery | `GET /v1/staff/recovery`, `POST /v1/staff/recovery`, `POST .../recovery/{request_id}/approve`, `.../cancel`, `.../complete` | `clinic_id`; key + account | `result` words; complete 503 when no sign-in administration is set | not run live yet: writes |
| Notes | `GET /v1/staff/notes`, `POST /v1/staff/notes` | `clinic_id`, `patient_id`; key + text + optional parent | 503 keys not configured; erased or null text says so | not run live yet: the write |
| Grievances | `GET /v1/staff/grievances`, `POST /v1/staff/grievances/{grievance_id}/status` | `clinic_id`; key + status | `result` words recorded, replay, invalid_transition | `category` words; not run live yet: the write |
| Staff: activate my own account | `POST /v1/staff/accounts/activate-me` | no body, no key (the route takes none); needs a verified second factor in the token | 200 `{activated}`; 403 if the account is not invited or has no live role | offered on the "cannot use the staff console" page, below that message (which still says no more than the API does); a confirmation first; after `activated` the console re-reads the grants and opens. NOT run live: needs a freshly invited account |
| Patient portal | `GET /v1/portal/me`, `POST /v1/portal/register`, `GET /v1/portal/consents`, `POST /v1/portal/consents`, `POST /v1/portal/activate`, `POST /v1/portal/claims`, `GET /v1/portal/grievances`, `POST /v1/portal/grievances`, `GET /v1/portal/my-data` | patient session only; key + the typed fields | me 403 = no patient account yet (the page offers to create it); consent 409 = notice changed; withdrawal is one click (recorded at once, result shown, "Grant again" beside it) and granting asks for a confirmation; activate / claim `outcome` words; grievance 422 category not on the list, 503 not set up; my-data is audited | the sign-in itself (Supabase phone code) is not run live yet; the clinic id the patient types (no route gives it); the grievance category list (no route gives it) |
| Patient voice (patient, active account) | `GET /v1/portal/voice/preference`, `PUT /v1/portal/voice/preference`, `POST /v1/portal/voice/sessions` | patient session only; the setting needs a key; a session needs key + `clinic_id` (from `GET /public/clinic`), one of three workflow words, and `consent: true` only after the person has read the notice the API supplied (the notice whose purpose is `voice_session` in `GET /v1/portal/consents`; assumed word) and ticked the agreement | preference 403 = not an active patient; session 503 = voice not set up (clinic switch, engine or retention setting), 403 = voice not turned on, 404 = not a patient of that clinic, 422; `created: false` = a repeat of an earlier request; `mic` is not interpreted | NOT run live. The screen opens the session record only: no microphone, no audio connection (no speech service is approved), and the frontend holds no consent text: with no `voice_session` notice in the consents list the screen says it is not set up and the Open button stays disabled. Check that a session row appears for the staff voice transcript list, with the same workflow word |
| Patient details, notices, requests, public grievance (patient, active account; the public route needs no sign-in) | `GET /v1/portal/profile`, `PUT /v1/portal/profile`, `GET /v1/portal/notifications`, `POST /v1/portal/notifications/{notification_id}/read`, `GET /v1/portal/requests`, `POST /v1/portal/requests`, `POST /public/grievances` | key on every write; profile body with ONLY the changed fields (`sex`, `preferred_language`); an unchanged field is omitted; request body `{clinic_id, kind, field}` with no free text; public grievance `{category, text}` with a key of 32 or more characters and NO token | 200 / 201; 403 not an active account; 404 a notice that is gone, or no published clinic for the public route; 422 a sex word not on the list, or a correction without a field; 429 and 503 on the public route; 503 a sex word while the clinic has no list | the sex words are shown as sent (underscores as spaces); whether `null` on a profile field leaves it as it is or clears it (not relied on: the screen never sends null; request 32); notice kinds are fixed words and the opaque reference is never shown; a repeat request answers `created: false` and the screen says "already on record"; the public category is typed because no public list route exists; the per-address limit behind the proxy; nothing in the requests changes the record by itself |
| Patient appointments and messages (patient, active account; both are behind owner switches and answer 503 while off) | `GET /v1/portal/booking/bookings`, `POST /v1/portal/booking/bookings/{booking_id}/cancel`, `GET /v1/portal/booking/slots`, `POST /v1/portal/booking/slots/{slot_id}/book`, `POST /v1/portal/messages`, `GET /v1/portal/messages/threads`, `GET /v1/portal/messages/threads/{thread_id}` | key on every write; slots need `clinic_id`; message body `{clinic_id, topic, text}` with topic `care` or `appointments` and no pre-chosen value | 200 / 201; 403 not an active account or a slot that may not be booked; 404 a booking not yours, or a thread that is gone; 409 slot taken, held, at the booking limit, or a booking already cancelled or started; 422 a blank or over-long text; 429 over the message limit; 503 switch off | with the switches OFF each section says "not turned on" calmly and shows no form; cancel shows only where `cancellable` is true; a repeat book answers `created: false` and the screen says it was already booked for you; the message answer is the same words whatever the red-flag rules found (the screen never shows or hints at the screening result); message text is shown as plain text, an erased one as "erased"; nothing in cancel contacts anyone; the topic `safety` appears only as a thread label |
| Idle sign-out (staff and patient; OFF in the product) | none (browser only) | `IDLE_SIGN_OUT_MINUTES` in `frontend/src/shared/idle.ts` is null for both | none | with the numbers still null nobody is signed out for being idle (leave a console and a portal page open for an hour and check). Once the owner gives a number: leave the page idle for that long and check the sign-in screen appears with the plain note, the page shows nothing of the earlier session, back does not bring data back, and a hidden tab that returns after the time is signed out at once |
| Offer run after a cancel | `POST /v1/staff/scheduling/backfills`, `GET .../backfills/{slot_id}`, `POST .../backfills/{slot_id}/decide`, `POST .../backfills/{slot_id}/reply`, `POST /v1/staff/scheduling/offers/interpret-reply`, `GET /v1/staff/messages/threads`, `GET /v1/staff/messages/threads/{thread_id}/messages` | key + clinic, slot, visit type; action send / skip / stop; outcome + stored message id | 404 no run yet; 409 not waiting for that; 503 not set up; nothing is sent to a patient | the `status` words of a run; whether Send records an offer that then appears in Offers; the suggested reading is only a suggestion; not run live yet: writes |
| Lists that replace typing | `GET /public/clinic`, `GET /v1/portal/grievance-categories`, `GET /v1/staff/break-glass/reasons`, `GET /v1/staff/break-glass/mine`, `GET /v1/staff/visits/upcoming` | `clinic_id` where the route takes one | 404 on /public/clinic = no clinic marked public (the form then asks for a clinic code); 503 on categories = not set up | that each list has rows; that the patient never types a clinic id |
| Offer draft | `GET /v1/staff/scheduling/offers/draft` | `clinic_id`, `visit_type_id` | 200 shows the status word, a closed reason in fixed words, any missing setting names, the ranked candidates (rank, short patient id, priority, joined time) and the draft text verbatim with its two times and the ordering rule; 403; 5xx | the draft text must match the clinic-approved template character for character; the candidate order must match the waitlist; the screen says it is a draft and offers no send; the two times are shown in seconds, as sent |
| Possible duplicates | `GET /v1/staff/identity/possible-duplicates?status=` | `status` (default pending) | 200; 403 (non front desk) | `reason`, `matched_kinds` words |
| Merges | `GET /v1/staff/identity/merges?status=` | `status` only when filtered | 200; 403 | status words |
| Merge detail | `GET /v1/staff/identity/merges/{merge_id}` | id | 200; 404; 403 | `proposed_by`, `second_approved_by` ids |
| Merge approve / unmerge (**flag OFF**) | `POST /v1/staff/merges/{merge_id}/approve`, `.../unmerge` | key | 200; 401 step-up; 409; 403 | only after the owner turns the flag on: step-up challenge with a real token, one retry, same key |

Operations in the client that no screen calls: `GET /readyz` (used only by the live-run guard through the proxy) and
`POST /v1/staff/merges` (propose; not built). The snapshot has more routes (messages, bookings, waitlist, slots); no console screen
uses them yet.

### F9 calls

| Screen | Call | Sends | Handled | Check against the backend |
|---|---|---|---|---|
| Audit log | `GET /v1/staff/audit` | filters, optional `clinic_id` (404 for a clinic you do not audit); `limit` 50; `before_id` for older | 200; 403 not available; 5xx fixed | rows newest first; `next_before_id` pages; do `details` ever hold personal text? |
| Register a patient | `POST /v1/staff/patients` | key; `{clinic_id, name, dob, phone?}` | 200 outcome; 404; 409; 422; 503 | the four outcome words; `candidate_ids` on `review` |
| Referral detail, Run | `POST /v1/staff/referrals/{document_id}/run` | key | 200 job; 404; 403; 503 | which roles may start a run; `replayed` on a retried key |
| Erasure requests | `GET /v1/staff/erasure-requests`, `GET /v1/staff/erasure-requests/reasons` | optional `status` | 200; 403; 5xx | `reason` words; `requested_by` equals my account id for "You" |
| Erasure reasons | `GET /v1/staff/erasure-requests/reasons` | no query | 200 `{reasons}` (sorted); 403 not front desk; 503 until the clinic sets CAREFLOW_ERASE_REASONS | the closed list for the ask form's picklist (the demo pack gives `demo_reason`) |
| Erasure, ask | `POST /v1/staff/patients/{patient_id}/erasure-requests` | key; `{reason}`; step-up | 200; 409; 422; 503 | flag OFF: not called |
| Erasure, approve / cancel | `POST /v1/staff/erasure-requests/{request_id}/approve` and `/cancel` | key; step-up | 200 (`erased` / `cancelled` true); 403; 409 | flag OFF: not called; `false` is a no-op |
| Patient page, full details | `GET /v1/staff/patients/{patient_id}/view` | id; read on a click only | 200; 404; 403; 429; 503 | one audit row per open |

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

## Dev-only: skip the staff second factor (VITE_DEV_SKIP_STAFF_MFA) - SWITCH OFF BEFORE ANY REAL USE

For a live pass on the DEV project with password-only accounts. Start the dev server from a shell that sets `VITE_DEV_SKIP_STAFF_MFA=1` (never in a file). *You should see:* the banner "DEVELOPMENT ONLY: the second factor (authenticator) is skipped in this build" on the sign-in page and on every staff page, and after the password you go straight to the console with no authenticator screen. Without the variable, none of that happens and the normal enrolment, challenge and step-up screens apply. Screens whose routes still refuse without a second factor are recorded as "not tested live: needs second factor", not as failures. Never use it with real patients.

## Pre-launch steps (before any real patient or real clinic use)

Decided by the user, 2026-11-13, after the first live pass was accepted. Do all three, in this order. Each one is a gate: a surprise stops the launch.

1. **Trigger the step-up prompt for real, with typed codes, and the dev-only skip switch OFF.** Start the dev server WITHOUT `VITE_DEV_SKIP_STAFF_MFA`, with the backend requiring a second factor (`CAREFLOW_REQUIRE_AAL2` true). Sign in with the password and a typed authenticator code, then do a write that needs a recently verified second factor (for example an erasure request or approve once its flag is on, or a merge approve; wait until the session's code is older than the backend window, 300 seconds). *You should see:* the write is refused with 401 and `WWW-Authenticate: step-up`, the "Confirm it is you" dialog opens asking for a 6-digit code, and after you type a fresh code the SAME write is retried once with the SAME idempotency key and succeeds. Cancelling the dialog changes nothing and says "A fresh authenticator code is needed to do this. Nothing was changed." This step is not done until a real 401 step-up has been seen; the first live pass never saw one.
2. **Switch the dev-only skip switch off and check that a production build ignores it.** Confirm the shell that starts the dev server does not set `VITE_DEV_SKIP_STAFF_MFA`, and that no committed file names it as a value. Then build with the variable set (`VITE_DEV_SKIP_STAFF_MFA=1 npm run build`) and open the built site (`npm run preview`): *You should see:* the normal sign-in, then the authenticator enrolment or challenge screen, and NO "DEVELOPMENT ONLY" banner on any staff page. `tests/flags.test.ts` must pass (it fails if any build honours the switch).
3. **Create a real auditor account and test the audit reader and the second-factor screens live.** Give the account the auditor role at the clinic, enrol its authenticator, sign in with typed codes. *You should see:* the Audit log page opens with nothing read; "Read the log" lists rows newest first with the clinic column; filters and "Older rows" work; the details of a row open only on a click as plain text. Then, with a second factor in place, walk the screens the backend lists as refusing without one (visit readiness, results and notes, care plans, care-gap set-up, autonomy, break-glass, grievances admin, staff recovery, erasure, staff accounts, portal claims, admin voice transcripts) and record each "You should see" against the screen text and the backend's `detail`, as in `docs/frontend-live-findings.md`.
