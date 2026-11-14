# Frontend status

Updated 2026-11-10, branch `frontend-f6` (merged to `main` at the end of each batch). Scope: `frontend/` only. The backend, the database, CI
and the design notes are not touched from this side; what the frontend needs from them is in `docs/frontend-backend-requests.md`.

Two flags are OFF and stay OFF until the owner says: merge approve and unmerge (`mergeActionsEnabled`), and sending the active-role header
(`SEND_ACTIVE_ROLE_HEADER`). Accessibility is a goal we test toward (WCAG 2.2 AA), not a claim of conformance, in the docs and in the UI.

## 1. What is built

| Phase | What |
|---|---|
| F0 to F2 | Scaffold, env allowlist, production CSP, generated API client with a freshness check; landing page; staff sign-in with a second factor, role-aware shell, step-up handling |
| F3 | Referrals and upload, review tasks, patients (search, record), scheduling reference lists, holds, offer draft, LIVE / TEST badge |
| F4 | Referral decisions, identity revoke, merge and duplicate lists, merge actions (flag OFF) |
| F5 | Live-run tooling (`npm run e2e:live`), schema checker, per-role reports |
| F6 | Error boundary, offline banner and held-back writes, session expiry, active-role plumbing (flag OFF), the clinic's own time zone |
| F7 batch 1 | Scheduling: slots (hold, book), bookings (cancel), waitlist (join), offers (list, copy text by `message_id`, mark delivered; nothing is sent), setup; reviewer view of a triage proposal; the clinic's reason list on a decision |
| F7 batch 2 | Alerts (acknowledge; the audience role is the escalation state), dashboard, care gaps panel, care plans, results |
| F7 batch 3 | Care-gap writes, staff accounts, autonomy (mode, two-person switch, history), reviewer statistics, cost canvas, voice transcripts |
| F7 batch 4 | Visit readiness, portal claims, break-glass, staff recovery, notes, grievances, result-correction tasks |
| F9 (2026-11-12) | The routes the console did not call yet: the audit reader (auditor only), register a patient (front desk, on the Patients page), the two-person erasure screen (list always; ask, approve and cancel behind a flag that is OFF; the reasons list has no route yet, so asking shows "not set up"), a Run button on the referral detail (front desk, nurse, doctor), and the decrypted record view (**pending the privacy owner's confirmation, see section 6**) |
| F10 (2026-11-13, backend b2a3cbf) | Red-flag rules under Admin (doctor and admin): status, origin, content exactly as sent, who decided as ids; a doctor approves or rejects a DRAFT version after a confirmation (two different doctors, never the author, second factor; admins read only); the front-desk supervisor role sees the task inbox; the alert class `identity_revoked_suspect` has a label; a revoke shows `admin_alert`. No rule content is written or suggested by the console. There is no retire action in the API, so none is built |
| F10c (2026-11-13, backend b467d89) | Rule authoring: a doctor writes a DRAFT version in the restricted language (phrases and token sets; no regular-expression field), with the route's own limits checked before sending, a review step, the fixed 409 and 422 states, and the draft shown as a draft that is never active. The list also shows token sets. Withdraw and two-person retire are not in the snapshot yet; the author flag for hiding Approve and Reject from the author is not either |
| F10b (2026-11-13, backend f70d373) | Medications on the patient record (nurse and doctor): the list (code as sent, verified, times, replaced rows marked), record, and correct or stop (a new row naming the old one; nothing edited). No list of medicines and no meaning for a code in the console. The session spend ledger and the gold-set runner added no routes, so they have no screen |
| F15 (2026-11-14) | Hardening and verification pass (`docs/frontend-security-review.md`): whole unit suite (1278) and whole Playwright suite (390 of 391; the one stall is explained there and passes alone) run; `npm audit`: 0 in what ships, 7 moderate in the lint toolchain (listed); CSP checked against what loads (inline styles removed); a source-scan test pins no unsafe patterns, no console output, storage limited to language and role; both query caches now keep nothing after a screen closes; idle sign-out built for staff and patients, OFF until the owner gives durations (request 33) |
| F14 (2026-11-14) | Offer draft shown read-only on the scheduling screen (ranked candidates, draft text, the two times, the ordering rule; nothing sends). Patient portal: "Your details" (profile), "Your notices" (list, mark as read), requests about the record (erasure, correction, nominee; confirmation first; no free text), a complaint without signing in on the sign-in page, "Appointments" (your bookings, cancel a future one, book an open time; each after a confirmation) and "Messages to the hospital" (send on care or appointments after a confirmation; read your own conversations as plain text). Both answer a calm "not turned on" while the owner switches are off. Voice notice: the backend now serves `voice_session` through the consents list; no frontend change. Tamil pack regenerated (`npm run i18n:pack`) |
| F13 (2026-11-14) | Staff: activate my own account (`POST /v1/staff/accounts/activate-me`) on the "cannot use the console" page, with a confirmation. Portal: consent withdrawal is one click with a "Grant again" button (granting keeps its confirmation); the voice notice comes only from the API (the frontend placeholder is gone; no notice means "not set up" and a blocked Open button; request 31). Typed offer draft types adopted. Tamil review pack and README regenerated for all 1614 strings by `npm run i18n:pack` (a test checks it is current). Left unbuilt by decision: offers send, message screen, patients labels |
| F12 (2026-11-14, backend 67ccb4a) | Red-flag rules: Approve and Reject are hidden from the author (`authored_by_you`), "Withdraw this draft" and "Retire this version" (two different doctors; one decision alone retires nothing), each with its own confirmation showing the content; the status "Withdrawn" and the result words retired, withdrawn and not_approved. Clinician sheet item 37 |
| F11 (2026-11-14) | Spec gap list (`docs/frontend-spec-gap.md`). Patient portal: the Voice section (own setting with a confirmation; session form with the three workflows, nothing chosen, a placeholder for the consent notice and an unticked agreement; fixed 403, 404, 422 and 503 states; opens the session record only, no microphone and no audio connection) and the consent history shown from the downloaded file. Backend requests 20 to 30 record everything else the spec asks for that has no route |
| F8 follow-up 2 (2026-11-12) | The scheduling lists and reference lists show the active clinic only (item 17); Hold, Book and Join say "This patient is not registered at the clinic that owns this slot" for that 404; the offer run lets front desk type the patient's reply (saved encrypted, text dropped from the page, new message id passed to the reading and the entry); the stored-reply picker stays for replies saved earlier |
| F8 follow-up (2026-11-11) | The clinic id comes from `GET /public/clinic` and the grievance category from `GET /v1/portal/grievance-categories` (a typed code is the fallback); consent notices show the patient-facing `label`; break-glass uses the reason list and my open grants; visit readiness picks from `GET /v1/staff/visits/upcoming`; labels for the readiness status, message sender and topic, voice workflow and status, reviewer verdict, cost state and reasons; the offer run after a cancel (prepare, send, skip, stop, enter the reply) |
| F8 (batch 5) | The patient portal on the sign-in and sign-up pages: phone-code sign-in, create the patient account, notices shown exactly as sent, grant or withdraw, activate, claim a record, file a grievance, download my data |

Every write carries an idempotency key, reused on a retry. A step-up challenge asks once and retries once. Messages shown for errors are
fixed per problem kind, never backend text. Every screen has loading, empty and error states (`tests/states.test.tsx` holds the table).

## 2. Mock-only versus checked against the real backend

**Checked against the real dev backend (read-only, 2026-11-02 to 11-03, three roles, signed in by hand):** front desk 24 calls, nurse 26,
doctor 26. No findings: every 200 answer matched the schema, no unexpected status, no 5xx, no write made.

**Everything else is mock-only evidence:** fake sign-in and fake API in the unit and browser tests, written from `openapi.snapshot.json`.
That includes every screen added since F5, every write, and the whole Tamil interface.

## 3. Still unchecked against the real backend

Everything added in F7 and F8 (alerts, dashboard, care gaps and their writes, care plans, results, accounts, autonomy, reviewer statistics,
cost canvas, voice, readiness, claims, break-glass, recovery, notes, grievances, the patient portal), every write, patient search and
its `label` field, uploads, and `GET /readyz` from the browser. `docs/frontend-live-checklist.md` has one row per call and the order for
the first live run.

## 4. Blocked, and on whom

| Item | Blocked on |
|---|---|
| Rows for holds, merges, duplicates, resolved tasks, and every new list, so row shapes can be checked | backend seed (request item 8) |
| Enums that stay open-ended by design (shown humanised, never hidden): grievance `category`, review task `kind`, a run's `status` word | not blocked: the console labels what it knows and shows the rest as sent |
| Merge approve and unmerge switched on | the first live approve with step-up |
| Active-role header switched on | the owner, after the dev backend has migration 0028 and a live check works |
| Cancel confirmation wording, consent purpose wording | the clinic / counsel; marked placeholders are in place |
| Tamil (all new strings) | native-speaker review; `TAMIL-UNREVIEWED.md` lists them |
| Patient portal sign-in | Supabase phone sign-in with test numbers and fixed codes for the demo (steps in `docs/frontend-live-checklist.md`, to be verified in the dashboard); no SMS provider |

## 5. The first live run

See `docs/frontend-live-checklist.md`: prerequisites (accounts per role, seed rows, phone sign-in for a test patient), the read-only
walk per role (`npm run e2e:live`, now covering every new list), then the writes in a fixed order, by hand, with `--record`.

## 6. For the privacy owner: what the console does with personal data it handles

Owner-requested record (2026-11-09). These hold today and are pinned by tests:

- **Invite e-mail addresses** are typed into the invite window, sent once, and never stored or shown again: the window clears on close, the
  account list shows only an id, a status and role words, and the address is not kept in the page, the query cache, or browser storage.
- **Voice transcripts** are read only on a click, held only while their window is open, and dropped on close (never cached, never logged).
  Every read and purge is audited by the database.
- **Message text** (the offer text, break-glass messages) is read on a click, held while shown, and dropped; the offer text is copied, not left
  on the page.
- **Patient search results** are not cached and are dropped on leaving the page.
- **The patient portal** keeps nothing about the patient in the browser except the sign-in service's own session record: no phone number, name,
  code, notice text or data. The language choice is the one other thing kept (a convenience, not patient data). Typed text (a grievance, a
  claim) is dropped from the page once it is sent. "Download my data" builds one file from the answer and keeps nothing. No analytics, no
  third-party scripts (`tests/source-guards.test.ts`).
- **The decrypted record view (PENDING THE PRIVACY OWNER'S CONFIRMATION before any real data).** A patient's page has an "Open the full details"
  button for front desk, nurse and doctor. It reads `GET /v1/staff/patients/{id}/view` ONLY on that click (the open is an audited read: who,
  role, patient id; never the contents), shows the name, date of birth and phone, and drops them on "Hide", on leaving the page and on a role
  change. The answer is a mutation result, not a cached query: it is not refetched on focus or reconnect and is never written to storage, a
  URL or a log. The backend itself refuses it outside the demo profile with synthetic data (503), and the console says so in plain words. The
  screen carries a visible "pending" notice. **Until the privacy owner confirms this, use synthetic data only.**
- **Register a patient** keeps the typed name, date of birth and phone only until the save; after it they are dropped from the page and only the
  record id (a link) remains. **The audit reader** shows rows exactly as sent, with each row's `details` only after a click, as plain text.
- **Erasure.** The confirmation says what the design note (`docs/design/patient-erasure-and-update-narrowing.md`) says is erased (the encrypted
  name, date of birth and phone) and kept (the request and its audit rows, the record number, the keyed matching fingerprints; messages are not
  erased by this step). That wording is marked as a placeholder until the privacy owner and counsel confirm it.
- The only things kept in the staff browser are the Supabase session, the language choice, and (per tab) the chosen role.

## 7. Role gates checked against the schema descriptions

The console's menu and buttons are conveniences; the backend decides. Compared with the route descriptions in `openapi.snapshot.json`
(2026-11-09; re-checked against the regenerated schema before this update):

| Gate in the console | Schema description | Result |
|---|---|---|
| Hold, book, cancel, join waitlist: front desk only | no role stated | **not verifiable**: front desk only is the owner's decision; the backend should state the roles |
| Mark an offer delivered, release a hold: front desk | "403 unless you are front desk" | matches |
| Add a provider, location, visit type, slot: front desk, admin | "front desk or admin" | matches |
| Care-gap barrier writes: nurse, doctor | barrier: "nurse, doctor or admin"; episode: "nurse or doctor" | **differs for barriers**: the schema also allows admin; the console follows the owner (nurse and doctor only) |
| Care plans list and results: nurse, doctor; write and decide a plan: doctor | list "a nurse or doctor"; write and confirm "a doctor" | matches |
| Accounts, reviewer statistics, cost canvas, voice, recovery, grievances: admin | "admin" | matches |
| Autonomy mode read by all four, set by admin; proposals and history: admin or doctor | same | matches |
| Portal claims: front desk | "front desk" | matches |
| Break-glass open: front desk, nurse, doctor; review: admin who did not open it | same | matches |
| Notes, visit readiness: nurse, doctor | same | matches |

## 8. Last full run

The counts for the last full run are given in the report that accompanies each push.

## Dev-only switch: skip the staff second factor (VITE_DEV_SKIP_STAFF_MFA) - SWITCH OFF BEFORE ANY REAL USE

Decided by the user (2026-11-13) so the live pass can sign in with the password only on the DEV project.
- A build-time constant, OFF by default, set ONLY by `vite.config.ts` when the dev server (`vite serve`, mode `development`) was started from a shell that set `VITE_DEV_SKIP_STAFF_MFA=1`. It reads the process environment, never a file, so it is in no committed file. It is false in every build (`vite build`) whatever the mode, and the code also requires `import.meta.env.DEV`.
- While on: a password-only session goes straight to the console (no enrolment, challenge or step-up prompt; a step-up 401 is reported as "a fresh code is needed, nothing was changed"), and EVERY staff page shows a banner "DEVELOPMENT ONLY: the second factor (authenticator) is skipped in this build...".
- The backend is unchanged: it enforces whatever it enforces (for example CAREFLOW_REQUIRE_AAL2). Routes that refuse without a second factor are "not tested live: needs second factor".
- Pinned by `tests/flags.test.ts` (fails if any build honours it) and `tests/dev-skip-mfa.test.tsx` (banner and password-only sign-in, flag mocked on).
- Never used for real patients. To switch it off: start the dev server without the variable.

## Bundle size note (2026-11-13)

A production build has one shared chunk of about 770 kB (205 kB gzip) holding React, supabase-js, react-query and the English and Tamil strings, plus a 229 kB (50 kB gzip) staff app. Both languages load for everyone. This is accepted for now. If the patient portal ever has to run well on slow mobile data, the fix is to load Tamil on demand (only when a person picks it); lazy page loading was tried and not kept (it broke 7 console tests that expect pages at once, and saved only about 165 kB).

Polish pass: `frontend/e2e/keyboard-zoom.spec.ts` walks the heaviest screens keyboard-only (Tab reaches the controls, a visible focus indicator on each, no trap) and at 200% zoom (no horizontal scroll, axe clean), in English and Tamil; a scan found no unused exports in `src/`.
