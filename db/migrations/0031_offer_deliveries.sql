-- 0031_offer_deliveries: a truthful record that a PERSON delivered an offer (Phase 3 delivery with no external service; pre-approved by the owner 2026-11-05; docs/design/phase3-manual-delivery.md).
--
-- The system never sends an offer to a patient. The front desk copies the drafted text and sends it from the clinic's own phone, then presses "mark as delivered": one row here says WHO did it, WHEN, and
-- by which channel. The only channel is the closed word 'manual'; nothing else is accepted until a real channel is approved. The row holds ids and a closed word, never message text.
--
-- * The offer is the slot HOLD that `offers.send_offer` created (composite foreign key to sched.holds on (id, clinic_id)); one delivery record per hold (a second press is a replay, not a second row).
-- * RLS: front desk, nurse and doctor of the clinic read; ONLY front desk inserts, and only as themselves (delivered_by = auth.uid()). No UPDATE or DELETE grant, and a trigger refuses both for every role
--   (it is a record of what a person did).
-- * NEW GRANT (grants ledger): INSERT and SELECT on sched.offer_deliveries to careflow_app, nothing else.

create table sched.offer_deliveries (
    id              uuid primary key default gen_random_uuid(),
    clinic_id       uuid not null references iam.clinics (id),
    hold_id         uuid not null,
    channel         text not null check (channel in ('manual')),
    delivered_by    uuid not null references iam.accounts (id),
    delivered_at    timestamptz not null default clock_timestamp(),
    origin          text not null check (origin in ('live', 'test', 'eval', 'canary')),
    idempotency_key text not null unique check (length(idempotency_key) > 0),
    foreign key (hold_id, clinic_id) references sched.holds (id, clinic_id),
    unique (hold_id)
);

create index offer_deliveries_clinic_idx on sched.offer_deliveries (clinic_id, delivered_at desc);

alter table sched.offer_deliveries enable row level security;
alter table sched.offer_deliveries force row level security;

create policy offer_deliveries_select on sched.offer_deliveries for select to careflow_app
    using (iam.has_clinic_role(clinic_id, array['front_desk', 'nurse', 'doctor']));
create policy offer_deliveries_insert on sched.offer_deliveries for insert to careflow_app
    with check (
        iam.has_clinic_role(clinic_id, array['front_desk']) and delivered_by = auth.uid()
        -- the hold must be a LIVE OFFER of this clinic and origin (reviewer finding 2026-11-05: the database, not only the route, enforces it)
        and exists (select 1 from sched.holds h where h.id = hold_id and h.clinic_id = offer_deliveries.clinic_id and h.origin = offer_deliveries.origin
                     and h.idempotency_key like 'offer-hold:%' and h.status = 'active' and h.expires_at > clock_timestamp())
    );

create function sched.offer_deliveries_append_only() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    raise exception 'sched.offer_deliveries is append-only: % is not permitted', tg_op using errcode = 'restrict_violation';
end
$$;
revoke all on function sched.offer_deliveries_append_only() from public;

create trigger offer_deliveries_no_update_delete before update or delete on sched.offer_deliveries
    for each row execute function sched.offer_deliveries_append_only();
create trigger offer_deliveries_no_truncate before truncate on sched.offer_deliveries
    for each statement execute function sched.offer_deliveries_append_only();
alter table sched.offer_deliveries enable always trigger offer_deliveries_no_update_delete;
alter table sched.offer_deliveries enable always trigger offer_deliveries_no_truncate;

revoke all on sched.offer_deliveries from public;
grant select, insert on sched.offer_deliveries to careflow_app;

do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on sched.offer_deliveries from %I', r);
        end if;
    end loop;
end $$;

-- Verification: RLS on and forced, exactly the two policies, the app role holds only SELECT and INSERT, both trigger kinds are ENABLE ALWAYS.
do $$
begin
    if not exists (select 1 from pg_class where oid = 'sched.offer_deliveries'::regclass and relrowsecurity and relforcerowsecurity) then
        raise exception '0031: row-level security is not enabled and forced on sched.offer_deliveries';
    end if;
    if (select count(*) from pg_policies where schemaname = 'sched' and tablename = 'offer_deliveries') <> 2 then
        raise exception '0031: sched.offer_deliveries must have exactly two policies';
    end if;
    if has_table_privilege('careflow_app', 'sched.offer_deliveries', 'update') or has_table_privilege('careflow_app', 'sched.offer_deliveries', 'delete')
       or has_table_privilege('careflow_app', 'sched.offer_deliveries', 'truncate') or not has_table_privilege('careflow_app', 'sched.offer_deliveries', 'insert') then
        raise exception '0031: the application role must hold INSERT and SELECT only';
    end if;
    if (select count(*) from pg_trigger where tgrelid = 'sched.offer_deliveries'::regclass and not tgisinternal and tgenabled = 'A') <> 2 then
        raise exception '0031: both append-only triggers must be ENABLE ALWAYS';
    end if;
end $$;
