# Grants ledger

Every NEW database grant, policy widening or SECURITY DEFINER function added from 2026-11-05 on, in plain words, so the owner can review them later in one place. A line is added in the SAME commit as the migration that creates the grant. Tightenings (removals and narrowings) are listed too, marked as such. Format: migration, what, who gets it, why, how to undo.

| Migration | Kind | In plain words | Who | Why | Undo |
|---|---|---|---|---|---|
| 0031 | NEW TABLE + GRANT | A new table `sched.offer_deliveries`: a record that a person delivered an offer by hand. The application role gets SELECT and INSERT on it, nothing else; a trigger refuses UPDATE, DELETE and TRUNCATE for every role. | Front desk inserts (as themselves); front desk, nurse and doctor read | Phase 3 delivery with no external service | `drop table sched.offer_deliveries` in a new migration (nothing else depends on it) |
| (none yet) | | | | | |

Earlier tightenings already shipped for reference: 0029 (merge state machine, review rows immutable; no grant), 0030 (audit rows only for the caller's own clinic and a staff role; the queue accepts only clean new jobs; no grant).
