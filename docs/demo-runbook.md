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
