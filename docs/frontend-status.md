# Frontend status

Updated 2026-11-03, branch `frontend-f5`. Scope: `frontend/` only. The backend, the database, CI and the design notes are not touched from
this side; what the frontend needs from them is in `docs/frontend-backend-requests.md`.

## 1. What is built (F0 to F5)

| Phase | What | State |
|---|---|---|
| F0 | Scaffold: React, Vite, strict TypeScript, pinned dependencies, env allowlist (3 public names), bundle secret check, production CSP, generated API client with a freshness check | merged |
| F1 | Landing page, English and Tamil, "Coming soon" for anything unbuilt | merged |
| F2 | Staff sign-in, shell, role-aware navigation (convenience only), step-up handling, dev-only claim spike | merged |
| F3 | Console screens: referrals, review tasks, patients (search, record), scheduling lists, holds, offers draft; LIVE / TEST badge | merged (PR #13) |
| F4 | Referral decisions (approve, reject), identity revoke, merge and duplicate lists, merge actions behind a flag that is OFF | merged (PR #15) |
| F5 | Live integration pass tooling (`npm run e2e:live`), schema checker, per-role reports, Tamil AI-review corrections, requests doc | merged (PR #17) |
| F6 (part 1) | Error boundary (fixed message, no error text), offline banner and held-back writes, session-expiry tests, active-role plumbing behind the OFF flag (single-role users never send it; the fixed 403 falls back to the role picker without signing out), accessibility additions (200% zoom, focus order, picker and banner at every width) | this branch, PR pending |
| F7 (batch 1) | Scheduling: slots (hold, book), bookings (cancel with a marked-placeholder confirmation), waitlist (join), offers (list, copy text, mark delivered; nothing is sent), setup (add provider, location, visit type, slot); reviewer view of a triage proposal (unknown shown as "Not recorded") and the clinic's reason list on a decision | on `main`, mock-only until a live pass |
| F7 (batch 2) | Alerts (acknowledge; the audience role is the escalation state), admin dashboard (unreadable never shown as zero), care gaps panel (read-only), care plans (list, doctor approve/reject, doctor writes a plan), record a result; Scheduling for admin; all times in the clinic time zone (labelled demo placeholder) | on `main`, mock-only until a live pass |
| F7 (batch 3) | Care-gap writes (record barrier, start episode, resolve, mark rule done, re-check), staff accounts (list, invite with its 503 state, suspend, reinstate, grant, revoke), autonomy (mode, the two-person switch), reviewer statistics, cost canvas, voice transcripts (read, purge); the clinic's own time zone from /v1/iam/me; offer text by message_id; patient id handed over from the open record or referral | on `main`, mock-only until a live pass |

Working today in the console: 28 backend operations through one generated client. Every write carries an idempotency key, retried with
the same key; a step-up challenge asks once and retries once. Messages shown for errors are fixed per problem kind, never backend text.

Tests: 675 unit tests, 175 browser tests (against mocked hosts), typecheck, lint, API freshness check, Tamil list check, bundle secret check.

## 2. Mock-only versus checked against the real backend

**Checked against the real dev backend (read-only, 2026-11-02/03, three roles, signed in by hand):**
front desk 24 calls, nurse 26, doctor 26. No findings: every 200 answer matched the OpenAPI schema, no unexpected status, no 5xx,
no write made, no active-role header sent. The shell showed role, the TEST badge and, as designed, "Operating profile unknown".
Screens that showed rows: referral list (4) and detail, open review tasks (3) and, for nurse and doctor, the task detail,
providers, locations, visit types, priority levels. The 403 answers on the offer draft and possible duplicates for nurse and doctor
are by design (front desk only).

**Everything else is mock-only evidence** (fake auth and fake API in unit and browser tests). That includes every write path,
every empty-list row shape, and the whole Tamil interface (unreviewed by a native speaker).

## 3. Still unchecked against the real backend

- Merge detail and the patient record screen reached from it (no merges exist in the dev data).
- Patient search and the three fields the console reads beyond the schema (`label`, `labels_withheld`, `truncated`): it needs personal
  data, so it is done by hand.
- Every write: acknowledge and resolve a task, release a hold, review a referral, confirm and revoke identity, upload and register
  a document, merge propose, approve and unmerge.
- Row shapes for lists that were empty: acknowledged and resolved tasks, all four hold statuses, merges, possible duplicates.
- `GET /readyz` from the browser.

## 4. Blocked, and on whom

| Item | Blocked on | Note |
|---|---|---|
| Rows for holds, merges, possible duplicates, resolved tasks; then a front-desk re-run | backend session (seed extension) | request item 8 |
| Reviewing seeded proposals in the dev app | backend session (demo-profile fake Front Door) | list works, review route needs the components |
| Active-role header and a role picker | backend (active-role PR; backend does not enforce it yet) and the owner's go-ahead | header flag stays OFF; design note accepted |
| `operating_profile` in the shell | backend (`/v1/iam/me` does not return it yet) | the shell already shows "unknown" |
| Merge approve and unmerge switched on | the backend spike page being updated AND one live approve with step-up working | flag stays OFF |
| Revoke reasons, "edit" referral decision hidden | clinician items (revoke reason wording, whether edit is for clinicians only) | placeholders in place, marked |
| Clinical, consent and legal wording | clinician and legal sign-off | placeholders only |
| Tamil strings (419, all unreviewed) | native-speaker review | AI-reviewed, labelled pending native review; `TAMIL-UNREVIEWED.md` lists them |
| Privacy confirmation for the decrypted patient view | owner (recorded as a pre-launch blocker on the backend side) | affects what the record screen may show in `clinical` |

## 5. F6: plan (F6.1, F6.2 and the plumbing half of F6.4 were approved and built; the rest is PLAN ONLY)

Candidates are ordered by value to a clinic that already has the read side working. "Routes" are from the current
`openapi.snapshot.json`; "exists" means the route is in the snapshot, not that it has been used against a live backend.

### F6.1 Error boundary and session expiry / offline handling (frontend only)

- Routes: none new. Uses `/v1/iam/me` for the session check.
- What: a top-level error boundary with a fixed message and a way back to the home screen; a clear, fixed "session ended" state;
  an offline banner that stops writes from being sent while offline and never claims a write succeeded.
- Decision (owner, 2026-11-04): an unsent write is dropped with a fixed message that says nothing was saved; what the person typed or chose stays in the form so they can send it again; there is no hidden retry queue.
- Tests: unit (boundary renders a fixed message, no error text shown), browser (token expiry mid-session returns to sign-in once;
  offline blocks a write and sends nothing; the same idempotency key is reused on a real retry).

### F6.2 Accessibility sweep (frontend only)

- Routes: none.
- What: keyboard-only pass over every screen, focus order, long Tamil text at 200% zoom, colour contrast, live-region announcements
  for success and error banners; fix what the sweep finds.
- Decision (owner, 2026-11-04): WCAG 2.2 AA is a goal, not a claim, in the docs and the UI. The UI makes no accessibility claim.
- Tests: extend vitest-axe to every route; browser tests for keyboard paths on the write screens.

### F6.3 Operating profile in the shell (small; after the backend returns it)

- Routes: `/v1/iam/me` (exists; `operating_profile` is not in the snapshot yet).
- What: show the profile only when it is one of the exact known words (`demo`, `clinical`); anything else shows "unknown".
- Decisions: none beyond the wording of the label.
- Tests: known words, unknown word, missing field, wrong type.

### F6.4 Active-role header and role picker (after the backend PR merges; flag stays OFF until the owner says)

- Routes: none new; the header `X-CareFlow-Active-Role` on existing routes. Needs CORS to allow it (requests item 1).
- What: when a user holds more than one role, a picker in the shell; the header is sent only with the flag ON.
- Decision (owner, 2026-11-04): the choice is remembered per tab only (sessionStorage) and forgotten at every sign-out and sign-in, so a role is picked again after each sign-in.
- Tests: header present only with flag ON; flag-off pin test stays; switching role clears cached lists; a 403 after switching shows the fixed "not available to your role" message.

### F6.5 Merge approve with step-up (after one live approve works against the dev backend)

- Routes: `POST /v1/staff/merges/{merge_id}/approve`, `POST .../unmerge` (exist, flag OFF in all but the test build).
- What: switch the flag on in dev only after the live approve; keep the two-person rule visible (the proposer cannot approve).
- Decisions: the owner decides when the flag goes ON; confirmation wording is a placeholder until a clinician or owner signs it off.
- Tests: the existing browser tests plus a live `--record` pass done by hand.

### F6.6 Scheduling actions on the routes that exist but have no screen

- Routes: `GET /v1/staff/scheduling/slots`, `GET .../bookings`, `GET .../waitlist`, `POST .../slots/{slot_id}/hold`, `POST .../slots/{slot_id}/book`, `POST .../bookings/{booking_id}/cancel`, `POST .../waitlist/join`, `POST .../offers/send` (all in the snapshot; none used by the console yet).
- What: read-only slots, bookings and waitlist lists first; then hold, book and cancel with idempotency keys; offer send last.
- Decisions: which of these the clinic wants in the first release; cancellation and offer wording are placeholders (not clinical content from this side).
- Tests: role-matrix coverage is the backend's; here, unit and browser tests per screen, then a live read pass and a hand-driven write pass.

### F6.7 Remaining staff routes with no screen yet

- Routes: `GET /v1/staff/audit`, `POST /v1/staff/patients` (create), `POST /v1/staff/patients/labels`, `GET /v1/staff/patients/{id}/view` (decrypted view, audited, rate-limited), `GET /v1/staff/messages/{id}`, `POST .../screen`, `POST /v1/staff/referrals/{id}/run`.
- What: not planned in detail. Each needs the owner to say whether the clinic wants it, and the patient view needs the privacy confirmation noted in section 4.

### Not in F6

The patient portal (no patient sign-in and no patient routes exist).

### Suggested order

F6.1 and F6.2 first (no backend dependency). F6.3 as soon as the backend returns the field. F6.4 and F6.5 when their backend gates clear.
F6.6 when the clinic names what it wants. Each ends with a live read pass for the roles involved.
