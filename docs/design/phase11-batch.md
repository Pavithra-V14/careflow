# Phase 11 batch: residual fixes, producers, portal, Care Journey storage (2026-11-06)

Owner instruction of 2026-11-06 (items A to G). Migrations 0047 to 0050, each with a reviewer pass (findings applied before merge), a grants-ledger row, role-matrix cases and live tests.

## Working rules adopted (marked PENDING COUNSEL where legal)
- **Owner-delete recommendation, working rule, pending counsel:** no deletes from append-only ledgers by anyone, including the owner, except the audited erasure path and, for transcript chunks, the audited purge of expired rows. Restore drills restore from backup into a separate empty project.
- **Placeholders (labelled, not decisions):** canary cap 20 (now a row in `docs.canary_settings`, seeded only by tests), care-gap alert age 14 days, voice transcript retention 30 days, and one demo care-gap rule in `config/demo-phase4.yaml`.

## Reviewer findings and what was done
- 0047/0048: the worker could never insert a sample (it cannot read `iam.clinics`): fixed with `ops.clinic_origin()`. Origins of staff inserts are pinned to the clinic. Rule numbers are bounded. `last_done_by` is recorded. Episodes for erased patients are refused. Function privileges are asserted for PUBLIC and the three Supabase roles.
- 0049: the claim now requires the NEWEST identity event to be a confirmation (a revoked record is not claimable) and the current care-delivery grant, and has a failure limit per account AND per phone token and clinic; registration no longer races.
- Left as they are, for the owner: no auto-approval path exists so the sampler samples nothing real; the spec names `assisted` as the default mode and this build uses `shadow`; a nurse or doctor may re-check their own sample.

## Deliberately not built
Visit Readiness storage and routes (clinician content and five model roles); the front-desk resolution of a pending claim request; the two-person `autonomous` switch; medications and notes; the runners reading the stored autonomy mode; retrofitting the database `aal2` check onto older tables; moving the result wake-up to the outbox.
