-- 0075_session_spend_ledger (owner instruction 2026-11-13; docs/design/spend-ledger-options.md option (b2); BUILD-SPEC.md 6.1, 15.2; CLAUDE.md rules 5, 8, 10, 13; pre-launch item 13).
--
-- A DURABLE spend ledger for a model call made in the API process under a person's own session (reply interpretation), without a worker identity and without any table grant on ops.spend_ledger:
--   * ops.spend_limits: an OWNER-MANAGED table (the application role has no grant and no policy: it can neither read nor write it) holding, per clinic, the most one call may be recorded for (`max_usd_per_call`).
--     No row = the clinic's session calls cannot be recorded (unknown is not a value). Filled by the owner, like iam.break_glass_settings (0066). The cap is the OWNER'S number: no default is invented here.
--   * ops.record_session_spend(clinic, origin, provider, model, usd_actual, usd_shadow, key): SECURITY DEFINER. A front-desk person, nurse or doctor of the clinic with a verified second factor. The function FORCES
--     role `decision` and class `ordinary` (a safety call is never budget-limited and never recorded here), refuses an amount above the clinic's cap, refuses an origin that is not the clinic's, prefixes the key with
--     the caller's id (no key squatting across people) and inserts into the append-only ledger as its owner. A replay of the same key returns false and writes nothing.
--   * ops.session_spend_today(clinic, origin): the same callers; returns the day's total (India time, every class counted, like the worker's read) for that clinic and origin, and nothing else.
-- RESIDUAL (stated, as in 0016): the database bounds WHO writes and the SHAPE and size of a row, not the honesty of the amount; a person using raw SQL could under-record. Spend can only be added (append-only), and an over-cap amount is refused.
-- NEW GRANTS (ledger): EXECUTE on the two functions to careflow_app. No table grant.

create table ops.spend_limits (
    clinic_id       uuid primary key references iam.clinics (id),
    max_usd_per_call numeric(14, 6) not null check (max_usd_per_call > 0 and max_usd_per_call <= 1000),
    set_at          timestamptz not null default clock_timestamp()
);
alter table ops.spend_limits enable row level security;
alter table ops.spend_limits force row level security;
revoke all on ops.spend_limits from public;
do $$
declare r text;
begin
    foreach r in array array['careflow_app', 'anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on ops.spend_limits from %I', r);
        end if;
    end loop;
end $$;
comment on table ops.spend_limits is 'Owner-managed: the most one session model call may be recorded for, per clinic. No grant to any application role. No row = session calls are refused.';

create function ops.record_session_spend(
    p_clinic_id uuid, p_origin text, p_provider text, p_model_id text, p_usd_actual numeric, p_usd_shadow numeric, p_idempotency_key text
) returns boolean
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_cap   numeric;
    v_rows  integer;
begin
    if v_actor is null or p_clinic_id is null or p_origin is null or p_provider is null or p_model_id is null or p_usd_actual is null or p_usd_shadow is null or p_idempotency_key is null
       or not iam.aal2_session()
       or length(btrim(p_provider)) = 0 or length(p_provider) > 100 or length(btrim(p_model_id)) = 0 or length(p_model_id) > 100
       or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 200
       or p_usd_actual < 0 or p_usd_shadow < 0
       or not iam.has_clinic_role(p_clinic_id, array['front_desk', 'nurse', 'doctor'])
       or p_origin is distinct from ops.clinic_origin(p_clinic_id) then
        raise exception 'session spend refused' using errcode = 'insufficient_privilege';
    end if;
    select l.max_usd_per_call into v_cap from ops.spend_limits l where l.clinic_id = p_clinic_id;
    if v_cap is null or p_usd_actual > v_cap or p_usd_shadow > v_cap then
        raise exception 'session spend refused' using errcode = 'insufficient_privilege';  -- no limit set, or above it: the same generic refusal
    end if;
    insert into ops.spend_ledger (clinic_id, role, provider, model_id, call_class, usd_actual, usd_shadow, origin, idempotency_key)
    values (p_clinic_id, 'decision', p_provider, p_model_id, 'ordinary', p_usd_actual, p_usd_shadow, p_origin, 'session:' || v_actor::text || ':' || p_idempotency_key)
    on conflict (clinic_id, idempotency_key) do nothing;
    get diagnostics v_rows = row_count;
    return v_rows = 1;
end
$$;

create function ops.session_spend_today(p_clinic_id uuid, p_origin text) returns numeric
    language plpgsql stable security definer set search_path = ''
as $$
begin
    if auth.uid() is null or p_clinic_id is null or p_origin is null or not iam.aal2_session()
       or not iam.has_clinic_role(p_clinic_id, array['front_desk', 'nurse', 'doctor'])
       or p_origin is distinct from ops.clinic_origin(p_clinic_id) then
        raise exception 'session spend read refused' using errcode = 'insufficient_privilege';
    end if;
    return coalesce((select sum(s.usd_actual) from ops.spend_ledger s
                      where s.clinic_id = p_clinic_id and s.origin = p_origin and s.day = (clock_timestamp() at time zone 'Asia/Kolkata')::date), 0);
end
$$;

revoke all on function ops.record_session_spend(uuid, text, text, text, numeric, numeric, text) from public;
revoke all on function ops.session_spend_today(uuid, text) from public;
grant execute on function ops.record_session_spend(uuid, text, text, text, numeric, numeric, text) to careflow_app;
grant execute on function ops.session_spend_today(uuid, text) to careflow_app;

do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r)
           and (has_function_privilege(r, 'ops.record_session_spend(uuid,text,text,text,numeric,numeric,text)', 'execute') or has_function_privilege(r, 'ops.session_spend_today(uuid,text)', 'execute')) then
            raise exception '0075: the session spend functions must not be executable by %', r;
        end if;
    end loop;
    if has_function_privilege('public', 'ops.record_session_spend(uuid,text,text,text,numeric,numeric,text)', 'execute') or has_function_privilege('public', 'ops.session_spend_today(uuid,text)', 'execute') then
        raise exception '0075: the session spend functions must not be executable by PUBLIC';
    end if;
    if has_table_privilege('careflow_app', 'ops.spend_limits', 'select') or has_any_column_privilege('careflow_app', 'ops.spend_limits', 'select')
       or has_table_privilege('careflow_app', 'ops.spend_limits', 'insert') or has_table_privilege('careflow_app', 'ops.spend_limits', 'update') then
        raise exception '0075: the application role must have no access to ops.spend_limits';
    end if;
end $$;
