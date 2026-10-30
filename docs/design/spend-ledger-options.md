**DECIDED (owner, 2026-10-30): stay on option (c), the in-memory ledger. When a real model call for reply interpretation is wanted, build the definer-function variant of (b) (b2); that is a NEW GRANT and needs the owner's sign-off at that time, after its own design with exact grants and an rls-security-reviewer pass. Option (a) is REJECTED.** The hard blocker stands: no real reply-interpretation call until then. The rest of this note is the analysis the decision rests on. No code, no migration, no grant.

# A durable spend ledger for the reply-interpretation call: the options

**The problem.** Phase 3 reply interpretation would run a `decision`-tier model call when a front-desk person opens a patient's reply (design `phase3-scheduling-agent.md`, sections 1a, 3 and 9). The gateway needs a spend ledger to enforce the per-clinic daily ceiling (spec 6.1, 15.2). Today the only durable ledger is `ops.spend_ledger` (migration 0016): INSERT is allowed to the `queue_worker` of the clinic only; SELECT to admin, business owner, auditor and the worker. The call would run in the API process under the front desk's own session, and the signed-off design says no worker identity and no new grant on `sched` or `msg` in Phase 3 (section 1a), no stored drafts or tasks in the first cut (section 9 item 3), and **no real model call for reply interpretation until a durable ledger is decided and its grant reviewed by the owner** (section 9 item 2, the hard blocker).

**Facts that shape every option.**
- `PostgresSpendLedger` writes each entry in its OWN short transaction and commits at once, so a call whose request later rolls back still has its spend on record. A write made inside the request's transaction would vanish with a rollback while the money was spent.
- `ops.spend_ledger` is append-only for everyone (trigger), amounts cannot be negative, so a wrong entry cannot be corrected in place.
- The queue worker has no SELECT policy on `msg.messages` (0011: nurse, doctor, and front desk for `appointments` and `safety`), so a worker job could not read a reply to interpret it without a new policy.
- Pool latency is about 0.5 s per statement round trip (POOL-DIAG, `ci-sharding.md`); a second connection per request is not free.

## Option (a): interpretation as a queue job (the worker's existing ledger insert)

- **Grants.** None on the ledger. But the worker would also need to READ the reply (a new SELECT policy on `msg.messages` for `queue_worker`, or a definer function), and a place to put the suggestion for the front desk (see "what is stored"). So "no new grant" holds only for the ledger.
- **What is stored.** The suggestion must outlive the job: a stored suggestion row or a front-desk-visible review task. That is exactly what section 9 item 3 ruled out for the first cut, and a front-desk SELECT policy on that new thing.
- **Failure modes.** Latency (a job, not a click); a job that dead-letters leaves a reply with no suggestion (a visible "needs a person" must be raised); the worker sees patient reply text (a wider blast radius than today, where only people read messages); the ceiling is enforced per clinic correctly because one writer serialises it.
- **Tests.** Everything in (b) plus: the worker can read only appointment replies of its clinics; the job is idempotent; a dead letter becomes a visible hold.
- **What it changes in the signed-off design.** Reverses section 1a ("no worker identity, no new grants on `sched`/`msg`") and section 9 item 3 (no stored drafts/tasks), and moves patient text to the worker. **Three signed-off decisions reopened.**

## Option (b): one narrow insert path for the requesting session

Two shapes; the second is narrower.
- **(b1) a policy:** `ops.spend_ledger` INSERT for a front-desk person of the clinic, restricted to `role = 'decision'`, `call_class = 'ordinary'`, own clinic, plus a SELECT path so the gateway can read today's total.
- **(b2) a SECURITY DEFINER function** `ops.record_session_spend(...)`: no table grant; the function checks the caller holds front desk in the clinic, forces `role = 'decision'` and `call_class = 'ordinary'`, caps `usd_actual` per row at a stated ceiling, takes the clinic from the caller's role, and a second function returns only the day's total. EXECUTE granted to `careflow_app`.
- **Grants.** One new path either way (a policy plus a select policy, or two EXECUTE grants); needs an rls-security-reviewer pass and the owner's sign-off before it is written.
- **What is stored.** One ledger row per call: clinic, role name, provider, model id, class, two amounts, origin, key. No patient data, no suggestion, no task. The suggestion itself stays computed on demand and shown, not stored (section 9 item 3 holds).
- **Failure modes.** (1) A front-desk user (or a bug in the API process) can over-record and starve its OWN clinic's ordinary calls for the day, or under-record by writing nothing; the database bounds who writes and the row's shape, not honesty (the same stated residual as 0016). (b2) lowers it with a per-row cap. (2) The write must be made in its own transaction on a second connection with the caller's claims, so it survives a request rollback (the pool cost above). (3) A crash between the model call and the write loses that call's spend; mitigate by writing an estimated-maximum reservation row BEFORE the call (a ledger cannot be corrected, so the reservation over-counts, which fails safe). (4) Clock/day: the database stamps the day, unchanged.
- **Tests.** Role matrix for the new path: front desk of the clinic can write the allowed shape; every other role, another clinic's front desk, another role name, class `safety`, an oversize amount, a day column, and a negative amount are refused; the total function returns only the caller's clinic; a rolled-back request still has its spend row; the ceiling stop is a visible hold for a person; the safety class is never budget-limited (the existing gateway tests).
- **What it changes in the signed-off design.** Reverses only the sentence "NO new insert grant on the spend ledger" in section 9 item 2 (which is itself the hard blocker's trigger). Sections 1a and 9 item 3 stay intact.

## Option (c): the in-memory ledger for the first cut (the current decision)

- **Grants / stored.** None. The `InMemorySpendLedger` per process.
- **Failure modes.** Lost on restart; N API processes allow N times the ceiling; no audit trail of spend.
- **Tests.** The existing gateway and budget tests; the Phase 3 consumer-side test (ceiling hit shows "no suggestion: needs a person").
- **What it changes.** Nothing. But under the signed-off hard blocker it permits **fakes only**: no real model call for reply interpretation, in any profile, until (a) or (b) is decided.

## Recommendation

1. **Stay on (c) now.** Nothing needs the real call yet: no provider is approved, and the draft/send mechanism is already built without it.
2. **When a provider is approved for synthetic demo use and a real reply interpretation is wanted, build (b2)** (a definer function with a per-row cap, a reservation row before the call, a total function), with a rls-security-reviewer pass and the owner's sign-off on the new EXECUTE grants first. It is the smallest change that makes the ledger durable and keeps the three other signed-off decisions.
3. **Do not choose (a).** It reopens three signed-off decisions and puts patient reply text in front of the worker, for no benefit the front-desk-driven design needs.
4. A reversal/adjustment mechanism for a wrongly recorded entry is a separate later change (stated residual of 0016) and should be decided alongside (b2).

## Decisions needed

1. Accept the recommendation (stay on (c); (b2) when a real call is wanted), or choose (a) or (b1).
2. If (b2): the per-row cap, the reservation amount, and who is allowed to read the day's total (front desk only through the function).
