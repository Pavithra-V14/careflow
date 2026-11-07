-- 0042_canary_document_function: lets the queue worker register a CANARY referral document (Phase 4 follow-up, owner decision 2026-11-06 item 2; BUILD-SPEC.md 12.4; CLAUDE.md rules 5, 6, 8, 10).
--
-- A canary is a synthetic, clearly fabricated referral that goes through the Front Door like a real one and carries origin = 'canary', so a reviewer who approves it can be noticed (alert class
-- canary_approved, 0039). The documents table lets only front desk and nurse insert (0017); the worker must not gain a table-wide insert. This migration adds NO policy and NO table grant. It adds one
-- SECURITY DEFINER function:
--
--   docs.register_canary_document(p_clinic_id, p_document_id, p_idempotency_key) returns uuid
--
--   * the caller must be signed in and hold queue_worker in THE CLINIC NAMED (the demo worker account); otherwise the one fixed refusal;
--   * the document id must be the one the application derives from the clinic and key (the function cannot check the hash, so the key is also bound to the id: key = 'canary:<id>');
--   * the row is kind `referral`, origin `canary` (never any other), a fixed placeholder size/type/hash (there is no stored file: the text of a demo canary is made up by the demo text source), the
--     path the table requires, registered_by = the worker account;
--   * a replay with the same key returns the same id and writes nothing more; one audit.log row (key prefix `sys:`, the reserved prefix of 0035) names the caller, action `canary.injected`.
--
-- The function runs as its owner, so row-level security on docs.documents is bypassed BY DESIGN for this one insert, and every condition above is the function's own. Execute is granted to
-- careflow_app only. RESIDUAL: the worker can create canary documents at any rate through this function (the rate limit lives in the application: careflow/canary_injection.py); every one is audited and
-- carries origin canary, so it is excluded from live volume, cost and KPI figures.

create function docs.register_canary_document(p_clinic_id uuid, p_document_id uuid, p_idempotency_key text) returns uuid
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_id    uuid;
begin
    if v_actor is null or p_clinic_id is null or p_document_id is null or p_idempotency_key is distinct from 'canary:' || p_document_id::text
       or not iam.has_clinic_role(p_clinic_id, array['queue_worker']) then
        raise exception 'canary document refused' using errcode = 'insufficient_privilege';
    end if;
    select id into v_id from docs.documents where clinic_id = p_clinic_id and idempotency_key = p_idempotency_key and origin = 'canary';
    if found then
        return v_id;
    end if;
    insert into docs.documents (id, clinic_id, origin, kind, storage_path, content_type, size_bytes, sha256, registered_by, idempotency_key)
    values (p_document_id, p_clinic_id, 'canary', 'referral', 'clinic/' || p_clinic_id::text || '/doc/' || p_document_id::text, 'application/pdf', 1, repeat('0', 64), v_actor, p_idempotency_key)
    on conflict (clinic_id, idempotency_key) do nothing
    returning id into v_id;
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

do $$
begin
    if has_function_privilege('public', 'docs.register_canary_document(uuid, uuid, text)', 'execute') then
        raise exception '0042: the function must not be executable by PUBLIC';
    end if;
    if not has_function_privilege('careflow_app', 'docs.register_canary_document(uuid, uuid, text)', 'execute') then
        raise exception '0042: the application role must be able to execute the function';
    end if;
    if exists (select 1 from pg_roles r where r.rolname in ('anon', 'authenticated', 'service_role')
                and has_function_privilege(r.rolname, 'docs.register_canary_document(uuid, uuid, text)', 'execute')) then
        raise exception '0042: anon, authenticated and service_role must not execute the function';
    end if;
    if not (select rolsuper or rolbypassrls from pg_roles where rolname = current_user) then
        raise exception '0042: the migrating role must bypass row-level security (the function relies on it, as 0038 and 0040 do)';
    end if;
end $$;
