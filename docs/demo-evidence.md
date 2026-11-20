# Demo evidence: what has actually been shown live on the dev system

This is a ledger. Each row says what was seen, on what date, by whom, and where the record lives. It takes facts only from `docs/frontend-live-findings.md`, `docs/demo-runbook.md`, `docs/status-now.md` and the backend reports of this build. **If a step is not in the "shown" table it has not been shown.** Nothing here is a clinical validation, an approval or a go-live claim: every walk used the dev project in the demo profile with synthetic data, and the answer for real patients is still NO-GO (`docs/design/pre-launch-checklist.md`).

**How the walks were made.** The user typed every password and authenticator code. A walk tool recorded each call, the status it got and whether the response matched the published schema ("0 schema mismatches"). "Read-only" means the tool failed if it made a write. No credential, token or key was recorded. Where a second factor was off in a dev window it is said so; those results do not show the second-factor behaviour (checklist rows 76 to 78).

## 1. Shown live (with dates)

| Area | What was seen | Date | Where recorded |
|---|---|---|---|
| **Sign-in with a second factor** | Front desk signed in several times with an authenticator code; `/v1/iam/me` answered 200 each time. | 13 Nov 2026 | live-findings, section "MFA" |
| **Front desk** | Writes recorded by hand: 225 calls, 0 schema mismatches, every write carried an idempotency key. Scripted offer cycle (prepare offer, send, mark delivered) on a fictional patient: all calls 200 with keys; one call was slow (8 s once, over 15 s once). Later read-only walk: 58 calls, 0 mismatches. | 13 and 14 Nov 2026 | live-findings, "Front desk" sections |
| **Nurse** | Read-only: 54 calls (13 Nov, redone) and 73 calls (14 Nov), 0 mismatches. Third pass: 98 calls, 0 mismatches (the first run, 66 calls, was discarded because the backend answered 502 from call 55). | 13, 14, 19 Nov 2026 | live-findings |
| **Admin** (same account, role chosen at sign-in) | Read-only: 50 calls (13 Nov) and 62 calls (14 Nov), 0 mismatches. | 13, 14 Nov 2026 | live-findings |
| **Doctor** | Read-only: 52 calls (13 Nov), 73 calls (14 Nov), 99 calls (19 Nov), 0 mismatches. Human decisions on fictional data were clicked by the user and by a script (13 Nov); the hand-recorded writes mostly did not succeed and the scripted decisions are marked **not for training labels** (rule 9). | 13, 14, 19 Nov 2026 | live-findings, "Doctor writes" |
| **Real-model demo run** | A made-up scanned letter read by Gemini through the gateway (about 4 to 5 seconds); the red-flag rules screened the text before any other model; the decision model (Jev through OpenRouter) suggested which of two invented cards fits; a triage proposal was stored with engine, model, confidence, rule and each alternative's probability, and waited for a person; Sarvam drafted a Tamil suggestion that was not sent. The three providers passed the live check. | recorded in `docs/status-now.md` (the model-run date is not stated there) | status-now section (a); `docs/design/real-model-demo.md`; `docs/design/hosted-providers.md` |
| **Reviewer view of a model-made proposal** | The doctor's screen showed engine `jev`, model `typesafe/jev-router`, confidence 51%, decision rule `chosen_option_ranks_first` and alternatives 51% and 49%, exactly as the API sent them; the six older proposals showed "Not recorded" (no zero, no blank). 86 calls, 0 writes, 0 mismatches. | 14 Nov 2026 | live-findings, "Doctor referral review walk with a real engine" |
| **Retrieval (knowledge search)** | The owner's run on the dev database: the embedding live check passed; the six invented demo cards were indexed and 10 of 10 made-up questions found their card first. This shows only that the mechanism works on six invented cards; the thresholds are labelled placeholders. | 14 Nov 2026 | status-now section (a) |
| **Front desk supervisor** | Read-only: 62 calls, 0 mismatches; every route answered the same as plain front desk. The account also holds `front_desk`, so what the role alone unlocks was **not** separated. | 14 Nov 2026 | live-findings, "Front desk supervisor walk" |
| **Staff messages and patient labels** | Messages list, a conversation's headers and one message read after its confirmation: 200, 0 mismatches for front desk, nurse and doctor. The "Show names" labels read: 200 after the confirmation in all three walks. Reads only. | 19 Nov 2026 | live-findings, third pass |
| **Family links: front desk page** | Passed with the second factor: 200, "There are no family link requests." (an empty list; no request was made). | 20 Nov 2026 | live-findings, "Demo link completed" |
| **Portal claim** | Identity confirmed by the front desk with a second factor on one record (synthetic patient): `POST .../confirm-identity` 200. Then one claim by the patient: `POST /v1/portal/claims` 200, screen "Your record is linked." Afterwards "Linked to a record: Yes". These are the only two writes of the portal demo. | 20 Nov 2026 | live-findings, "Demo link completed" |
| **Portal walk (read-only)** | After the claim: 49 calls, 0 mismatches, no 403. Passed on load: home and account, notices and decisions, profile, notifications, requests, family section, complaints (own list and categories), voice preference, claim. | 20 Nov 2026 | live-findings |
| **Public complaint form (reads)** | `GET /public/grievance-categories` answered 503 and the screen said nothing could be sent; after the backend fix it answered 200 with eight categories and the Send button stayed off until a category and text were given. Nothing was sent. | 20 Nov 2026 | live-findings, "Public complaint form" and "Booking and messages switched on" |
| **Booking list and messages list (empty states)** | With both switches on: `GET /v1/portal/booking/bookings` 200 with no appointments; `GET /v1/portal/messages/threads` 200 with no conversations. Front desk scheduling read: locations 2, visit types 2, providers 2, slots 12 rows. | 20 Nov 2026 | live-findings, "Booking and messages switched on" |

## 2. Not shown live, and why

| Step | Why it is not shown |
|---|---|
| **A patient booking an appointment** | **Not done.** On 20 Nov the slots list answered 200 but was empty, so no "Book" button was offered and the one authorised booking was not made. Cause found afterwards (backend diagnosis, not seen live): the seeded demo slots were in the past and the bookable-type flag matters; the backend now returns a closed `empty_reason` word, and the runbook names the likely cause (migration 0099, commit d78268e). **The booking walk has not been re-run since**, so the fix is verified by tests only. |
| **Visit preparation (patient and staff)** | No appointment existed to open it from; the staff view needs a recent second factor and the nurse and doctor accounts had no authenticator. |
| **Nurse and doctor screens that need a second factor** | Visit readiness, results and notes, care plans, care-gap set-up, `/knowledge` (cards and templates), autonomy, break-glass, staff recovery, erasure and staff accounts: the database refuses a password-only session (`403`). Recorded by the owner as "not tested live: needs second factor". The nurse and doctor have no authenticator yet. |
| **The step-up prompt** (401 with `WWW-Authenticate: step-up`) | Not seen live. No write answered it and the erasure buttons were off. Checklist row 78. |
| **The audit reader with a real auditor account** | No auditor account exists. Checklist row 77. |
| **Sending a message, opening a conversation, filing a complaint, requesting a family link, any request status change** | No write was authorised for these. Only the empty lists and read paths are shown. |
| **Patient sign-in by phone code** | Phone sign-in could not send a code (the portal showed its fixed "The code could not be sent" message); no SMS provider is approved. The portal was reached with the demo e-mail sign-in, which is demo-only and never offered to real patients (checklist rows 96, 97). |
| **Voice** | No audio or engine exists. The voice preference is read (200); nothing else. |
| **The supervisor role alone** | No account holds only that role, so the backend's enforcement for it has not been exercised. |
| **Tamil screens read by a person** | None of the Tamil strings is reviewed by a person; the AI machine-checked them and flagged 46 for the reader. The screens were tested in the console's own browser tests, not reviewed by a reader. |
| **Real clinical content** | No red-flag rule, threshold, card or wording has been approved. The six demo cards are invented. Nothing shown stands in for the clinician's decisions. |
| **A real week of traffic, restore and rollback drills, hosting** | Not run; no production host exists (checklist rows 27, 28, 54). |

## 3. Automated checks, as of 20 Nov 2026

These are not live demos; they are listed so nobody mistakes them for one.

- **The offline suite** on the merged main: the last full run before the latest fixes was 2717 passed and 1801 skipped. The skipped tests need the database and run only in CI.
- **CI run 44737235379** (commit 79049d9, pushed 12:36 UTC): read at 15:03 UTC, **completed, success**, every job green including live shards 9 and 10 (they were slow, not hung). That confirms the live tests up to migration 0098 in CI.
- **Commit dafdbb2** (migrations 0099 and 0100, on main since about 14:25 UTC): the backend CI run (44756274470) was still pending at 15:03 UTC, so the live tests of 0099 and 0100 are **unconfirmed**. The separate Frontend CI run failed its "API schema freshness" check because the generated `schema.d.ts` is older than the new `openapi.snapshot.json`; the frontend window regenerates it with `npm run gen:api`.

## 4. How to add a row

Add a row only after the step has really been run, with its date, who typed the credentials, the status codes of every write, and the file where the full record sits. A step that was planned or prepared but not run goes in section 2, with the reason.
