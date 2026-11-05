# Grants ledger

Every NEW database grant, policy widening or SECURITY DEFINER function added from 2026-11-05 on, in plain words, so the owner can review them later in one place. A line is added in the SAME commit as the migration that creates the grant. Tightenings (removals and narrowings) are listed too, marked as such. Format: migration, what, who gets it, why, how to undo.

| Migration | Kind | In plain words | Who | Why | Undo |
|---|---|---|---|---|---|
| 0031 | NEW TABLE + GRANT | A new table `sched.offer_deliveries`: a record that a person delivered an offer by hand. The application role gets SELECT and INSERT on it, nothing else; a trigger refuses UPDATE, DELETE and TRUNCATE for every role. | Front desk inserts (as themselves); front desk, nurse and doctor read | Phase 3 delivery with no external service | `drop table sched.offer_deliveries` in a new migration (nothing else depends on it) |
| 0032 | NEW FUNCTION GRANT + TIGHTENINGS | (a) NEW: the application role may EXECUTE `mpi.erase_patient(patient, reason, key)`, a definer function that erases a patient's identity fields and writes the audit row; it refuses anyone who is not front desk of that clinic. (b) NEW COLUMN `mpi.patients.lock_version` with UPDATE for the app role (a counter nothing reads, so row locks keep working). (c) REMOVED: the app role's UPDATE on a patient's encrypted name, date of birth, phone, their key versions and `erased_at`; all UPDATE on identity tokens. (d) NARROWED: UPDATE on match candidates to `status`, `resolved_at`, `resolved_by`. | The application role (the function checks front desk of the clinic inside) | Review finding M1 | Drop the function and restore the column grants in a new migration |
| (none yet) | | | | | |

Earlier tightenings already shipped for reference: 0029 (merge state machine, review rows immutable; no grant), 0030 (audit rows only for the caller's own clinic and a staff role; the queue accepts only clean new jobs; no grant).
