-- 0017_docs_documents: the record that an uploaded document was received and VERIFIED (BUILD-SPEC.md §10.4, §11.5, §17.1 `docs`, §17.3;
-- CLAUDE.md rules 5, 6, 7, 8, 9, 10). Package P2-3.
--
-- A row exists ONLY for a document whose file the server has already checked itself (type, size, that it exists where the row says): a failed
-- upload leaves no row ("nothing half-registered", §17.3). The row holds NO file content and NO file name: a clinic, a kind, a server-made storage
-- path, the type and size the server observed, and the sha256 of the bytes.
--
-- * storage_path is not free text: a CHECK ties it to this row's clinic and id ('clinic/<clinic_id>/doc/<id>'), so a row can never point at another
--   clinic's object, and the path never contains anything the client chose.
-- * content_type is the allowlist (PDF, JPEG, PNG); size has a hard technical ceiling of 100 MiB here (the application's own, much lower, limit is
--   in careflow/documents.py); sha256 is 64 lowercase hex characters.
-- * The only status is 'registered'. The extraction states (§10.4) are added by the migration of the package that extracts: designing them now
--   would be guessing (Appendix D, Phase 0 reasoning).
-- * APPEND-ONLY for everyone, the owner included (trigger): a document record is part of the clinical record. No UPDATE, no DELETE, no TRUNCATE.
--   Erasure of a document (DPDP) is a separate, audited procedure that does not exist yet and is recorded as an open item.
-- * Who: front desk and nurse of the clinic may INSERT, and only as themselves (registered_by = auth.uid()). front desk, nurse, doctor and the
--   queue_worker (the extraction worker, next package) may SELECT. Admin, business owner and auditor get nothing: a referral letter is case-level
--   patient data (BUILD-SPEC.md §18.4). careflow_app has no UPDATE or DELETE privilege.
-- * Origin tag with no default (§0); every write carries an idempotency key, unique PER CLINIC (so one clinic cannot probe another's keys).
--
-- WHY front desk may read EVERY document of its clinic (a privacy decision, flagged to the owner): in the clinical profile, with no approved model route,
-- front desk keys the facts of a referral letter in by hand (BUILD-SPEC.md section 2), so it needs the letters it did not upload itself. Narrowing the read to
-- registered_by = auth.uid() would break that. Admin, business owner and auditor stay out (case-level data, 18.4). A doctor may read but not register.
--
-- RESIDUAL RISKS, stated: (1) the id is supplied by the server (a uuid5 of the clinic and the idempotency key), never by a client, but the primary key
-- is global: a caller who inserts an id that exists in another clinic gets a unique violation, a theoretical existence probe that needs a 122-bit guess;
-- the application maps it to a fixed message. (2) The database cannot verify that the size, type and hash were really observed by the server: only the
-- server code path (careflow/documents.py) inserts, and a row is not an attestation. (3) No audit.log row is written here.
--
-- NOT HERE: the Supabase Storage bucket and its policies (storage.objects) belong to the real UploadPort adapter, which does not exist yet; real
-- uploads are disabled in the clinical profile until a malware scanner exists (docs/design/uploads-supabase-storage.md).

create table docs.documents (
    id              uuid primary key,
    clinic_id       uuid not null references iam.clinics (id),
    origin          text not null check (origin in ('live', 'test', 'eval', 'canary')),
    kind            text not null check (kind in ('referral', 'report', 'other')),
    storage_path    text not null,
    content_type    text not null check (content_type in ('application/pdf', 'image/jpeg', 'image/png')),
    size_bytes      bigint not null check (size_bytes > 0 and size_bytes <= 104857600),
    sha256          text not null check (sha256 ~ '^[0-9a-f]{64}$'),
    status          text not null default 'registered' check (status in ('registered')),
    registered_by   uuid not null references iam.accounts (id),
    created_at      timestamptz not null default clock_timestamp(),
    idempotency_key text not null check (length(idempotency_key) > 0),
    constraint documents_path_is_this_rows_own check (storage_path = 'clinic/' || clinic_id::text || '/doc/' || id::text),
    constraint documents_key_per_clinic unique (clinic_id, idempotency_key),
    constraint documents_id_clinic_key unique (id, clinic_id)
);

create index documents_clinic_created_idx on docs.documents (clinic_id, created_at desc);

create function docs.documents_immutable() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    raise exception 'docs.documents is append-only: % is not permitted', tg_op using errcode = 'restrict_violation';
end
$$;

create trigger documents_no_update_delete before update or delete on docs.documents
    for each row execute function docs.documents_immutable();
create trigger documents_no_truncate before truncate on docs.documents
    for each statement execute function docs.documents_immutable();

alter table docs.documents enable row level security;
alter table docs.documents force row level security;

create policy documents_insert on docs.documents for insert to careflow_app
    with check (iam.has_clinic_role(clinic_id, array['front_desk', 'nurse']) and registered_by = auth.uid());
create policy documents_select on docs.documents for select to careflow_app
    using (iam.has_clinic_role(clinic_id, array['front_desk', 'nurse', 'doctor', 'queue_worker']));

revoke all on docs.documents from public;
grant usage on schema docs to careflow_app;
grant select on docs.documents to careflow_app;
-- Column-level INSERT: created_at and status always come from the database.
grant insert (id, clinic_id, origin, kind, storage_path, content_type, size_bytes, sha256, registered_by, idempotency_key)
    on docs.documents to careflow_app;

revoke all on function docs.documents_immutable() from public;

do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on docs.documents from %I', r);
            execute format('revoke all on function docs.documents_immutable() from %I', r);
            execute format('revoke all on schema docs from %I', r);
        end if;
    end loop;
end $$;

comment on table docs.documents is
    'A document the server has VERIFIED (type, size, existence). Append-only; no file content or name; the storage path is tied to the row. BUILD-SPEC 17.3.';
