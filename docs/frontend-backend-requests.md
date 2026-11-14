# Frontend to backend requests

The frontend session never changes `careflow/`, `db/`, `tests/` or `scripts/`. Anything it needs from the backend is written
here, with why it is needed and what the frontend does until it exists.

## Open

### 1. Production CORS (the backend session is already handling it)

In development the Vite dev server proxies `/v1` and `/readyz` to the API, so no CORS is involved. A production browser at the
frontend's origin calling the API's origin needs:

- `Access-Control-Allow-Origin` set to the exact frontend origin(s), never `*` (requests carry a bearer token).
- `Access-Control-Allow-Headers` including `Authorization`, `Content-Type`, `X-Idempotency-Key` and **`X-CareFlow-Active-Role`**
  (the active-role request header proposed in `docs/design/active-role.md`; without it the browser blocks every staff request at preflight).
- `Access-Control-Allow-Methods` including `GET`, `POST`.
- **`Access-Control-Expose-Headers: WWW-Authenticate`.** Without it the browser hides this header from the page's JavaScript,
  so the frontend cannot tell a step-up 401 from any other 401. This is the one that is easy to miss.

### 2. `GET /v1/iam/me`: clinic name, clinic origin, and an operating-profile indicator

Today each role grant is `{clinic_id, role}` only. The staff shell needs, per role grant:

- `clinic_name` (string): shown in the shell. Until it exists the frontend shows a shortened clinic id.
- `clinic_origin` (`"live"` or `"test"`): the persistent LIVE / TEST badge (BUILD-SPEC 19.3) is derived from this. Until it
  exists the frontend shows **"environment unknown"**: it never guesses LIVE or TEST (CLAUDE.md rule 3, unknown is not a value).

And one field at the top level of the response (or on `/readyz`, whichever fits the backend better):

- an operating-profile indicator, `"demo"` or `"clinical"` (BUILD-SPEC 2), so the frontend can degrade visibly and not silently
  when running `clinical` without an approved external route.

`/readyz` currently documents an empty response schema, so nothing there can be generated into the client either.

### 3. The active role must reach the server for least privilege to be real

The staff shell makes a person with several role grants pick ONE active role (BUILD-SPEC 19.3), but today that choice only
changes what the frontend shows: the API still evaluates all of the account's grants on every request. For the choice to
limit what the person can do, the backend would need to accept the chosen grant (for example a request header naming the
clinic and role) and authorise against only that grant. The frontend sends nothing until the backend defines this; please
decide the mechanism. Until then "active role" is a display and navigation choice, not a permission boundary.

A design proposal (no code yet, awaiting the owner's OK) is in `docs/design/active-role.md`: an `X-CareFlow-Active-Role` header, verified
against the live grants in the database and used only to narrow. If adopted, production CORS must also allow that request header.

### 4. Step-up claim shape: RECORDED (2026-10-30, dev project, a real token)

Captured by the console's development-only claim panel from a real access token of a dev staff account after TOTP enrolment and
a refresh (the token itself was never shown, logged or read by the frontend session). The panel prints structure with values
blanked, plus, because the owner ticked the box, the `aal` level and the `amr` method names. Pasted by the owner:

| What | Observed | Backend assumption (`careflow/auth.py`, `step-up-spike.md`) |
|---|---|---|
| `aal` | the string `"aal2"` | exactly `"aal2"`: **matches** |
| `amr` | a list of 2 entries; each has exactly the keys `method` and `timestamp` | a non-empty list, every entry exactly `{method, timestamp}`: **matches** |
| `amr[].method` | `"totp"` and `"password"` (totp listed first) | one entry named `"totp"`: **matches** |
| `amr[].timestamp` | an integer with 10 digits (unix seconds) | an integer count of unix seconds: **matches** |
| `iat` | an integer with 10 digits | not used |
| timestamps vs `iat` | both amr timestamps are **before** `iat` | a timestamp in the future is refused: not triggered |
| after one refresh | the amr timestamps are **unchanged** (`iat` moves) | a refresh keeps the original timestamp: **matches** |

What this does NOT cover: the legacy string form of `amr` (not seen), a token from a LATER sign-in challenge (this one followed
enrolment; the shape is expected to be the same but was not captured), a token older than the step-up window, and the backend
accepting such a token end to end (the live merge route was not called). The window is measured from the original TOTP time and
a refresh does not move it, so after `CAREFLOW_STEP_UP_MAX_AGE_SECONDS` a refreshed token is correctly stepped-down again.

The backend session should record this in `docs/design/step-up-spike.md` (that page is the backend's; the frontend does not
edit it). The frontend's merge approve and unmerge stay OFF until that is done and one live approve with a step-up has worked
against the dev backend.

### 5. F3 console: what the screens need that the snapshot does not give them yet

None of these blocks the F3 screens (they handle the absence), but each leaves a screen showing less than it could, or
depends on a guess. All F3 screens run only against mocks until a live backend is reachable.

- **Patient search labels.** The search route returns ids, `verified`, `registered_via` and `matched_kinds` only. The screen also
  understands `label` (per hit) and `labels_withheld` / `truncated` (on the response), as the per-row label read in
  `docs/design/patient-view-fields.md` would add them. **Those three names are my guess, not a contract.** Please confirm
  the names and types, or tell me what to read instead. Until then they are mock-only.
- **Offer draft.** `GET /v1/staff/scheduling/offers/draft` returns an `Envelope` whose `data` is `object | array` and whose `reason`
  is free text (it can name clinician-owned settings). The screen shows only the `status` word and marks candidate details and
  sending "Coming soon". A typed `data` (the ranked candidates) and a closed vocabulary for `reason` would let it show more
  without ever showing backend text.
- **Scan status after the upload.** `scan` (`clean`, `not_scanned`, `not_checked`) comes back only from `documents/register`. The
  referral list and detail do not carry it, so once the person leaves the upload screen the console cannot show it again.
  Adding it to `ReferralOut` / `ReferralDetailOut` would let "not scanned yet" stay visible. The screen treats every word
  except `clean` as not clean.
- **A signed read URL** for the document a reviewer is looking at (item 10 in `frontend-contract.md`): the detail screen shows no
  file today.
- **Typed task provenance and kinds.** `kind` is a free string and `provenance` an untyped object. The inbox ships a
  hard-coded list of the kinds the resolve route closes (`referral_hold`, `unmerge_review`, `referral_dead_letter`,
  `identity_confirmation_revoked`) and of the urgent kinds (`red_flag`, `screening_unassessable`), copied from
  `careflow/review_tasks.py` and the 0013 migration. If the backend exposed them (an enum, or a `resolvable` flag per task),
  the copy could go. The backend still decides: a wrong copy only shows or hides a button.
  **Backend, 2026-11-04:** every review task (list and detail) now carries `resolvable: boolean` (true for exactly the kinds the resolve route closes), so the copied list can go. The urgent kinds are already `priority: immediate`. `kind` itself stays a string (new kinds appear with new features); a closed enum is not added.
- **Resolve takes no reason.** The screen shows "PLACEHOLDER - clinician to define" where reason codes would go and sends none.
  If a clinician decides a reason is required, the route needs a body field first.
- **Closed words for translation.** `run_state` and the proposal `mode` are strings; the console shows them as given (humanised).
  An enum in the schema would let each get a reviewed Tamil label.
- **Upload storage origin.** The console refuses to send a file to any host other than the Supabase project origin
  (`SUPABASE_URL`), and the CSP's `connect-src` allows only that and the API origin. If storage will live elsewhere, say so:
  `csp.ts` and the check in `UploadPage.tsx` both change.
- **Names.** Lists show short ids (patients, holds, tasks); names are item 1 of `frontend-contract.md`.

### 6. Type `Envelope.data` for tasks and offers, and name the reason words (requested by the owner)

The backend should give the console typed shapes instead of free text, so the UI never has to treat them as strings:

- **`Envelope.data`** (currently `object | array | null`) should be a typed schema per route: for the offer draft, the ranked
  candidates and the draft message; for review tasks, a typed `provenance` per task kind (the document id of a referral hold,
  the rule id of a red flag, and so on) in place of the untyped object.
- **`Envelope.reason`** (currently free text, for example `not_configured: ...` plus the names of missing clinician-owned settings)
  should be a closed set of reason words, listed as an enum in the schema, with any setting names in a separate typed field.
  Each word then gets a reviewed English and Tamil label in the console, and the console shows no backend text.
- **`kind`** on review tasks (and on holds and merges where applicable) should be an enum, so the console can label each kind
  and stop carrying its own copy of which kinds can be resolved or are urgent.

Until then the console shows only the `status` word of an envelope, never `reason` or `data`, and marks candidate details and
sending "Coming soon".

### 7. F4: questions the referral decision, revoke and merge screens raised

All three screens are built to the snapshot; these are the places where the schema does not say enough.

- **"Edit" is hidden (owner decision, 2026-10-30).** `POST /v1/staff/referrals/{id}/review` takes only `decision` (`approve`, `edit`,
  `reject`) and records only the word. The console offers **approve and reject only**; "edit" is shown as "Coming soon" and
  cannot be chosen or sent (a test pins it). What a reviewer may edit is a clinician question: **clinician item 26**. No reason
  is collected or sent either (a clinician defines reason codes; the screen shows "PLACEHOLDER - clinician to define").
- **`review_task: on_record` (confirmed by the backend).** It means NOTHING NEW was recorded: a replayed key, or the record was already
  unverified. It does not mean a review task exists. The console says "Nothing new was recorded: this was already done, or the
  record was already unverified." and does not say the confirmation was revoked. An unknown word is never read as success either.
- **The revoke reasons are not final (clinician item 23; the clinic has not accepted them).** The route accepts `wrong_person`, `planted_or_suspect`, `mistaken_click`
  (`docs/design/identity-confirmation-revoke.md` leaves the list open). The console shows them as given, in English in both
  languages, with a marked placeholder saying the wording is not final.
- **Merge approve and unmerge are built but OFF.** `mergeActions` is a compile-time flag that is false in every build except
  `--mode e2e` (the mock browser build), pinned by tests. It stays off until the `aal` / `amr` claim shape has been seen in a
  real token (`docs/design/step-up-spike.md`). Turning it on is a one-line change in `vite.config.ts` plus the test that pins it.
- **Unmerge `decisions_flagged` (confirmed by the backend).** It counts bookings, holds, waitlist entries and match-review rows
  recorded while the link was active (capped at 500 per kind); a review task lists them and nothing is undone. The console says
  exactly that. The cap means a count of 500 or more per kind is a floor, not a total; the screen does not try to tell.
- **No propose-merge screen.** `POST /v1/staff/merges` exists but F4 does not build it (not asked for). A proposal made
  elsewhere appears in the merge list as "Awaiting second approval".

### 8. F5 live pass: the dev seed needs rows for four lists

The front-desk live run (dev clinic `7246aea2-97cd-4405-994c-46fa5a1652ee`) found these lists EMPTY, so the shape of their rows and the
detail screens behind them were never checked against the backend:

- review tasks (`/v1/staff/review-tasks`, all three statuses) and the task detail screen
- slot holds (`/v1/scheduling/holds`, all four statuses)
- identity merges (`/v1/identity/merges`) and the merge detail screen
- possible duplicates (`/v1/identity/possible-duplicates`)

The seed already there has 5 fictional patients, 2 locations, 2 providers, 2 visit types, 3 priority levels, 8 slots and 4 referral
documents, with no human decisions. Once the seed gives each of these lists at least one row, the front-desk pass is re-run to cover
the referral queue, tasks, holds, merges and duplicates. Until then the nurse and doctor passes report these lists as "no rows to check".

### 9. The clinic time zone

Owner decision (2026-11-07): every time the console shows or takes is in the CLINIC's time zone, from a clinic setting, not the browser's.
The backend sends no such setting yet, so the console uses a labelled demo placeholder (`Asia/Calcutta`, `frontend/src/shared/clinicTime.ts`).
Please expose the zone (an IANA name) on `GET /v1/iam/me` per grant, or on a clinic read, and the console will use it
(`setClinicTimeZone`). Until then the slot form, the result form and every time shown say "demo placeholder".

### 10. `message_id` on an offer row

`OfferRowOut` has no message id, so "Copy offer text" finds the message created with the offer by time (within a minute). When the
snapshot has `message_id`, the console switches to it and deletes the guess (owner decision 2026-11-07).

### 11. Closed words as schema enums

Several fields reach the console as bare strings, so it humanises the word it is sent (for example "task overdue") and has no English or
Tamil label for it. Please make these enums in the schema; the console will then add labels for every value:
`AlertOut.alert_class`, `CarePlanRowOut.status` (the console offers approve and reject on `confirmation_requested`, a word it took from
the backend code), `CareGapRowOut.source` (it acts on `unresolved_barrier`, `after_the_fact_review` and `overdue_recurrence`),
`DashboardMetricOut.metric_id`, `VoiceSessionRowOut.workflow` and `.status`, `ReviewerStatOut.verdict`, `CostCanvasOut.state` and `.reasons`.

### 12. Autonomy history: DONE (2026-11-10)

The backend added `GET /v1/staff/console/autonomy/history`; the console shows it under the pending proposals.

### 13. A route to list the break-glass reason codes: DONE (2026-11-10)

The backend added `GET /v1/staff/break-glass/reasons?clinic_id` (and `GET /v1/staff/break-glass/mine?clinic_id` for your own open grants). The original request follows.

`POST /v1/staff/break-glass` takes a reason code that must be on the clinic's list (`iam.break_glass_reasons`), but no route returns the
list, so the console can offer only a field limited to the code's shape (lowercase letters, digits, underscores) and the backend's 422
if the code is not on the list. A route like `GET /v1/staff/console/reason-codes` for these codes would let the console show a picklist
instead.

### 14. A way to find a visit for visit readiness: DONE (2026-11-10)

The backend added `GET /v1/staff/visits/upcoming?clinic_id&limit` (the booking id is the visit id). The original request follows.

Visit readiness is keyed by a visit id, and no route lists visits, so the console asks for the id. A visit list (or the visit id on
a booking row) would remove that.

### 15. The clinic id for the patient forms, and the grievance categories: DONE (2026-11-10)

The backend added `GET /public/clinic` (no sign-in; 404 until the owner publishes a clinic) and `GET /v1/portal/grievance-categories`. The original request follows.

The patient portal's claim and grievance requests need a `clinic_id`, but a patient has no way to know one and no route gives it. The
console asks the patient to type a "clinic code" (a uuid the hospital tells them), which is poor. A way to learn it would remove that:
either the hospital's id on a public configuration route, or the clinic implied by the invitation. Likewise the grievance `category` must
be on the clinic's list but no route returns the list, so the form takes a code limited to its shape and the backend answers 422 if it is
not on the list. A route that lists the categories (with labels) would let the form show a picklist.

### 16. A route to store the patient's typed reply: DONE (2026-11-12)

`POST /v1/staff/scheduling/offers/replies` is in the snapshot and the offer run uses it: front desk types the reply, confirms, and the returned `message_id` goes to the interpret and reply steps. The original request, kept for the record:: DONE (2026-11-11)

The backend added `POST /v1/staff/scheduling/offers/replies` (front desk; body `{clinic_id, patient_id, text}`; 201 `{message_id, thread_id, created, screening_id, outcome}`). The text is stored encrypted as a patient message in the appointments thread and screened by the red-flag rules in the same call; use `message_id` in the backfill reply and interpret-reply. The original request follows.

The offer run's reply step takes the id of a STORED message (`POST .../backfills/{slot_id}/reply` and `POST .../offers/interpret-reply` both
need `message_id`), but no route in the snapshot creates one, so the front desk cannot type in what a patient said. The console lets the
person pick a stored reply from the patient's appointments thread, read it, see a suggested reading and enter it; it cannot store new text.
A route to record a staff-entered reply (topic appointments, sender recorded, audited) would complete the step.

### 17. FINDING (frontend): Hold and Book say "That could not be found" because the slot and the patient are from different clinics

**What the live run showed.** In Scheduling > Slots, choosing a patient from the search and pressing Hold or Book shows "That could not be found." (the generic `not_found` text).

**Where it comes from.** The backend, as a 404 from `POST /v1/staff/scheduling/slots/{slot_id}/hold` or `.../book`. It is not a patient lookup and not row-level security: those routes do not look the patient up first, they insert the hold or booking, and the database refuses it because the patient is not a patient of the SLOT's clinic (a composite foreign key on `(patient_id, clinic_id)`). The route maps that to a 404 whose `detail` is `"unknown patient for this clinic"`; a slot that really is invisible gives `detail: "not found"`. Row-level security is not involved because foreign-key checks do not go through it.

**Why the two clinics differ.** `GET /v1/staff/scheduling/slots` returns the open slots of EVERY clinic the account can read (each row carries `clinic_id`), while `PatientPicker` searches only the ACTIVE clinic (`active.clinicId`). The run showed both signs of it: the slot rows are "Demo Provider One / Demo Location North / Demo Visit Type A", while the Offers page of the same session lists the visit type "Test visit". Those belong to two different clinics, both named like the demo clinic.

**What the frontend should do.**
1. Show only slots whose `clinic_id` equals the active clinic (or take the clinic from the slot and give `PatientPicker` that clinic id, which needs the account to be front desk of it). The same applies to the bookings list, the holds list and the waitlist, which also span every readable clinic.
2. Show the `detail` of a 404 when it is `"unknown patient for this clinic"` ("This patient is not registered at the clinic that owns this slot") instead of the generic text.
3. Confirm in the browser's network tab before changing anything: the 404 response body of the Hold request should read `{"detail":"unknown patient for this clinic"}`. If it reads `{"detail":"not found"}` instead, tell the backend window: that would mean the slot itself is not visible and this finding is wrong.

**Backend status.** The refusal is correct, so it is unchanged. What the backend added (2026-11-11): an optional `clinic_id` query parameter on `GET /v1/staff/scheduling/slots`, `/bookings`, `/holds`, `/waitlist`, `/offers`, `/providers`, `/locations`, `/visit-types` and `/priority-levels`. Pass the active clinic's id and only that clinic's rows come back; a clinic you cannot read (or that does not exist) is the one 404. Without it the lists still span every readable clinic. Not confirmed against live data (live tests run only in CI); the reasoning is from the code and the screenshots.

### 17 (frontend status). Item 17 is fixed on the console side

The console now shows only the active clinic's rows in the slot, booking, hold, waitlist and offer lists and in the reference lists (providers,
locations, visit types, priority levels), and says "This patient is not registered at the clinic that owns this slot" for the 404 detail
"unknown patient for this clinic". The backend has added the optional `clinic_id` to the slot, booking, hold and waitlist lists (2026-11-12) and the console now sends it
(the client-side filter stays as a guard). The offers and the four reference lists took the same parameter on 2026-11-12 and the console sends it there too.

### 18. A route to list the clinic's erasure reasons: DONE (2026-11-12, `GET /v1/staff/erasure-requests/reasons`; the ask form uses it)

`POST /v1/staff/patients/{patient_id}/erasure-requests` accepts only a reason from the clinic's list (a required setting, `CAREFLOW_ERASE_REASONS`,
503 while it is unset), but no route returns the list, and the console keeps no list of its own (which reasons a clinic may erase for is a privacy and
legal decision). Please add a read route like `GET /v1/staff/console/reason-codes` (`{configured, codes[]}`) for front desk of the clinic. Until then the
console's "Ask for an erasure" shows "not set up" and cannot be submitted. The seam is `src/staff/pages/erasureReasons.ts`.

### 19. Decided (2026-11-12)

The audit log stays auditor only. Run stays offered to front desk, nurse and doctor. Erasure screens say "a recent authenticator code" (the backend's 300-second window stands). When the erasure reasons route is in the snapshot, the ask form switches to it; when `GET /v1/staff/audit` takes `clinic_id`, the console sends the active clinic and keeps the clinic column. The erased/kept wording stays a placeholder for the privacy owner and counsel.

### 19 (original questions). Questions from the F9 screens

- The audit route says "an auditor of some clinic" and not admin, so the audit reader is shown to auditors only. Should an admin read it too?
- `POST /v1/staff/referrals/{document_id}/run` says only "403 if you may not start a run". The console offers Run to front desk, nurse and doctor
  and lets the backend decide; please say which roles may.
- The audit rows span every clinic the auditor audits; the console shows them all with the clinic id. Should the route take `clinic_id`?
- `GET /v1/staff/console/red-flag-rules` does not say who wrote a version, so the console cannot hide Approve and Reject from the author; it hides them from anyone who already decided and shows the database's `author_cannot_decide` refusal. If the author id (or a "you wrote this" flag) can be returned, the console will hide the buttons from the author too. There is no retire route; `retired` is shown as a status only.
- A person holding front desk and the supervisor role chooses the role per sign-in like any other pair. The supervisor sees only the task inbox in the console; confirm that is the intent (the alerts route does not list the supervisor).
- **DONE (2026-11-14, snapshot 67ccb4a):** `authored_by_you`, the withdraw route and two-person retire (decision `retire`) are in the snapshot and built. Original request: an `authored_by_you` flag (boolean) on each rule version in `GET /v1/staff/console/red-flag-rules`. When it is in the snapshot the console will hide Approve and Reject from the author (keeping the confirmation and the `author_cannot_decide` refusal as the fallback). Also requested, when built: the withdraw route and the two-person retire route, so their buttons can be added.

### 20. Patient profile: name, sex, preferred language (requested 2026-11-14, frontend spec gap) - ROUTES LANDED, screen built (F14); the avatar is not built
`GET /v1/portal/me` returns no display name, and `POST /v1/portal/register` takes no body, so the portal cannot show the initials avatar and name (BUILD-SPEC 19.3) or collect sex and preferred language (19.4). Please add a way to read and set them (name, sex, preferred language), with the closed word lists in the schema.

### 21. Family members and guardians (requested 2026-11-14)
No dependant or guardian routes exist (BUILD-SPEC 19.3, 20.1, PATIENT-WORKFLOWS A3). Needed: list and add a dependant, act on a dependant's behalf, and the recorded proxy trail. Counsel has to confirm the under-18 specifics first; the frontend writes no consent wording for it.

### 22. Notification channels and optional email (requested 2026-11-14)
No route to read or set the notification preference or an optional email address (19.3, 19.4).

### 23. Devices and sessions (requested 2026-11-14)
No route to list a patient's signed-in devices and revoke one (19.3 Security, PATIENT-WORKFLOWS H3). Passkeys would be a new auth feature and need the owner's approval first.

### 24. Patient erasure, correction and nominee requests (requested 2026-11-14) - ROUTES LANDED, screen built (F14)
The patient path is the grievance channel with whatever category words the clinic configures. Please say whether these are separate request types, and if so add the routes or the category words (20.1). The console invents no category.

### 25. Staff invite fields (requested 2026-11-14)
`InviteIn` has only `email`, `clinic_id` and `role`. BUILD-SPEC 19.4 also lists name, mobile and the medical-council registration number (doctors and nurses). Please add them, or say they are held elsewhere.

### 26. Grievance without signing in (requested 2026-11-14) - ROUTE LANDED, form built on the sign-in page (F14); the category is now a list from `GET /public/grievance-categories` (the typed code is gone)
PATIENT-WORKFLOWS H2 says any patient can reach the grievance channel without signing in. The only grievance routes need an active patient account. Please add a public route, or confirm the public footer contact is the whole channel.

### 27. Protocol card and message-template editor routes (requested 2026-11-14)
BUILD-SPEC 20.2 describes an admin card editor that validates against the card schema and runs the card's own test cases before publishing. No card or template routes are in the snapshot. Until they are, there is no screen. The console will invent no card or template content.

### 28. Patient scheduling routes (requested 2026-11-14) - ROUTES LANDED (booking list, slots, book, cancel); screen built (F14)
Slots, holds, bookings and cancel exist only under `/v1/staff`. Patient booking and cancelling (PATIENT-WORKFLOWS B1, B3, B5) need patient-scoped routes, with the server enforcing that only a future appointment can be cancelled.

### 29. Patient messaging routes (requested 2026-11-14) - ROUTES LANDED (send, threads, thread); screen built (F14)
Threads and messages exist only under `/v1/staff`. The patient needs to list their own threads by topic, read an offer, send a reply, and see the account-stage limits (B2, B4, D1 to D3). A rejected attempt should look identical to a missing thread, as the workflows say.

### 30. Patient visit-readiness routes (requested 2026-11-14)
No patient route for intake questions, the procedure checklist or reporting a barrier (E2 to E4).

## Backend status, 2026-11-14 (answers to the open items; the frontend may delete a line once it has read it)

Built in this pass (no new decision was needed):

- **19 (red-flag rules):** `authored_by_you` is on each version in `GET /v1/staff/console/red-flag-rules` (a boolean; the author id is never returned). `POST /v1/staff/console/red-flag-rules/{rule_id}/withdraw` withdraws a DRAFT (status `withdrawn`). `POST .../{rule_id}/decision` now takes `retire` as well as `approve` and `reject`: two different doctors each record `retire` on an APPROVED version; a retired version never comes back. `POST /v1/staff/console/red-flag-rules` lets a doctor write a DRAFT in a restricted language (literal phrases and token sets, no regular expression). Decision words added: `retired`, `withdrawn`, `not_approved`. The supervisor sees only the task inbox by design (the alerts route does not list that role).
- **6 / 5 (typed envelope):** `GET /v1/staff/scheduling/offers/draft` now returns `OfferDraftOut`: `status`, a closed `reason` (`not_configured` or `no_ranked_candidates`), `missing_settings` (the setting names, in their own field) and typed `data` (`candidates`, `draft`). Review-task `kind` stays a string by decision (new kinds appear with new features); `resolvable` and `priority` already carry what the console needs.
- **11 (closed words as enums), 2 (clinic name, origin, profile), 9 (clinic time zone), 10 (`message_id` on an offer row):** already in the snapshot (`Me`, `OfferRowOut`, the enums).
- **Revoke response:** `admin_alert` (`raised`, `not_for_this_reason`, `not_configured`, `on_record`) is on the revoke result; a new alert class `identity_revoked_suspect`; a new staff role `front_desk_supervisor`.
- **Medications:** `POST` and `GET /v1/staff/medications` (a code from the clinic's vocabulary setting, a verified flag, times; no dose or free text).

Built on 2026-11-14 after the owner's decision (migration 0080, mechanism only, wording and word lists are required settings): **20** (`GET`/`PUT /v1/portal/profile`; the name comes from the patient's own record; sex words need `CAREFLOW_SEX_WORDS`), **22** (in-app notices only: `GET /v1/portal/notifications`, `POST .../{id}/read`; no e-mail, SMS or push), **24** (`POST`/`GET /v1/portal/requests`: kind erasure, correction or nominee, a closed field name for a correction, NO free text; front desk works them at `GET /v1/staff/patient-requests` and `POST .../{id}/status`, and may link the existing two-person erasure request), **26** (`POST /public/grievances`: no sign-in, a required per-address limit, an owner-managed hourly cap, an idempotency key of 32 or more characters, the same encrypted storage and admin flow as a signed-in grievance; the grievance list rows gain `source`), and the **voice consent notice** (`voice_session` is a consent notice purpose served by `GET /v1/portal/consents`, a labelled placeholder in the demo pack).

**29 (patient messaging), built 2026-11-14, mechanism, OFF unless the owner sets `CAREFLOW_PATIENT_MESSAGING=on`** (sending also needs the keys and `CAREFLOW_MESSAGE_MAX_PER_MINUTE`; 503 otherwise): `POST /v1/portal/messages` (`clinic_id`, `topic` care or appointments, `text`; care only at stage `active`; never `safety`), `GET /v1/portal/messages/threads`, `GET /v1/portal/messages/threads/{thread_id}` (text decrypted for the owner of the thread; a thread that is not yours or is hidden by your stage is one 404). The red-flag rules screen the text before it is stored and the answer never says what they found; no reply is written by the system. Who you are is checked before the setting (non-patient 403, patient 503 while off).

**28 (patient booking), built 2026-11-14, mechanism, OFF unless the owner sets `CAREFLOW_PATIENT_BOOKING=on`** (503 otherwise, after your identity is checked): `GET /v1/portal/booking/slots?clinic_id=` (open FUTURE slots of the visit types the owner listed; nothing is offered until the owner lists some), `POST /v1/portal/booking/slots/{slot_id}/book` (a slot held for you is bookable; one held for someone else or for nobody is not; 409 if taken, past, at the owner's per-patient cap, or the key was reused; 403 for anything you may not book), `GET /v1/portal/booking/bookings` (each with `cancellable`), `POST /v1/portal/booking/bookings/{booking_id}/cancel` (a FUTURE booking of your own; 404 if it is not yours; 409 if already cancelled by another request or the slot has started). The provider's name is not shown (a privacy decision for the owner).

NOT built, after the design pass (2026-11-14), and why:

- **21 family members and guardians:** acting for a dependant means every patient-scoped definer function (profile, requests, messaging, booking, consent, export) must accept an "acting for" identity and check a recorded guardianship. That changes the authorisation of every patient route at once, and the under-18 rules (who may act, from what age, what the proxy trail must hold) are counsel's. Building the table without those rules would be inventing the legal content. It needs counsel's specifics first, then one reviewed migration that changes all the patient functions together.
- **27 protocol card and message-template editor:** there is no card or template table (`kb.cards` does not exist) and no card schema in the repository. Designing the schema and the publish rules is the clinical lead's decision (spec 20.2 says cards are clinician content, validated against their own test cases, published by two people). Needs a card schema from a clinician first.
- **30 patient visit readiness:** the owner's standing decision is that a nurse or doctor relays the patient's replies (there is no patient-facing channel), and the readiness run is a graph that only a nurse or doctor can write to. A patient route would be the patient-facing channel that decision excludes. A patient can already answer through messaging (29) and the nurse relays it into the run. Devices and push registration are skipped (they need an outside service).

Older items, NOT built, and why (each needs a decision or a service first):

- **5 (scan status on the referral list, a signed read URL):** no table records a malware verdict per document yet, and a signed read URL is a storage call that is only verified for the synthetic demo. Waits on the scanner decision (checklist 2).
- **20 (profile: name, sex, preferred language):** new personal data fields: the privacy owner's field list (and the sex word list) must be decided first.
- **21 (family members and guardians):** counsel confirms the under-18 specifics first.
- **22 (notification channels, optional e-mail):** no SMS or e-mail provider is approved; the preference words and e-mail as a verified contact are undecided.
- **23 (devices and sessions):** a call to the Auth admin API, which stays unwired (owner decision); passkeys are excluded.
- **24 (erasure, correction, nominee as separate request types):** counsel decides whether they are separate types or categories; the category list is the clinic's required setting `CAREFLOW_GRIEVANCE_CATEGORIES`.
- **25 (staff invite fields):** new personal data fields and the unwired Auth admin call.
- **26 (grievance without signing in):** an unauthenticated route taking free text needs abuse limits and counsel's wording; undecided.
- **27 (card and template editor):** protocol cards and templates are clinician content with no table yet (`kb.cards` does not exist); needs a card-schema design and a clinician first.
- **28 to 30 (patient scheduling, messaging and readiness routes):** they depend on the account-stage gating, consent and guardian decisions (items 21 and 22) and on the patient-facing channel decision for readiness (owner decision so far: none; a nurse or doctor relays replies).

### 31. Where the voice session notice comes from: DONE (2026-11-14, `voice_session` is a closed `purpose` value)
The voice consent text must come only from the API. The frontend currently looks in `GET /v1/portal/consents` for a notice whose `purpose` is `voice_session` (an ASSUMED word: the snapshot types `purpose` as a free string). It shows that notice exactly as sent and blocks the Open button until one exists; with none it says voice is not set up. Please confirm the word, or give the notice its own route. The notice is not one of the three consent purposes, so the console does not offer Grant or Withdraw on it. The demo pack should supply a labelled placeholder.

**31 (voice notice), answered 2026-11-14:** `voice_session` is now a closed value of the consent notice `purpose` (the schema enum on `NoticeOut` and `ConsentIn`), served by the existing `GET /v1/portal/consents` like the other purposes; the demo pack carries a labelled DEMO ONLY placeholder notice and label for it; outside the demo the text is the owner's `CAREFLOW_CONSENT_NOTICES` entry (counsel's wording); with no notice published the list has no `voice_session` item. It can be granted or withdrawn like the others (an ordinary consent event on the current notice), so the console may offer or hide those buttons as it chooses. The separate route and setting added earlier in this pass (`/voice/consent-notice`) were removed.

### 32. What does null mean in the profile write? DONE (answered 2026-11-14; the profile now has a Clear action with a confirmation)
`PUT /v1/portal/profile` takes `sex` and `preferred_language`, each a value or null. The description says it "sets values", but not whether null leaves a field alone or clears it. The portal sends ONLY the fields the person changed, never null, and offers no way to clear a value. Please say in the route description what null does and whether a person may clear a field; if clearing is allowed, say how the clinic's list (or none) applies. Until then clearing stays unbuilt.

**32 (null in the profile write), answered 2026-11-14:** a field you leave out of `PUT /v1/portal/profile` is left as it is; a field you send as an explicit `null` is cleared, and a person may clear either one. Clearing needs no word from the clinic's list (a cleared sex is no value, not a word), so it works whether or not `CAREFLOW_SEX_WORDS` is set; a non-null sex word is still checked against the list (422 if not on it, 503 if the list is not configured). The route description says so.

**33 (public grievance categories), built 2026-11-14:** `GET /public/grievance-categories` (no sign-in) returns `{categories: [...]}`, the closed sorted list from the required setting `CAREFLOW_GRIEVANCE_CATEGORIES` (the same list the filing route checks). 503 until that list AND the per-address limit `CAREFLOW_PUBLIC_READ_IP_PER_MINUTE` (required, no default) are set; 429 over the per-address limit in any 60 seconds. It reads no database table and nothing about any person. The demo pack carries a labelled DEMO ONLY limit.

**32 again, in plain words:** for each profile field, `sex` and `preferred_language`: leave it out of the request and the stored value stays; send `null` and it is cleared; send a value and it is set (a sex word must be on the clinic's list). The route description says so and `tests/test_profile_null_semantics.py` checks it.

### 35. `allowed_next` on the staff patient-request list and two new review-view fields: DONE (2026-11-14)
- **`GET /v1/staff/patient-requests`**: every row now has **`allowed_next`**, the statuses the backend would accept next for that row: from `open` it is `["acknowledged", "resolved", "declined"]`, from `acknowledged` it is `["resolved", "declined"]`, from `resolved` or `declined` it is `[]`. It is exactly the rule the database function applies (`iam.update_patient_request`). Only a front-desk person of the clinic can list or change these requests, so there is no per-role variation; the status change itself still needs a recent second factor.
- **`GET /v1/staff/referrals/{document_id}/review-view`**: two new fields, **`model_id`** (the model that answered, e.g. `typesafe/jev-router`) and **`decision_rule`** (the rule the accepted answer passed, e.g. `chosen_option_ranks_first`), beside the existing `engine` and `confidence` and each alternative's `probability`. All are `null` when not recorded (a proposal made before this, or by the fake demo engine, shows them as null: never shown as 0 or as high). The confidence is the model's own number and is **never calibrated**; show it as such. `openapi.snapshot.json` carries both changes; regenerate the frontend types from it.

### 34. Idle sign-out durations: DECIDED (2026-11-14)
Decided by the user: idle sign-out is on, driven by two build-time settings with no default (`CAREFLOW_IDLE_MINUTES_STAFF`, `CAREFLOW_IDLE_MINUTES_PATIENT`), with a "still there?" warning one minute before sign-out. The demo and browser-test builds supply labelled placeholders (15 minutes for staff, 10 for patients); the real values are the privacy owner's and are a pre-launch checklist row (docs/frontend-live-checklist.md). Nothing is needed from the backend.

## Decisions recorded here for later readers

- **TypeScript 7 is not used yet** because typescript-eslint (`<6.1.0`) and openapi-typescript (`^5.x`) do not support it;
  revisit when both do. The frontend pins TypeScript 6.0.3, with a scoped npm `overrides` entry satisfying openapi-typescript's
  peer range (not `--legacy-peer-deps`).
- **ESLint is 9.39.5, not 10**, because eslint-plugin-jsx-a11y 6.10.2 (the latest release) supports ESLint up to 9.
- Versions published in the last 7 days were avoided at install time; i18next, react-i18next, vite, react-router,
  @tanstack/react-query and typescript-eslint are therefore one release behind the newest.
