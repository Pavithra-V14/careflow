# Runbook: dead-lettered jobs are growing

BUILD-SPEC.md 7.3 and 16.2; `careflow/dead_letters.py`; metric `dead_letter_count` (target 0, alert `dead_letter_growth`). **Status: drafted for the owner's review; nothing here is a clinical decision.**

**What it means.** A queued job (today: `front_door_run`, a referral letter being processed) failed five times, or had no handler, or hit a budget ceiling, and the queue gave up on it. The letter is NOT lost and NOT silently dropped: the sweep at the end of every tick raises a nurse review task of kind `referral_dead_letter` (ids and counts only), so a person looks at the referral. This runbook is for the engineer who sees the alert, not for the nurse.

**Who:** engineering on call. **Safe to repeat:** yes (the task key is `dead-letter:<job id>`; a second sweep creates nothing).

1. **Confirm the count and the age.** `select id, task_type, attempts, created_at from queue.jobs where dead_lettered_at is not null order by created_at desc` as the migrator (never print the payload column). Note how many are new since the last alert.
2. **Check the tasks exist.** Every dead-lettered job should have a nurse task `dead-letter:<id>`. A job without one means the worker is not ticking: go to step 4 first.
3. **Find the common cause from ids and error classes only** (never the payload or letter text): the worker log shows the class, e.g. a missing handler (deploy mismatch), `BudgetExceeded` (ceiling reached), a model or storage outage (see the model-outage runbook), or one bad document. Fix the cause; do not edit the job.
4. **Is the worker running?** `CAREFLOW_RUN_WORKER` and `CAREFLOW_WORKER_ACCOUNT_ID` must be set and the log must show ticks. A stopped worker stops the sweep too.
5. **Re-run a job only through the product path** (the nurse re-submits or the front desk re-registers the document), never by editing queue rows. A dead-lettered job is never un-dead-lettered by SQL.
6. **Close out.** The count returns to 0 only when a person has handled each task. Record the cause in the incident note (ids only).

**Never:** copy a job's error text or payload into a ticket, chat or log; delete queue rows; mark a nurse task done on their behalf.
