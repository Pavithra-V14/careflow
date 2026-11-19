-- 0096_demo_placeholder_labels: demo completeness pass (owner instruction 2026-11-19). MECHANISM ONLY: no value is entered here, nothing is seeded, no default exists.
--
-- Every owner-managed table that a screen needs and that ships empty gets ONE nullable text column, `provenance` (at most 200 characters), where a demo seed script writes the label
-- "DEMO PLACEHOLDER, not approved by <owner>". The owner-managed tables have no other note or label field and the application cannot read them, so this is the only place the label can live with the row.
-- (iam.family_link_rules already has it since 0093; iam.consent_notices carries the label in the notice text itself, which the patient reads.) Real values leave `provenance` null.
--
-- ops.demo_placeholder_rows() is a SECURITY DEFINER function that returns, per source, how many rows carry a placeholder label (provenance starting "DEMO PLACEHOLDER", or a consent notice whose
-- body starts "DEMO ONLY" or "DEMO PLACEHOLDER"). It returns COUNTS ONLY: no id, no clinic, no text. The application's startup check calls it and REFUSES TO START outside demo + synthetic_only
-- if any count is above zero, so a placeholder can never sit under a clinical deployment unnoticed. It reads the tables as their owner (the application role has no grant on them) and changes nothing.
-- It checks no caller identity on purpose: it runs at startup with no signed-in person, and it discloses only a number per source.

alter table iam.break_glass_reasons       add column provenance text check (provenance is null or length(provenance) <= 200);
alter table iam.break_glass_settings      add column provenance text check (provenance is null or length(provenance) <= 200);
alter table iam.public_grievance_limits   add column provenance text check (provenance is null or length(provenance) <= 200);
alter table docs.canary_settings          add column provenance text check (provenance is null or length(provenance) <= 200);
alter table ops.spend_limits              add column provenance text check (provenance is null or length(provenance) <= 200);
alter table sched.patient_bookable_visit_types add column provenance text check (provenance is null or length(provenance) <= 200);

create function ops.demo_placeholder_rows()
    returns table (o_source text, o_rows bigint)
    language sql stable security definer set search_path = ''
as $$
    select 'iam.break_glass_reasons', count(*) from iam.break_glass_reasons where provenance like 'DEMO PLACEHOLDER%'
    union all select 'iam.break_glass_settings', count(*) from iam.break_glass_settings where provenance like 'DEMO PLACEHOLDER%'
    union all select 'iam.public_grievance_limits', count(*) from iam.public_grievance_limits where provenance like 'DEMO PLACEHOLDER%'
    union all select 'docs.canary_settings', count(*) from docs.canary_settings where provenance like 'DEMO PLACEHOLDER%'
    union all select 'ops.spend_limits', count(*) from ops.spend_limits where provenance like 'DEMO PLACEHOLDER%'
    union all select 'sched.patient_bookable_visit_types', count(*) from sched.patient_bookable_visit_types where provenance like 'DEMO PLACEHOLDER%'
    union all select 'iam.family_link_rules', count(*) from iam.family_link_rules where provenance like 'DEMO PLACEHOLDER%'
    union all select 'iam.consent_notices', count(*) from iam.consent_notices where body like 'DEMO ONLY%' or body like 'DEMO PLACEHOLDER%'
$$;

revoke all on function ops.demo_placeholder_rows() from public;
do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on function ops.demo_placeholder_rows() from %I', r);
        end if;
    end loop;
end $$;
grant execute on function ops.demo_placeholder_rows() to careflow_app;

comment on function ops.demo_placeholder_rows() is 'Counts of demo placeholder rows per owner-managed source (no ids, no text). The startup check refuses a non-demo deployment while any count is above zero.';

do $$
begin
    if not (select rolsuper or rolbypassrls from pg_roles where rolname = current_user) then
        raise exception '0096: the migrating role must bypass row-level security (ops.demo_placeholder_rows counts rows through it; without that it would report zero and a placeholder would go unseen)';
    end if;
    if has_table_privilege('careflow_app', 'iam.break_glass_settings', 'select') or has_table_privilege('careflow_app', 'ops.spend_limits', 'select') then
        raise exception '0096: the application role must still have no grant on the owner-managed tables';
    end if;
    if not has_function_privilege('careflow_app', 'ops.demo_placeholder_rows()', 'execute') or has_function_privilege('public', 'ops.demo_placeholder_rows()', 'execute') then
        raise exception '0096: demo_placeholder_rows must be executable by careflow_app only';
    end if;
end $$;
