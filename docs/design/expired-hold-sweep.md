# Expired-hold sweep (pre-approved 2026-11-05, built as migration 0033)

**Why it is only a tidy-up.** Every operation that touches a slot already treats an active hold past its time as expired and frees the slot, so a late or missing tick never makes a slot unbookable. What was never cleaned up are the rows nobody touches: the hold stays `active` and the slot stays `held` in lists.

**What changed.** One SECURITY DEFINER function, `sched.release_expired_holds()`, executable by the application login and refusing (one generic error) anyone who is not a live `queue_worker` of at least one clinic. It expires active holds past their time (database clock) and reopens slots left `held` with no live hold, ONLY in clinics where the caller is a queue worker. It writes no audit row (a system tidy-up, not a decision) and touches no patient data. The worker's tick runs it as an end-of-tick sweep (`careflow.scheduling.release_expired_holds_sweep`), before the dead-letter sweep. A failing sweep is logged and the tick goes on (existing behaviour).

**Why a function and not a table grant.** The worker's database identity deliberately cannot update `sched.holds` or `sched.slots`; a function that can only do this one thing is smaller than two table permissions. **Referral intake stays manual** (no outbox producer was added, as decided): nothing starts a Front Door run on its own.

Tests: four live tests in `tests/test_dead_letters_live.py` (own clinic only, a live hold is untouched, only a queue worker may run it, no identity refused).
