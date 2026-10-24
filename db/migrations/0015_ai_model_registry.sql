-- 0015_ai_model_registry: the model registry (BUILD-SPEC.md §2, §5.1, §6.1, §17.1 `ai`; CLAUDE.md rules 5, 7, 8, 13).
--
-- One row per (role, provider, pinned model id): which engine may answer which role, with what data policy, at what priority, and what it
-- costs. This is PLATFORM configuration (it has no clinic_id, like kb.red_flag_rules) and it is a privacy control: `data_policy` is the
-- mechanism that stops a hosted engine from quietly seeing real patient text (§2), so it is treated as a decision somebody owns.
--
-- * Roles are the nine of §5.1. `model_id` must be PINNED (a trailing latest / default / stable / current is refused, §5.1), carry no
--   leading or trailing space and no control character (so the database and careflow/gateway.py agree on every id).
-- * `data_policy` is synthetic_only or phi_approved. A phi_approved row must name WHO approved it and WHEN (approved_by, approved_at),
--   the same rule the data-flow register applies to permitted_in_clinical: there is no self-approval by omission.
-- * Prices are per 1,000 units: the ACTUAL price (0 for a free tier) and the SHADOW price (what the equivalent paid model would cost), so
--   cost per outcome can be computed for free rows too (§5.1). `voice_live` is priced per 1,000 AUDIO tokens, every other role per 1,000
--   text tokens: the unit is a column, and a CHECK ties audio to voice_live and nothing else.
-- * `priority` orders the fallback chain within a role; among ENABLED rows of one role it is unique, so the order is never a tie.
-- * New rows are DISABLED by default: nothing is routable until somebody turns it on.
-- * No row is seeded. The registry starts empty, and so does every route: a hosted service is added only after the owner approves it
--   (rule 13), with its data-flow-register entry.
--
-- WHO MAY DO WHAT. careflow_app has SELECT only, and only for an active admin or queue_worker of any clinic (the console reads it; the
-- runner builds the gateway from it). It cannot INSERT, UPDATE or DELETE: changing a platform-wide data policy is an operator action
-- through the migrator, not something any one clinic's admin can do (there is no platform-admin role yet). Whatever role the writer has, a
-- trigger makes the identity of a row immutable (role, provider, model id, data policy, unit and the approval): to change a policy, add a
-- new row and disable the old one. Rows are never deleted and the table is never truncated, for anyone, the migrator included
-- (a disabled row is the history; same device as audit.log, 0001).
--
-- RESIDUAL RISK, stated: the migrator / owner can disable the trigger or set session_replication_role, as for audit.log (BUILD-SPEC.md
-- 18.1). The registry binds the application role and every ordinary session; the migrator is trusted.

create table ai.model_registry (
    id               uuid primary key default gen_random_uuid(),
    role             text not null check (role in ('llm_large', 'llm_planner', 'llm_fast', 'vlm', 'embed', 'rerank', 'llm_judge',
                                                    'decision', 'voice_live')),
    provider         text not null check (length(btrim(provider)) > 0),
    model_id         text not null check (length(btrim(model_id)) > 0 and btrim(model_id) = model_id and model_id !~ '[[:cntrl:]]'
                                         and model_id !~* '(^|[-_:/@])(latest|default|stable|current)$'),
    data_policy      text not null check (data_policy in ('synthetic_only', 'phi_approved')),
    priority         integer not null check (priority >= 1),
    enabled          boolean not null default false,
    unit             text not null check (unit in ('text_tokens', 'audio_tokens')),
    price_in_per_1k  numeric(14, 6) not null check (price_in_per_1k >= 0),
    price_out_per_1k numeric(14, 6) not null check (price_out_per_1k >= 0),
    shadow_in_per_1k numeric(14, 6) not null check (shadow_in_per_1k >= 0),
    shadow_out_per_1k numeric(14, 6) not null check (shadow_out_per_1k >= 0),
    timeout_seconds  numeric(8, 2) not null default 20 check (timeout_seconds > 0),
    approved_by      uuid references iam.accounts (id),
    approved_at      timestamptz,
    -- Origin tag (§0): no default, so a test row can never be silently tagged `live`.
    origin           text not null check (origin in ('live', 'test', 'eval', 'canary')),
    created_at       timestamptz not null default clock_timestamp(),
    -- Rule 5: every write carries a client-supplied key, unique across the table.
    idempotency_key  text not null unique check (length(idempotency_key) > 0),
    constraint model_registry_unit_matches_role check ((unit = 'audio_tokens') = (role = 'voice_live')),
    constraint model_registry_phi_needs_an_approver check (data_policy <> 'phi_approved' or (approved_by is not null and approved_at is not null)),
    constraint model_registry_approval_is_whole check ((approved_by is null) = (approved_at is null)),
    constraint model_registry_one_row_per_engine unique (role, provider, model_id)
);

-- Among enabled rows of one role the priority is unique: the fallback order is never a tie.
create unique index model_registry_one_priority_per_role on ai.model_registry (role, priority) where enabled;

-- ---- identity is immutable; nothing is deleted ---------------------------------------------------------------------------------------

create function ai.model_registry_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    if tg_op = 'UPDATE' then
        if (new.id, new.role, new.provider, new.model_id, new.data_policy, new.unit, new.approved_by, new.approved_at,
            new.origin, new.created_at, new.idempotency_key)
           is distinct from
           (old.id, old.role, old.provider, old.model_id, old.data_policy, old.unit, old.approved_by, old.approved_at,
            old.origin, old.created_at, old.idempotency_key) then
            raise exception 'a registry row''s identity and data policy are immutable: add a new row and disable this one'
                using errcode = 'restrict_violation';
        end if;
        return new;
    end if;
    raise exception 'ai.model_registry is append-only: % is not permitted', tg_op using errcode = 'restrict_violation';
end
$$;

create trigger model_registry_no_identity_change before update or delete on ai.model_registry
    for each row execute function ai.model_registry_guard();
create trigger model_registry_no_truncate before truncate on ai.model_registry
    for each statement execute function ai.model_registry_guard();

-- ---- row-level security ----------------------------------------------------------------------------------------------------------------

alter table ai.model_registry enable row level security;
alter table ai.model_registry force row level security;

-- Read-only for the people and the process that need to see it. No INSERT / UPDATE / DELETE policy exists: those are migrator actions.
create policy model_registry_select on ai.model_registry for select to careflow_app
    using (iam.has_any_role(array['admin', 'queue_worker']));

revoke all on ai.model_registry from public;
grant usage on schema ai to careflow_app;
grant select on ai.model_registry to careflow_app;

do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on ai.model_registry from %I', r);
            execute format('revoke all on function ai.model_registry_guard() from %I', r);
            execute format('revoke all on schema ai from %I', r);
        end if;
    end loop;
end $$;

revoke all on function ai.model_registry_guard() from public;

comment on table ai.model_registry is
    'The model registry (BUILD-SPEC 5.1). Platform configuration, read-only to the application. data_policy is a privacy control: phi_approved needs a named approver. Rows are never deleted; a disabled row is the history.';
