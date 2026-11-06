-- 0047_residual_fixes_aal2_and_voice_admin (owner decisions of 2026-11-06, items D, E, F; BUILD-SPEC.md 9.4, 18.1; CLAUDE.md rules 5, 8, 10). Fix forward: no merged migration is edited.
--
-- (E1) A barrier cannot be recorded for an ERASED patient: a security-definer BEFORE INSERT trigger on ops.care_barriers refuses (restrict_violation) when the patient of that clinic has erased_at set.
-- (E2) ops.care_review_samples pins the clinic on its decision: agents.review_decisions gets a unique (id, clinic_id) and the sample's foreign key becomes (decision_id, clinic_id). The old id-only key is dropped.
-- (E3) The canary cap moves INTO the database: docs.canary_settings (one row per clinic, owner-managed, NO default and no application grant) holds max_per_day; docs.register_canary_document loses its
--      fourth argument and refuses when the clinic has no row (a required setting). The application can no longer influence the cap (the 0043 residual "the cap is supplied by the worker" is closed).
--      The placeholder 20 is a LABELLED DEMO PLACEHOLDER seeded only by tests and the demo script, never by a migration.
-- (F)  iam.aal2_session(): true only when the caller's verified claims show aal2 AND a well-formed totp entry in amr that is not in the future (the same shape rule as careflow.auth.mfa_failure). Missing or
--      malformed anything is false (unknown is not a value). It is a plain boolean for policies and functions; THIS migration and the later ones use it on every NEW staff-facing table and function. Existing
--      tables are not retrofitted here (their direct-SQL tests set claims without aal): that retrofit is recorded as open in docs/gap-audit.md.
-- (D)  Admin-only, audited reading and purging of voice transcripts (9.4). Three security-definer functions, each: admin of the clinic AND iam.aal2_session(), fixed refusal otherwise (42501), an audit row in the
--      same call. Reading returns CIPHERTEXT (the application decrypts with the vault; the database never holds the key). The purge deletes only chunks past their expiry (the table guard already forbids any other
--      delete) and carries an idempotency key. The OWNER-DELETE RECOMMENDATION (no deletes from append-only ledgers except audited erasure and expiry purge) is the working rule, MARKED PENDING COUNSEL.

create function iam.aal2_session() returns boolean
    language sql stable set search_path = ''
as $$
    with c as (select nullif(current_setting('request.jwt.claims', true), '')::jsonb as j)
    select coalesce(
        (select c.j ->> 'aal' = 'aal2'
            and jsonb_typeof(c.j -> 'amr') = 'array'
            and exists (
                select 1 from jsonb_array_elements(c.j -> 'amr') e
                where jsonb_typeof(e) = 'object' and e ->> 'method' = 'totp'
                  and case when (e ->> 'timestamp') ~ '^[0-9]{1,12}$' then (e ->> 'timestamp')::bigint <= extract(epoch from clock_timestamp()) else false end
            )
         from c),
        false)
$$;
revoke all on function iam.aal2_session() from public;
grant execute on function iam.aal2_session() to careflow_app;
do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on function iam.aal2_session() from %I', r);
        end if;
    end loop;
end $$;

-- (E1)
create function ops.care_barriers_not_erased() returns trigger
    language plpgsql security definer set search_path = ''
as $$
begin
    if exists (select 1 from mpi.patients p where p.id = new.patient_id and p.clinic_id = new.clinic_id and p.erased_at is not null) then
        raise exception 'no barrier can be recorded for an erased patient' using errcode = 'restrict_violation';
    end if;
    return new;
end
$$;
revoke all on function ops.care_barriers_not_erased() from public;
do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on function ops.care_barriers_not_erased() from %I', r);
        end if;
    end loop;
end $$;
create trigger care_barriers_not_erased before insert on ops.care_barriers for each row execute function ops.care_barriers_not_erased();

-- (E2)
alter table agents.review_decisions add constraint review_decisions_id_clinic unique (id, clinic_id);
alter table ops.care_review_samples drop constraint care_review_samples_decision_id_fkey;
alter table ops.care_review_samples add constraint care_samples_decision_in_clinic foreign key (decision_id, clinic_id) references agents.review_decisions (id, clinic_id);

-- (E3)
create table docs.canary_settings (
    clinic_id    uuid primary key references iam.clinics (id),
    max_per_day  integer not null check (max_per_day between 1 and 1000),
    updated_at   timestamptz not null default clock_timestamp()
);
alter table docs.canary_settings enable row level security;
alter table docs.canary_settings force row level security;
revoke all on docs.canary_settings from public;
do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role', 'careflow_app'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on docs.canary_settings from %I', r);
        end if;
    end loop;
end $$;

drop function docs.register_canary_document(uuid, uuid, text, integer);

create function docs.register_canary_document(p_clinic_id uuid, p_document_id uuid, p_idempotency_key text) returns uuid
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_id    uuid;
    v_today integer;
    v_cap   integer;
begin
    if v_actor is null or p_clinic_id is null or p_document_id is null or p_idempotency_key is distinct from 'canary:' || p_document_id::text
       or not iam.has_clinic_role(p_clinic_id, array['queue_worker']) then
        raise exception 'canary document refused' using errcode = 'insufficient_privilege';
    end if;
    perform pg_advisory_xact_lock(hashtextextended('canary-cap:' || p_clinic_id::text, 0));
    select id into v_id from docs.documents where clinic_id = p_clinic_id and idempotency_key = p_idempotency_key and origin = 'canary';
    if found then
        return v_id;
    end if;
    select max_per_day into v_cap from docs.canary_settings where clinic_id = p_clinic_id;
    if v_cap is null then
        raise exception 'canary document refused' using errcode = 'insufficient_privilege';  -- no cap configured for the clinic: nothing is injected
    end if;
    select count(*) into v_today from docs.documents
     where clinic_id = p_clinic_id and origin = 'canary' and created_at >= date_trunc('day', clock_timestamp() at time zone 'utc') at time zone 'utc';
    if v_today >= v_cap then
        raise exception 'canary document refused' using errcode = 'insufficient_privilege';
    end if;
    begin
        insert into docs.documents (id, clinic_id, origin, kind, storage_path, content_type, size_bytes, sha256, registered_by, idempotency_key)
        values (p_document_id, p_clinic_id, 'canary', 'referral', 'clinic/' || p_clinic_id::text || '/doc/' || p_document_id::text, 'application/pdf', 1, repeat('0', 64), v_actor, p_idempotency_key)
        on conflict (clinic_id, idempotency_key) do nothing
        returning id into v_id;
    exception when unique_violation then
        raise exception 'canary document refused' using errcode = 'insufficient_privilege';
    end;
    if v_id is null then
        select id into v_id from docs.documents where clinic_id = p_clinic_id and idempotency_key = p_idempotency_key and origin = 'canary';
        if found then
            return v_id;
        end if;
        raise exception 'canary document refused' using errcode = 'insufficient_privilege';
    end if;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values ('canary', p_clinic_id, v_actor, 'queue_worker', 'canary.injected', 'document', v_id::text, jsonb_build_object('role_scope', iam.role_scope()), 'sys:canary-document:' || v_id::text);
    return v_id;
end
$$;
revoke all on function docs.register_canary_document(uuid, uuid, text) from public;
grant execute on function docs.register_canary_document(uuid, uuid, text) to careflow_app;
do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on function docs.register_canary_document(uuid, uuid, text) from %I', r);
        end if;
    end loop;
end $$;

-- (D)
create function msg.voice_sessions_for_admin(p_clinic_id uuid) returns table (session_id uuid, workflow text, status text, started_at timestamptz, retention_days integer, chunk_count bigint, expired_chunk_count bigint)
    language plpgsql security definer set search_path = ''
as $$
begin
    if p_clinic_id is null or not iam.aal2_session() or not iam.has_clinic_role(p_clinic_id, array['admin']) then
        raise exception 'voice transcript access refused' using errcode = 'insufficient_privilege';
    end if;
    return query
        select s.id, s.workflow, s.status, s.opened_at, s.transcript_retention_days,
               (select count(*) from msg.voice_transcript_chunks c where c.session_id = s.id and c.clinic_id = s.clinic_id),
               (select count(*) from msg.voice_transcript_chunks c where c.session_id = s.id and c.clinic_id = s.clinic_id and c.expires_at <= clock_timestamp())
          from msg.voice_sessions s where s.clinic_id = p_clinic_id order by s.opened_at desc limit 200;
end
$$;

create function msg.read_voice_transcript(p_clinic_id uuid, p_session_id uuid) returns table (seq integer, body_ct bytea, body_key_version integer, created_at timestamptz)
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor  uuid := auth.uid();
    v_origin text;
begin
    if p_clinic_id is null or p_session_id is null or v_actor is null or not iam.aal2_session() or not iam.has_clinic_role(p_clinic_id, array['admin']) then
        raise exception 'voice transcript access refused' using errcode = 'insufficient_privilege';
    end if;
    select s.origin into v_origin from msg.voice_sessions s where s.id = p_session_id and s.clinic_id = p_clinic_id;
    if v_origin is null then
        raise exception 'voice transcript access refused' using errcode = 'insufficient_privilege';
    end if;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_origin, p_clinic_id, v_actor, 'admin', 'voice.transcript_read', 'voice_session', p_session_id::text, '{}'::jsonb, 'sys:voice-read:' || gen_random_uuid()::text);  -- a read is not a replayable write: each read is its own audit row
    return query select c.seq, c.body_ct, c.body_key_version, c.created_at from msg.voice_transcript_chunks c where c.session_id = p_session_id and c.clinic_id = p_clinic_id and c.expires_at > clock_timestamp() order by c.seq;
end
$$;

create function msg.purge_expired_voice_chunks(p_clinic_id uuid, p_idempotency_key text, p_limit integer) returns integer
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor  uuid := auth.uid();
    v_n      integer;
    v_origin text;
begin
    if p_clinic_id is null or v_actor is null or p_idempotency_key is null or length(p_idempotency_key) not between 1 and 200 or p_limit is null or p_limit not between 1 and 1000
       or not iam.aal2_session() or not iam.has_clinic_role(p_clinic_id, array['admin']) then
        raise exception 'voice transcript purge refused' using errcode = 'insufficient_privilege';
    end if;
    select origin into v_origin from iam.clinics where id = p_clinic_id;
    if exists (select 1 from audit.log where idempotency_key = 'sys:voice-purge:' || p_clinic_id::text || ':' || p_idempotency_key) then
        return 0;  -- a replay of the same purge request does nothing more
    end if;
    with gone as (
        delete from msg.voice_transcript_chunks where id in (
            select c.id from msg.voice_transcript_chunks c where c.clinic_id = p_clinic_id and c.expires_at <= clock_timestamp() order by c.expires_at limit p_limit
        ) returning 1
    ) select count(*) into v_n from gone;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_origin, p_clinic_id, v_actor, 'admin', 'voice.transcript_purge', 'voice_transcript_chunks', null, jsonb_build_object('deleted', v_n), 'sys:voice-purge:' || p_clinic_id::text || ':' || p_idempotency_key);
    return v_n;
end
$$;

do $$
declare
    f text;
    r text;
begin
    foreach f in array array['msg.voice_sessions_for_admin(uuid)', 'msg.read_voice_transcript(uuid,uuid)', 'msg.purge_expired_voice_chunks(uuid,text,integer)'] loop
        execute format('revoke all on function %s from public', f);
        execute format('grant execute on function %s to careflow_app', f);
        foreach r in array array['anon', 'authenticated', 'service_role'] loop
            if exists (select 1 from pg_roles where rolname = r) then
                execute format('revoke all on function %s from %I', f, r);
            end if;
        end loop;
    end loop;
    foreach f in array array['ops.care_barriers_not_erased()', 'iam.aal2_session()', 'docs.register_canary_document(uuid,uuid,text)', 'msg.voice_sessions_for_admin(uuid)', 'msg.read_voice_transcript(uuid,uuid)', 'msg.purge_expired_voice_chunks(uuid,text,integer)'] loop
        if has_function_privilege('public', f, 'execute') then
            raise exception '0047: % must not be executable by PUBLIC', f;
        end if;
        foreach r in array array['anon', 'authenticated', 'service_role'] loop
            if exists (select 1 from pg_roles where rolname = r) and has_function_privilege(r, f, 'execute') then
                raise exception '0047: % must not be executable by %', f, r;
            end if;
        end loop;
    end loop;
    if has_function_privilege('careflow_app', 'ops.care_barriers_not_erased()', 'execute') then
        raise exception '0047: the barrier trigger function must not be executable by the application role';
    end if;
    if has_table_privilege('careflow_app', 'docs.canary_settings', 'select') or has_table_privilege('careflow_app', 'docs.canary_settings', 'insert') then
        raise exception '0047: the application role must have no access to docs.canary_settings';
    end if;
end $$;
