-- 0007_red_flag_rules_schema: the STRUCTURE for deterministic red-flag rules and the nurse task they raise
-- (BUILD-SPEC.md §5.2, §11.6, §12.1, §20.2; CLAUDE.md rules 1, 2, 5, 8, 11).
--
-- # CLINICAL-PLACEHOLDER: needs clinician sign-off
--
-- This migration contains NO clinical content: no red-flag phrase, no pattern, no vitals boundary. It creates
-- the tables a clinician-authored rule set will live in, and ONE inert stub row. Every rule is created
-- `draft_unreviewed` and is unusable in the `clinical` profile until a named clinical lead approves it under
-- the two-person rule (§20.2); the database refuses `approved` without two distinct named approvers.
--
-- * kb.red_flag_rules: versioned (a new version is a new row; rows are never edited or deleted). `clinic_id`
--   NULL means a platform-wide rule that applies to every clinic; otherwise the rule is that clinic's own.
-- * agents.review_tasks: the human task a match raises. A red-flag task is always `immediate` and always
--   assigned to a nurse, enforced by CHECK, so no code path can create a quiet one (§12.1).
-- * careflow_app: SELECT only on kb.red_flag_rules. Authoring and publishing are a later feature behind the
--   two-person rule; until it exists, rules are loaded by the migrator, not by the application.
--   On agents.review_tasks the app may insert, read and (nurse/doctor) acknowledge or resolve; never delete.

-- ---- helper: "does the caller hold any of these roles in ANY clinic?" (for platform-wide rows) --------

create function iam.has_any_role(p_roles text[]) returns boolean
    language sql stable security definer set search_path = ''
as $$
    select exists (
        select 1
        from iam.staff_roles sr
        join iam.accounts a on a.id = sr.account_id
        where sr.account_id = auth.uid()
          and a.status = 'active'
          and a.account_type = 'staff'
          and sr.revoked_at is null
          and sr.role = any (p_roles)
    )
$$;
revoke all on function iam.has_any_role(text[]) from public;
grant execute on function iam.has_any_role(text[]) to careflow_app;

-- ---- kb.red_flag_rules --------------------------------------------------------------------------

create table kb.red_flag_rules (
    id                  uuid primary key default gen_random_uuid(),
    clinic_id           uuid references iam.clinics (id),   -- NULL = applies to every clinic
    rule_code           text not null check (rule_code ~ '^[a-z][a-z0-9_]*$'),
    version_id          integer not null check (version_id >= 1),
    -- Draft by default: nothing is usable in the clinical profile until it is approved.
    status              text not null default 'draft_unreviewed' check (status in ('draft_unreviewed', 'approved', 'retired')),

    -- Match criteria. All three are CLINICIAN-AUTHORED; an empty rule matches nothing (inert).
    --   phrases: words or phrases, matched after case-folding and whitespace collapsing, on token boundaries
    --   patterns: regular expressions, matched against the same normalised text
    --   vitals_criteria: structured range boundaries for a separate vitals evaluator (not read by the text scanner)
    phrases             text[] not null default '{}',
    patterns            text[] not null default '{}',
    vitals_criteria     jsonb not null default '{}'::jsonb check (jsonb_typeof(vitals_criteria) = 'object'),

    governance_metadata jsonb not null check (jsonb_typeof(governance_metadata) = 'object'),
    authored_by         uuid references iam.accounts (id),
    approved_by_1       uuid references iam.accounts (id),
    approved_by_2       uuid references iam.accounts (id),
    approved_at         timestamptz,
    retired_at          timestamptz,

    -- Origin tag (§0): no default, so a test rule can never be silently tagged `live`.
    origin              text not null check (origin in ('live', 'test', 'eval', 'canary')),
    created_at          timestamptz not null default clock_timestamp(),
    idempotency_key     text not null unique check (length(idempotency_key) > 0),

    -- The two-person rule, in the database (§12.5, §20.2): approved needs two DIFFERENT named approvers.
    check (status <> 'approved' or (approved_by_1 is not null and approved_by_2 is not null
                                    and approved_by_1 <> approved_by_2 and approved_at is not null)),
    -- An unreviewed rule must say so, visibly, in its own metadata (CLAUDE.md rule 1).
    -- coalesce: a missing key makes the comparison NULL, and a CHECK treats NULL as satisfied.
    check (status <> 'draft_unreviewed'
           or coalesce(governance_metadata ->> 'placeholder' = '# CLINICAL-PLACEHOLDER: needs clinician sign-off', false)),
    check (status <> 'retired' or retired_at is not null),
    check (cardinality(phrases) <= 500 and cardinality(patterns) <= 500)
);

-- One row per (clinic, rule, version); NULL clinic is one shared namespace.
create unique index red_flag_rules_version_key
    on kb.red_flag_rules (coalesce(clinic_id, '00000000-0000-0000-0000-000000000000'::uuid), rule_code, version_id, origin);
create index red_flag_rules_lookup_idx on kb.red_flag_rules (clinic_id, origin, status);

-- The foundational stub: inert (no criteria), draft, carrying the mandatory placeholder. It exists so the
-- table is never empty-by-accident and so the placeholder discipline is visible in the data itself.
insert into kb.red_flag_rules (clinic_id, rule_code, version_id, status, governance_metadata, origin, idempotency_key)
values (
    null, 'unauthored_stub', 1, 'draft_unreviewed',
    jsonb_build_object(
        'placeholder', '# CLINICAL-PLACEHOLDER: needs clinician sign-off',
        'note', 'Inert stub: no phrases, patterns or vitals boundaries. A named clinical lead must author and approve real rules (two-person rule, BUILD-SPEC.md 12.5 and 20.2).',
        'authored_by', 'nobody: no clinical content has been supplied'
    ),
    'test', 'stub-0007-unauthored-red-flag-rule'
);

alter table kb.red_flag_rules enable row level security;
alter table kb.red_flag_rules force row level security;

-- Read: the clinic's own staff and workers (the engine runs as one of these), plus platform-wide rows for any
-- active staff account. Patients, auditors and business owners read nothing here.
create policy red_flag_rules_select on kb.red_flag_rules for select to careflow_app
    using (
        case when clinic_id is null
             then iam.has_any_role(array['front_desk', 'nurse', 'doctor', 'admin', 'queue_worker'])
             else iam.has_clinic_role(clinic_id, array['front_desk', 'nurse', 'doctor', 'admin', 'queue_worker'])
        end
    );

revoke all on kb.red_flag_rules from public;
grant usage on schema kb to careflow_app;
grant select on kb.red_flag_rules to careflow_app;   -- no insert, update or delete: publishing is a later, two-person feature

-- ---- agents.review_tasks ------------------------------------------------------------------------

create table agents.review_tasks (
    id                uuid primary key default gen_random_uuid(),
    clinic_id         uuid not null references iam.clinics (id),
    origin            text not null check (origin in ('live', 'test', 'eval', 'canary')),
    kind              text not null check (kind ~ '^[a-z][a-z0-9_]*$'),
    priority          text not null check (priority in ('immediate', 'standard')),
    assigned_role     text not null check (assigned_role in ('nurse', 'doctor', 'front_desk')),
    status            text not null default 'open' check (status in ('open', 'acknowledged', 'resolved')),
    source_message_id text not null check (length(source_message_id) between 1 and 200),
    -- Which rules fired and where: rule ids, versions and match positions. NEVER the patient's own words
    -- (rules 6 and 7); the nurse opens the source message to read them.
    provenance        jsonb not null check (jsonb_typeof(provenance) = 'object'),
    created_at        timestamptz not null default clock_timestamp(),
    acknowledged_by   uuid references iam.accounts (id),
    acknowledged_at   timestamptz,
    resolved_at       timestamptz,
    idempotency_key   text not null unique check (length(idempotency_key) > 0),
    -- A red flag is always immediate and always goes to a nurse (§12.1): no quiet red-flag task can exist.
    check (kind <> 'red_flag' or (priority = 'immediate' and assigned_role = 'nurse')),
    check (status = 'open' or (acknowledged_by is not null and acknowledged_at is not null)),
    check ((status = 'resolved') = (resolved_at is not null))
);

create index review_tasks_open_idx on agents.review_tasks (clinic_id, created_at) where status = 'open';

alter table agents.review_tasks enable row level security;
alter table agents.review_tasks force row level security;

-- Front desk can read too: an INSERT ... RETURNING needs the SELECT policy to pass, and front desk may raise a
-- task from the request path. The row holds rule ids and positions only, never patient text.
create policy review_tasks_select on agents.review_tasks for select to careflow_app
    using (iam.has_clinic_role(clinic_id, array['front_desk', 'nurse', 'doctor', 'admin', 'queue_worker']));
-- Whoever runs the screening (a worker, or staff in the request path) can raise the task.
create policy review_tasks_insert on agents.review_tasks for insert to careflow_app
    with check (iam.has_clinic_role(clinic_id, array['queue_worker', 'front_desk', 'nurse', 'doctor', 'admin']));
-- Only a nurse or doctor acknowledges or resolves, and only as themselves.
create policy review_tasks_update on agents.review_tasks for update to careflow_app
    using (iam.has_clinic_role(clinic_id, array['nurse', 'doctor']))
    with check (iam.has_clinic_role(clinic_id, array['nurse', 'doctor'])
                and (acknowledged_by is null or acknowledged_by = auth.uid()));

revoke all on agents.review_tasks from public;
grant usage on schema agents to careflow_app;
grant select, insert on agents.review_tasks to careflow_app;
grant update (status, acknowledged_by, acknowledged_at, resolved_at) on agents.review_tasks to careflow_app;

do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on kb.red_flag_rules, agents.review_tasks from %I', r);
            execute format('revoke all on schema kb, agents from %I', r);
        end if;
    end loop;
end $$;
