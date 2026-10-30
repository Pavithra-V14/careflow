# Uploads: Supabase Storage behind an `UploadPort` (accepted deviation from BUILD-SPEC.md §17.3)

Decision of the owner, 2026-10-24. **Status: accepted deviation. UPDATED 2026-10-30: the owner approved building the real storage adapter for the DEV and CI Supabase projects with SYNTHETIC data only (`data_policy` `synthetic_only`): a private bucket, no public URLs, short-lived signed URLs only, an object path with no patient text, the scanner port before anything is shown as clean, and unscanned means held for a person. No real patient document, ever, until a separate decision on region and the privacy owner's written confirmation (pre-launch blocker 18 in `phase2-status.md`).**

## Built (2026-10-30, branch b2-storage-adapter)
- `careflow/adapters/storage.py`: `SupabaseUploadAdapter`, the real `UploadPort`, behind the existing port (the fake stays for tests). Explicit timeouts (connect 5 s, read and write 30 s, 60 s per call); only a document path (`clinic/<id>/doc/<id>`) ever reaches a request; a signed URL for another host is refused; downloads stop one byte past the size cap; every failure is a fixed-words error with no path, key or body in it.
- `data-flow-register.yaml`: entry `supabase-storage` (data class `patient_plaintext`, `synthetic_only`, not permitted in clinical).
- `tests/test_storage_adapter.py`: the adapter against a DOCUMENTED MOCK (`httpx.MockTransport`), plus the application wiring.
- `scripts/storage_spike.py`: the Appendix B spike, with REAL calls (below).
- Wiring (`careflow/app.py` `_intake_from_environment`): the real storage is built ONLY when `CAREFLOW_STORAGE_BUCKET` is set, `CAREFLOW_STORAGE_VERIFIED` holds the date the spike was recorded, the profile is demo with `CAREFLOW_DATA_POLICY=synthetic_only`, and `SUPABASE_SECRET_KEY` is present. Otherwise the upload routes answer 503, as before.

## What the owner does, step by step (dev project first, then the CI project)
1. Supabase dashboard of the DEV project, Storage, New bucket. **Name:** `careflow-documents`. **Public bucket:** OFF (private). **Restrict file size:** ON, **20 MB** (20971520 bytes). **Allowed MIME types:** `application/pdf, image/jpeg, image/png`. Create.
2. Tell me; the policy review (below) is already done, and the 0027 migration is applied only after the owner's OK.
3. After the OK and the migration: I run `python scripts/storage_spike.py --bucket careflow-documents --dev-project` against the dev project (synthetic bytes only, every object deleted at the end) and report what the service did.
4. If every expectation held, set `CAREFLOW_STORAGE_BUCKET=careflow-documents` and `CAREFLOW_STORAGE_VERIFIED=<that date>` in the dev `.env`, and repeat step 1 and the migration for the CI project (its secrets go in the repository's secrets; the CI live tests keep using the fake storage).

## The storage policies (PROPOSED, NOT APPLIED): in plain words
The file is `docs/design/proposed-migrations/0027_storage_objects_policies.sql`. It sits OUTSIDE `db/migrations` so the CI migrate job cannot apply it before the owner agrees. It adds four RESTRICTIVE policies on `storage.objects`, one each for read, insert, update and delete, for the roles `anon` and `authenticated` only. Each says: "an object may be touched by these roles only if it is NOT in the bucket `careflow-documents`." Restrictive policies are AND-ed with any permissive ones, so even if somebody later adds a broad permissive rule for another purpose, nobody holding a browser-side role can read, write, replace or delete a document. It grants nothing, creates no permissive policy and no role, and changes no other bucket. The server uses the project's secret key (the service role), which bypasses these policies; that key never leaves the server. The migration also checks, and fails if it is ever untrue, that the application's own database role holds no privilege on the storage tables.

## Pre-launch blockers recorded by this decision
- No real patient document: needs the region decision (production in an India region, 16.5) and the privacy owner's written confirmation. The dev and CI projects are Tokyo and synthetic only.
- The malware scanner (`malware-scanner-options.md`) before any real use.
- The spike must be recorded (`CAREFLOW_STORAGE_VERIFIED`) before the adapter is built in any deployment.

## The deviation

BUILD-SPEC §17.3 and §16.2 name **Uploadcare** for the browser-to-storage upload of referral documents ("Signed browser to storage upload
(Uploadcare) ... server-side re-verification of the file"). The owner decided **not to add a new service**. Uploads will use **Supabase Storage**,
which belongs to the Supabase project already in use for the database and auth (the same project, so no new account, key or vendor), behind an
`UploadPort` interface so the choice can be reversed or replaced without touching the pipeline.

## What stays the same

- The pipeline in §17.3 is unchanged: signed upload from the browser, **server-side re-verification** of the file (type, size, that it exists
  at the claimed location; never trust the client), registration in `docs`, then extraction.
- Server-enforced size and type allowlists (§11.5). Storage objects are private, reached through short-lived signed URLs, with a bucket policy
  keyed on the same clinic isolation as the rest of the data (row-level security on `storage.objects`, reviewed like any migration).
- The data-flow register gets an entry for Supabase Storage when the adapter is written (it is the project's own host, but the file contents are
  patient data, so the entry says `patient_plaintext` and its `permitted_in_clinical` stays `unconfirmed` until the owner approves it).

## Known gap, recorded as a PRE-LAUNCH BLOCKER

BUILD-SPEC §11.5 and Appendix B: "Malware scanning on uploads is required before real (non-demo) use ... this is a pre-launch blocker, not
optional." **Supabase Storage has no built-in malware scanning.** Uploadcare offered a scanning add-on (itself unverified, Appendix B). With this
deviation there is no scanner at all until one is chosen. Therefore:

- Uploads are for **synthetic documents only** (the `demo` profile) until a scanning step exists.
- The scanning step is an owner decision and a new service or tool (for example a scanner that runs on the worker, or a hosted scanning API);
  it is NOT added by this decision (CLAUDE.md rule 13) and must be approved on its own.
- The `clinical` profile must not accept a real upload while the gap is open. P2-3 will add a check to the readiness rule so a `clinical`
  deployment without a configured scanner reports not ready.

## Other consequences to check when P2-3 starts

- Storage lives in the Tokyo dev and CI projects (synthetic only) and must be in an India region in production (§16.5), like the database.
- Supabase Storage's signed-upload behaviour and size limits must be confirmed by a real call before the adapter depends on them (Appendix B
  discipline; add the item when P2-3 starts).
