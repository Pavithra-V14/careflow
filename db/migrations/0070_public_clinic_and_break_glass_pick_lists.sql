-- 0070_public_clinic_and_break_glass_pick_lists (owner request 2026-11-10; frontend requests 13 to 15; CLAUDE.md rules 5, 7, 8, 10).
--
-- 1. iam.public_clinics: an OWNER-managed table that says which ONE clinic is the public face of this deployment. The patient forms that run before sign-in (a claim, a grievance) need the clinic id and name.
--    At most one row (a unique index on a constant), NO grant to any application role: the application cannot mark a clinic public. iam.public_clinic() is the only way the application reads it: it returns the
--    id and the NAME of that clinic, nothing else, and nothing at all while the owner has marked none (unknown is not a value: the route then answers 404). It needs no session, so it takes no claims; it can
--    only ever reveal the one clinic the owner chose to publish.
-- 2. iam.break_glass_reason_codes(p_clinic_id): the clinic's closed list of reason codes (0058: no application grant on the table), for the break-glass form. Front desk, nurse, doctor or admin of that clinic,
--    second factor required; anyone else gets 42501. It writes nothing.

create table iam.public_clinics (
    clinic_id    uuid primary key references iam.clinics (id),
    published_at timestamptz not null default clock_timestamp()
);
create unique index public_clinics_one_row on iam.public_clinics ((true));
alter table iam.public_clinics enable row level security;
alter table iam.public_clinics force row level security;
revoke all on iam.public_clinics from public;
do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role', 'careflow_app'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on iam.public_clinics from %I', r);
        end if;
    end loop;
end $$;

create function iam.public_clinic() returns table (clinic_id uuid, name text)
    language sql stable security definer set search_path = ''
as $$
    select c.id, c.name from iam.public_clinics p join iam.clinics c on c.id = p.clinic_id limit 1;
$$;

create function iam.break_glass_reason_codes(p_clinic_id uuid) returns table (code text)
    language plpgsql stable security definer set search_path = ''
as $$
begin
    if auth.uid() is null or p_clinic_id is null or not iam.aal2_session() or not iam.has_clinic_role(p_clinic_id, array['front_desk', 'nurse', 'doctor', 'admin']) then
        raise exception 'break-glass reasons refused' using errcode = '42501';
    end if;
    return query select rr.code from iam.break_glass_reasons rr where rr.clinic_id = p_clinic_id order by rr.code;
end
$$;

do $$
declare r text; f text;
begin
    foreach f in array array['iam.public_clinic()', 'iam.break_glass_reason_codes(uuid)'] loop
        execute format('revoke all on function %s from public', f);
        foreach r in array array['anon', 'authenticated', 'service_role'] loop
            if exists (select 1 from pg_roles where rolname = r) then
                execute format('revoke all on function %s from %I', f, r);
            end if;
        end loop;
        execute format('grant execute on function %s to careflow_app', f);
    end loop;
end $$;

do $$
declare r text; f text;
begin
    if not (select rolsuper or rolbypassrls from pg_roles where rolname = current_user) then
        raise exception '0070: the migrating role must bypass row-level security';
    end if;
    if has_any_column_privilege('careflow_app', 'iam.public_clinics', 'select, insert, update, references') or has_table_privilege('careflow_app', 'iam.public_clinics', 'delete') or has_table_privilege('careflow_app', 'iam.public_clinics', 'truncate') then
        raise exception '0070: the application role must have no grant on iam.public_clinics';
    end if;
    foreach f in array array['iam.public_clinic()', 'iam.break_glass_reason_codes(uuid)'] loop
        if has_function_privilege('public', f, 'execute') or not has_function_privilege('careflow_app', f, 'execute') then
            raise exception '0070: % must be executable by the application login and not by PUBLIC', f;
        end if;
        foreach r in array array['anon', 'authenticated', 'service_role'] loop
            if exists (select 1 from pg_roles where rolname = r) and has_function_privilege(r, f, 'execute') then
                raise exception '0070: % must not be executable by %', f, r;
            end if;
        end loop;
    end loop;
end $$;
