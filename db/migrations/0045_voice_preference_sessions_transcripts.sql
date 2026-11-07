-- 0045_voice_preference_sessions_transcripts: the database half of the in-app voice channel (Phase 8 follow-up, owner decision 2026-11-06 item 2; BUILD-SPEC.md 19.4, 23, 11.6, 9.4; CLAUDE.md rules 5, 6, 7, 8, 10).
--
-- THREE NEW TABLES in schema msg, forced row-level security and an origin tag from this migration:
--
--   msg.voice_preferences       the patient's OWN `voice_enabled` choice (off by default; a row exists only once they chose). The account itself reads and writes it, nobody else. This is the per-patient
--                               opt-in of 23 / 19.4: the mic button needs it AND the clinic flag, independently.
--   msg.voice_sessions          one row per opened voice session: who (the patient's account), which clinic, which of the three scoped workflows, when consent was recorded (a row exists only when the patient
--                               consented in this session: 23.4, a per-session event, distinct from the signup consents), and `transcript_retention_days`, a column with NO DEFAULT that the application fills
--                               from its REQUIRED setting (9.4: 30 days is the spec's starting value, a clinic setting; unset means the route refuses and no session row is written).
--   msg.voice_transcript_chunks the transcript sink: patient speech as CIPHERTEXT only (never plaintext in the table), one row per transcribed chunk, `expires_at` computed by a trigger from the session's
--                               retention (the caller cannot choose it), append-only for everyone, and a row cannot be deleted before it has expired.
--
-- WHO. A PATIENT account, for its own rows only. The session INSERT is checked in the database against the preference (a session cannot be opened unless the account's own preference row says enabled) and
-- against the account being bound to a patient record of that clinic: the opt-in is enforced by row-level security, not only by the route. The chunk INSERT is allowed only into the account's own OPEN session.
-- No staff role, no admin and no worker has any policy here. There is NO SELECT on transcript chunks for the application role at all (the admin-only, audited reading of 9.4 is a later package with its own
-- grant and review); the sink only inserts.
--
-- A chunk carries a key, unique per clinic (rule 5); the sink uses a PLAIN insert without RETURNING and treats a unique violation as a replay (no ON CONFLICT: that would need a SELECT right this table deliberately lacks).
--
-- Nothing is deleted by the application role (no DELETE grant). A purge of EXPIRED chunks is a separate, audited retention package; the guard here only guarantees that no chunk, for any role including the
-- owner, is deleted before its expiry.
--
-- RESIDUALS. (1) Anyone who can sign in as the patient can change the preference and open sessions (the account is the trust boundary). (2) A patient could insert chunks of invented speech into their own
-- open session; chunks are working context, not a clinical record, and the red-flag rules ran on what the live monitor saw, not on what is stored. (3) The ciphertext key is the application's (careflow/crypto.py).

create table msg.voice_preferences (
    account_id    uuid primary key references iam.accounts (id),
    origin        text not null check (origin in ('live', 'test', 'eval', 'canary')),
    voice_enabled boolean not null default false,
    updated_at    timestamptz not null default clock_timestamp()
);

create function msg.voice_preferences_touch() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    new.updated_at := clock_timestamp();
    return new;
end
$$;

create trigger voice_preferences_touch before update on msg.voice_preferences for each row execute function msg.voice_preferences_touch();

create table msg.voice_sessions (
    id                        uuid primary key default gen_random_uuid(),
    clinic_id                 uuid not null references iam.clinics (id),
    account_id                uuid not null references iam.accounts (id),
    origin                    text not null check (origin in ('live', 'test', 'eval', 'canary')),
    workflow                  text not null check (workflow in ('slot_offer', 'intake_question', 'readiness_item')),
    status                    text not null default 'open' check (status in ('open', 'ended')),
    consent_recorded_at       timestamptz not null default clock_timestamp(),
    opened_at                 timestamptz not null default clock_timestamp(),
    ended_at                  timestamptz,
    transcript_retention_days integer not null check (transcript_retention_days between 1 and 3650),
    idempotency_key           text not null check (length(idempotency_key) between 1 and 300),
    constraint voice_sessions_key_per_clinic unique (clinic_id, idempotency_key),
    constraint voice_sessions_id_clinic unique (id, clinic_id),
    constraint voice_sessions_end_pair check ((status = 'open') = (ended_at is null))
);

create function msg.voice_sessions_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    if tg_op <> 'UPDATE' then
        raise exception 'msg.voice_sessions rows are never deleted' using errcode = 'restrict_violation';
    end if;
    if old.status <> 'open' then
        raise exception 'an ended voice session is final' using errcode = 'restrict_violation';
    end if;
    if new.status <> 'ended' or new.id <> old.id or new.clinic_id <> old.clinic_id or new.account_id <> old.account_id or new.origin <> old.origin or new.workflow <> old.workflow
       or new.consent_recorded_at <> old.consent_recorded_at or new.opened_at <> old.opened_at or new.transcript_retention_days <> old.transcript_retention_days
       or new.idempotency_key <> old.idempotency_key then
        raise exception 'a voice session can only be ended' using errcode = 'restrict_violation';
    end if;
    new.ended_at := clock_timestamp();
    return new;
end
$$;

create trigger voice_sessions_guard before update or delete on msg.voice_sessions for each row execute function msg.voice_sessions_guard();
create trigger voice_sessions_no_truncate before truncate on msg.voice_sessions for each statement execute function msg.voice_sessions_guard();

create table msg.voice_transcript_chunks (
    id               uuid primary key default gen_random_uuid(),
    session_id       uuid not null,
    clinic_id        uuid not null,
    seq              integer not null check (seq >= 0),
    body_ct          bytea not null check (length(body_ct) between 1 and 20000),
    body_key_version integer not null check (body_key_version >= 1),
    idempotency_key  text not null check (length(idempotency_key) between 1 and 300),
    created_at       timestamptz not null default clock_timestamp(),
    expires_at       timestamptz not null,
    constraint voice_chunks_seq_per_session unique (session_id, seq),
    constraint voice_chunks_key_per_clinic unique (clinic_id, idempotency_key),
    foreign key (session_id, clinic_id) references msg.voice_sessions (id, clinic_id)
);

create function msg.voice_chunks_guard() returns trigger
    language plpgsql set search_path = ''
as $$
declare
    v_days integer;
begin
    if tg_op = 'INSERT' then
        select transcript_retention_days into v_days from msg.voice_sessions where id = new.session_id and clinic_id = new.clinic_id;
        if v_days is null then
            raise exception 'no such voice session' using errcode = 'restrict_violation';
        end if;
        new.created_at := clock_timestamp();
        new.expires_at := clock_timestamp() + make_interval(days => v_days);  -- the expiry is the database's, from the session's retention; the caller cannot choose it
        return new;
    end if;
    if tg_op = 'DELETE' and old.expires_at <= clock_timestamp() then
        return old;  -- only an EXPIRED chunk may go (the audited purge package); no application grant reaches this
    end if;
    raise exception 'msg.voice_transcript_chunks is append-only until a chunk has expired' using errcode = 'restrict_violation';
end
$$;

create trigger voice_chunks_guard before insert or update or delete on msg.voice_transcript_chunks for each row execute function msg.voice_chunks_guard();
create trigger voice_chunks_no_truncate before truncate on msg.voice_transcript_chunks for each statement execute function msg.voice_chunks_guard();

alter table msg.voice_preferences enable row level security;
alter table msg.voice_preferences force row level security;
alter table msg.voice_sessions enable row level security;
alter table msg.voice_sessions force row level security;
alter table msg.voice_transcript_chunks enable row level security;
alter table msg.voice_transcript_chunks force row level security;

-- "this account is an active patient account": the one condition every policy below shares.
create policy voice_prefs_select_own on msg.voice_preferences for select to careflow_app
    using (account_id = auth.uid());
create policy voice_prefs_insert_own on msg.voice_preferences for insert to careflow_app
    with check (account_id = auth.uid() and exists (select 1 from iam.accounts a where a.id = account_id and a.account_type = 'patient' and a.status = 'active'));
create policy voice_prefs_update_own on msg.voice_preferences for update to careflow_app
    using (account_id = auth.uid() and exists (select 1 from iam.accounts a where a.id = account_id and a.account_type = 'patient' and a.status = 'active'))
    with check (account_id = auth.uid());

create policy voice_sessions_select_own on msg.voice_sessions for select to careflow_app
    using (account_id = auth.uid());
create policy voice_sessions_insert_own on msg.voice_sessions for insert to careflow_app
    with check (
        account_id = auth.uid() and status = 'open' and ended_at is null
        and exists (select 1 from iam.accounts a where a.id = auth.uid() and a.account_type = 'patient' and a.status = 'active')
        and exists (select 1 from msg.voice_preferences p where p.account_id = auth.uid() and p.voice_enabled)
        and exists (select 1 from mpi.patients pt where pt.account_id = auth.uid() and pt.clinic_id = voice_sessions.clinic_id and pt.erased_at is null)
    );
create policy voice_sessions_end_own on msg.voice_sessions for update to careflow_app
    using (account_id = auth.uid() and status = 'open')
    with check (account_id = auth.uid() and status = 'ended');

create policy voice_chunks_insert_own on msg.voice_transcript_chunks for insert to careflow_app
    with check (exists (select 1 from msg.voice_sessions s where s.id = voice_transcript_chunks.session_id and s.clinic_id = voice_transcript_chunks.clinic_id and s.account_id = auth.uid() and s.status = 'open'));

revoke all on msg.voice_preferences, msg.voice_sessions, msg.voice_transcript_chunks from public;
grant select on msg.voice_preferences, msg.voice_sessions to careflow_app;
grant insert (account_id, origin, voice_enabled) on msg.voice_preferences to careflow_app;
grant update (voice_enabled) on msg.voice_preferences to careflow_app;
grant insert (clinic_id, account_id, origin, workflow, transcript_retention_days, idempotency_key) on msg.voice_sessions to careflow_app;
grant update (status) on msg.voice_sessions to careflow_app;
grant insert (session_id, clinic_id, seq, body_ct, body_key_version, idempotency_key) on msg.voice_transcript_chunks to careflow_app;

do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on msg.voice_preferences, msg.voice_sessions, msg.voice_transcript_chunks from %I', r);
        end if;
    end loop;
end $$;

do $$
declare t text;
begin
    foreach t in array array['voice_preferences', 'voice_sessions', 'voice_transcript_chunks'] loop
        if not (select c.relrowsecurity and c.relforcerowsecurity from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'msg' and c.relname = t) then
            raise exception '0045: msg.% must have forced row-level security', t;
        end if;
        if has_table_privilege('careflow_app', 'msg.' || t, 'delete') or has_table_privilege('careflow_app', 'msg.' || t, 'truncate') then
            raise exception '0045: the application role must not delete from msg.%', t;
        end if;
        if exists (select 1 from pg_roles r where r.rolname in ('anon', 'authenticated', 'service_role') and has_table_privilege(r.rolname, 'msg.' || t, 'select')) then
            raise exception '0045: anon, authenticated and service_role must have no access to msg.%', t;
        end if;
    end loop;
    if has_table_privilege('careflow_app', 'msg.voice_transcript_chunks', 'select') or has_table_privilege('careflow_app', 'msg.voice_transcript_chunks', 'update') then
        raise exception '0045: the application role must only insert transcript chunks';
    end if;
    if has_column_privilege('careflow_app', 'msg.voice_transcript_chunks', 'expires_at', 'insert') then
        raise exception '0045: the caller must not be able to set a chunk expiry';
    end if;
    if has_column_privilege('careflow_app', 'msg.voice_sessions', 'consent_recorded_at', 'insert') then
        raise exception '0045: the caller must not be able to set the consent time';
    end if;
    if (select count(*) from pg_policies where schemaname = 'msg' and tablename like 'voice\_%') <> 7 then
        raise exception '0045: expected 7 voice policies';
    end if;
end $$;
