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

### 16. FINDING (frontend): Hold and Book say "That could not be found" because the slot and the patient are from different clinics

**What the live run showed.** In Scheduling > Slots, choosing a patient from the search and pressing Hold or Book shows "That could not be found." (the generic `not_found` text).

**Where it comes from.** The backend, as a 404 from `POST /v1/staff/scheduling/slots/{slot_id}/hold` or `.../book`. It is not a patient lookup and not row-level security: those routes do not look the patient up first, they insert the hold or booking, and the database refuses it because the patient is not a patient of the SLOT's clinic (a composite foreign key on `(patient_id, clinic_id)`). The route maps that to a 404 whose `detail` is `"unknown patient for this clinic"`; a slot that really is invisible gives `detail: "not found"`. Row-level security is not involved because foreign-key checks do not go through it.

**Why the two clinics differ.** `GET /v1/staff/scheduling/slots` returns the open slots of EVERY clinic the account can read (each row carries `clinic_id`), while `PatientPicker` searches only the ACTIVE clinic (`active.clinicId`). The run showed both signs of it: the slot rows are "Demo Provider One / Demo Location North / Demo Visit Type A", while the Offers page of the same session lists the visit type "Test visit". Those belong to two different clinics, both named like the demo clinic.

**What the frontend should do.**
1. Show only slots whose `clinic_id` equals the active clinic (or take the clinic from the slot and give `PatientPicker` that clinic id, which needs the account to be front desk of it). The same applies to the bookings list, the holds list and the waitlist, which also span every readable clinic.
2. Show the `detail` of a 404 when it is `"unknown patient for this clinic"` ("This patient is not registered at the clinic that owns this slot") instead of the generic text.
3. Confirm in the browser's network tab before changing anything: the 404 response body of the Hold request should read `{"detail":"unknown patient for this clinic"}`. If it reads `{"detail":"not found"}` instead, tell the backend window: that would mean the slot itself is not visible and this finding is wrong.

**Backend status.** No backend change was made: the refusal is correct, and a more specific answer would add nothing the slot's `clinic_id` does not already give. Not confirmed against live data (live tests run only in CI); the reasoning is from the code and the screenshots.

## Decisions recorded here for later readers

- **TypeScript 7 is not used yet** because typescript-eslint (`<6.1.0`) and openapi-typescript (`^5.x`) do not support it;
  revisit when both do. The frontend pins TypeScript 6.0.3, with a scoped npm `overrides` entry satisfying openapi-typescript's
  peer range (not `--legacy-peer-deps`).
- **ESLint is 9.39.5, not 10**, because eslint-plugin-jsx-a11y 6.10.2 (the latest release) supports ESLint up to 9.
- Versions published in the last 7 days were avoided at install time; i18next, react-i18next, vite, react-router,
  @tanstack/react-query and typescript-eslint are therefore one release behind the newest.
