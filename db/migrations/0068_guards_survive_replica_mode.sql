-- 0068_guards_survive_replica_mode (owner decision of 2026-11-08; BUILD-SPEC.md 16.5; CLAUDE.md rule 10). Fix forward.
--
-- A trigger created the usual way does not fire while a session runs with `session_replication_role = replica` (a restore, logical replication), so a restore could rewrite or empty an append-only ledger. The
-- guards of 0029, 0031 and 0032 were made ENABLE ALWAYS; the later ones were not. This migration switches EVERY remaining guard to ENABLE ALWAYS: all non-internal triggers in the application schemas that fire on
-- UPDATE, DELETE or TRUNCATE only (never on INSERT, which is where functional triggers do their work: expiry dates, defaults, review-task fan-out), except the two triggers that only stamp a time (`touch`, and the alert acknowledgement stamp). Guards
-- that fire only on those events cannot disturb a restore, which writes rows by INSERT/COPY; they only refuse a rewrite. The list is read from the catalog when the migration runs, and the closing assertion
-- fails the migration if any such guard is left enabled the ordinary way.

do $$
declare t record; n integer := 0;
begin
    for t in
        select tg.tgrelid::regclass as tbl, tg.tgname
          from pg_trigger tg join pg_class c on c.oid = tg.tgrelid join pg_namespace ns on ns.oid = c.relnamespace join pg_proc p on p.oid = tg.tgfoid
         where not tg.tgisinternal and tg.tgenabled = 'O'
           and ns.nspname in ('iam', 'mpi', 'sched', 'clinical', 'docs', 'msg', 'kb', 'agents', 'ai', 'ops', 'audit')
           and (tg.tgtype & 4) = 0                      -- no INSERT event
           and (tg.tgtype & (8 | 16 | 32)) <> 0         -- UPDATE, DELETE or TRUNCATE
           and p.proname not like '%touch%' and p.proname <> 'alerts_ack_once'  -- (it stamps a time: it must not rewrite a replicated acknowledgement)
    loop
        execute format('alter table %s enable always trigger %I', t.tbl, t.tgname);
        n := n + 1;
    end loop;
    raise notice '0068: % guard triggers switched to ENABLE ALWAYS', n;
    if exists (
        select 1 from pg_trigger tg join pg_class c on c.oid = tg.tgrelid join pg_namespace ns on ns.oid = c.relnamespace join pg_proc p on p.oid = tg.tgfoid
         where not tg.tgisinternal and tg.tgenabled = 'O' and ns.nspname in ('iam', 'mpi', 'sched', 'clinical', 'docs', 'msg', 'kb', 'agents', 'ai', 'ops', 'audit')
           and (tg.tgtype & 4) = 0 and (tg.tgtype & (8 | 16 | 32)) <> 0 and p.proname not like '%touch%' and p.proname <> 'alerts_ack_once') then
        raise exception '0068: a guard trigger is still enabled the ordinary way';
    end if;
end $$;
