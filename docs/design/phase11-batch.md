# Phase 11 batch: residual fixes, producers, portal, Care Journey storage (2026-11-06)

Owner instruction of 2026-11-06 (items A to G). Migrations 0047 to 0050, each with a reviewer pass (findings applied before merge), a grants-ledger row, role-matrix cases and live tests.

## Working rules adopted (marked PENDING COUNSEL where legal)
- **Owner-delete recommendation, working rule, pending counsel:** no deletes from append-only ledgers by anyone, including the owner, except the audited erasure path and, for transcript chunks, the audited purge of expired rows. Restore drills restore from backup into a separate empty project.
- **Placeholders (labelled, not decisions):** canary cap 20 (now a row in `docs.canary_settings`, seeded only by tests), care-gap alert age 14 days, voice transcript retention 30 days, and one demo care-gap rule in `config/demo-phase4.yaml`.

## Reviewer findings and what was done
- 0047/0048: the worker could never insert a sample (it cannot read `iam.clinics`): fixed with `ops.clinic_origin()`. Origins of staff inserts are pinned to the clinic. Rule numbers are bounded. `last_done_by` is recorded. Episodes for erased patients are refused. Function privileges are asserted for PUBLIC and the three Supabase roles.
- 0049: the claim now requires the NEWEST identity event to be a confirmation (a revoked record is not claimable) and the current care-delivery grant, and has a failure limit per account AND per phone token and clinic; registration no longer races.
- Left as they are, for the owner: no auto-approval path exists so the sampler samples nothing real; the spec names `assisted` as the default mode and this build uses `shadow`; a nurse or doctor may re-check their own sample.

## Deliberately not built (after the second batch)
The two-person `autonomous` switch; medications and notes; the database `aal2` check on the older tables (accepted residual); erasure and key rotation of care-plan text and results (needs a counsel decision on retention); a patient-facing channel for readiness replies and a real rebooking hand-off; any real model route.

## Second batch (2026-11-07): decisions applied
1. Visit Readiness built to mechanism level (0052): content is the required setting `CAREFLOW_VISIT_READINESS_CONTENT`; model roles are fakes.
2. The care-gap rule definitions, the care-plan loop, the readiness content and the re-check rule are clinician-owned items 27 to 30 in `docs/design/clinician-sheet.md`.
3. Care plans stay held for a doctor (no model route). Demo task settings: doctor, normal (`standard`) priority.
4. **Departure from the spec, recorded:** BUILD-SPEC.md names `assisted` as the default autonomy mode. This build keeps `shadow` as the no-row default (the most human mode). The stored mode can only tighten the configured policy, so a deployment whose runners are configured `assisted` is not forced to `shadow` by an absent row.
5. Front-desk resolution of portal claims is built (audited, aal2). A record with no identity event stays unclaimable until the existing confirm-identity route creates one.
6. Consent notice texts: required setting with a labelled demo placeholder; counsel owns the real text. Residual: any clinic admin with a second factor can publish platform-wide through SQL.
7. A sample must be re-checked by someone other than the decider, enforced in the database.
8. Reads of results, plan text, steps, waits and the visit readiness tables need aal2; the queue worker is exempt and has narrow permissive policies.
9. A stored autonomy mode can only make behaviour more human than the configured policy.
10. The database aal2 rework of the OLDER tables is skipped: an accepted residual.
11. A recorded result wakes its plan through the outbox: the result route writes the event in its own transaction, the tick relays it, and the worker identity resumes the plan (`care_plan_result_handler`).

Reviewer findings applied before merge: worker update policies narrowed to what the handler needs; approval re-checks consent; a retry of the same decision returns the stored outcome; the consent publish is serialised; the claim-request resolution columns carry a clinic-pinned key.

## Third batch (2026-11-08)
- **Consent notices:** `iam.publish_consent_notice` is dropped (0053). Publishing is by migration or the owner-run `scripts/seed_consent_notices.py`, which connects as the migrator, publishes only the NEXT version, never overwrites, and audits.
- **Worker reads of results:** narrowed to results an outbox event names or a waiting plan's open or claimed wait points to. Residual: the step guard's "not superseded" test runs as the caller and cannot see a superseding result the worker may not read.
- **Two-person switch to autonomous:** two DIFFERENT admins (the spec says an admin AND a doctor; the owner chose two admins). Off unless `CAREFLOW_AUTONOMOUS_MODE_ENABLED` is set; ignored in the clinical profile until `CAREFLOW_CLINICAL_AUTONOMY_SIGNOFF` is set. Inherent limit: one person who controls two admin accounts of the same clinic can satisfy it.
