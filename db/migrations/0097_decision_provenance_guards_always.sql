-- 0097_decision_provenance_guards_always: a fix forward for 0095 (found by the CI test tests/test_guards_replica_mode_db_live.py, 2026-11-19).
--
-- 0095 created the append-only guard triggers of agents.decision_provenance the ordinary way. The project's rule (0068) is that every guard that fires on UPDATE, DELETE or TRUNCATE is ENABLE ALWAYS, so a session in
-- replica mode (a restore, logical replication) cannot slip past it. This makes the two triggers of 0095 ENABLE ALWAYS. Nothing else changes.

alter table agents.decision_provenance enable always trigger decision_provenance_no_update_delete;
alter table agents.decision_provenance enable always trigger decision_provenance_no_truncate;

do $$
begin
    if exists (
        select 1 from pg_trigger t
         where t.tgrelid = 'agents.decision_provenance'::regclass and not t.tgisinternal and t.tgenabled <> 'A'
    ) then
        raise exception '0097: every guard trigger on agents.decision_provenance must be ENABLE ALWAYS';
    end if;
end $$;
