-- 0086_spend_ledger_translation_role (owner decision 2026-11-14; BUILD-SPEC.md 6.1, 15.2, 5.1; CLAUDE.md rules 5, 8, 10). Fix forward: no merged migration is edited.
--
-- ops.spend_ledger.role allowed the model roles of the gateway but not `translation`, so the Sarvam translation route could only be counted by the in-memory per-role ceiling. This adds the one value
-- `translation` to that CHECK so its spend can land in the durable ledger (and count toward the clinic's daily total) like every other role's. Nothing else changes: same columns, same append-only
-- guards (they fire on rows, not on this DDL), same forced RLS, same column grants (INSERT stays with the queue worker of the clinic only), same idempotency key per clinic. The new value carries no clinical
-- meaning and no new privilege. Existing rows all satisfy the wider check, so the re-validation cannot fail.
--
-- NEW GRANTS (ledger): none.

alter table ops.spend_ledger drop constraint spend_ledger_role_check;
alter table ops.spend_ledger add constraint spend_ledger_role_check
    check (role in ('llm_large', 'llm_planner', 'llm_fast', 'vlm', 'embed', 'rerank', 'llm_judge', 'decision', 'voice_live', 'translation'));
