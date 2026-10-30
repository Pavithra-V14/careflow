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
-- WHAT IT DOES, in four statements: one restrictive policy per command (select, insert, update, delete) on storage.objects, for roles anon and authenticated, that is true for every object EXCEPT those
-- in the bucket `careflow-documents`. A restrictive policy is AND-ed with the permissive ones, so for this bucket the answer is always "no" for those roles, and for every other bucket nothing changes.
--
-- It ALSO closes storage.buckets for the same two roles (a role that could update the bucket row could make it public or lift its size and type limits), with the same shape: restrictive, true for every
-- bucket except `careflow-documents`.
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

create policy careflow_documents_closed_select on storage.objects as restrictive for select to anon, authenticated
    using (bucket_id <> 'careflow-documents');
create policy careflow_documents_closed_insert on storage.objects as restrictive for insert to anon, authenticated
    with check (bucket_id <> 'careflow-documents');
create policy careflow_documents_closed_update on storage.objects as restrictive for update to anon, authenticated
    using (bucket_id <> 'careflow-documents') with check (bucket_id <> 'careflow-documents');
create policy careflow_documents_closed_delete on storage.objects as restrictive for delete to anon, authenticated
    using (bucket_id <> 'careflow-documents');

create policy careflow_documents_closed_buckets on storage.buckets as restrictive for all to anon, authenticated
    using (id <> 'careflow-documents') with check (id <> 'careflow-documents');

-- Verify: the application's role has no privilege on the storage tables (fail the migration if it ever did).
do $$
begin
    if has_table_privilege('careflow_app', 'storage.objects', 'select,insert,update,delete')
       or has_table_privilege('careflow_app', 'storage.buckets', 'select,insert,update,delete') then
        raise exception 'careflow_app must hold no privilege on storage.objects or storage.buckets';
    end if;
end
$$;
