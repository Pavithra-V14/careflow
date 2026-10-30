**SHAPE APPROVED (owner, 2026-10-30). Retention periods are NOT decided** (a clinician and the owner decide): when the retention job is built they are REQUIRED settings with no defaults, and the 90-day, 30-day and 8-year figures below stay the spec's starting values, not configuration. **The purge role and function are a new privilege: no code until a separate design with exact grants and an rls-security-reviewer pass exists and the owner has signed off the grants.**

**(Original note, 2026-10-29.) Design note only. No code, no migration.**

# Retention and erasure of agent working state and review tasks

BUILD-SPEC 9.4 sets the retention classes and says "a scheduled, audited deletion job enforces this; a retention policy nobody runs is worse than none". Nothing runs today, and the tables that hold agent working state are append-only with deletion blocked for every role including the owner (0021, 0018, 0023, audit.log). This note says what is kept, for how long, who may erase, and what an append-only table needs for a deletion that is allowed.

## 1. What is kept, and for how long

The periods below are the spec 9.4 defaults ("starting values, not fixed law", configurable per clinic). **Every period and every "who decides" in this note is a clinic / clinical-lead decision, not an engineering one.** Where the spec gives no number, the cell says OPEN.

| Table (migration) | Class (9.1) | Holds | Proposed rule |
|---|---|---|---|
| `agents.graph_checkpoints`, `graph_checkpoint_blobs`, `graph_checkpoint_writes` (0021) | Agent working state | Ids, fixed-word statuses, (card id, version) pairs; never letter text (a test greps the bytes) | Delete 90 days after the run's last checkpoint (spec 9.4). A run still paused (awaiting review) is never deleted. |
| `ops.queue`, `ops.outbox` (0006) | Agent working state | Task type, payload ids, status | 90 days after completion (spec 9.1 class); dead letters follow their review task. |
| `agents.review_tasks` (0007, 0013, 0020, 0025) | Operational state | Kind, priority, role, status, provenance (rule ids, ids; never patient words) | "Active, then archived": OPEN. Proposal: an open or acknowledged task is never deleted; a resolved one is archived (kept, hidden from queues) for the clinical-record period, because it records why a person decided. |
| `agents.triage_proposals`, `agents.review_decisions` (0018) | Clinical record (a human decision on a patient's referral) | Candidate cards, the reviewer's decision | Clinical-record period (8 years after last encounter; minor until 21, min 8). Never deleted by the working-state job. Only decisions by a human are training labels (rule 9). |
| `mpi.identity_confirmations` (0023, 0025) | Clinical record (identity verification history) | Who confirmed or revoked which patient, reason word | Kept for the life of the patient record. See section 4. |
| `audit.log` | Audit | Actor, action, ids, closed words | Permanent (or at least 8 years per clinic policy). Never deleted; on erasure the identity in it stays a bare account uuid (spec 9.4). |
| `msg.messages`, `msg.screenings`, `docs.documents` and their storage objects | Operational / clinical record | Encrypted bodies, hashes | Already specified elsewhere (30-day prompt and response bodies, document retention); not changed here. |

## 2. Who may erase

- **Working-state expiry (the 90-day rows):** nobody by hand. A scheduled job running as a dedicated purge identity (section 3), audited, with a dry-run count first. A person can pause the job; no person can run a one-off delete of other ranges.
- **A patient erasure request:** spec 12.5 (two-person rules) lists "processing an erasure request" among the two-person actions. Proposal: one front-desk or admin person raises it, a DIFFERENT person with the clinical lead's authority approves it (the same two-person rule as merges), and the executing function writes one `audit.log` row (ids and counts only). What erasure removes is already fixed by spec 9.4: identifiable fields, with audit entries kept and the identity tokenised. This note adds only the working-state consequences in section 4.
- **Never by anyone:** a human decision (review decision), an audit row, a confirmation or revoke event, a protocol or rule version.

## 3. What an append-only table needs for an allowed deletion

Today a trigger blocks `update`, `delete` and `truncate` for every role, so an allowed deletion cannot exist. The smallest safe shape (a future migration, with the rls-security-reviewer pass):

1. **One purge role** (`careflow_purge`, NOLOGIN or a dedicated login; a NEW ROLE, so it needs the owner's explicit sign-off) that is not `careflow_app`, holds no other privilege, and is used only by the scheduled job.
2. **One SECURITY DEFINER function per table family** (`retention.purge_checkpoints(cutoff)`, ...), `execute` granted to the purge role only, `search_path` pinned. It deletes only rows older than the cutoff that are not in a protected state (for checkpoints: no paused run), counts them, and writes the audit row in the same transaction.
3. **The append-only trigger gets one narrow exception**: it allows `delete` only when `current_user` is the function owner AND a transaction-local flag set by that function is on. A plain `set local` flag settable by anyone is NOT acceptable; the exception must be unreachable from `careflow_app` (the function sets the flag, and `careflow_app` has no execute on it).
4. **No exception for `update`, `truncate`, or any other table**, and none for `audit.log`, `agents.review_decisions`, `mpi.identity_confirmations`.
5. **Tests that must exist before it ships:** the restricted application role still cannot delete anything; the purge role can delete only through the function; a row inside the window and a paused run survive; the function is idempotent (a second run deletes nothing); the audit row carries counts only; a dry run deletes nothing.

Residuals to state when built: the table owner can still disable the triggers (as in 0012, 0021, 0023); a purge bug deletes data permanently, so the first production run is a dry run with the counts reviewed.

## 4. What erasure of a patient does to the append-only tables

- **`mpi.identity_confirmations` references the patient** (composite foreign key). Erasure today blanks the patient's identifiable columns and sets `erased_at`; it never deletes the row, so the events can stay. Test teardown cannot delete confirmed patients for the same reason (an acknowledged test-data residue, see `deferred-items.md`).
- **Checkpoints and review tasks mention the patient by id only.** After erasure the id points at a blank record, which is enough; no separate scrub is needed, and the 90-day job removes working state on its normal clock.
- **Review tasks and decisions that cite a document:** the document's body is erased by the documents procedure; the decision row stays (it is the record of what a person decided).
- OPEN for the clinical lead: whether a review decision about an erased patient must also be removed after the statutory period, and whether the tokenised identity in `audit.log` is enough under the clinic's reading of the erasure rules.

## 5. Decisions needed (owner / clinical lead)

1. Confirm or change each retention period in section 1, and the archived-task rule for resolved review tasks.
2. Approve a new purge role (and its function-only execute grant), or choose another mechanism.
3. Approve the two-person erasure shape in section 2 and who the second person is.
4. Decide the two OPEN items in section 4.

## 6. What is NOT built

No job, no role, no function, no migration. Until the owner approves, `agents.*` working state accumulates (documented in 0021 as a residual) and the pre-launch list carries this as a blocker for real data.
