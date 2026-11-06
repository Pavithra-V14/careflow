# Runbook: restore drill

BUILD-SPEC.md 14.6, 16; `careflow/drills.py`; pre-launch checklist items 28 and 40. **Status: the procedure is drafted; the drill has NOT been run.** It needs a real backup of a **dev or CI** project (synthetic data only) restored into a separate empty project. Never restore into, or from, a project holding real patient data until the privacy owner has signed that off.

**Before:** decide the owner-delete rule (checklist item 40). Several append-only ledgers are not delete-proof against the owner, and append-only triggers do **not** fire under `session_replication_role = replica`, which a restore or logical replication can use.

**The recorded checks, each true or false on the RESTORED database** (the names the checker expects):
- `row_counts_match`: the restored tables hold the counts the source held at backup time.
- `migration_ledger_checksums_match`: the migration ledger's checksums equal the repository's (no merged migration differs).
- `append_only_triggers_fire`: attempt an UPDATE and a DELETE on an append-only table as the owner and as the app role; both are refused. This catches a restore that left the triggers disabled.
- `forced_rls_on_every_clinic_table`: every clinic-scoped table has row-level security **forced** (the catalog check used in CI).
- `role_matrix_passes_on_restored_database`: the role-matrix suite passes against the restored project, as the restricted application role.
- `encryption_keys_decrypt_a_sample`: with the key store restored, a sample of synthetic patient ciphertext decrypts. (Keys are not in the database backup: see `key_rotation.md`.)

Write the record (the shape is in `tests/test_drills.py`). `careflow.drills.restore_evidence` yields the claim for the rubric item `restore_drilled`. A missing, failed, non-boolean or unknown check gives **no** claim.
