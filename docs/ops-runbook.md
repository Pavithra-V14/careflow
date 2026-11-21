# Operations runbook (no new services)

What an operator can do with what the project already has: the hosted Supabase project, the repository's scripts and the existing runbooks. **It documents only what exists, and says plainly where something is not available.** Nothing here approves a launch; the launch conditions are in `docs/design/pre-launch-checklist.md`. Setting names are in `docs/config-reference.md`. Never paste a key, a connection string or a patient value into chat, an issue, a commit or a log.

Related runbooks (each drafted, none yet rehearsed): `docs/runbooks/key_rotation.md`, `restore-drill.md`, `rollback-drill.md`, `model-outage.md`, `overdue-red-flag.md`, `dead-letter-growth.md`, `wrong-merge.md`, `breach.md`, `staff-accounts.md`, `sms-dlt-registration.md`.

## 1. Backup and restore

**What the repository has:** nothing that takes a backup. There is no backup job, no backup setting and no stored backup in this project; the database is a hosted Supabase project, so backups are the platform's feature, taken and restored from the Supabase dashboard. **I could not check from the code which backup features the project's plan includes.** Look yourself before relying on anything below.

**Do this once, and write the answer in the pre-launch checklist (rows 27 and 54):**
1. In the Supabase dashboard open the project, then **Database, Backups**. Note what is listed: whether scheduled (daily) backups exist, how many days are kept, and whether point-in-time recovery is offered and switched on.
2. The spec's targets (spec 16.5) are at most 15 minutes of data loss and at most 4 hours to recover. Scheduled daily backups alone cannot meet 15 minutes; only point-in-time recovery can. If the plan has no point-in-time recovery, the target is **not met** and that is a decision for you (upgrade the plan, or accept a longer loss window in writing).
3. Check where the backups live and which region: the production project must be in an India region (spec 16.5, checklist row 27). The dev and CI projects are in Tokyo and hold synthetic data only.

**Restoring.** The drill is written in `docs/runbooks/restore-drill.md` and has NOT been run (checklist row 54). The shape of it:
1. Restore into a NEW project, never over the live one, from the dashboard's restore option.
2. On the restored project run, as the owner: `python -m careflow.migrate --check` (the ledger and checksums must match the repository), `python scripts/verify_app_role.py --env <env>` (the restricted `careflow_app` role exists, cannot bypass row-level security, and the pooler settings are applied) and `python scripts/check_worker_accounts.py`.
3. If the `careflow_app` role is missing on the restored project, `python scripts/create_app_role.py --env <env>` creates it (the role is deliberately not created by a migration).
4. Run the role-matrix suite against the restored project as the restricted role, and the append-only checks: attempt an UPDATE and a DELETE on an append-only table as the owner and as the app role; both must be refused. **Known limit (checklist row 40):** the append-only triggers do not fire under `session_replication_role = replica`, which restores and logical replication can use; the drill must check the triggers fire after the restore.
5. The **encryption keys are not in the database.** A restored database can only be read with the keys of the key versions it holds (`CAREFLOW_ENC_SECRET_V<n>`, `CAREFLOW_HMAC_SECRET_V<n>`). Keep every key version that any retained backup holds (`docs/runbooks/key_rotation.md`, step 5). Without them a backup is ciphertext.
6. Record the result in the shape `tests/test_drills.py` expects; `careflow.drills.restore_evidence` turns it into the pre-deployment claim. Time it; the target is 4 hours.

**Not available:** a point-in-time restore that has been tried; a backup copy held outside Supabase; a quarterly schedule (nobody runs the drill automatically).

## 2. Rotating each key, and what breaks while you rotate

Rotate on a schedule the owner sets and **immediately** if one was exposed (checklist row 12 lists the keys that were once in a local file and are still to be rotated). Never print a key; each step below says where it is entered.

| Key | How | What breaks while rotating |
|---|---|---|
| Demographic encryption and HMAC keys (`CAREFLOW_ENC_SECRET_V<n>`, `CAREFLOW_HMAC_SECRET_V<n>`) | The documented manual procedure: `docs/runbooks/key_rotation.md` and `scripts/rotate_keys.py status`, `rotate --from n --to m`, `verify --to m`. Add the new pair, restart, set `CAREFLOW_ACTIVE_KEY_VERSION`, re-encrypt, verify, keep the old pair until every backup holding it is gone | Until `CAREFLOW_ACTIVE_KEY_VERSION` is set, registration answers a visible 503 when several versions are loaded. The old version must stay loaded until the re-encryption is verified, or records under it cannot be read. Take a backup first |
| Application database password (the `careflow_app` role; `DATABASE_URL_APP`) | The owner changes the role's password on the database and updates the secret in the hosting provider's secret manager, then restarts | New connections fail between the two steps; every request answers 503 until the restart. Do it in a quiet window. Confirm with `scripts/verify_app_role.py` |
| Migrator / owner credential (`DATABASE_URL_MIGRATOR`) | Change it in the Supabase dashboard (Database settings), update wherever the owner keeps it | Only the migration runner and owner-run scripts use it; the running server does not. Checklist row 82: keep it out of daily tooling and rotate it after each use |
| Supabase secret key (`SUPABASE_SECRET_KEY`) | Create a new secret key in the dashboard's API keys, update the places that hold it, then delete the old one | Only the Auth admin call and the Storage intake use it, and both are not wired in this build; nothing patient-facing stops. Delete the old CI secret key (checklist row 12) |
| Token signing keys (the project's JWT signing keys) | Rotate in the Supabase dashboard (JWT signing keys). The backend selects the verification key by the token's `kid` from the JWKS, so it follows a rotation without a restart | Tokens signed by the old key stay valid until you revoke that key; revoking it signs everyone out (staff sign in again). A JWKS fetch failure answers a fixed 503 ("authentication temporarily unavailable") |
| GitHub Actions secrets (CI project) | Replace each secret in the repository settings | CI runs fail until updated |
| Hosted provider keys (`GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `SARVAM_API_KEY`) | Replace in the owner's `.env` (script `scripts/set_provider_keys.ps1` writes them without echoing), restart. **Synthetic demo only; none may exist in a `clinical` deployment** | Calls to that provider fail; the system falls back to the fakes or to a person (section 3) |
| Label pseudonym key (`CAREFLOW_LABEL_PSEUDONYM_KEY`) | Replace | Reviewer pseudonyms in later exports differ from earlier ones, so exports cannot be joined across the change |

**Not available:** automated key rotation (by design: a documented manual procedure, spec 17.2); a key vault (the plan is the hosting provider's secret manager, which is not yet chosen: checklist row 27).

## 3. When a provider is down

The rule everywhere: **the red-flag rules never use a model** and keep running first, on every path, whatever is down. A dependency being down never closes a loop as safe. What happens by dependency, and what the user sees:

| Down | What the product does | What a person sees |
|---|---|---|
| A model or hosted provider (Gemini, Jev, Sarvam) | Demo only. The letter is held for a person (`hold_for_human`, a review task showing the raw document); a triage proposal shows "no suggestion: needs a person"; replies are not interpreted; translation is not used. Nothing is auto-approved or auto-sent. `clinical` has no hosted provider at all | The review queue; the runbook `model-outage.md` |
| The database | The API answers 503. Every route fails closed; nothing is queued in memory | A fixed 503. The frontend's static "in an emergency call 108" notice is backend-free (frontend) |
| The token-signing keys endpoint (JWKS) | A token that cannot be verified is refused; the answer is a fixed 503 | "authentication temporarily unavailable" |
| The worker (queue not drained) | Jobs wait; the watchdog raises a stale-queue alert; red-flag rules and booking keep working because they do not need the worker | In-app alerts (`queue_age`); `dead-letter-growth.md` if jobs fail repeatedly |
| E-mail or chat sender | Not wired (ports with fakes). The in-app alert is the real channel | The in-app banner; `delivery_failed` appears if a channel is configured and dead |
| Tracing and metrics services | Not wired; the in-app watchdog and the audit log do not depend on them | Nothing |
| Embeddings | Card search abstains or answers 503; nothing is guessed | "Search is not available" |
| Voice engine | None exists; the session route answers 503 and the mic is hidden | Patients continue in text |
| The storage service for uploads | Upload routes answer 503 or a clear error; nothing is half-registered | Front desk retries |

`GET /readyz` answers only `{"ready": true|false}`. In `clinical` it is `false` while a required model role has no approved route, so the platform stops sending traffic. In `demo` it is true because the data is synthetic.

The fixed-words failures (the same words every time, never a value or a reason): the single 401 for any credential problem, the single 403 for any wrong role or missing second factor, one 404 for another's or a missing record, the 503 "not set up" answers for a switch or a required setting that is unset, and the family-link and patient-feature "not configured" words.

### The outage drill (2026-11-21): prove it before you need it

`scripts/drill_outages.py` breaks each dependency on purpose, on **fakes only**: no provider is called, no connection is opened, no key is read, nothing is spent. It runs in the synthetic demo only and refuses anywhere else. Run it from the repository (or the run folder) with the demo environment:

```powershell
$env:CAREFLOW_PROFILE = "demo"; $env:CAREFLOW_DATA_POLICY = "synthetic_only"
.\.venv\Scripts\python.exe scripts\drill_outages.py
```

It prints one fixed-words line per check (`PASS <id>: ...` or `FAIL <id>: ...`) and `drill result: PASS` only when every check held (exit code 0). What it proves, per row of the table above: a vision, decision, translation, embedding or database failure or timeout ends in a human-review outcome or a fixed-words refusal, never a guess; the rules run before any model and a model outage never blocks a red-flag nurse task; a stage that failed or paused wrote nothing it should not; a retry with the same idempotency key repeats no effect; spend is recorded once per answered call and a spent ceiling stops further calls without calling the provider; a restart mid-run resumes from its checkpoint without repeating a step. The same checks run in the offline test suite (`tests/test_outage_drills.py`, with a negative control that breaks the code under test and requires the drill to say FAIL). A FAIL line names the check; nothing in the output is a secret or patient text. **It proves the code's reaction to a failed edge; it does not prove a real provider's behaviour** (the live provider checks are `scripts/provider_live_check.py`, and the restore and rollback drills in `docs/runbooks/` need real hosting).

## 4. Reading the audit log for an incident

**Who may read it:** an account holding the `auditor` role at a clinic (`GET /v1/staff/audit`). Others get a 403; a clinic you do not audit gets a 404. Checklist row 77: create a REAL auditor account and test this live before launch; it has not been exercised with a real auditor sign-in yet.

**How:** newest first, a page at a time (`limit`, default 100; pass `next_before_id` as `before_id` for the next older page). Filters: `action`, `resource_type`, `resource_id`, `actor_id`, `since`, `until`, `clinic_id`. For an incident:
1. Fix the window: `since` and `until` around the time reported (times are stored with a time zone).
2. Start from the thing touched: `resource_type` and `resource_id` (an opaque id), then read every row on it in order.
3. Then pivot on the person: `actor_id` for the same window, to see everything that account did.
4. Look at `action` names for the sensitive acts: for example `iam.family_link.verified`, `iam.family_link.staff_revoked`, break-glass opens and reviews, merges and unmerges, rule approvals, autonomy-mode changes, erasures, role changes.
5. Rows hold who, which role, which resource and a few closed details. **They hold no patient values, names, message text or keys**, so an incident reader sees what was done and by whom, not the patient's data; that is deliberate.

**Guarantees you can rely on:** the log is append-only (the database refuses updates and deletes, including by its owning role, except under the replica-role limit in section 1); an audit entry is written in the same transaction as the act it records; retention is permanent in this build (checklist rows 62, 73).

**Not available:** a dashboard page for the log (the route exists; the screen is the frontend's); export to a file; alerting on audit patterns; reading the log from the database directly as part of normal practice (the owner credential bypasses row-level security: checklist row 82).

For a suspected breach follow `docs/runbooks/breach.md` (the 72-hour clock); it is a skeleton: the contacts and counsel's wording are blank (checklist row 56).

## 5. Checks before the first real patient

Run these in order. Each points at the checklist row that holds the detail. **Today the answer is NO-GO** (`python scripts/predeploy_check.py --report-only` prints it; every evidence cell is empty).

1. **Hosting and region** (rows 27, 75): production in an India region; the backend in the same region as the database; a production secret manager chosen; production keys generated there. Dev and CI keys never reused.
2. **Profile and data policy**: `CAREFLOW_PROFILE=clinical`, `CAREFLOW_DATA_POLICY` not `synthetic_only`, none of the demo switches or provider keys set (the application refuses to start if a demo gate is on outside demo + synthetic).
3. **Credentials** (rows 12, 76, 82): every key that was ever in a local file rotated; the second factor ON at both layers (`CAREFLOW_REQUIRE_AAL2` unset or true); the owner credential held outside daily tooling; `scripts/verify_app_role.py` passes; `scripts/check_worker_accounts.py` passes.
4. **Migrations**: `python -m careflow.migrate --check` clean on production; the role matrix and the live suite green against a CI project built the same way.
5. **Clinical content** (rows 21 to 24, 48): red-flag rules, cards, intake questions, thresholds and the gold set authored and approved by named clinicians under the two-person rule; the red-flag recall gate at 100% on a clinician-reviewed set; nothing seeded or invented.
6. **Counsel and the privacy owner** (rows 60 to 62, 84 to 86, 90, 91): consent notice texts, retention periods, family-link rules, guardian and erasure wording, the grievance route.
7. **Uploads and messaging** (rows 2, 9, 18, 29, 55): a malware scanner wired, the document storage overwrite behaviour verified, DLT/SMS registration done, and the Tamil strings reviewed (row 43).
8. **Drills** (rows 28, 54): the rollback drill and the restore drill run and timed (sections 1 and the runbooks); the key rotation rehearsed (section 2).
9. **Auditor and break-glass** (rows 63, 77): a real auditor account tested live; the real break-glass reason list seeded.
10. **The scorecard** (rows 25, 49, 57): the pre-deployment checklist run with evidence at 70 out of 100 or more and no category below 12 out of 20; the `clinical` capability sheet signed off or closed; named owner, on-call and success definition (the go/no-go yes/no gates).
11. **Rollout ladder** (spec 16.4): sandbox, then shadow for at least a week, then assisted. The default mode here is `shadow`, which is the safer start. Autonomous stays off.

Anything not on this page (a hosted dashboard, a Slack channel, e-mail alerts, a log shipper) does not exist in this build; if you want one, it is a new service and needs your approval first.
