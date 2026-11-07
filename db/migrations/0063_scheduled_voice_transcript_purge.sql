-- 0063_scheduled_voice_transcript_purge (owner request 2026-11-08; BUILD-SPEC.md 9.4, 23; CLAUDE.md rules 5, 8, 10).
--
-- Expired voice transcript chunks (each carries the expiry the database computed from its session's retention setting) are deleted by an ADMIN through msg.purge_expired_voice_chunks (0047: admin, second
-- factor, audited). Nothing ran it on a schedule. The worker tick now does, through THIS function: the same delete rule (only chunks past their expiry; the chunk guard of 0045 and 0057 allows nothing
-- else), the same audit action (`voice.transcript_purge`), callable ONLY by the queue worker of the clinic. It is not an admin act, so no second factor is asked (the worker has no session); the audit
-- row names the worker account and says `schedule`. A run that deletes nothing writes nothing (no audit row per tick). The retention PERIOD stays the required setting on the session (the expiry is
-- fixed when the chunk is written) and the sweep only runs while CAREFLOW_VOICE_TRANSCRIPT_RETENTION_DAYS is set.

create index if not exists voice_chunks_expiry_idx on msg.voice_transcript_chunks (clinic_id, expires_at);

create function msg.purge_expired_voice_chunks_as_worker(p_clinic_id uuid, p_limit integer) returns integer
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor  uuid := auth.uid();
    v_n      integer;
    v_origin text;
begin
    if p_clinic_id is null or v_actor is null or p_limit is null or p_limit not between 1 and 1000 or not iam.has_clinic_role(p_clinic_id, array['queue_worker']) then
        raise exception 'voice transcript purge refused' using errcode = 'insufficient_privilege';
    end if;
    select c.origin into v_origin from iam.clinics c where c.id = p_clinic_id;
    with gone as (
        delete from msg.voice_transcript_chunks where id in (
            select ch.id from msg.voice_transcript_chunks ch where ch.clinic_id = p_clinic_id and ch.expires_at <= clock_timestamp() order by ch.expires_at limit p_limit
        ) returning 1
    ) select count(*) into v_n from gone;
    if v_n > 0 then
        insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
        values (v_origin, p_clinic_id, v_actor, 'queue_worker', 'voice.transcript_purge', 'voice_transcript_chunks', null, jsonb_build_object('deleted', v_n, 'by', 'schedule'), 'sys:voice-purge-sweep:' || gen_random_uuid()::text);
    end if;
    return v_n;
end
$$;

revoke all on function msg.purge_expired_voice_chunks_as_worker(uuid, integer) from public;
do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on function msg.purge_expired_voice_chunks_as_worker(uuid, integer) from %I', r);
        end if;
    end loop;
end $$;
grant execute on function msg.purge_expired_voice_chunks_as_worker(uuid, integer) to careflow_app;

do $$
declare r text; f text := 'msg.purge_expired_voice_chunks_as_worker(uuid,integer)';
begin
    if not (select rolsuper or rolbypassrls from pg_roles where rolname = current_user) then
        raise exception '0063: the migrating role must bypass row-level security';
    end if;
    if has_function_privilege('public', f, 'execute') or not has_function_privilege('careflow_app', f, 'execute') then
        raise exception '0063: % must be executable by the application login and not by PUBLIC', f;
    end if;
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) and has_function_privilege(r, f, 'execute') then
            raise exception '0063: % must not be executable by %', f, r;
        end if;
    end loop;
    if has_table_privilege('careflow_app', 'msg.voice_transcript_chunks', 'delete') then
        raise exception '0063: the application role must not be able to delete chunks directly';
    end if;
end $$;
