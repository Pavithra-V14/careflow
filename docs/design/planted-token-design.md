**FIRST LINE: design note only. No code, no migration. Option A, if chosen, needs ONE migration (0023: a column, a function change, a verify path) and a small matcher change; option B is not recommended. Waiting for the owner's OK.**

# Pre-launch blocker 10: a worker-created record can carry a token for a person who is not registered yet (design note)

Source: rls-security-reviewer finding 1 on migration 0022 (2026-10-28); owner decision: not accepted silently.

## 1. The risk

The queue worker computes identity tokens itself (it holds the vault key). Migration 0022 stops it attaching a token to an EXISTING patient, but it can still register a junk record carrying the token(s) of a person who has not been registered yet. When that person's real referral arrives, `_find` finds exactly ONE record whose tokens agree on both strong identifiers (`STRONG_IDENTIFIERS = {name_dob, phone}`), `classify` returns `match`, and the referral is linked confidently and silently to the planted record. (If the person IS already registered, two records agree, `classify` returns `ambiguous` and a person decides: surfaced.) The planted record's other fields are the attacker's. Who can do it: the worker process (a bug, a compromise). It blocks **real identity matching** and sits next to the `STRONG_IDENTIFIERS` sign-off (clinician-decisions item 13).

## 2. Options

**A. Worker-created records are "unverified" and are never auto-linked to (recommended first).**
- A new column on `mpi.patients`, set by the registration function only: `registered_via` in (`front_desk`, `referral_worker`), default `front_desk` for every existing and every direct front-desk row. The worker cannot choose it (no parameter).
- The matcher: a hit on a `referral_worker` record, even agreeing on both strong identifiers, is NOT a confident match. It is a review (the existing `near_match` reason, so `match_candidates` and its CHECK are untouched) and a person decides, seeing the stored details. Pooling across an active merge uses the master's flag.
- Verification: a person confirms a worker-created record (a front-desk action; later the review console) which flips it to `front_desk`-equivalent; from then on it matches normally. Until that action exists, every repeat referral for a worker-created patient goes to review: safe, but a visible workload.
- What it changes in the matcher: `_find` also returns the registration source per hit; `classify` takes a set of unverified ids and counts only verified records as `confident`. About 10 lines plus the query column.
- Costs: one migration; the verify path needs an UPDATE of one column (a column grant plus policy for front desk, or a small definer function), which is a new, narrow privilege to review; the review workload for repeat referrals until verification exists.

**B. The database derives the tokens.** The key (and plaintext name, date of birth, phone as function arguments) would move into the database. Costs against the crypto design (BUILD-SPEC 17.2, `careflow/crypto.py`): the HMAC key leaves the application vault and must live in a database secret store readable by the owner role; the plaintext demographics would cross the SQL boundary and could appear in `pg_stat_statements`, slow-query logs and error text, which today never see plaintext; key rotation (`scripts/rotate_keys.py`, the key_version scheme and the per-field subkeys) would have to be duplicated in SQL; it contradicts the current promise that the database holds only ciphertext and keyed tokens. Not recommended.

**C. Provenance by document (cheap add-on, not a fix on its own).** The function takes the id of a registered `docs.documents` row of the clinic and refuses a second patient from the same document. It does not stop planting, but it bounds a worker to one record per human-registered document and gives every worker-created record an audit anchor. Combine with A later if wanted.

**D. Do nothing until a human verification step exists.** Not recommended: the first real referral after launch could be linked to a planted record.

## 3. Recommendation

A first (the owner's lean), with C as an optional second step. Residual after A: a worker can still create many unverified junk records (spam/DoS, visible); a planted record can no longer capture a real person silently.

## 4. Tests that would prove A

- Offline, matcher: a hit on an unverified record that agrees on both strong identifiers returns `review`/`near_match`, never `match`; the same hit on a verified record returns `match`; one verified plus one unverified agreeing record is `ambiguous`/review as before; a merge whose master is verified but whose subordinate is unverified (and the reverse) follows the master.
- Offline, pipeline: the function path passes no `registered_via`; the direct path is unchanged.
- Live, as the restricted role: the registration function writes `referral_worker`; a direct front-desk insert gets `front_desk`; the worker cannot set or change the column (no parameter; no UPDATE policy); only the narrow verify path flips it and only for the caller's clinic; the end-to-end planted-token attack: plant a token for an unregistered person as the worker, then ingest that person's real referral and assert it lands in review and is NOT linked.
- Static, the migration: a new file, existing rows default to `front_desk`, nothing else changes.
- rls-security-reviewer on 0023.

## 5. Questions for the owner

1. Option A (with the verify path) as recommended? And is a front-desk "confirm identity" action the right verification, or should a nurse's approval of the triage proposal also count? (Recommendation: an explicit front-desk confirmation, not the triage approval, because approving triage does not check identity.)
2. Option C now or later? (Recommendation: later.)
3. Accept the review workload for repeat referrals until the verify action exists in a console?

# Related: worker accounts hold `queue_worker` only

The reviewer's residual: a worker ACCOUNT that also holds `front_desk` in the same clinic keeps front desk's direct INSERT policies, which sidestep 0022. The rule is written in `docs/runbooks/staff-accounts.md`. A cheap enforcement, proposed here (no code): **a database check, not a convention.** Either (i) a trigger on `iam.staff_roles` that refuses a `queue_worker` row for an account that holds any other role in any clinic, and refuses any other role for an account that holds `queue_worker` (a small migration, reviewed by rls-security-reviewer); or (ii) a read-only query run by the CI migrate job and by `scripts/verify_app_role.py` that fails if any account holds `queue_worker` plus another role: `select account_id from iam.staff_roles group by account_id having bool_or(role = 'queue_worker') and count(distinct role) > 1`. Option (i) is the real enforcement, (ii) is a stop-gap. There is no `scripts/seed_dev_staff.py` in the repository, so no seeding script needs a change today.
