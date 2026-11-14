# Threat model (2026-11-04, extended 2026-11-08): worker, step-up routes, storage, model gateway, patient view; break-glass, patient rights, staff recovery, the erasure path, the clinical record

Scope: the five places where the system holds the most power or touches the most sensitive data. Method: for each, what is protected, who could attack it, how, what stops them today (with the file that does), and what is left (residual, with the pre-launch item in `pre-launch-checklist.md`). **This is an engineering note, not a clinical or legal document; it was written from the code and the design notes, and every "control" below names the test or file so it can be checked.** Out of scope: the frontend, hosting and the network (not built), the breach runbook.

Actors considered: a curious or malicious staff member; a staff member of another clinic; a patient or guardian; an attacker with a stolen staff token; an attacker who controls a letter's content (a referral, a message); a compromised dependency or provider; an operator with the database login.

## 1. The worker (the unattended queue consumer)
**Protects:** clinic isolation and the integrity of the identity records the worker writes (it registers patients from referral letters and runs the Front Door graph).
**Shape:** an asyncio task inside the API process (`CAREFLOW_RUN_WORKER` + `CAREFLOW_WORKER_ACCOUNT_ID`); it has NO token. Each of its transactions sets `request.jwt.claims` with `sub = <that account>`, and the database role is the app login with row-level security keyed on that sub (`careflow/tick.py`).
| Threat | Control | Residual |
|---|---|---|
| The worker attaches an identity token to someone else's patient or forges a possible-duplicate | Migration 0022: the worker's ONLY write path is `mpi.register_patient_from_referral` (SECURITY DEFINER); its three INSERT policies were dropped (blocker 1, CLOSED) | none known |
| The worker plants a token for a person not yet registered, so a later real referral links silently | 0023: worker-created records are unverified and never auto-linked; front desk confirms; revoke exists (0025). Blocker 10 CLOSED in code | item 3 (STRONG_IDENTIFIERS sign-off) and 11 (no pre-0023 worker row) |
| The worker account also holds a human role and acts with it | 0024: an account holding `queue_worker` can hold no other role; `scripts/check_worker_accounts.py` runs in CI and reports older violations | none |
| Anyone who can run code in the API process or reach the app database login can claim to be the worker (there is no token to steal, the claim is set by the process) | The app login is a restricted role (no BYPASSRLS); the worker's reach is what its RLS policies grant, nothing else; claims are transaction-local | **Residual:** the process is the trust boundary. A compromised API process or leaked `DATABASE_URL_APP` can act as ANY account by setting claims (this is true of every request path, not only the worker). Production needs the secret manager, network limits on the pooler and a separate worker process with its own login (item 27). Design note: `blocker1-worker-token-forgery.md` |
| A poisoned job payload makes the worker call a model with another clinic's data | Jobs carry one clinic id; the graph reads through RLS as the worker; model calls are routed by data class (section 4) | tested for the Front Door; re-check when new job types are added |
| A job loops or floods | Attempt ceiling 5 in the table, per-run budget (`careflow/budget.py`), dead letters raise a nurse task | item 30 (runbook drafted) |

## 2. The step-up routes (merge propose, approve, unmerge)
**Protects:** the two-person rule on joining or splitting patient records, the highest-impact identity action.
**Shape:** `require_step_up` (`careflow/app.py`) runs after authentication and before any database work; the pure check is `careflow.auth.step_up_failure`: `aal2`, a well-formed `amr` with a `totp` entry, not in the future, no older than `CAREFLOW_STEP_UP_MAX_AGE_SECONDS`. The claim shape was recorded from a real dev token on 2026-10-30 (`step-up-spike.md`). The active-role header is parsed first (`careflow/active_role.py`).
| Threat | Control | Residual |
|---|---|---|
| A stolen session token (password only) approves a merge | Needs a recent TOTP: otherwise one fixed 401 with `WWW-Authenticate: step-up`, no reason in the body | a stolen token WITH a fresh TOTP is accepted for the window; keep the window short (the setting has no default) |
| One person proposes and approves | The database enforces two different people (merge table constraints and policies); the route also refuses | tested in the matrix (`test_the_two_person_rule_end_to_end`) |
| A forged claim inside a token (`careflow_active`, `amr`) | Tokens are verified against the project's JWKS; a forged `careflow_active` is dropped before it reaches the database (`claims_with_active`) | the claim shape depends on the provider's token format (Appendix B, recorded once, re-check after any Auth change) |
| Replay of an approval | Every write needs an idempotency key; a replay answers the original result and writes nothing twice | none |
| Merge hides a wrong link | Unmerge breaks the link and raises a nurse task listing decisions made meanwhile; nothing is deleted (`wrong-merge.md`) | clinical review of flagged decisions is a person's |

## 3. Storage (referral documents)
**Protects:** the confidentiality and integrity of uploaded referral letters (patient data) and the system from hostile files.
**Shape:** private bucket `careflow-documents`; signed upload then server-side re-verification (type, size, existence at the claimed path), then registration in `docs.documents`. Migration 0027 denies EVERY direct operation on `storage.objects` and `storage.buckets` for `anon` and `authenticated` (no list, read, upload, delete, signed URL or bucket call from a browser token). The server-side secret key is used only by the adapter and reaches CI shard 3 only (`tests/test_ci_shards.py`).
| Threat | Control | Residual |
|---|---|---|
| A browser token reads or lists another clinic's documents | 0027 deny-everything policies; a live test tries list, read, upload, delete, signed URLs and bucket calls as `anon` and `authenticated` | the secret key is the whole boundary: it must live only in the secret manager (item 12 rotation; item 27) |
| A malicious file (malware, a PDF built to crash the reader or inject text) | Allowlists on type and size; the reader treats unreadable documents as `document_unreadable` and routes to a nurse; extracted text is untrusted input (section 4); real uploads are disabled outside `demo` | **No malware scanner exists (item 2).** Uploads stay synthetic-only until one does |
| A path chosen by the client points at someone else's object | The server computes the clinic-prefixed path and re-verifies existence at that path; the bucket name is fixed | tested in the upload tests; re-check if the path scheme changes |
| A deleted document returns because a signed upload URL for its path is still valid | Known and recorded | the erasure procedure must account for it (items 4, 18) |
| Documents leave the intended region | Dev and CI projects are Tokyo and synthetic only | item 18 (India region, privacy owner's confirmation) |

## 4. The model gateway (every model call)
**Protects:** patient text never reaching a provider that is not approved for it, and no model opinion overriding a deterministic safety rule.
**Shape:** `careflow/gateway.py` routes by data class: a `PHI` request goes only to a registry row marked `phi_approved`, else `NoApprovedRoute` and a visible degrade; the red-flag rules run BEFORE any model call on every path (the one carve-out is image-to-text transcription, whose output is screened by the rules before anything else; a failed or empty transcription is never "clear"); `readiness` makes a `clinical` deployment report not ready without an approved route. Hosted adapters for SYNTHETIC demo only, behind gates (not CI, demo profile, `synthetic_only`, a key, a verified date, a per-role spend ceiling with no default), are on the UNMERGED branch `c1-providers`; this tree has fakes only. Prompts are scanned for patient values in CI (`careflow/prompt_scan.py`).
| Threat | Control | Residual |
|---|---|---|
| Patient text sent to an unapproved provider | The data-class filter is applied first and no call class bypasses it; the data-flow register (spec 17.6) lists each external call; `refuse_demo_outside_demo` stops demo adapters outside demo | any new adapter must be added to the register and the test (rule 13) |
| Prompt injection in a letter or message changes a priority or a fact | Rules first; model output can only escalate, never de-escalate a flagged case; grounding checks tier-verify facts before a rule sees them; Jev output is never evidence (`NOT_EVIDENCE_ENGINES`, on `c1-providers`, not in this tree yet); injected-text gold tests are a hard gate (14.3) | the gate has no evidence yet (item 25); the grounding checks are tested on fakes, not real models |
| A model call is repeated, runs away or is too costly | Per-run budget, per-request and per-clinic daily ceilings, circuit breakers (10 failures, probe after 60 s); the safety class is counted but never refused | the ledger is IN MEMORY (per process): a restart or a second process resets it. A durable ledger is item 13 and needs a grant |
| Secrets or patient text in logs and errors | Fixed error bodies; logs carry ids and error classes only (many tests assert a sentinel string never appears) | keep the sentinel tests for every new route |
| Provider-side retention or training on patient text | Only `phi_approved` rows (an agreement) may see patient text; none exists | item 20 |
| A model's output is stored as a training label | Auto-approved outputs never become labels; only human decisions do (CLAUDE.md rule 9) | no training pipeline exists; keep it so |

## 5. The patient view (decrypting demographics for staff)
**Protects:** names, dates of birth and phone numbers (encrypted at rest) from bulk or casual exposure.
**Shape:** `careflow/patient_view.py`, demo profile with synthetic data only: a list row shows the name, YEAR of birth and last four phone digits within the caller's own clinic; the full date of birth and phone appear only when ONE record is opened (front desk, nurse, doctor of that clinic), which writes an audit row (who, role, patient id, never contents). The label read is capped (hard cap 25 rows, no total, no export). Opens per minute and label rows per minute are required settings with no default; unset means 503, never unlimited. Responses are `no-store`; nothing decrypted is logged or kept.
| Threat | Control | Residual |
|---|---|---|
| A staff member scrapes the register | Row caps, per-user rate limits (charged for search labels too), no export, no total count, audit row per open | **the limiter is per process**: several processes multiply the limit (item 17). A staff member with legitimate access can still read one record at a time |
| Another clinic's staff reads a record | Row-level security on `mpi.patients`; the view reads through the caller's own RLS; a record outside the clinic is the fixed 404 | covered by the matrix |
| An account without a front-desk, nurse or doctor role (admin, auditor, owner, worker, patient) reads | 403 for every other identity (matrix) | none |
| Keys: a key version that is not loaded, or a tampered ciphertext | Fixed 503, never a partial answer; rotation drill exists (`docs/runbooks/key_rotation.md`) | item 12 (keys in `.env.cli` were exposed in an upload and must be rotated) |
| The view is turned on with real data | Off unless profile `demo` AND data policy `synthetic_only`; otherwise 503 | the privacy owner's written confirmation of the field list is required first (item 17) |

## 6. Break-glass access (migrations 0058, 0062)
**Protects:** a patient's messages from staff whose role does not normally reach them (front desk cannot read the care topic), while still allowing an emergency read.
**Shape:** a time-limited grant for ONE patient, opened with a reason CODE from an owner-kept list; reads return ciphertext the application decrypts; an admin reviews afterwards.
| Threat | Control | Residual |
|---|---|---|
| A staff member opens grants for curiosity | A reason code from the owner-kept table (no free text), a window of at most 4 hours, a cap on re-opens per person and patient in 24 hours, every read an audit row (refusals too), review by an admin who did not open it | the cap and window are numbers the application passes in; a bug that runs SQL as the application role could pass larger ones |
| A reviewer is the opener | `reviewed_by <> opened_by` as a table check and in the function | one human holding two accounts |
| Reads after the window, after review, after the patient is erased, or by a colleague | The read function checks holder, window, review state, erased patient and a live staff role on every call | none known |
| The second factor is old | The database checks `aal2` is present, not how old it is | the read route does not ask for step-up (gap audit) |

## 7. The patient portal: rights, grievances, download (0049, 0051, 0059, 0062)
**Protects:** the patient's own data and the clinic's grievance trail.
| Threat | Control | Residual |
|---|---|---|
| A patient reads another patient's grievance, messages or bookings | Every read function filters by the caller's own bound record; policies compare `account_id = auth.uid()`; the export never includes the care topic below the `active` stage nor clinical notes | an automatically claimed account never reaches `active` (owner question) |
| A staff member forges a stage | `iam.patient_stage()` is derived from claim rows only a front-desk definer function can resolve | none known |
| Grievance text leaks through a log or an audit row | Stored encrypted (bound to clinic and id); audit rows carry the category only | the admin who reads the list sees the text (by design) |
| A download is silently cut | The functions return one row more than the cap and the route sets `truncated` | none |

## 8. Staff recovery (0060)
**Protects:** the second factor of a staff account.
| Threat | Control | Residual |
|---|---|---|
| One person resets a factor alone | Request by one admin, approval by a DIFFERENT admin who is not the account; checks repeated in the table constraints | one human holding two admin accounts (checklist 58) |
| An admin of clinic A weakens an account that also works at clinic B | A request is refused for an account with a live role in any other clinic, at request AND at approval | recovery of multi-clinic accounts is outside this path |
| The factor is reset before the database agrees | The route calls the Auth port only after the database returned a fresh completion in the same transaction; a port failure rolls it back | the real Auth call is unverified and unwired |

## 9. The audited erasure path (0032, 0056, 0057, 0059, 0061)
**Protects:** the right to erasure without breaking the append-only ledgers.
| Threat | Control | Residual |
|---|---|---|
| One person erases a patient | Two different front-desk people; the function is the only path, with column-level grants that give the application role no update on the erased columns | none known |
| Erased text survives in a later table | Each new table that holds patient text (messages, voice chunks, plan text, results, grievances, notes) is added to the same function in the migration that creates it, with a live test; the guards allow only the erase and rotation transitions, for the owner too | tokens, result codes/times, audit rows and message headers are kept by decision (checklist 61) |

## 10. The clinical record and the scheduled purge (0050, 0061, 0063)
| Threat | Control | Residual |
|---|---|---|
| A wrong result is silently acted on | A correction (declared, or inferred; a declared amendment cannot hide a same-instant correction) raises a doctor review task per step that used it | an in-flight run is not stopped; no 30-day window |
| Expired voice words stay in the database | The worker tick deletes expired chunks through a worker-only definer function (one audit row per run that deleted something) | the purge needs the retention setting; other agent working state has no scheduled purge yet |
| A note is edited | Append-only, an addendum is a new row; the guard allows only erase and re-encryption | none |

## 11. Red-flag rules: authoring, approval, withdrawal, retirement (0072, 0076, 0077, 0078)
| Threat | Control | Residual |
|---|---|---|
| A draft or unreviewed rule screens real patients (or its absence is read as safe) | `kb.active_red_flag_rules` admits drafts only to the demo profile; an unknown profile is `clinical`; a profile with no usable rule is `unassessable`, never `clear`; a rule that cannot compile blocks a clear | the demo profile counts drafts (synthetic data only) |
| One person approves or retires a rule | Approval needs two different doctors who are not the author (ledger rows, a guard that refuses `approved` without them for the owner too); retirement needs two different doctors; every step needs a second factor re-checked in the database; one reject closes a draft | one human with TWO doctor accounts (checklist 58a); the owner or a migrator with direct database access can still INSERT an already-approved row (the insert is not guarded; update, delete and truncate are) |
| A rule's content is edited after review | Content (phrases, token sets, patterns, metadata, code, version) is immutable for everyone; the reviewed content is hashed into the ledger | none known |
| A slow or hostile pattern stalls screening | Authoring writes only literal phrases and token sets (no regular expression, bounded counts and lengths, no invisible or unusual space characters); old `patterns` rows come only from the migrator | a migrator-loaded pattern still has no timeout |
| A newer draft or withdrawn version switches off an approved rule | Drafts and `withdrawn` rows never shadow an approved version (SQL and Python agree); only a RETIRED newest version switches a rule off, and that takes two doctors | a retired newest version intentionally withdraws the whole rule code |
| The app runs against an older database | The app refuses to start unless the catalog holds the objects its code needs (`careflow/schema_check.py`) | the check names objects, not every migration |

## 12. The front-desk supervisor role and the revoke alert (0073)
| Threat | Control | Residual |
|---|---|---|
| The new role widens access | It only ADDS to front desk: grantable to someone who already holds front desk, revoked with it, never at invitation, and it sees one kind of task assigned to it | none beyond front desk's own |
| An alert is forged | The insert policy binds an alert to a real revoke event written by the same person (count one, admin audience) | a front-desk person can raise an alert only for their own revoke |
| The staff role functions lose a check on re-issue | The migration copies them with the second-factor check that 0065 patched in and asserts it | none |

## 13. Medications (0074) and the session spend ledger (0075)
| Threat | Control | Residual |
|---|---|---|
| A medication list survives erasure | The audited two-person erasure deletes a patient's rows (the guard allows a delete only for an erased patient) | the privacy owner's written confirmation (checklist 79) |
| A medication row is rewritten | Append-only for everyone (ENABLE ALWAYS guards); a correction or stop is a new superseding row; restrictive second-factor policies on read and write | none |
| A session call's spend is under-recorded or starves a clinic's ceiling | A definer function with an owner-set per-call cap and forced role and class; the ledger is append-only | a person with a direct session could under-record or fill the day (stated residual of 0016) |
| An unknown cap is read as no cap | No limit row means every session spend is refused | none |

## 13b. Patient-facing writes (migrations 0080 to 0084; whole-set review 2026-11-14)
Scope: the patient profile, in-app notices, requests, the public grievance route, patient messaging and patient booking. The patient has NO table grant on any of it: every call is a definer function that checks `auth.uid()` itself and refuses with one answer for every reason. Messaging and booking are off unless the owner turns them on.

| Threat | Control | Residual |
|---|---|---|
| A patient reads or writes another patient's thread, booking or request | Definer functions scope by the bound account; a thread, booking or request that is not yours gives the same answer as a missing one | none known |
| A patient message commits unscreened | The deferred trigger `msg.require_screening` refuses to commit a patient message without a screening row; the route screens in the same transaction; any failure rolls the message back | the screening is not bound to the text screened (the 0013 residual) |
| A patient records `clear` for their own message | A `clear` must match the database's own rule count and hash; the screening is allowed only for a message written in the same transaction | a patient with direct database access as the application role could compute both: OPEN, checklist 89a |
| A patient learns which phrases escalate | `kb.patient_screening_rules` is limited to a bound patient of that clinic | the phrases of every rule status are readable by a patient session: OPEN, checklist 87a, 89e |
| Staff plant an audit key to block a patient or colleague | All definer audit keys carry the reserved `sys:` prefix with a random part (0035 refuses it to the application role); 0084 fixed the two booking keys that did not | none known |
| A slot is double-booked, or the owner's cap is passed by concurrent bookings | The slot row lock, the one-confirmed-booking index, and a row lock on the owner-managed bookable list (no advisory lock any session could take) | bookings of one visit type in one clinic are serialised (throughput, not safety) |
| A patient books what they should not | Only visit types the owner lists; future slots only; a slot held for someone else or for nobody is never bookable | which types to list is the clinical lead's decision (checklist 88) |
| The public grievance route is abused | No sign-in, so it is off unless the per-address limit, the owner-managed hourly cap and a published clinic all exist; unguessable key; same encrypted storage | one attacker can fill the cap; the per-address limit is per process (checklist 85) |
| New personal data survives erasure | Message bodies and grievance text are erased with the patient | the profile row, open requests and public grievance text: OPEN, checklist 84, 89d |

## 13c. Self-review of migrations 0079 to 0088 and the routes added since (2026-11-14, handover pass)
**Scope.** Migrations 0079 to 0088; the routes added since the last security review: patient messaging and booking, the public grievance categories, the staff patient-request list (`allowed_next`), the review-view fields (`model_id`, `decision_rule`), knowledge search, and the spend-ledger change (0086). **Method.** (a) Static checks over the ten migrations against CLAUDE.md's rules, run as code: every `create table` has enable AND force row level security in the same file; every `security definer` function pins `search_path`; every definer function the application role may call reads `auth.uid()`; no `raise exception` interpolates a value; every function has a `revoke all ... from public` somewhere (or is a re-issue that keeps its ACL, or a trigger function); nothing is granted to `public`. (b) Every `/v1` route is in the role-matrix (`MATRIX` or `SEPARATE_HARNESS`; `test_every_route_is_in_the_matrix` checks it) and every write in the OpenAPI snapshot is checked for the idempotency header. (c) The log statements of the new modules and the patient messaging route were read for PHI. (d) The three earlier reviewer passes (0079 to 0083 as a set; 0085; 0086 and 0087) and their fix-forward migrations (0084, 0087, 0088) were taken as read. (e) The Front Door graph change (decision fields on the candidates) was checked against the structural test: no node gained a port, a side effect or a pause.
**Result: no real bug found.** Every static check passed except the items below, each of which is by design or already documented. No migration was needed and none was edited.
| Item | Verdict |
|---|---|
| Write routes without the client idempotency header: `POST /v1/staff/accounts/activate-me`, `/v1/staff/patients/labels`, `/v1/staff/patients/search`, `/v1/staff/knowledge/search` | **By design, not a defect.** The first three predate this review and write nothing a replay could duplicate (activation is idempotent; labels and search write one audit row with a server-made key). Knowledge search writes nothing at the application layer; its only writes are the gateway's spend-ledger rows, each with a server-made `gw-<uuid>` key (rule 5 holds: every write carries a key). |
| `iam.file_public_grievance` (0080) reads no `auth.uid()` | **By design:** it is the public (signed-out) filing path. Its controls are the per-clinic hourly cap row, the per-address limiter, the closed category list and the stored-encrypted text. Reviewed in the 0079 to 0083 set. |
| `kb.index_card_embedding` (0085 to 0088) checks the role but not `aal2` | **Accepted, no route exposes it.** It is called only by the worker identity and the owner-run indexing script. **A future admin-facing route that calls it MUST check `aal2` first** (the staff-write rule); recorded so it is not forgotten. |
| Trigger functions (`kb.card_embeddings_guard`, `kb.red_flag_rule_decisions_guard`) keep the default execute for `public` | **Harmless:** a trigger function cannot be called as a function. Not changed. |
| Log lines | The new modules log nothing. The patient messaging route logs the opaque account id and an error class name when a patient's own message cannot be decrypted, never text. The demo wiring logs fixed words about which provider stage is real or fake. |
| Hosted providers in the clinical profile | Refused at every layer: `build_providers` (not CI; demo + `synthetic_only`), `demo_real_models.allowed`, `knowledge.from_environment` (demo + `synthetic_only` + `CAREFLOW_KNOWLEDGE_SEARCH=on`), each adapter's own refusal of a PHI-labelled request, and the registry loader (a row that is not `synthetic_only` refuses the whole file). Tests: `tests/test_providers.py`, `tests/test_knowledge.py`, `tests/test_demo_real_models.py`. |
| aal2 on the new staff-write functions | `iam.update_patient_request` (0080) requires `iam.aal2_session()` and front desk of the clinic. The patient functions (0082, 0083) rely on the patient's own sign-in (a phone code, no staff second factor) and check `auth.uid()`, the active patient account, the record and the stage themselves. |
| Paused nodes | No node was added or changed in its effects; the structural tests pass. The decision call happens in the retrieval stage, after the rules, and writes nothing itself. |
**Known limits this review does not close (each is written down elsewhere).** "Synthetic" is a declared label, not an inspection: a typed question to knowledge search is masked before any embedding model, but a deployment that mislabels real text as synthetic defeats the gates (`docs/design/hosted-providers.md`). A mistaken card version has no tombstone and version N+1 retires N (a question for Priya, sheet item 38). `content_sha256` of a card vector is caller-asserted. The extension version is not pinned. A billed-but-rejected pair of decision requests is not recorded in the ledger.

## 14. Dependencies
`pip-audit` was re-run on 2026-11-14 over the exported lock (pip-audit 2.9.0): no known vulnerabilities. Earlier, on 2026-11-13, it found one advisory, `opentelemetry-api` 1.45.1 (GHSA-mf5v-w3w3-f6cg, fixed in 1.45.2); the lock was raised to 1.45.2. The job is still report-only (checklist 69).

## What this note does NOT claim
That the system is secure against a compromised API process, a stolen database password or a hostile operator. Those depend on hosting, secrets management and monitoring that do not exist yet (items 26 to 28). The review of all migrations (`rls-security-reviewer`) and the spec review are separate passes; their findings are recorded where they are reported, not here.
