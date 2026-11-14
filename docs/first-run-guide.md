# First-run guide: run everything against the dev project

This guide is for a person who is not a programmer. Everything below uses **synthetic (made-up) data** in the **dev** Supabase project. Nothing here touches real patients.

You will use three PowerShell windows:

| Window | Folder | What runs in it |
|---|---|---|
| A (setup) | `E:\Claude\careflow-run` | one-off commands (update, migrate, seed) |
| B (backend) | `E:\Claude\careflow-run` | the backend and the queue worker (one program) |
| C (frontend) | `E:\Claude\careflow-frontend\frontend` | the website you click in |

**Golden rules.** Never paste a password, key or the value of a `.env` line into a chat. When a step says "paste me this", paste only what the step names.

---

## 1. Get the current code (window A)

```powershell
cd E:\Claude\careflow-run
git fetch origin
git checkout --detach origin/main
git log --oneline -1
```

You should see one line starting with a short code, then the title of the newest merge. If `git checkout` complains about changes, paste me the message.

## 2. Check and update the database (window A)

```powershell
cd E:\Claude\careflow-run
.\.venv\Scripts\python.exe -m careflow.migrate --check
```

It prints how many migrations are pending and their file names, in order (for example `0036_...`, `0037_...`, `0038_...`). The list must have **no gap in the numbers** between the first and the last. Then apply them:

```powershell
.\.venv\Scripts\python.exe -m careflow.migrate
```

You should see `N migration(s) applied` and the same names. **About a gap you may have seen (0036, then 0038):** migration `0037_front_desk_scheduling_checkpoints.sql` is in the repository (it was added together with 0038). The runner applies pending files strictly in number order and refuses to run if a lower number is still pending after a higher one was applied, so a correct list never skips a number. If your list ever shows 0036 and 0038 but not 0037, paste me the whole list.

**Migrate BEFORE you start the app.** The app checks at startup that the database has the migrations its code needs (through 0078 today) and **refuses to start** with a plain message naming the missing migration if it does not (`the database has not applied migration 0076 ... run the migrations (python -m careflow.migrate) before starting the app`). Run the migrations, then start the app; never the other way round.

If you see an error mentioning *out of order*, *edited after it was applied* or *missing*, **stop** and paste me the whole message (it contains no secret).

## 3. The `.env` file (names only)

The file `E:\Claude\careflow-run\.env` must contain a line for each name below (`NAME=value`, no spaces around `=`). **Demo settings** are marked; they only make sense on the dev project.

| Name | What it is |
|---|---|
| `CAREFLOW_ENV` | `dev` (**demo setting**; seeds refuse to run otherwise) |
| `CAREFLOW_PROFILE` | `demo` (**demo setting**) |
| `CAREFLOW_DATA_POLICY` | `synthetic_only` (**demo setting**) |
| `CAREFLOW_DEMO_PHASE3` | `1` (**demo setting**: switches on the labelled demo placeholders for scheduling) |
| `CAREFLOW_DEMO_PHASE4` | `1` (**demo setting**: switches on the labelled demo placeholders for review, care gaps, consent text, break-glass, grievances) |
| `CAREFLOW_CLINIC_TIME_ZONE` | e.g. `Asia/Calcutta` (**demo setting**; the demo pack also supplies it if the line is missing) |
| `CAREFLOW_DEV_PROJECT_REF` | the dev project's short reference (used by the seed scripts as a safety check) |
| `SUPABASE_URL` | the dev project address |
| `SUPABASE_PUBLISHABLE_KEY` | the **publishable** key (starts `sb_publishable_`) |
| `SUPABASE_SECRET_KEY` | the **secret** key (starts `sb_secret_`); needed only to create sign-in accounts |
| `SUPABASE_JWKS_URL` | the project address followed by `/auth/v1/.well-known/jwks.json` |
| `DATABASE_URL_MIGRATOR` | the owner connection (used by migrate and the seed scripts) |
| `DATABASE_URL_APP` | the restricted application connection (used by the backend) |
| `CAREFLOW_ENC_SECRET_V1`, `CAREFLOW_HMAC_SECRET_V1` | the two local encryption keys |
| `CAREFLOW_STEP_UP_MAX_AGE_SECONDS` | `300` |
| `CAREFLOW_RUN_WORKER` | `true` (the queue worker runs inside the backend; the start script also sets it) |
| `CAREFLOW_WORKER_ACCOUNT_ID` | the id of the **worker account** (step 5) |
| `CORS_ALLOWED_ORIGINS` | `http://localhost:5173` |

Check that none is missing **without showing values**:

```powershell
cd E:\Claude\careflow-run
$names = 'CAREFLOW_ENV','CAREFLOW_PROFILE','CAREFLOW_DATA_POLICY','CAREFLOW_DEMO_PHASE3','CAREFLOW_DEMO_PHASE4','CAREFLOW_DEV_PROJECT_REF','SUPABASE_URL','SUPABASE_PUBLISHABLE_KEY','SUPABASE_SECRET_KEY','SUPABASE_JWKS_URL','DATABASE_URL_MIGRATOR','DATABASE_URL_APP','CAREFLOW_ENC_SECRET_V1','CAREFLOW_HMAC_SECRET_V1','CAREFLOW_STEP_UP_MAX_AGE_SECONDS','CAREFLOW_RUN_WORKER','CAREFLOW_WORKER_ACCOUNT_ID','CORS_ALLOWED_ORIGINS'
$have = Get-Content .env | ForEach-Object { ($_ -split '=',2)[0].Trim() }
$names | ForEach-Object { if ($have -contains $_) { "ok       $_" } else { "MISSING  $_" } }
```

(`CAREFLOW_WORKER_ACCOUNT_ID` will say MISSING until step 5. Everything else should say `ok`.)

## 4. The clinic (window A)

```powershell
.\.venv\Scripts\python.exe scripts\dev_clinic.py show
```

It lists clinics and, under each, the accounts that hold a role there (ids only). Write down the **clinic id** (a long code like `3f2a...`). If it says `no clinic yet`:

```powershell
.\.venv\Scripts\python.exe scripts\dev_clinic.py create --name "Demo Clinic"
```

and use the id it prints. In the commands below, replace `<CLINIC-ID>` with it.

## 5. The staff accounts and the worker account (window A)

You need one sign-in per role. Use any e-mail addresses you control (they can be made up, for example `frontdesk@example.test`: the dev project does not send mail). Run **one command per missing role**; each asks you twice for a password (at least 12 characters; the window shows nothing while you type). Write the passwords down somewhere private.

```powershell
.\.venv\Scripts\python.exe scripts\seed_dev_staff.py --email frontdesk@example.test --role front_desk --clinic-id <CLINIC-ID>
.\.venv\Scripts\python.exe scripts\seed_dev_staff.py --email nurse@example.test --role nurse --clinic-id <CLINIC-ID>
.\.venv\Scripts\python.exe scripts\seed_dev_staff.py --email doctor@example.test --role doctor --clinic-id <CLINIC-ID>
.\.venv\Scripts\python.exe scripts\seed_dev_staff.py --email admin@example.test --role admin --clinic-id <CLINIC-ID>
.\.venv\Scripts\python.exe scripts\seed_dev_staff.py --email worker@example.test --role queue_worker --clinic-id <CLINIC-ID>
```

Each prints `done: account <ID> holds <role> ...`. **The id printed for the `queue_worker` account is the worker account id.** Put it into `.env` as the line `CAREFLOW_WORKER_ACCOUNT_ID=<that id>` (open `.env` in Notepad, add the line, save). The worker account is never signed in to by a person.

Add `--dry-run` to any command to see what it would do without doing it.

**The front desk supervisor** is a second role that ADDS to front desk (migration 0073). Give it to an account that ALREADY exists and already holds front desk in that clinic (no password is asked for an existing account; a new account, or one without front desk there, is refused):

```powershell
.\.venv\Scripts\python.exe scripts\seed_dev_staff.py --email <THE-FRONT-DESK-EMAIL> --role front_desk_supervisor --clinic-id <CLINIC-ID> --dry-run
```

The dry run prints the plan (`existing account <id> will hold ['front_desk', 'front_desk_supervisor']`). Run it again without `--dry-run` to grant it.

## 6. Seed the demo data, the consent text and the break-glass reasons (window A)

```powershell
.\.venv\Scripts\python.exe scripts\seed_dev_demo_data.py --clinic-id <CLINIC-ID>
.\.venv\Scripts\python.exe scripts\seed_consent_notices.py
.\.venv\Scripts\python.exe scripts\seed_break_glass_reasons.py --clinic-id <CLINIC-ID>
```

- The first prints the project host and asks you to **type it exactly**; it then adds made-up patients, slots, referrals and a merge, and (new) one DEMO result, one DEMO medication, one care plan with a step, three patient requests and three in-app notices for a closed demo patient account. All are synthetic and labelled; none is clinical. It is safe to run again.
- The second publishes the labelled **demo** consent text (the real wording is counsel's).
- The third adds the demo break-glass reason `demo_reason` to the clinic.

If any prints `refusing: ...`, paste me that line.

## 7. Start the backend and the worker (window B)

```powershell
cd E:\Claude\careflow-run
powershell -ExecutionPolicy Bypass -File .\scripts\run_demo_backend.ps1
```

It copies the `.env` values into this window only, sets the demo switches, and starts the backend on `http://127.0.0.1:8000`. You should see lines ending with `Application startup complete.` and `Uvicorn running on http://127.0.0.1:8000`. Leave this window open. Check it in a browser: open `http://127.0.0.1:8000/readyz`. Any short answer (even one that says "not ready") proves the server is running; "unable to connect" means it is not. Stop it later with **Ctrl+C**.

If it prints `Missing in .env: NAME`, add that line (step 3) and start again.

## 8. Start the website (window C)

First time only:

```powershell
cd E:\Claude\careflow-frontend\frontend
npm ci
```

`E:\Claude\careflow-frontend\frontend\.env.local` must contain exactly these three names (`SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `CAREFLOW_API_BASE_URL=http://127.0.0.1:8000`). **Never** put the secret key there. Then:

```powershell
npm run dev
```

Open `http://localhost:5173/staff/` in your browser.

## 9. Which account to use for what

| Account | Signs in to see |
|---|---|
| front desk | referrals, scheduling (hold, book, waitlist, offers, mark delivered), identity |
| nurse | referrals, review of a proposal, care gaps |
| doctor | the same as a nurse, plus care plans |
| admin | alerts, the dashboard, accounts, autonomy |

The first sign-in of each account asks you to **set up an authenticator app** (scan the code with Google Authenticator, Microsoft Authenticator or similar); after that every sign-in asks for the 6-digit code.

---

## 10. The click-through (about 20 minutes)

After each step compare with **What I should see**. If it does not match, copy only the **Paste me** item.

**Step 1. Sign in (front desk).**
Open `http://localhost:5173/staff/`, enter the front-desk e-mail and password, then the authenticator code.
- *What I should see:* a page with a **TEST** badge, your role (front desk) and a menu: Referrals, Tasks, Patients, Identity, Scheduling, Alerts.
- *Paste me:* the words on the screen (not any code or password), plus the last 20 lines of window B.

**Step 2. Open a referral (front desk).**
Menu **Referrals**, click the first row.
- *What I should see:* a list of about four referral letters with a status; the detail page shows the document facts and a status such as "waiting to run" or "proposal open".
- *Paste me:* the screen text, and in window B any line containing `ERROR`.

**Step 3. Review (sign out, sign in as the nurse).**
Menu **Referrals** (or **Tasks**), open a referral that has an open proposal, read it, press **Approve** (or **Reject**).
- *What I should see:* the proposal shows what changes on approve and on reject; after pressing, a confirmation and the status changes. (If the screen asks for the authenticator code again, enter it: that is the "step-up" check.)
- *Paste me:* the message shown, and window B lines with `ERROR`.

**Step 4. Book a slot (sign in as front desk).**
Menu **Scheduling**, tab **Slots**, choose an open slot, press **Hold**, then **Book** for a demo patient.
- *What I should see:* the slot goes from open to held to booked; it disappears from the open list. Trying to book the same slot again is refused.
- *Paste me:* the screen text of any red message.

**Step 5. Create an offer (front desk).**
In **Scheduling**, **Bookings**, cancel the booking you just made (confirm the placeholder dialog), then open **Offers** (or **Waitlist**) and prepare an offer for the freed slot.
- *What I should see:* the freed slot is offered to the highest-priority waiting patient; an offer row appears with the state `awaiting_delivery` and a text you can copy.
- *Paste me:* the screen text, and window B lines with `ERROR`.

**Step 6. Mark the offer delivered (front desk).**
On that offer press **Mark delivered**. Nothing is sent anywhere: this records that **you** delivered it by hand.
- *What I should see:* the state changes to `delivered` with the time.
- *Paste me:* the screen text of any red message.

**Step 7. See an alert (sign in as admin).**
Menu **Alerts**.
- *What I should see:* either alerts (a short class name, a count, and an **Acknowledge** button; no names or health details), or the words "no alerts". Alerts appear for example when a quietly planted test referral (a "canary") is approved by a reviewer, or when work is overdue; **an empty list is normal on a fresh start**.
- *Paste me:* the screen text and whether you pressed Acknowledge.

**Step 8. Open the care-gap panel (sign in as nurse).**
Menu **Care**.
- *What I should see:* a panel with overdue items, unresolved barriers and the review sample, each with an age; a nurse can record a barrier. It may be empty at first.
- *Paste me:* the screen text.

**Step 9. The admin dashboard (admin).**
Menu **Dashboard**.
- *What I should see:* four measured numbers; every other metric is listed as "not measured" (never shown as zero).
- *Paste me:* the screen text.

**Step 10. Sign out and check the worker (window B).**
Press sign out in the page. In window B look at the last lines.
- *What I should see:* no repeating `ERROR` lines. The worker is quiet when there is nothing to do.
- *Paste me:* the last 30 lines of window B.

---

## Optional: one dummy letter through the real models (window A)

Only after the three keys, limits and live checks are done (`docs/design/real-model-demo.md`) and the demo seed has been run (step above). Add these two lines to the `.env` in `E:\Claude\careflow-run` the same way you added the others (the key script cannot do these):

```
CAREFLOW_DEMO_PHASE3=1
CAREFLOW_DEMO_REAL_MODELS=on
```

Then, in window A:

```powershell
cd E:\Claude\careflow-run
.\.venv\Scripts\python.exe scripts\demo_real_models.py --clinic-id <CLINIC-ID>
```

It asks you to type the project host once. It then makes one made-up page (invented names and tokens, never real ones), has Gemini read it, runs the red-flag rules on the text first, asks the decision model for a suggestion, writes a triage proposal that waits for a person, and asks Sarvam for a Tamil draft of a made-up sentence. **It prints only: each stage, real or fake, its status, seconds and cost, whether the rules fired, the order the stages ran in, and the proposal id.** Nothing is sent to anyone.

- *Run it again:* a second run on the same document only reports the earlier result (no model is called). Add **`--fresh`** to make a NEW dummy letter document and run the whole path again (the earlier one is not touched).
- *If a stage did not work:* its line carries a fixed-words reason (`budget refused`, `timed out x3`, `provider error ... (status 503)`, `provider refused the request ... (status 404)`, `gate missing`); `NOT READ` means no text was read, and then the rules show `NOT RUN` (never `clear`).
- *Variations:* add `--kind pdf` (a PDF with a text layer, read without the vision model) or `--flagged` (the page carries the demo rule's invented token: the rules fire and no other model is asked).
- *What I should see:* a line per stage; `real` for each provider whose key and live-check date are set, `fake` plus a reason otherwise; `PROPOSAL WAITING FOR REVIEW: <id>`; and the sign-in console showing a new triage task for "Demo Kilo Sample".
- *Safe to run twice:* the second run reports the same proposal and calls no model.
- *Paste me:* the printed lines (they carry no text or keys).

---

## Optional: the knowledge search over invented cards (window A)

For seeing the retrieval mechanism work, on **invented placeholder cards only** (no clinical card exists). The demo seed must have been run, and the migration with it (`migrate` in step 2 applies 0085; it needs the `vector` extension, which Supabase allows). In the `.env` of `E:\Claude\careflow-run` add:

```
CAREFLOW_DEMO_PHASE4=1
CAREFLOW_KNOWLEDGE_SEARCH=on
```

(The thresholds and the budget come from the labelled demo pack under `CAREFLOW_DEMO_PHASE4=1`.) Then, in window A, index the cards and measure the search:

```powershell
cd E:\Claude\careflow-run
.\.venv\Scripts\python.exe scripts\index_demo_cards.py --clinic-id <CLINIC-ID>
```

It asks you to type the project host once, writes the vectors as the clinic's worker (a second run changes nothing) and prints only: the mode (`fake` or `real`), the model, how many cards were created, seconds, cost, and how many of the made-up questions found their card. Add `--real` to use Gemini's embedding model instead of the local fake (needs the embeddings ceiling and `CAREFLOW_DEMO_REAL_MODELS=on`; the key script asks for the ceiling). Restart the backend, then `POST /v1/staff/knowledge/search` (a nurse or doctor, body `{clinic_id, query}`) returns card ids, versions and scores, or `abstained: true` with a reason.

- *What I should see:* `indexing: 6 created`, then `search ... 10 of 10 questions found their card first` with the fake; a doctor-only card is never returned to a nurse.
- *Paste me:* the printed lines (they carry no text or keys).

---

## When something fails: the three quick checks

1. Window B still open and showing `Uvicorn running`? If not, restart step 7.
2. `http://127.0.0.1:8000/readyz` answers in the browser (any short answer is fine)?
3. `.env.local` of the frontend has the three names and the backend address `http://127.0.0.1:8000`?

For anything else paste me: the step number, what you did, what the screen said, and the last 30 lines of window B. Do not paste passwords, keys, or the contents of `.env`.
