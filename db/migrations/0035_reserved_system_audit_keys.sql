-- 0035_reserved_system_audit_keys: audit keys beginning `sys:` belong to the database's own functions (reviewer finding 2026-11-05; fix-forward for 0030, CLAUDE.md rule 10).
--
-- THE HOLE. Since 0030 any front-desk, nurse, doctor or admin session may insert an audit.log row for its own clinic, and the key is globally unique. The definer functions of 0032 and 0034 write their own
-- audit rows; a caller who could PREDICT such a key could plant a row under it first, and the function's own insert would then fail or (with ON CONFLICT DO NOTHING) silently write no audit row at all.
--
-- THE FIX. The functions now use server-made keys with the reserved prefix `sys:` (some with a random uuid in them), and the application role's INSERT policy refuses any key with that prefix. The
-- application's own writers (careflow/audit.py, careflow/messages.py) use `audit:` and `message-read:` and are unaffected. Only the definer functions (running as the table owner, which bypasses row-level
-- security) can write a `sys:` key.
--
-- No new grant: this only tightens the policy 0030 created.

drop policy log_insert_own_clinic on audit.log;
create policy log_insert_own_clinic on audit.log for insert to careflow_app
    with check (
        actor_id is not null
        and actor_id = auth.uid()
        and clinic_id is not null
        and iam.has_clinic_role(clinic_id, array['front_desk', 'nurse', 'doctor', 'admin'])
        and idempotency_key not like 'sys:%'
    );

do $$
begin
    if (select count(*) from pg_policies where schemaname = 'audit' and tablename = 'log' and cmd = 'INSERT') <> 1 then
        raise exception '0035: audit.log must have exactly one INSERT policy';
    end if;
    if not exists (select 1 from pg_policies where schemaname = 'audit' and tablename = 'log' and policyname = 'log_insert_own_clinic' and with_check like '%sys:%') then
        raise exception '0035: the audit insert policy does not reserve the sys: prefix';
    end if;
end $$;
