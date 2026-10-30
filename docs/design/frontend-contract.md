# Frontend contract: routes in the OpenAPI snapshot, and what the frontend still needs

Prepared 2026-10-30 for the frontend session (read-only for the frontend: `frontend/` is not touched by backend work). Source of truth: `openapi.snapshot.json`, regenerated with `python scripts/export_openapi.py` and checked by `tests/test_openapi_snapshot.py` (a route or schema change without a regenerated snapshot fails CI).

## Is the snapshot current?

Yes, **as of branch `batch3-revoke-tasks-offers` (PR #9, CI running)**: the snapshot was regenerated after the revoke, review-task and offer routes were added and again after main (PR #8) was merged in; it did not change on the merge, and the snapshot test passes in the 1257-test offline run. Main itself (through PR #8) does NOT yet contain the seven routes added by PR #9 (marked below); the snapshot on main becomes current for them when PR #9 merges.

## Routes in the snapshot (25 operations)

| Method | Path | Summary |
|---|---|---|
| GET | `/readyz` | Readyz |
| GET | `/v1/iam/me` | Me |
| POST | `/v1/staff/merges` | Propose Merge |
| POST | `/v1/staff/merges/{merge_id}/approve` | Approve Merge |
| POST | `/v1/staff/merges/{merge_id}/unmerge` | Unmerge |
| GET | `/v1/staff/messages/{message_id}` | Read Message |
| POST | `/v1/staff/messages/{message_id}/screen` | Screen Message |
| POST | `/v1/staff/patients` | Register Patient |
| POST | `/v1/staff/patients/{patient_id}/confirm-identity` | Confirm Patient Identity |
| POST | `/v1/staff/patients/{patient_id}/revoke-identity` | Revoke Patient Identity |
| POST | `/v1/staff/referrals/{document_id}/review` | Review Referral |
| POST | `/v1/staff/referrals/{document_id}/run` | Start Referral Run |
| GET | `/v1/staff/review-tasks` | List Review Tasks |
| GET | `/v1/staff/review-tasks/{task_id}` | Open Review Task |
| POST | `/v1/staff/review-tasks/{task_id}/acknowledge` | Acknowledge Review Task |
| POST | `/v1/staff/review-tasks/{task_id}/resolve` | Resolve Review Task |
| GET | `/v1/staff/scheduling/bookings` | List Bookings |
| POST | `/v1/staff/scheduling/bookings/{booking_id}/cancel` | Cancel Booking |
| GET | `/v1/staff/scheduling/offers/draft` | Draft Offer |
| POST | `/v1/staff/scheduling/offers/send` | Send Offer Route |
| GET | `/v1/staff/scheduling/slots` | List Slots |
| POST | `/v1/staff/scheduling/slots/{slot_id}/book` | Book Slot |
| POST | `/v1/staff/scheduling/slots/{slot_id}/hold` | Hold Slot |
| GET | `/v1/staff/scheduling/waitlist` | List Waitlist |
| POST | `/v1/staff/scheduling/waitlist/join` | Join Waitlist |

New in PR #9: `revoke-identity`, the four `review-tasks` routes, `offers/draft`, `offers/send`.

## Conventions the frontend can rely on

- Auth: a Supabase access token in `Authorization: Bearer`; `GET /v1/iam/me` returns the account, status and the clinic roles it holds (with clinic ids). Merge routes need a second factor verified within the step-up window (401 otherwise; the claim shape is still an unverified spike, `step-up-spike.md`).
- Every write needs `X-Idempotency-Key` (a blank or missing key is a 422); a replay answers the original with `replayed: true`.
- Errors are fixed bodies: `{"detail": "not found"}` (also for a record in another clinic: invisible and missing are one 404), a fixed 403, a fixed 409 per route, and for 422 a list of `{type, loc, msg}` with no echoed input. These error bodies are not declared in the OpenAPI document (only 422 is); the per-route status meanings are in each route's docstring.
- Responses carry ids, closed words and times only. Patient names and dates of birth are encrypted at rest and **no route returns them**.
- CORS origins come from configuration; in `clinical` an empty list means none.
- Lists use `{items, limit, has_more}` with `limit` 1 to 500.

## What the frontend will need that does not exist yet

Grouped by the staff-console packages. None of these is a decision for the frontend session; each is a backend package for the owner to schedule. Items marked (D) need an owner decision first, (C) a clinician.

**Patients and identity** (lookup and status added on branch `batch4`, not yet merged)
1. Find a patient: `POST /v1/staff/patients/search` (exact name + date of birth, optional phone; front desk) returns ids, the kinds that agreed, `verified` and `registered_via`. **It returns no name, date of birth or phone**, and fuzzy or partial search does not exist (tokens are keyed hashes). Showing a stored name or a patient list needs an owner decision on what a staff session may see decrypted, with read logging (D). Every search writes one audit row (who, role, the kind of search, the result count; never the query or the results). The field list for decrypted values per role is a proposal awaiting approval: `patient-view-fields.md`.
2. `GET /v1/staff/patients/{id}`: `verified`, `registered_via`, the latest confirm/revoke event and its time, and `merged_into` (act on that record). The confirm and revoke buttons can use it.
3. List of possible duplicates (`mpi.match_candidates`) and of merges (propose/approve/unmerge exist, but there is no way to list or open a merge or a candidate): still missing.

**Scheduling**
4. Lists the booking screens need: providers, locations, visit types and priority levels (ids exist in slot rows; names do not).
5. Bookings and waitlist rows carry patient ids but no display name (see item 1) and no visit-type/provider names.
6. Releasing a hold on purpose (`release_hold` is in the design but there is no route; holds expire by time only), and listing current holds/offers.
7. Slot creation and editing for an admin (no route).

**Referrals and documents** (upload and queue routes added on branch `batch4`, not yet merged)
8. Upload: `POST /v1/staff/documents/uploads` (a signed URL) and `POST /v1/staff/documents/register` (same idempotency key) exist and are role-matrix covered, but they answer **503 until a real storage adapter and its bucket policy exist** (an owner decision; the matrix runs them against a fake storage in `demo`). Uploads are also refused (503) outside `demo` until a malware scanner is wired.
9. Referral queue: `GET /v1/staff/referrals` and `GET /v1/staff/referrals/{document_id}` exist: run state, holds you may see, and for a nurse or doctor the triage proposal (card ids and versions). Front desk cannot read proposals, so `proposal_visible` is false for it and the proposal fields mean "you cannot see", not "none".
10. A signed READ url for the document the reviewer is looking at: still missing (needs the storage adapter).

**Review tasks (new in PR #9)**
11. The task list has no patient or document reference except inside `provenance` on the detail route (ids only). A reviewer opening a hold needs "which referral"; that is `provenance.document_id` for holds today. Confirm the shape per kind with the frontend session before building the screen.
12. Closing a `red_flag` task has no route, by design, until a clinician defines what closing requires (clinician sheet item 25).

**Messages**
13. Thread and message lists (only a single message read and the screening exist); a staff "send a message" route other than the offer; reply interpretation (blocked on the spend ledger, `spend-ledger-options.md`).

**Admin and audit**
14. Staff invitation and role management (done by script and runbook only), clinic settings and the autonomy mode, and an audit-log reader for the auditor role (RLS allows it; no route).
15. The Live/Test dashboard and metrics (generated from `metrics-catalog.yaml`; no route).

**Patient portal**: no route at all (Phase 1 portal backend package not scheduled; needs the consent, activation and guardian tables).

## Suggested order for the next backend packages (a recommendation, the owner decides)
Items 8 and 9 (upload and a referral queue) unblock the Front Door screens; items 1, 2 and 3 unblock identity work; item 4 is small and unblocks scheduling forms; the rest follow the console packages.
