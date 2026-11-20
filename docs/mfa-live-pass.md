# Second-factor live pass (dev project, synthetic data only)

Goal: sign in to the staff console with a real authenticator code as the front desk, nurse and doctor dev accounts, with the backend requiring the second factor again, so the screens whose database checks need a real `aal2` token can be exercised. Until you do this pass, those screens answer 403 even though the app-level second-factor check is off in the demo. **No credentials, secrets or codes appear in this document and none may be pasted anywhere.**

> **Do NOT screenshot, copy or paste the authenticator secret or the QR code** (not into chat, an issue, a commit, a note or a file). Whoever holds that secret can sign in as that account. Scan the QR directly with your authenticator app and close the screen. If you think one was exposed, remove that account's factor (section 5) and enrol again.

## 1. What the demo does now, and why a pass is needed

In the demo the backend runs with `CAREFLOW_REQUIRE_AAL2=false` (allowed only in profile=demo with synthetic data), and the console may be started with the dev-only skip (`VITE_DEV_SKIP_STAFF_MFA=1`, see `docs/frontend-status.md`). But the database does not accept that: the functions behind the screens below check `iam.aal2_session()` themselves, and that cannot be switched off. A password-only token therefore gets a 403 there. The second-factor pass proves the real behaviour (pre-launch checklist rows 76 and 78).

## 2. Before you start

- Use the DEV project only. Have the front desk, nurse and doctor dev accounts (`docs/demo-runbook.md` step 3; created with `scripts/seed_dev_staff.py`). The admin account is needed for the voice screen (section 4).
- Have an authenticator app on your phone (Google Authenticator, Microsoft Authenticator or similar).
- Close the screens you do not need; nobody should be watching over your shoulder while you scan a QR code.

## 3. Turn the second factor on

1. **Backend:** in `E:\Claude\careflow-run\.env` delete the line `CAREFLOW_REQUIRE_AAL2=false` (or change it to `true`; unset also means required). Restart the backend (window A, `scripts\run_demo_backend.ps1`). The backend log should no longer say the second factor is off.
2. **Console:** start the staff console WITHOUT `VITE_DEV_SKIP_STAFF_MFA` (open a fresh shell; the switch is set per shell, never in a file; check with `echo $env:VITE_DEV_SKIP_STAFF_MFA`, which must print nothing). Restart the dev server.
3. **Enrol each account once** (front desk, nurse, doctor, and admin if you will test voice): open `http://localhost:5173/staff/`, sign in with e-mail and password; the console asks you to **set up an authenticator**; scan the QR code with your app straight from the screen; type the 6-digit code to confirm. Do this for each account separately and use a different authenticator entry per account (label each one). Then sign out.
4. **Sign in again** as each account: password, then a typed 6-digit code. A code is valid for about 30 seconds; the console asks for a fresh one for the sensitive acts (merge, approvals, erasure: "step-up", `CAREFLOW_STEP_UP_MAX_AGE_SECONDS`).

*If the console says it needs a second factor and loops:* the account has no factor yet (enrol it), the code was typed late (type the next one), or the clock of your phone is wrong.

## 4. What to try, and what you should see

`200` means the call succeeds with the second factor; `403` is the one fixed refusal (wrong role, no second factor, or a clinic you do not belong to). A feature switch that is off answers `503` after the identity check (turn it on first, `docs/demo-runbook.md` section 14).

| Screen or route | Switch | front desk | nurse | doctor | admin |
|---|---|---|---|---|---|
| Family links: `GET /v1/staff/family-links?clinic_id=`, verify and revoke (`POST .../decision`, `.../revoke`) | `CAREFLOW_FAMILY_LINKS=on` (and the rules seeded, section 14) | 200 | 403 | 403 | 403 |
| Knowledge search: `POST /v1/staff/knowledge/search` | `CAREFLOW_KNOWLEDGE_SEARCH=on` | 403 | 200 | 200 | 403 |
| Card editor: draft, edit, submit (`/v1/staff/knowledge/drafts`) | `CAREFLOW_KNOWLEDGE_EDITOR=on` | 403 | 200 | 200 | 403 |
| Card approval: `POST .../drafts/{id}/decision` (a doctor who is NOT the author; the author gets 403) | same | 403 | 403 | 200 (not the author) | 403 |
| Staff visit preparation: `GET /v1/staff/visits/{id}/prep`, `GET /v1/staff/visits/upcoming` | `CAREFLOW_PATIENT_VISIT_PREP=on` | 403 | 200 | 200 | 403 |
| Portal claim review: `GET /v1/staff/portal-claims`, `POST .../resolve` | none | 200 | 403 | 403 | 403 |
| Voice sessions (staff): `GET /v1/staff/voice/sessions`, transcript, purge | none (no voice engine exists, so the list is empty) | 403 | 403 | 403 | 200 (an empty list) |

Notes you will otherwise trip over:
- A **patient** opening the voice session (`POST /v1/portal/voice/sessions`) answers 503 because no voice engine is wired. That is expected, not a fault.
- The portal has **patient** consent and voice-preference screens (`/v1/portal/consents`, `/v1/portal/voice/preference`); I found no separate staff consent route in the API, so there is nothing to try as staff there.
- Reading approved card text for search does not need the second factor for humans (owner decision 2026-11-18, checklist row 92).
- **Does confirming a patient's identity need a real second factor? Plainly: only through the app-level check.** `POST /v1/staff/patients/{id}/confirm-identity` is a front-desk act; the database policy that records a confirmation (migration 0023) does not check `aal2`. So with the demo setting (`CAREFLOW_REQUIRE_AAL2=false`) a password-only front-desk session CAN confirm an identity; with `CAREFLOW_REQUIRE_AAL2` on (the real default, and the whole of this pass) it needs the second factor like every staff route. **Resolving a portal claim is different: it needs a real `aal2` token at the database in both settings** (`mpi.resolve_claim_request`), so that step fails with a password-only session even in the demo. That is why the claim walk in `docs/demo-runbook.md` section 15 needs this pass first for the approval step.
- To prove the negative, sign in as one of these accounts WITHOUT entering the code (or with the dev skip) and repeat a call: it must answer 403, never 200.

Record what you saw in `docs/frontend-live-findings.md` (the screens and the status words only; never a code, a secret or a screenshot of the enrolment).

## 5. Turn it off again afterwards

1. **Backend:** put `CAREFLOW_REQUIRE_AAL2=false` back in `.env` only if you want password-only dev walks again, and restart the backend. (It is allowed only in profile=demo with synthetic data; the application refuses to start with it off anywhere else.)
2. **Console:** for password-only walks start the dev server from a shell that sets `VITE_DEV_SKIP_STAFF_MFA=1`. Never write it in a file, and never in a production build.
3. **A factor you no longer want, or one you think was exposed:** remove it in the Supabase dashboard (Authentication, Users, the user; look for the action that removes the user's MFA factors, the wording depends on the dashboard version), then enrol again at the next sign-in. If you are locked out of a real staff account, the recovery route needs two admins (`docs/runbooks/staff-accounts.md`).
4. Delete nothing from the project that you do not recognise. Do not keep authenticator entries for dev accounts next to real ones in your app.

## 6. Before real use

Production needs the second factor ON at both layers and a real auditor account tested with it (checklist rows 76, 77, 78). This pass is dev only.
