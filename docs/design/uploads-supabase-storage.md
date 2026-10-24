# Uploads: Supabase Storage behind an `UploadPort` (accepted deviation from BUILD-SPEC.md §17.3)

Decision of the owner, 2026-10-24. **Status: accepted deviation, planned for package P2-3. Nothing is built or called yet.**

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
