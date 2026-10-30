# Review-task routes: list, open, acknowledge, resolve (hold release)

Owner instruction 2026-10-29 (3b): referral holds were resolved by direct database update because no route existed; the frontend needs list, open, decide and release. Built in `careflow/review_tasks.py` and the `/v1/staff/review-tasks` router. **No migration, no grant, no role, no policy**: every statement runs in the caller's own transaction under the row-level security of 0007, 0013 and 0020, and the only write is an update of the four columns `careflow_app` already holds (`status`, `acknowledged_by`, `acknowledged_at`, `resolved_at`).

| Route | Does | Refusals |
|---|---|---|
| `GET /v1/staff/review-tasks` | List what the caller may see (filters: status, kind, assigned role; immediate first, then oldest). No provenance in the list. | 403 unless the caller holds front desk, nurse, doctor or admin somewhere |
| `GET /v1/staff/review-tasks/{id}` | Open one task with its provenance (ids and closed words only, 0007) | 404 for an invisible task (same as missing), 403 as above |
| `POST .../{id}/acknowledge` | `open` to `acknowledged`, by the caller. The same person again is a replay. | 404, 403 (visible but this role may not change it), 409 (held by another person or closed) |
| `POST .../{id}/resolve` | `open` or `acknowledged` to `resolved`. **Releasing a referral hold is resolving its `referral_hold` task.** | as above, plus 409 for a kind that does not close here |

Both writes need `X-Idempotency-Key`, write one `audit.log` row (`review_task.acknowledge` / `review_task.resolve`: kind and replay flag only) in the same transaction, and a replay writes nothing more.

## What "decide" means here

The decision on a triage proposal already has its route (`POST /v1/staff/referrals/{document_id}/review`, which records the human decision and resolves the `triage_proposal` task). This change adds no second way to close that task. `resolve` only closes the kinds in `RESOLVABLE_KINDS`: `referral_hold`, `unmerge_review`, `referral_dead_letter`, `identity_confirmation_revoked`. Those are administrative closes with no judgement of their own.

## Left for a clinician to define (never closed quietly)

`red_flag` and the screening kinds are refused by `resolve` with a 409 ("closed through its own route"). What it takes to close a red-flag task (who, what must be recorded, whether a second person confirms) is a clinical-safety decision; until it is made, a nurse can acknowledge such a task but nothing here closes it. Added to the clinician sheet as item 25.

## Who can do what (from the existing policies)

| Role | Sees | May acknowledge or resolve |
|---|---|---|
| nurse, doctor | every kind of their clinic | any visible task (resolve: listed kinds only) |
| front desk | `unmerge_review`; its own `referral_hold`s (assigned to front desk) | its own holds only |
| admin | `unmerge_review` | nothing (403) |
| others | nothing (403) | nothing |

A task another person has acknowledged cannot be resolved by someone else (the update policy's WITH CHECK refuses it, reported as a 409).

## Tests

Offline `tests/test_review_tasks.py` (state rules, unlisted kinds never written, fail-closed mapping). Role matrix: a case set per route (every identity in the matrix, other-clinic reader, filters and paging, key and id validation, unauthenticated). Live audit test: one row per transition as the person, none for a replay or a refusal.
