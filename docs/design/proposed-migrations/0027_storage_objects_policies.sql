-- PROPOSED, NOT APPLIED. This file lives OUTSIDE db/migrations on purpose: the CI migrate job applies every file in db/migrations to the CI project, and the owner has asked to see the policies and a
-- reviewer verdict BEFORE anything touches storage.objects. When the owner approves, it moves to db/migrations/0027_storage_objects_policies.sql in its own change and the migrate job applies it to CI;
-- the dev project is migrated by hand with `python -m careflow.migrate`.
--
-- 0027: close the document bucket to every database role except the service role (docs/design/uploads-supabase-storage.md; owner decision 2026-10-30: dev and CI projects, synthetic data only).
--
-- THE MODEL. The browser never holds a Storage credential. It receives a short-lived SIGNED UPLOAD URL for a path the server chose, and the server reads, verifies and deletes with the project's SECRET key
-- (the service role), which bypasses row-level security by design. So NO database role that a browser or the application can become (`anon`, `authenticated`, and the application's own `careflow_app`)
-- should be able to touch an object in the bucket through the Storage API or SQL. Row-level security on storage.objects already denies everything to those roles when no policy allows it; this migration
-- makes that explicit and durable with RESTRICTIVE policies, so a permissive policy added later for ANOTHER purpose (a profile-picture bucket, a mistaken broad `using (true)`) cannot open this bucket.
--
-- WHAT IT DOES, in five statements: one restrictive policy per command (select, insert, update, delete) on storage.objects and one for all commands on storage.buckets, for roles anon and authenticated,
-- each saying "false". A restrictive policy is AND-ed with the permissive ones, so for those roles the answer is always "no", for every bucket, now and after any later permissive policy is added.
-- (A role that could update the bucket row could make it public or lift its size and type limits, which is why storage.buckets is closed too.)
--
-- WHAT IT DOES NOT DO. It creates no permissive policy, grants nothing, creates no role, and does not create the bucket (the owner does, in the dashboard: private, 20 MiB limit, PDF/JPEG/PNG only;
-- the server re-verifies size, type and magic number itself). It does not touch `careflow_app`, which must hold no privilege on the storage schema (a verify step at the end fails the migration if it does).
-- It does not stop the service role (the server) or the project's superuser; those are the trusted writers, as with the other append-only tables.
--
-- NOT COVERED: storage.s3_multipart_uploads and storage.s3_multipart_uploads_parts (the S3-compatible multipart API) are not guarded here; the server never uses them and the bucket is only reachable with the service key
-- or a signed URL, but they are listed so nobody assumes otherwise. Signed upload URLs are validated by the Storage service itself and write with a privileged connection: these policies DO NOT govern them (RESIDUAL 3).
-- THE MIGRATION IS SUPABASE-ONLY (it needs the storage schema and the anon/authenticated roles). Before it moves into db/migrations it is dry-run in the dev project as the real migrator role inside
-- `begin; ... rollback;`, because whether that role may create policies on storage.objects (owned by supabase_storage_admin) is not verified.
--
-- RESIDUALS. (1) The bucket's `public` flag lives in storage.buckets, not in a policy: the dashboard step must create it PRIVATE, and `scripts/storage_spike.py` checks that an unauthenticated read is refused.
-- (2) The service role can read every object of the bucket: the secret key must stay server-side (it is in .env / secrets, never in a browser). (3) A signed upload URL, once handed out, lets its holder
-- write that ONE path until it expires; the server re-hashes on every read (ContentChanged). (4) The bucket name is a constant here and in the application; renaming it needs a new migration.

drop policy if exists storage_objects_deny_select on storage.objects;
drop policy if exists storage_objects_deny_insert on storage.objects;
drop policy if exists storage_objects_deny_update on storage.objects;
drop policy if exists storage_objects_deny_delete on storage.objects;
drop policy if exists storage_buckets_deny_all on storage.buckets;

create policy storage_objects_deny_select on storage.objects as restrictive for select to anon, authenticated using (false);
create policy storage_objects_deny_insert on storage.objects as restrictive for insert to anon, authenticated with check (false);
create policy storage_objects_deny_update on storage.objects as restrictive for update to anon, authenticated using (false) with check (false);
create policy storage_objects_deny_delete on storage.objects as restrictive for delete to anon, authenticated using (false);

create policy storage_buckets_deny_all on storage.buckets as restrictive for all to anon, authenticated using (false) with check (false);

-- Verify (fails the migration if untrue). (1) Restrictive policies are inert if row-level security is OFF, so every ordinary table in the storage schema that anon or authenticated can reach must have it ON.
-- (2) The application's own role holds no privilege on the storage schema, at table, column or schema level.
do $$
declare
    t record;
begin
    for t in
        select c.oid, c.relname, c.relrowsecurity
        from pg_class c join pg_namespace n on n.oid = c.relnamespace
        where n.nspname = 'storage' and c.relkind in ('r', 'p')
    loop
        if not t.relrowsecurity
           and (has_any_column_privilege('anon', t.oid, 'select,insert,update,references')
                or has_any_column_privilege('authenticated', t.oid, 'select,insert,update,references')
                or has_table_privilege('anon', t.oid, 'delete,truncate') or has_table_privilege('authenticated', t.oid, 'delete,truncate')) then
            raise exception 'storage.% is reachable by anon or authenticated and has row-level security OFF', t.relname;
        end if;
        if has_any_column_privilege('careflow_app', t.oid, 'select,insert,update,references') or has_table_privilege('careflow_app', t.oid, 'delete,truncate') then
            raise exception 'careflow_app must hold no privilege on storage.%', t.relname;
        end if;
    end loop;
    if not (select relrowsecurity from pg_class where oid = 'storage.objects'::regclass)
       or not (select relrowsecurity from pg_class where oid = 'storage.buckets'::regclass) then
        raise exception 'row-level security must be ON for storage.objects and storage.buckets';
    end if;
    if has_schema_privilege('careflow_app', 'storage', 'usage') then
        raise exception 'careflow_app must hold no USAGE on the storage schema';
    end if;
end
$$;

-- OWNER DECISION 2026-10-30 (final shape): every direct operation on storage.objects and storage.buckets is denied to anon and authenticated, for EVERY bucket (not only careflow-documents);
-- only the backend, with its server-side key, touches storage. No policy mentions a patient id or a clinic id. A live test proves an anon and an authenticated session are each refused for list, read, upload and delete.
