-- 0043_canary_document_daily_cap: a daily cap on canary documents per clinic (owner decision 2026-11-06 item 4; BUILD-SPEC.md 12.4; CLAUDE.md rules 5, 8, 10). Fix-forward for 0042, which is merged and untouched.
--
-- 0042 left a residual: the queue worker could create canary documents at any rate through docs.register_canary_document. This migration replaces that function with one that takes the cap as a fourth
-- argument and refuses to register a NEW canary document once the clinic already has that many registered today (UTC day).
--
--   * p_max_per_day is a REQUIRED value with no default: NULL or less than 1 is refused (unknown is not a value; an unset cap must never mean "no cap"). The application passes its required setting
--     CAREFLOW_CANARY_MAX_PER_DAY (a placeholder of 20 in the demo pack only; unset elsewhere, and then no canary is injected).
--   * a REPLAY (the same key for an existing canary document) still returns the same id and writes nothing, and is allowed even when the day's cap is used up, so a retried tick cannot be refused its own document.
--   * the count is of rows in docs.documents with origin 'canary' for the clinic created since the start of the current UTC day.
--   * everything else is as in 0042: the caller must be signed in and hold queue_worker in the named clinic; the key is bound to the document id; the row is origin canary with the fixed placeholder
--     facts; one audit row with a reserved `sys:` key.
--
-- The old three-argument function is DROPPED (it had no cap); the application role's only way to register a canary document is now the capped one. Privileges are as before: execute for careflow_app
-- only, revoked from PUBLIC, anon, authenticated and service_role.
--
-- The cap check is serialised per clinic by a transaction advisory lock, so concurrent ticks cannot overshoot it. The cap must be between 1 and 1000.
--
-- RESIDUAL: the cap is supplied by the caller, who is the trusted worker; a compromised worker could pass a large number. The cap therefore limits a buggy or runaway loop, not a malicious worker. Every
-- canary document is still audited and carries origin canary (excluded from live volume, cost and KPI figures).

drop function docs.register_canary_document(uuid, uuid, text);

create function docs.register_canary_document(p_clinic_id uuid, p_document_id uuid, p_idempotency_key text, p_max_per_day integer) returns uuid
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_id    uuid;
    v_today integer;
begin
    if v_actor is null or p_clinic_id is null or p_document_id is null or p_idempotency_key is distinct from 'canary:' || p_document_id::text
       or p_max_per_day is null or p_max_per_day < 1 or p_max_per_day > 1000
       or not iam.has_clinic_role(p_clinic_id, array['queue_worker']) then
        raise exception 'canary document refused' using errcode = 'insufficient_privilege';
    end if;
    perform pg_advisory_xact_lock(hashtextextended('canary-cap:' || p_clinic_id::text, 0));  -- serialise the count-then-insert per clinic, so concurrent ticks cannot overshoot the cap
    select id into v_id from docs.documents where clinic_id = p_clinic_id and idempotency_key = p_idempotency_key and origin = 'canary';
    if found then
        return v_id;
    end if;
    select count(*) into v_today from docs.documents
     where clinic_id = p_clinic_id and origin = 'canary' and created_at >= date_trunc('day', clock_timestamp() at time zone 'utc') at time zone 'utc';
    if v_today >= p_max_per_day then
        raise exception 'canary document refused' using errcode = 'insufficient_privilege';
    end if;
    begin
        insert into docs.documents (id, clinic_id, origin, kind, storage_path, content_type, size_bytes, sha256, registered_by, idempotency_key)
        values (p_document_id, p_clinic_id, 'canary', 'referral', 'clinic/' || p_clinic_id::text || '/doc/' || p_document_id::text, 'application/pdf', 1, repeat('0', 64), v_actor, p_idempotency_key)
        on conflict (clinic_id, idempotency_key) do nothing
        returning id into v_id;
    exception when unique_violation then
        raise exception 'canary document refused' using errcode = 'insufficient_privilege';  -- the id belongs to another row: the fixed refusal, not a raw error that reveals it
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

revoke all on function docs.register_canary_document(uuid, uuid, text, integer) from public;
grant execute on function docs.register_canary_document(uuid, uuid, text, integer) to careflow_app;

do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on function docs.register_canary_document(uuid, uuid, text, integer) from %I', r);
        end if;
    end loop;
end $$;

do $$
begin
    if exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname = 'docs' and p.proname = 'register_canary_document' and p.pronargs <> 4) then
        raise exception '0043: the uncapped three-argument function must be gone';
    end if;
    if has_function_privilege('public', 'docs.register_canary_document(uuid, uuid, text, integer)', 'execute') then
        raise exception '0043: the function must not be executable by PUBLIC';
    end if;
    if not has_function_privilege('careflow_app', 'docs.register_canary_document(uuid, uuid, text, integer)', 'execute') then
        raise exception '0043: the application role must be able to execute the function';
    end if;
    if exists (select 1 from pg_roles r where r.rolname in ('anon', 'authenticated', 'service_role')
                and has_function_privilege(r.rolname, 'docs.register_canary_document(uuid, uuid, text, integer)', 'execute')) then
        raise exception '0043: anon, authenticated and service_role must not execute the function';
    end if;
    if not (select rolsuper or rolbypassrls from pg_roles where rolname = current_user) then
        raise exception '0043: the migrating role must bypass row-level security (the function relies on it, as 0042 does)';
    end if;
end $$;
