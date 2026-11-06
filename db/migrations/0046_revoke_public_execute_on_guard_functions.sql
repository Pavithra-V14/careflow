-- 0046_revoke_public_execute_on_guard_functions: fix forward for 0044 and 0045 (CLAUDE.md rule 10; no merged migration is edited).
--
-- CI run 42223957943 (tests/test_rls_msg_live.py) found that the four trigger functions the two migrations created were left with PostgreSQL's default EXECUTE for PUBLIC, so anon (and the application role)
-- could execute them by name. They are trigger bodies only: EXECUTE is checked when a trigger is CREATED, not when it fires, so revoking it changes no behaviour of the tables. Every other trigger function
-- in these schemas already has it revoked; this brings the four into line. No table, policy or grant changes here.

revoke all on function ops.care_barriers_guard() from public;
revoke all on function msg.voice_preferences_touch() from public;
revoke all on function msg.voice_sessions_guard() from public;
revoke all on function msg.voice_chunks_guard() from public;

do $$
declare
    f text;
    r text;
begin
    foreach f in array array['ops.care_barriers_guard()', 'msg.voice_preferences_touch()', 'msg.voice_sessions_guard()', 'msg.voice_chunks_guard()'] loop
        foreach r in array array['anon', 'authenticated', 'service_role', 'careflow_app'] loop
            if exists (select 1 from pg_roles where rolname = r) then
                execute format('revoke all on function %s from %I', f, r);
                if has_function_privilege(r, f, 'execute') then
                    raise exception '0046: % must not be executable by %', f, r;
                end if;
            end if;
        end loop;
        if has_function_privilege('public', f, 'execute') then
            raise exception '0046: % must not be executable by PUBLIC', f;
        end if;
    end loop;
end $$;
