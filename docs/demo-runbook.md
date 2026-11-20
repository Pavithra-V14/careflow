# Demo runbook: show the whole product on synthetic data

For a person who is not a programmer. **Everything here is made-up data in the dev project.** No real patient, no real document, no real message. About 45 minutes the first time, 20 minutes once the accounts exist. The longer, step-by-step setup is `docs/first-run-guide.md`; this page is the show itself, in the order to present it.

**Rules for the demo.** Never type, paste or read out a key, a password or the contents of `.env`. Nothing is ever sent to a patient: every "send" in the product is a person copying text by hand. Every model output is a suggestion; a person decides.

**Where things are (used in every command below).** Run folder `E:\Claude\careflow-run`; the demo clinic `b230e20e-655f-410d-8398-d2eadcb44dd2`; website folder `E:\Claude\careflow-frontend\frontend`. Three PowerShell windows: **A** (one-off commands), **B** (the backend), **C** (the website).

---

## 0. Before you start (once)
1. Window A: bring the run folder up to date.
   ```powershell
   cd E:\Claude\careflow-run
   git fetch origin
   git checkout --detach origin/main
   .\.venv\Scripts\python.exe -m careflow.migrate --check
   .\.venv\Scripts\python.exe -m careflow.migrate
   ```
   *You should see:* a list of pending migrations in order with no gap in the numbers, then each applied. *If it stops with a message,* it names the migration; do not continue (the backend refuses to start on an un-migrated database).
2. The `.env` in `E:\Claude\careflow-run` holds the settings (names only are listed in `docs/first-run-guide.md` step 3). For this demo it also needs these lines: `CAREFLOW_DEMO_PHASE3=1`, `CAREFLOW_DEMO_PHASE4=1`, `CAREFLOW_KNOWLEDGE_SEARCH=on`, `CAREFLOW_DEMO_REAL_MODELS=on`. **Add them yourself; never paste the file anywhere.**

## 1. Start the backend (window B)
```powershell
cd E:\Claude\careflow-run
powershell -ExecutionPolicy Bypass -File .\scripts\run_demo_backend.ps1
```
*You should see:* `Application startup complete.` and `Uvicorn running on http://127.0.0.1:8000`; leave the window open. The log also says, in words, which hosted providers were built (`DEMO REAL MODELS ...: text: real ...; decision: real ...`) or why one was not.
*If it prints `Missing in .env: NAME`:* add that line and start again. *If it says a migration is missing:* go back to step 0.1.

## 2. Start the website (window C)
```powershell
cd E:\Claude\careflow-frontend\frontend
npm run dev
```
Open `http://localhost:5173/staff/`. (First time only: `npm ci` before `npm run dev`.) *You should see:* the sign-in page. *If the page is blank or cannot reach the backend:* window B is not running, or `.env.local` is missing one of its three names.

## 3. The people (staff sign-ins and roles)
One sign-in per role; the first sign-in of each asks you to set up an authenticator app, after that every sign-in asks for a 6-digit code.

| Role | What they show in the demo |
|---|---|
| **Front desk** | referrals list, patients and identity, scheduling (hold, book, cancel, waitlist, offers, mark delivered), patient requests, break-glass |
| **Nurse** | referral review, care gaps, knowledge search |
| **Doctor** | the same as the nurse, plus care plans and the red-flag rule approval (two different doctors) |
| **Admin** | alerts, the dashboard, accounts, autonomy |
| **Front desk supervisor** | one narrow thing: the "identity confirmation taken back" tasks (nothing else) |

*Creating a missing account* (window A, one command per account; it asks for a password twice, shows nothing as you type, 12 characters or more; the email can be made up):
```powershell
cd E:\Claude\careflow-run
.\.venv\Scripts\python.exe scripts\seed_dev_staff.py --email nurse@example.test --role nurse --clinic-id b230e20e-655f-410d-8398-d2eadcb44dd2
```
Roles: `front_desk`, `nurse`, `doctor`, `admin`, `queue_worker` (the worker; no other role). **The supervisor** is given to an account that already exists and already holds front desk in the clinic (no new password):
```powershell
.\.venv\Scripts\python.exe scripts\seed_dev_staff.py --email <THE-FRONT-DESK-EMAIL> --role front_desk_supervisor --clinic-id b230e20e-655f-410d-8398-d2eadcb44dd2 --dry-run
```
*You should see:* `plan: existing account <id> will hold ['front_desk', 'front_desk_supervisor']`; run it again without `--dry-run` to grant. *If it says* `refusing: front_desk_supervisor is granted only to an existing account that already holds front_desk in this clinic`: that account is not front desk there; give it `front_desk` first. Add `--dry-run` to any seed command to rehearse it.

## 4. The seeded patients and papers (window A, once)
```powershell
cd E:\Claude\careflow-run
.\.venv\Scripts\python.exe scripts\seed_dev_demo_data.py --clinic-id b230e20e-655f-410d-8398-d2eadcb44dd2
.\.venv\Scripts\python.exe scripts\seed_consent_notices.py
.\.venv\Scripts\python.exe scripts\seed_break_glass_reasons.py --clinic-id b230e20e-655f-410d-8398-d2eadcb44dd2
```
It asks you to type the project host exactly; then it adds made-up patients (every name says "Demo ... Sample", dummy phone numbers), slots, four referrals (one waiting to run, two with an open proposal, one held), two possible-duplicate pairs and a merge, one DEMO result and medication, a care plan, three patient requests and three notices for a closed demo patient account, and one invented DRAFT red-flag rule. Safe to run twice. *If it says* `refusing: ...`: the line is a fixed rule (wrong environment, wrong project, clinic not a test clinic, no front-desk account yet); fix that and run again.

## 5. Sign in and look around (front desk)
Window C site: sign in as front desk. *You should see:* a **TEST** badge, your role, and a menu (Referrals, Tasks, Patients, Identity, Scheduling, Alerts). Open **Referrals**: about four letters, each with a status. *If the list is empty:* step 4 was not run for this clinic.
Show **Patients** (search needs a name and date of birth; the result shows ids and which identifiers agreed, never the stored values), then **Scheduling**: hold a slot, book it for a demo patient, cancel it, prepare an offer for the freed slot, press **Mark delivered**. *You should see:* open, held, booked; booking the same slot twice is refused; the offer goes to the highest clinical priority first; `awaiting_delivery`, then `delivered` with a time. Nothing is sent anywhere.

## 6. The real-model run (window A)
Needs the three keys set up once (`docs/design/real-model-demo.md`; the key script never shows a key) and their live checks passed. Then:
```powershell
cd E:\Claude\careflow-run
.\.venv\Scripts\python.exe scripts\demo_real_models.py --clinic-id b230e20e-655f-410d-8398-d2eadcb44dd2 --fresh
```
It asks you to type the project host once. `--fresh` makes a NEW dummy letter every time; without it a second run only reports the first result. Add `--kind pdf` (a PDF read without the vision model) or `--flagged` (the letter carries the demo rule's invented token: the rules fire and **no model is asked**).

**What each line means.** Each stage prints: its mode (`real` or `fake`), status, seconds, cost, and a note.
| Stage | Good | What a different word means |
|---|---|---|
| **1 text** | `real ... read` (about 2 to 6 s): Gemini read the made-up scanned page through the gateway | `NOT READ` plus a reason: `budget refused` (a spend limit), `timed out x3` (no answer within 60 s, three tries: about 3 minutes in the worst case), `provider error ... (status NNN)`, `provider refused the request ... (status NNN)` (a real provider error), `gate missing` (a key, a recorded live-check date or a ceiling is not set). `fake` plus a reason: that provider is not built |
| **2 rules** | `clear`: the deterministic red-flag rules ran on that text before any other model and found nothing | `FIRED`: a nurse task was raised and nothing else ran (correct for `--flagged`). `NOT RUN`: the page was not read, so there was no text to screen (this is **not** a clear). `unassessable`: no usable rule; held for a person |
| **3 decision** | `suggested`, with the card, a confidence, the model (`typesafe/jev-router`) and each option's probability | `unavailable` plus a reason. **`answer rejected: ...`** means the model answered and our check rejected the answer (for example a tie); it is not a provider error. One repair request is made first; an answer accepted only after it shows `jev+repair`. The confidence is **never calibrated** and means little here: the two "cards" are invented placeholders |
| **4 draft** | `suggested`: Sarvam translated an invented, labelled sentence into Tamil. Nothing is stored or sent | `refused` (a spend limit), `failed`, `unavailable` (gate missing) |
The last lines give the order the stages ran in (`text -> rules -> decision`; a flagged letter stops at `rules`) and **`PROPOSAL WAITING FOR REVIEW: <id>`**. *If it prints* `refusing: ...`: a fixed rule (not dev, not demo, wrong project, `CAREFLOW_DEMO_PHASE3` off, clinic not seeded). The script never prints the letter, a name, a key or a model's words.

## 7. Review the proposal (sign in as the doctor)
Menu **Referrals**, open the new referral ("Demo Kilo Sample"), read the proposal, press **Approve** or **Reject** (a reject needs a reason from the list). *You should see:* what changes on approve and on reject; the engine, the model, the confidence (shown as the model's own number, never calibrated), the rule the answer passed, and each alternative's probability; after pressing, a confirmation and a new status. If asked for the authenticator code again, enter it (a "step-up" check). *Nothing is approved by waiting:* an unreviewed proposal stays open. *If the page says it is not waiting for a decision:* it was already decided.

## 8. Knowledge search (nurse or doctor)
One-off, window A: `.\.venv\Scripts\python.exe scripts\index_demo_cards.py --clinic-id b230e20e-655f-410d-8398-d2eadcb44dd2 --real` (add nothing for the local fake embedder). It prints the mode, the model, cards created, and how many of 10 made-up questions found their card first (10 of 10 on the owner's run). Restart window B, then `POST /v1/staff/knowledge/search` with `{clinic_id, query}` as a nurse or doctor (the website shows it where the frontend window has put it). *You should see:* card ids, versions and scores, or `abstained: true` with a reason. The cards are **six invented ones** (words that mean nothing); a doctor-only card is never shown to a nurse. *If it answers 503:* knowledge search is off (`CAREFLOW_KNOWLEDGE_SEARCH=on` missing, or not demo + synthetic). *If it abstains on everything:* the abstention thresholds are not set (the demo pack supplies placeholders under `CAREFLOW_DEMO_PHASE4=1`).

## 9. Break-glass (front desk, nurse or doctor)
Reading a patient's messages normally needs a reason on record. Open a patient's messages: the page asks for a **reason from the clinic's list** (the demo list holds `demo_reason`) and a recent second factor; access lasts a limited time and is audited. *You should see:* the messages after the reason is accepted, and an audit entry. *If it refuses:* the reason is not on the clinic's list (step 4's third command adds the demo one), or the code is too old (enter a fresh one), or your role may not.

## 10. Patient requests (front desk)
Menu **Patient requests** (the website's name may differ): the three seeded requests (an erasure, a correction, a nominee) for the closed demo patient account. Each row says what may come next (`allowed_next`: from open: acknowledge, resolve or decline; from acknowledged: resolve or decline; resolved or declined: nothing). Press **Acknowledge** (a recent second factor is checked). *You should see:* the status change; the patient would get an in-app notice (the demo patient cannot sign in). An erasure request may link the two-person erasure request you started for that patient. *If it says* `invalid_transition`: that status cannot follow the current one.

## 11. The Tamil screens
Switch the language of the website to Tamil (the language choice is in the website's header or settings; ask the frontend window if you cannot find it). *You should see:* the same screens in Tamil. **Say this out loud when showing it:** every Tamil string is **unreviewed** by a Tamil reader (`docs/approval-pack/tamil-review.md`), the Sarvam draft is a machine suggestion, and nothing in Tamil is ever sent to a patient. The deterministic red-flag rules run on the original text whatever the language.

## 12. Finish
Sign out. In window B look at the last lines: *you should see* no repeating `ERROR` lines (the worker is quiet when idle). Close windows C and B with Ctrl+C.

---

## 13. Optional extras: visit preparation, family links and the card editor (synthetic data only)
These three features are OFF until you switch them on. Add the lines below to `E:\Claude\careflow-run\.env` yourself (never paste the file anywhere), run the migrations, restart the backend (step 1) and the website, and sign in with the accounts of step 3.

**The settings** (exactly `on`; each is off when missing):
```
CAREFLOW_PATIENT_VISIT_PREP=on
CAREFLOW_FAMILY_LINKS=on
CAREFLOW_KNOWLEDGE_EDITOR=on
CAREFLOW_DEMO_PHASE4=1
```
`CAREFLOW_DEMO_PHASE4=1` supplies the labelled demo placeholder checklist for visit preparation (`CAREFLOW_VISIT_READINESS_CONTENT`); without it, or without your own value, a patient sees "Nothing to prepare for this visit." Keep `CAREFLOW_KNOWLEDGE_SEARCH=on` and `CAREFLOW_DEMO_REAL_MODELS=on` from step 0 if you want approved cards to be indexed (the card editor works without them; a card is then approved but not searchable).

**Update and migrate** (window A; the migrations are 0089 to 0093):
```powershell
cd E:\Claude\careflow-run
git fetch origin
git checkout --detach origin/main
.\.venv\Scripts\python.exe -m careflow.migrate --check
.\.venv\Scripts\python.exe -m careflow.migrate
```

**Family links need rules first.** The rule table ships EMPTY, because the real age limits are counsel's; until it has a row every request is refused with "family links are not set up: the clinic has not entered its rules". For a demo walk only, enter the labelled placeholders (rehearse with `--dry-run` first; it asks you to type the project host; it refuses outside dev + demo + synthetic and never touches a real rule):
```powershell
.\.venv\Scripts\python.exe scripts\seed_demo_family_rules.py --clinic-id b230e20e-655f-410d-8398-d2eadcb44dd2 --dry-run
.\.venv\Scripts\python.exe scripts\seed_demo_family_rules.py --clinic-id b230e20e-655f-410d-8398-d2eadcb44dd2
```
The rows are labelled "DEMO PLACEHOLDER, not counsel's values" (a guardian link for a dependant aged 0 to 9, a carer link for 30 to 130, consent required). Take them out again with `--remove`. **Say out loud when showing it that these numbers are not counsel's.**

**What to show, and what you should see**
- **Visit preparation.** A patient (signed in, with a confirmed future booking) opens the visit: the checklist the clinic configured, each item markable done or not done; a nurse or doctor sees the same marks read-only (`GET /v1/staff/visits/{id}/prep`). With no checklist configured: "Nothing to prepare for this visit." *If you get "visit preparation is not set up":* the switch is not on, or the backend was not restarted. *If the visit is "not found":* it is not that patient's confirmed future visit.
- **Family links.** The patient asks to be linked (guardian or carer); front desk lists the request, picks the dependant's record (found with the patient search), says whether the linked person's consent was obtained, and verifies; the age is checked by the server against the placeholder rule (try a dependant of 15 as a guardian: `age_not_allowed`). The patient can revoke; front desk can revoke with a reason word. A verified link **grants nothing yet**. *If it says "family links are not set up: the clinic has not entered its rules":* run the seed above.
- **Card editor.** A nurse or doctor drafts a card or a message template (they type every word; nothing is seeded), edits it, submits it; a DOCTOR WHO IS NOT THE AUTHOR approves or rejects it; an approval is version 1, 2, 3 and so on. With the real-model settings on, the worker indexes an approved card within a tick and knowledge search can then find it (a card for doctors only is never shown to a nurse). *If the approval is refused:* the approver is the author, or not a doctor, or needs a fresh authenticator code.

---

## 14. Demo placeholders for every non-clinical screen (synthetic data only; nothing is approved)

`docs/demo-placeholders.md` lists what is entered, what stays blocked and who owns it. Everything below is labelled "DEMO PLACEHOLDER, not approved by the clinic owner" (or "DEMO ONLY"). The application **refuses to start** in any profile except demo with synthetic data while a labelled row exists, so these cannot leak into a clinical deployment; take them out with `--remove` first.

**1. Migrate** (window A; the new migration is 0096, which adds the label column and the check):
```powershell
cd E:\Claude\careflow-run
git fetch origin
git checkout --detach origin/main
.\.venv\Scripts\python.exe -m careflow.migrate --check
.\.venv\Scripts\python.exe -m careflow.migrate
```

**2. Enter the placeholders** (each asks you to TYPE the project host, refuses outside dev + demo + synthetic, prints counts only; rehearse with `--dry-run`; use your dev clinic id):
```powershell
.\.venv\Scripts\python.exe scripts\seed_demo_placeholders.py --clinic-id b230e20e-655f-410d-8398-d2eadcb44dd2 --dry-run
.\.venv\Scripts\python.exe scripts\seed_demo_placeholders.py --clinic-id b230e20e-655f-410d-8398-d2eadcb44dd2
.\.venv\Scripts\python.exe scripts\seed_demo_family_rules.py --clinic-id b230e20e-655f-410d-8398-d2eadcb44dd2
.\.venv\Scripts\python.exe scripts\seed_consent_notices.py
```
The first enters the break-glass reasons and limits, the public-grievance cap, the canary cap, the per-call spend cap and the bookable visit types. The second is the family-link rules (section 13). The third publishes the labelled DEMO ONLY consent notices from the demo pack (it needs `CAREFLOW_DEMO_PHASE4=1` and is permanent by design: a notice cannot be removed, which is why the startup refusal exists).

**3. The switches** (lines in `E:\Claude\careflow-run\.env`, exactly `on` or `1`; restart the backend after any change; never paste the file anywhere):

| To see | Switch |
|---|---|
| The review console, watchdog, canaries, cost canvas, grievance and erase words, consent labels, time zone, care-gap shape | `CAREFLOW_DEMO_PHASE4=1` |
| Patient messaging | `CAREFLOW_PATIENT_MESSAGING=on` (also needs `CAREFLOW_MESSAGE_MAX_PER_MINUTE`, which the demo pack supplies) |
| Patient booking (the visit types the script marked bookable) | `CAREFLOW_PATIENT_BOOKING=on` |
| Visit preparation | `CAREFLOW_PATIENT_VISIT_PREP=on` |
| Family links | `CAREFLOW_FAMILY_LINKS=on` |
| Card and template editor | `CAREFLOW_KNOWLEDGE_EDITOR=on` |
| Break-glass | none (a grant needs the reasons and limits from step 2, a second factor, and a patient of the clinic) |
| Public grievance form | none beyond the demo pack and the clinic being published (`scripts\dev_clinic.py publish --clinic-id <id>`) |
| The checkpoint-retention report | optional line: `CAREFLOW_RETENTION_CHECKPOINT_DAYS=90` (the specification's starting value, **not approved**; report only, deletes nothing) |

**3b. Check that everything is in place** (read-only, prints PASS or FAIL lines and counts only, never a value; asks you to TYPE the project host; exit status 0 only if every check passes):
```powershell
.\.venv\Scripts\python.exe scripts\demo_smoke.py --clinic-id b230e20e-655f-410d-8398-d2eadcb44dd2
```
It checks: migrations applied, the clinic is published, staff roles exist, every demo placeholder is labelled, the six demo cards are indexed, a search returns a card (with the local embedder: nothing is spent), provider keys and spend ceilings present when `CAREFLOW_DEMO_REAL_MODELS=on` (names only), the rules-before-models guarantees, and that the switches in `.env` match this section. A FAIL line says in plain words what to run.

**4. Take everything out again** (labelled rows only; a row without the label is never touched):
```powershell
.\.venv\Scripts\python.exe scripts\seed_demo_placeholders.py --clinic-id b230e20e-655f-410d-8398-d2eadcb44dd2 --remove
.\.venv\Scripts\python.exe scripts\seed_demo_family_rules.py --clinic-id b230e20e-655f-410d-8398-d2eadcb44dd2 --remove
```
Consent notices cannot be removed. **What you should see:** the screens above open with their placeholder lists; every value is visibly a demo (labelled or "DEMO ONLY"). **Say out loud when showing it that none of it is approved.** Still blocked, with reasons by owner: `docs/demo-placeholders.md`.

---

## 15. Patient portal sign-in by e-mail and password (demo only)

**Start the demo backend ONLY with the script, from the run folder:** `cd E:\Claude\careflow-run` then `.\scripts\run_demo_backend.ps1` (port 8000; `-Port 8010` if 8000 is taken, and then the frontend proxy must follow). The script reads the `.env` of the folder you are in, prints names only (never a value), and REFUSES to start unless `CAREFLOW_PROFILE` (if set) is `demo`, `CAREFLOW_DATA_POLICY` (if set) is `synthetic_only` and `CAREFLOW_ENV` (if set) is `dev`. It sets these names: `CAREFLOW_PROFILE=demo`, `CAREFLOW_DATA_POLICY=synthetic_only`, `CAREFLOW_DEMO_PHASE3=1`, `CAREFLOW_DEMO_PHASE4=1`, `CAREFLOW_RUN_WORKER=true` and, only if unset, `CAREFLOW_CLINIC_TIME_ZONE`. **Why it matters here:** a patient account's origin tag comes from the profile (demo means `test`), so a backend started any other way (for example plain `uvicorn`) registers accounts under the wrong origin and the claim then answers 403 "origin mismatch": register a new account after starting it with the script.

Phone codes need a paid SMS provider, which is a new external service and is not approved. For dev walks a patient can sign in with an e-mail address and password instead. It is a **demo convenience**: the backend refuses to start with it on outside the synthetic demo, and the claim route refuses it there too. It reuses the portal's real flow (register, consent, activate, **claim**, front-desk verification); nothing is shortcut.

**1. Create the sign-in yourself** in the Supabase dashboard of the DEV project: Authentication, Users, Add user, tick auto-confirm, choose a password. Never paste the password anywhere.

**2. Switch it on** (one line in `E:\Claude\careflow-run\.env`; the start script already sets the demo profile and synthetic data policy), then restart the backend:
```
CAREFLOW_DEMO_EMAIL_SIGNIN=on
```

**3. Prepare the synthetic patient record** (it asks you to TYPE the project host; use the same e-mail as the auth user; rehearse with `--dry-run`; prints counts only):
```powershell
.\.venv\Scripts\python.exe scripts\seed_dev_portal_patient.py --email you@example.test --clinic-id b230e20e-655f-410d-8398-d2eadcb44dd2 --dry-run
.\.venv\Scripts\python.exe scripts\seed_dev_portal_patient.py --email you@example.test --clinic-id b230e20e-655f-410d-8398-d2eadcb44dd2
```
The script creates ONE synthetic patient named `DEMO Portal Patient`, born `1990-01-01`, in the dev clinic. It does not bind your account.

**4. Walk the portal** (the portal sign-in must offer e-mail and password; see the frontend note below). The order matters, and each step has one closed answer:
1. Sign in, then **register** (`POST /v1/portal/register`), grant the care-delivery **consent** shown (`/v1/portal/consents`), then **activate** (`/v1/portal/activate`). A claim before activation answers `activation_required`; before consent, `consent_required`.
2. **The record must be identity-confirmed before it can be claimed.** The synthetic patient the script made has no confirmation (a confirmation is a front-desk act and the script does not fake one). So **as front desk, open the patient (search "DEMO Portal Patient") and confirm the identity** (`POST /v1/staff/patients/{id}/confirm-identity`). Do this before the claim if you want the claim to come back `claimed` at once. **"Verified" on the patient screen is NOT the same as confirmed.** The screen shows "Verified, registered via front desk" for any record a front-desk person registered, but the claim (and the approval of a claim) needs an explicit confirmation event. Look at the status line "latest identity event": if it is empty, press **Confirm identity** anyway; it works on a record that already shows Verified. Only when the latest event says `confirm` is the record claimable.
3. **Claim** with the name `DEMO Portal Patient` and date of birth `1990-01-01` (`POST /v1/portal/claims`). `claimed` binds the account. If the record was not confirmed yet, the answer is `pending_review`: a front-desk person resolves it (`GET /v1/staff/portal-claims`, then `POST .../resolve` approve naming the patient). **Do not press claim again while it is pending**: every repeat counts toward a lock after five tries (`locked`).
4. **Approving needs a REAL second factor at the database** (`docs/mfa-live-pass.md`): a password-only front-desk session gets 403. An approval for a record that is not confirmed answers `identity_not_confirmed` (confirm it first, step 2); `record_not_claimable` means erased, merged away, already bound or another clinic's.
5. After approval the portal reads work: `GET /v1/portal/me` shows `bound_to_a_patient_record: true` and the stage `active`.

**5. Take it away again:** `--remove` with the same e-mail erases the synthetic record (identifying fields removed, row and audit trail kept); delete the auth user in the dashboard; remove the switch line.

*If the claim answers 403:* the switch is not on (restart the backend), the token has no e-mail, or the project marks the address unconfirmed. *If it answers `pending_review`:* the name or date of birth was typed differently from step 3, or the e-mail differs from the one the script was given. **Say out loud when showing it that this sign-in is a demo shortcut for the SMS provider that is not approved.**

**If the portal account was registered before the backend ran through the script (a stale account).**
- What the stage words mean: they come from one function (`iam.patient_stage`) that reads the account row and the claim rows at the moment of the request. `Created` is `registered` (the account status is `invited`); `New patient` is `new_patient` (status `active`, no claim yet); `pending_activation` is a claim waiting for front desk; `active` needs an approved or automatic claim. The same sign-in moves from `Created` to `New patient` only because the activation write happened in between (or the page was read before it). Note `POST /v1/portal/activate` answers HTTP 200 for all three results; only `activated` or `already_active` means the account is active, `consent_required` is also a 200.
- The earlier account is NOT invisible: account reads look the account up by its id, not by origin, so they answer 200. Only the claim cares about the origin: a patient account's origin tag is set by the backend that registered it (`test` under the script, `live` under plain uvicorn). Registering again does not change it (the answer is `exists`).
- **Find out first:** with the backend started through the script, claim once. If the answer is a 403 whose text says "origin mismatch", the account is stale. If the answer is `pending_review` or `claimed`, the origin is fine and nothing needs resetting (the usual cause of `pending_review` is the missing confirmation, step 2).
- **The exact fix for a stale account:** copy the auth user's id (Supabase dashboard, Authentication, Users, "User UID": not the e-mail, never the password) and run, from the run folder, first with `--dry-run`:
```powershell
cd E:\Claude\careflow-run
.\.venv\Scripts\python.exe scripts\reset_dev_portal_account.py --account-id <THE-USER-UID> --dry-run
.\.venv\Scripts\python.exe scripts\reset_dev_portal_account.py --account-id <THE-USER-UID>
```
It asks you to type the project host, refuses outside dev + demo + synthetic, changes ONE column (the account's origin to `test`) of ONE patient account that holds no staff role, is not bound to a record and has no claim request, and prints counts and the origin word only. It writes no consent, no audit row, no patient record. The consent already granted stays on record and still counts. (A brand-new auth user also works: register, consent, activate, claim; then run `seed_dev_portal_patient.py` again with that user's e-mail.)
- **Consent:** one granted decision on the current notice is enough. Granting again with a new request key only adds another `granted` row (append-only; the newest decides); the same key is a no-op (`recorded: false`). It is an error only if the notice is not the current one (409), for example after the consent notices were published again: then grant the new version, or the activation and claim answer `consent_required`.
- **The order now:** register (`exists` is fine), consent, activate (check the word is `activated` or `already_active`), then front desk confirms the identity (even though it shows Verified), then the patient claims: it returns `claimed` directly. Without the confirmation it returns `pending_review`, and then the front desk's approval answers `identity_not_confirmed` until the confirmation is recorded.

---

## If something goes wrong: the fixed causes
1. **Window B not running or stopped:** every page fails; start step 1.
2. **Database behind the code:** the backend refuses to start; run the migrations (step 0.1).
3. **A setting is missing:** a route answers `503 ... not configured`, or a stage says `gate missing`; the log names the setting, never its value.
4. **A spend limit:** `budget refused`; the daily ceilings and the per-run cap are in `.env` (set by `scripts\set_provider_keys.ps1`).
5. **A provider did not answer or refused:** `timed out`, `provider error` or `provider refused ... (status NNN)`; try `--fresh` again, or run the live check (`docs/design/real-model-demo.md`).
6. **Our own check rejected a model's answer:** `answer rejected: ...`; the case goes to a person, which is the safe outcome.
7. **A role is not allowed:** the page says "your role may not do this"; sign in with the account for that role (table in step 3).
8. **A second factor is stale:** enter a fresh authenticator code.

**What to say about limits, truthfully.** Synthetic data only; every model output is a suggestion and a person decides; the confidence is never calibrated; the hosted providers are refused in the clinical profile; no real protocol card or red-flag rule exists (those are the clinician's); nothing is sent to any patient. What is and is not built: `docs/status-now.md`.
