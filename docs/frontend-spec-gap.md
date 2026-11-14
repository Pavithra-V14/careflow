# Frontend spec gap list

Written 2026-11-14 from a full re-read of `BUILD-SPEC.md` sections 19 to 21 and `PATIENT-WORKFLOWS.md`, plus the parts of sections 22 and 23
that those documents point at (visit readiness, voice). Compared with `openapi.snapshot.json` on `main` and with what is in `frontend/`.

Status words:

- **built** is in the code and tested against fakes. It is not "live-verified"; see `docs/frontend-live-checklist.md` for that.
- **partly built** says what is missing.
- **not built, can be built** needs only routes already in the snapshot. Built on 2026-11-14 (section 6).
- **blocked: route** the snapshot has no route for it; the request is in `docs/frontend-backend-requests.md` (item 20 onward).
- **blocked: decision** or **blocked: external** needs an answer from the owner, a clinician, counsel, or an approved outside service.

## 1. BUILD-SPEC section 19: UI/UX

| Ref | Requirement | Status | Notes |
|---|---|---|---|
| 19 intro | Render every screen, check with screenshots plus visual regression | partly built | Screenshots are taken per screen, language and width (`e2e/helpers.ts`, `e2e/patient.spec.ts`) and reviewed by eye. There are no stored baseline images, so nothing fails on a visual change. **blocked: decision** (item D1 in the report: baselines are binary files and differ between machines) |
| 19.1 | Two densities, one token system | built | `src/shared/tokens`, patient and staff stylesheets |
| 19.1 | WCAG 2.2 AA | partly built | A goal tested toward, never claimed: axe in unit tests, axe in Playwright at 320, 768 and 1280 px, keyboard-only walk, 200% zoom. No manual screen-reader pass has been done; that needs a person |
| 19.1 | Full Tamil and English (text expansion, wrapping, date and number formatting) | partly built | Both languages everywhere; overflow checked at three widths and 200% zoom in both; dates and numbers go through `src/shared/i18n/format.ts` and the clinic time zone. All 1558 Tamil strings are unreviewed by a native speaker (**blocked: external**) |
| 19.1 | Automated accessibility checks in CI | partly built | The checks exist and run on demand. Whether CI runs the browser tests is in the workflow file, which this side does not edit |
| 19.1 | Loading, empty and error state for every screen | built | `QueryView`, `WriteProblem`, per-screen states; pinned by tests for each screen |
| 19.1 | No stock photography; placeholder branding and emergency number | built | No images of people; the hospital name comes from `GET /public/clinic`; the emergency number is the static 108 banner; branding text is marked placeholder |
| 19.2 | Landing page in the specified order | built | Banner, about, departments and doctors, features, how it works, privacy promise, location and timings, FAQ, footer with grievance contact, privacy link and the small "Staff sign in" link |
| 19.2 | Hospital-supplied content (about, departments, doctors, hours, privacy promise, FAQ answers, grievance contact) | blocked: external | Rendered as marked placeholders until the hospital and counsel supply it. The frontend writes none of it. The medical-advertising check with counsel is also theirs |
| 19.2 | Features list only what is available | built | `src/patient/features.ts`; a test fails if an unbuilt feature loses its "Coming soon" label |
| 19.3 | Patient header: initials avatar and name | blocked: route | `GET /v1/portal/me` returns no name. Request 20 |
| 19.3 | Patient menu: profile | blocked: route | No profile read or write route. Request 20 |
| 19.3 | Patient menu: family-member switcher | blocked: route | No dependant routes. Request 21 (and counsel, section 20.1) |
| 19.3 | Patient menu: language | built | Switcher on every page; the choice is kept in the browser. A server-side "preferred language" has no route (request 20) |
| 19.3 | Patient menu: notifications | blocked: route | No notification-preference route. Request 22 |
| 19.3 | Patient menu: security (devices, passkeys) | blocked: route, and decision | No list-and-revoke-sessions route; passkeys are not configured. Request 23. Passkeys would also be a new auth feature, ask first |
| 19.3 | Patient menu: privacy and consent (withdraw, download my data, request erasure) | partly built | Grant and withdraw per purpose with a confirmation, and download are built. The consent history (purpose, decision, notice version, date) is now shown from the file the person downloads, with no extra call (2026-11-14). Erasure for a patient has no route: it goes through the grievance channel (built) and the grievance category list is a clinic setting. Request 24 asks for an explicit patient erasure request |
| 19.3 | Patient menu: sign out | built | |
| 19.3 | Staff: role badge, role switcher, current clinic | built | Active role is chosen per session; the header that sends it to the server is OFF until the backend reads it (request 3) |
| 19.3 | Admin: persistent LIVE / TEST badge | built | Same words as the dashboard |
| 19.4 | Patient sign-up: mobile number with OTP | built | |
| 19.4 | Patient sign-up: full name, date of birth, sex, preferred language | partly built | Name and date of birth are asked at the claim step (they are what the claim route takes). `POST /v1/portal/register` takes no body, so sex and preferred language have nowhere to go. Request 20 |
| 19.4 | Consent to care delivery (required); AI processing and service improvement (optional, separate) | built | Notices shown exactly as sent, each purpose separate, nothing pre-ticked |
| 19.4 | Optional email as a fallback channel | blocked: route | Request 22 |
| 19.4 | `voice_enabled` in the profile after registration, off by default | built (2026-11-14) | The Voice section of the portal: the person's own setting, with a confirmation. Off until chosen |
| 19.4 | Not collected at sign-up: Aadhaar, address, ID proofs, insurance, blood group | built | No such field anywhere in the portal |
| 19.4 | Staff by invitation only; name, work email, mobile, role, council registration number; second factor at first login | partly built | Invitation (email and role) and second-factor enrolment are built; there is no staff sign-up screen (a test enforces it). The invite route takes only `email` and `role`, so name, mobile and the registration number cannot be recorded. Request 25 |

## 2. BUILD-SPEC section 20: consent, rights, clinical content

| Ref | Requirement | Status | Notes |
|---|---|---|---|
| 20.1 | Separate, independently withdrawable consent purposes | built | |
| 20.1 | Withdrawal is one click | built (2026-11-14) | Withdraw is recorded at once, the result is shown with a "Grant again" button; granting keeps its confirmation (owner decision) |
| 20.1 | Access, correction, erasure and nominee requests through the grievance channel with tracking | partly built | Filing a grievance with a category from the clinic's list, and the list of my grievances, are built. Correction, erasure and nominee are only as good as the category words the clinic configures; nothing in the console makes them separate request types. Request 24 |
| 20.1 | Grievance reachable without signing in (PATIENT-WORKFLOWS H2) | blocked: route | The only grievance route needs an active patient account; the public footer shows the grievance contact as a marked placeholder. Request 26 |
| 20.1 | Under-18 accounts: guardian consent and a recorded proxy-booking trail | blocked: route, and decision | No route. Counsel has to confirm the specifics first. Request 21 |
| 20.1 | Training data only from de-identified, clinician-confirmed labels | not a frontend item | Backend and evaluation side |
| 20.2 | Seeded cards, red-flag rules and templates start `draft_unreviewed` and are unusable in `clinical` until two-person approval | partly built | Red-flag rules: list, author a draft, approve or reject (hidden from the author), withdraw a draft, two-doctor retire, all with the draft shown as never active. Protocol cards and message templates have no routes at all |
| 20.2 | Admin card editor with schema validation and the card's own test cases | blocked: route | No card or template routes in the snapshot. Request 27 |
| 20.2 | Never invent a clinical threshold, dose or red-flag phrase | built | The console supplies no rule content, no default text and no code list |

## 3. BUILD-SPEC section 21: frontend testing

| Requirement | Status | Notes |
|---|---|---|
| Vitest and Testing Library component tests | built | 48 files, 1117 tests at the last full run |
| Contract test: every client method targets an endpoint in the schema, with required parameters and body fields | built | `tests/contract.test.ts`, one case per operation; a test also requires every route to be named in the live checklist |
| CI check that the checked-in schema snapshot is not stale | partly built | `npm run check:api` exists. Wiring it into CI is a workflow change, which this side does not make |
| A production build must succeed in CI | partly built | `npm run build` and `npm run check:bundle` exist; CI wiring as above |

## 4. PATIENT-WORKFLOWS

| Workflow | Status | Notes |
|---|---|---|
| A1 self-registration: public page, phone OTP, fields, current notice fetched from the server, consent, account created | partly built | Everything the routes allow is built (the notice is never hard-coded). Sex and preferred language: see 19.4. Landing on a portal home that offers a first booking: blocked, see B1 |
| A2 claim a record created by a referral (phone and date of birth both match; otherwise flagged for front desk) | built | Claim by verified phone plus name plus date of birth plus clinic; `claimed`, `pending_review`, `locked`, `already_bound`, `consent_required` each have a fixed message. The appointment offer showing before sign-up depends on B2 |
| A3 guardian and dependants, proxy-booking trail | blocked: route | Request 21 |
| A4 returning sign-in; new device appears in Security | partly built | Sign-in built. The devices list is request 23 |
| B1 browse visit types, providers, slots; hold; book | blocked: route | The scheduling routes are staff-only. No patient routes. Request 28 |
| B2 reply to an appointment offer in free text; unclear replies re-asked | blocked: route | The patient has no message routes. Request 29. The front-desk "enter the patient's reply" flow is built for the console |
| B3 cancel a future appointment | blocked: route | Request 28 |
| B4 be offered a freed slot (message, or read aloud) | blocked: route | Needs 29. Staff side (offers, delivery) is built |
| B5 reschedule from a readiness barrier | blocked: route | Needs 28 and 29; the nurse side of barriers is built |
| C1 and C3 red flag in any text, automatic safety message | not a frontend item | Rules run on the backend. The static "call 108" banner is built on every patient page. Staff see alerts and tasks (built) |
| C2 red flag in a voice session | not a frontend item | The backend watches the transcript. The frontend must not widen the voice scope (see G1) |
| D1 and D2 messaging, topics by account stage | blocked: route | Patient message routes. Request 29. The staff inbox and threads are built |
| D3 reminders for overdue items | blocked: route | Needs 29 |
| E1 pre-visit brief | not patient-facing | By design the patient never sees it |
| E2 answer intake questions | blocked: route | No patient route. Request 30 |
| E3 procedure-readiness checklist | blocked: route | Request 30. The nurse side (status, relay a reply) is built |
| E4 report a barrier | blocked: route | Request 30 |
| F1 to F3 care plan, follow-ups, result corrections | partly built | Staff side built (care plans, results, correction tasks). The patient sees these only as offers and messages (28 and 29) |
| G1 voice mic button for patients who turned it on | partly built (2026-11-14) | Built: the preference switch, the per-session consent step, the workflow choice, opening a session and the 403, 404 and 503 states. The microphone and audio connection itself are **blocked: external** (see 23.1 below) |
| G2 Tamil in every workflow | partly built | Built everywhere that exists; unreviewed. Red-flag detection on Tamil text is the backend |
| H1 withdraw consents, download my data, request erasure | partly built | See 19.3 |
| H2 grievance without sign-in | blocked: route | Request 26 |
| H3 sessions and devices | blocked: route | Request 23 |
| "What a patient can never do" | built | No merge control, no auto-composed message, no auto-resolve of a readiness answer exists in the portal |

## 5. Section 23 (voice) and section 22 (readiness), where they touch the frontend

| Item | Status | Notes |
|---|---|---|
| Mic button only if the clinic flag is on AND the patient turned `voice_enabled` on | built (the gate; no mic yet) | The session form shows only when the person turned voice on. The clinic flag is not readable by the browser; the 503 answer from opening a session is the signal, shown as "voice is not available" |
| Spoken and on-screen consent and recording disclosure before any audio | built, text from the API only (2026-11-14) | The frontend holds no notice text. The Open button stays blocked, and the screen says voice is not set up, until the API supplies the notice (request 31). The agreement box is unticked. Nothing is spoken: no audio |
| Audio over a Gemini Live browser connection | blocked: external | Patient speech would leave the browser for Google's API. `BUILD-SPEC.md` names it, but the session route's reply carries no connection details and a service approval and data-processing agreement are needed for anything beyond synthetic data. Nothing here opens a microphone or a socket |
| Scope: slot offer, intake question, readiness item only | built (2026-11-14) | The workflow list is the three words in the schema; nothing free-form |
| Staff voice transcripts (read, purge) | built | |
| Patient-facing visit readiness | blocked: route | See E2 to E4 |

## 6. Phase B result (2026-11-14)

Everything marked "not built, can be built" is built: the patient voice setting and session gate, and the consent history. No other item
could be built on routes already in the snapshot: the rest are blocked on a route (requests 20 to 30), a decision, or an outside service.
