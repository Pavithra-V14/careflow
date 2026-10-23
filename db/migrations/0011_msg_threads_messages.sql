-- 0011_msg_threads_messages: message threads and messages, STAFF ACCESS ONLY (BUILD-SPEC.md §9.1, §9.4, §17.1, §17.2,
-- §17.5, §12.1; CLAUDE.md rules 5, 6, 7, 8, 10). Design: docs/design/msg-screening-binding-design.md.
--
-- * msg.threads: one thread per (patient, topic). Topics are care / appointments / safety; `readiness` arrives with its
--   own phase by a LATER migration widening the CHECK. Patients, guardians, admin, business owner and auditors get NO
--   policy here, so they read and write nothing (deny by default). Patient-side access, and the account-state gating of
--   17.5 (care opens only after activation), arrive with the portal and MUST be enforced in its policies.
-- * msg.messages: APPEND-ONLY, and the body is CIPHERTEXT (AES-256-GCM through careflow/crypto.py, key version stored
--   beside it, as for demographics in 0002). Erasure removes the body and sets erased_at; it is a migrator-run, audited
--   procedure, never an application path. A message is never edited: a correction is a new message.
-- * msg.message_rotation_batches: the ledger of message-body key rotations. The application role has NO access; only the
--   migrator-run rotation (careflow/rotation.py rotate_message_bodies) writes it.
-- * Every policy is row-local: `topic` is stored on the row (and pinned to its thread by a composite foreign key), so no
--   policy joins, calls a helper or recurses. front_desk is on an ALLOWLIST of topics, so any topic added later is
--   default-deny to front desk. front_desk may NOT read `care` (owner decision); it reads `safety` bodies by decision.
-- * sender_kind: only 'staff' can be inserted by this migration. 'patient' (a message RELAYED by staff on a patient's
--   behalf, with recorded_by naming who typed it) is enabled by 0012 together with the screening requirement. 'system'
--   stays denied until a defined use exists.
--
-- Residual risks, stated rather than hidden (BUILD-SPEC.md §18.1): the owner/migrator role can disable triggers or set
-- session_replication_role, so append-only and the erased-patient guard bind the application role and every ordinary
-- session, not a migrator-level actor; the migrator is trusted, as for audit.log. The database cannot check that a
-- rotated ciphertext decrypts to the same plaintext; that read-back is in careflow/rotation.py.
--
-- The role careflow_app is created by scripts/create_app_role.py; this migration only grants to it.

-- ---- prerequisite: lets child tables prove they share a patient's clinic AND origin ---------------------------------

alter table mpi.patients add constraint patients_id_clinic_origin_key unique (id, clinic_id, origin);

-- ---- the erased-patient guard (definer helper; see the note on triggers below) ----------------------------------------
--
-- A thread or message may only be created for a live (non-erased) patient. The check must lock the patient row
-- FOR SHARE so a concurrent erasure cannot slip between the check and the commit (the foreign key's own KEY SHARE lock
-- does NOT conflict with a non-key UPDATE of erased_at). Erasure procedure order, pinned: FIRST update
-- mpi.patients.erased_at (this takes the conflicting row lock), THEN erase the message bodies in later statements of the
-- same transaction, under READ COMMITTED.
--
-- Why a SECURITY DEFINER helper: a row lock under RLS is subject to the table's UPDATE policy, which is front-desk-only
-- (0002), so a nurse or doctor could never take it as themselves. Run as the owner it works for every permitted role. It
-- first checks the CALLER's role for the CLAIMED clinic and otherwise raises the SAME error an RLS denial gives, so it is
-- no existence or erasure oracle across clinics (triggers fire before the WITH CHECK policy would).
-- Nothing here is granted to careflow_app: trigger functions need no EXECUTE grant, and the helper is only called by them.

create function msg.guard_patient_live(p_patient uuid, p_clinic uuid, p_origin text, p_table text) returns void
    language plpgsql security definer set search_path = ''
as $$
declare
    v_erased timestamptz;
    v_found boolean;
begin
    if not iam.has_clinic_role(p_clinic, array['front_desk', 'nurse', 'doctor']) then
        raise exception using errcode = '42501',
            message = format('new row violates row-level security policy for table "%s"', p_table);
    end if;
    select true, p.erased_at into v_found, v_erased
      from mpi.patients p
     where p.id = p_patient and p.clinic_id = p_clinic and p.origin = p_origin
       for share;
    if v_found is not true or v_erased is not null then
        raise exception using errcode = '42501',
            message = format('new row violates row-level security policy for table "%s"', p_table);
    end if;
end
$$;

-- ---- msg.threads ------------------------------------------------------------------------------------------------

create table msg.threads (
    id              uuid primary key default gen_random_uuid(),
    clinic_id       uuid not null,
    patient_id      uuid not null,
    topic           text not null constraint threads_topic_check check (topic in ('care', 'appointments', 'safety')),
    -- Never updatable by the application role, so effectively always 'open' until a closing path is designed.
    status          text not null default 'open' check (status in ('open', 'closed')),
    -- The staff member who opened it. Forced to the signed-in caller by the INSERT policy.
    created_by      uuid not null references iam.accounts (id),
    -- Origin tag (§0): no default on purpose, so test data can never be silently tagged `live`.
    origin          text not null check (origin in ('live', 'test', 'eval', 'canary')),
    created_at      timestamptz not null default clock_timestamp(),
    -- Rule 5: every write carries an idempotency key.
    idempotency_key text not null unique check (length(idempotency_key) > 0),
    foreign key (patient_id, clinic_id, origin) references mpi.patients (id, clinic_id, origin),
    -- ONE thread per patient and topic. Can be relaxed later by a NEW migration if a clinic needs several.
    unique (patient_id, topic),
    unique (id, clinic_id, origin, topic)
);

comment on column msg.threads.topic is
    'care opens to PATIENTS only after account activation (BUILD-SPEC 17.5); this migration grants patients nothing, and the portal-phase policy must enforce that. safety bodies are readable by front desk by owner decision.';

create function msg.threads_guard() returns trigger
    language plpgsql security definer set search_path = ''
as $$
begin
    perform msg.guard_patient_live(new.patient_id, new.clinic_id, new.origin, 'threads');
    return new;
end
$$;

create trigger threads_guard before insert on msg.threads
    for each row execute function msg.threads_guard();

-- ---- msg.messages -----------------------------------------------------------------------------------------------

create table msg.messages (
    -- Chosen by the CLIENT (no default): the ciphertext's authenticated data binds it, so it must be known before the insert.
    id               uuid primary key,
    clinic_id        uuid not null,
    origin           text not null check (origin in ('live', 'test', 'eval', 'canary')),
    -- Denormalised from the thread and pinned to it by the composite foreign key below, so policies are row-local.
    topic            text not null constraint messages_topic_check check (topic in ('care', 'appointments', 'safety')),
    thread_id        uuid not null,
    sender_kind      text not null check (sender_kind in ('patient', 'staff', 'system')),
    -- Who typed the row. Forced to the signed-in caller by the INSERT policy. No sender_account_id until the portal.
    recorded_by      uuid not null references iam.accounts (id),
    -- AES-256-GCM ciphertext (nonce || ct || tag); key_version beside it (§17.2). Null only once erased.
    body_ct          bytea,
    body_key_version smallint,
    erased_at        timestamptz,
    created_at       timestamptz not null default clock_timestamp(),
    idempotency_key  text not null unique check (length(idempotency_key) > 0),
    foreign key (thread_id, clinic_id, origin, topic) references msg.threads (id, clinic_id, origin, topic),
    unique (id, clinic_id, origin, topic),
    check (
        (erased_at is null and body_ct is not null and body_key_version is not null and body_key_version > 0)
        or (erased_at is not null and body_ct is null and body_key_version is null)
    ),
    -- Technical bounds, not policy: 12-byte nonce + 16-byte tag + at least one byte; and 10,000 characters of at most
    -- 4 UTF-8 bytes each (enforced before encryption in careflow/messages.py) plus the 28 bytes of nonce and tag.
    check (body_ct is null or octet_length(body_ct) between 29 and 40100)
);

create index messages_thread_idx on msg.messages (thread_id, created_at);

create function msg.messages_guard() returns trigger
    language plpgsql security definer set search_path = ''
as $$
declare
    v_patient uuid;
begin
    -- Resolve the patient THROUGH the thread, in the CLAIMED clinic and origin. A thread in another clinic, or none,
    -- gives the same error as an RLS denial.
    select t.patient_id into v_patient
      from msg.threads t
     where t.id = new.thread_id and t.clinic_id = new.clinic_id and t.origin = new.origin;
    if v_patient is null then
        raise exception using errcode = '42501',
            message = 'new row violates row-level security policy for table "messages"';
    end if;
    perform msg.guard_patient_live(v_patient, new.clinic_id, new.origin, 'messages');
    return new;
end
$$;

create trigger messages_guard before insert on msg.messages
    for each row execute function msg.messages_guard();

-- Append-only for EVERY role including the owner. Exactly two UPDATE transitions exist, and neither is reachable by
-- the application role (it has no UPDATE privilege):
--   (1) erasure: body_ct and body_key_version become null and erased_at is set; nothing else changes;
--   (2) key rotation: only body_ct and body_key_version change, the key version MUST change, erased_at stays null.
-- Neither can happen to an already-erased row (an erasure is never reversible). "Nothing else changes" is a comparison of
-- every OTHER column, taken from the row itself, so a column added later is immutable by default.
create function msg.messages_append_only() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    if tg_op <> 'UPDATE' then
        raise exception 'msg.messages is append-only: % is not permitted', tg_op using errcode = 'restrict_violation';
    end if;
    if old.erased_at is null then
        if new.erased_at is not null and new.body_ct is null and new.body_key_version is null
           and (to_jsonb(new) - 'body_ct' - 'body_key_version' - 'erased_at')
               = (to_jsonb(old) - 'body_ct' - 'body_key_version' - 'erased_at') then
            return new;  -- erasure
        end if;
        if new.erased_at is null and new.body_ct is not null and new.body_key_version is distinct from old.body_key_version
           and (to_jsonb(new) - 'body_ct' - 'body_key_version') = (to_jsonb(old) - 'body_ct' - 'body_key_version') then
            return new;  -- key rotation
        end if;
    end if;
    raise exception 'msg.messages is append-only: this change is not permitted' using errcode = 'restrict_violation';
end
$$;

create function msg.messages_no_truncate() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    raise exception 'msg.messages is append-only: TRUNCATE is not permitted' using errcode = 'restrict_violation';
end
$$;

create trigger messages_append_only before update or delete on msg.messages
    for each row execute function msg.messages_append_only();
create trigger messages_no_truncate before truncate on msg.messages
    for each statement execute function msg.messages_no_truncate();

-- ---- msg.message_rotation_batches (migrator only) ----------------------------------------------------------------

create table msg.message_rotation_batches (
    id               uuid primary key default gen_random_uuid(),
    clinic_id        uuid not null references iam.clinics (id),
    origin           text not null check (origin in ('live', 'test', 'eval', 'canary')),
    from_version     smallint not null check (from_version > 0),
    to_version       smallint not null check (to_version > 0 and to_version <> from_version),
    messages_rotated integer not null check (messages_rotated > 0),
    first_message_id uuid not null,
    last_message_id  uuid not null,
    created_at       timestamptz not null default clock_timestamp(),
    idempotency_key  text not null unique check (length(idempotency_key) > 0)
);

-- ---- Row-level security -----------------------------------------------------------------------------------------

alter table msg.threads enable row level security;
alter table msg.threads force row level security;
alter table msg.messages enable row level security;
alter table msg.messages force row level security;
-- No policy and no grant: the application role cannot reach it; the migrator bypasses RLS by design.
alter table msg.message_rotation_batches enable row level security;
alter table msg.message_rotation_batches force row level security;

-- nurse and doctor: every topic. front_desk: an allowlist of topics.
create policy threads_select on msg.threads for select to careflow_app
    using (
        iam.has_clinic_role(clinic_id, array['nurse', 'doctor'])
        or (iam.has_clinic_role(clinic_id, array['front_desk']) and topic in ('appointments', 'safety'))
    );
create policy threads_insert on msg.threads for insert to careflow_app
    with check (
        created_by = auth.uid()
        and (
            iam.has_clinic_role(clinic_id, array['nurse', 'doctor'])
            or (iam.has_clinic_role(clinic_id, array['front_desk']) and topic = 'appointments')
        )
    );

create policy messages_select on msg.messages for select to careflow_app
    using (
        iam.has_clinic_role(clinic_id, array['nurse', 'doctor'])
        or (iam.has_clinic_role(clinic_id, array['front_desk']) and topic in ('appointments', 'safety'))
    );
create policy messages_insert on msg.messages for insert to careflow_app
    with check (
        recorded_by = auth.uid()
        and sender_kind = 'staff'
        and (
            iam.has_clinic_role(clinic_id, array['nurse', 'doctor'])
            or (iam.has_clinic_role(clinic_id, array['front_desk']) and topic = 'appointments')
        )
    );

-- ---- Privileges -------------------------------------------------------------------------------------------------

revoke all on msg.threads, msg.messages, msg.message_rotation_batches from public;
-- Explicit as well as by omission: the application role is never granted the functions or the ledger, and this keeps
-- that true even if default privileges are ever added.
revoke all on msg.message_rotation_batches from careflow_app;
revoke all on all functions in schema msg from public, careflow_app;

grant usage on schema msg to careflow_app;
grant select on msg.threads, msg.messages to careflow_app;
-- Column-level INSERT: created_at, status and (for threads) id are server-set, so a client cannot backdate a row; erased_at
-- is never settable. Message `id` is client-chosen because the ciphertext binds it.
grant insert (clinic_id, patient_id, topic, created_by, origin, idempotency_key) on msg.threads to careflow_app;
grant insert (id, clinic_id, origin, topic, thread_id, sender_kind, recorded_by, body_ct, body_key_version, idempotency_key)
    on msg.messages to careflow_app;
-- No UPDATE, DELETE or TRUNCATE for the application role on any msg table.
-- Note: columns added to these tables later receive no INSERT grant until one is added explicitly.

do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on all tables in schema msg from %I', r);
            execute format('revoke all on all functions in schema msg from %I', r);
            execute format('revoke all on schema msg from %I', r);
        end if;
    end loop;
end $$;
