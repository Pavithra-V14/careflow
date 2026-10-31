# Live integration checklist (F5)

Every call each console screen makes, and what to check against a RUNNING backend. Until now everything was proven against
mocks written from `openapi.snapshot.json`; this list is how we find where the backend differs from them.

**Not started.** The live pass needs the backend running and a signed-in dev staff account (see "What I need" at the end).
Nothing here has been run against a live backend.

## How to run it (by hand, never in CI)

1. Start the backend, then the dev server (`npm run dev` in `frontend/`; it proxies `/v1` and `/readyz` to `CAREFLOW_API_BASE_URL`).
2. `npm run e2e:live`: opens a browser window. **You** sign in (email, password, authenticator code); nothing is typed for you and no
   credential reaches the script. It then walks every screen read-only and writes `frontend/live-report/live-report.md`.
3. `npm run e2e:live -- --record`: opens the console and records while you use it yourself, for the writes the walk never makes
   (search a patient, acknowledge, release a hold, record a decision, revoke). Press Resume in the inspector when finished.
4. The report lists, with no values (paths have ids removed, only field paths and type words): unknown routes, statuses the schema
   does not list, 5xx, writes without an idempotency key, schema errors per response, and the operations the run never made.
   Nothing in `live-report/` is committed (git-ignored).

The run refuses to start if the dev server or the backend does not answer. The isolation tests (`tests/live-tools.test.ts`) pin that
no workflow can start it.

## Calls per screen

"Handled" is what the screen does for each status. Every refusal shows a fixed message; the backend's text is never shown.

| Screen | Call | Sends | Handled | Check against the backend |
|---|---|---|---|---|
| Every staff screen after sign-in | `GET /v1/iam/me` | bearer token | 200 account; 403 "cannot use the console"; other 4xx/5xx "could not be loaded" | `roles[]` has `clinic_id`, `role`; are `clinic_name`, `clinic_origin` (`live`/`test`) and `operating_profile` present yet? (requests item 2) |
| Referrals list | `GET /v1/staff/referrals` | no query | 200; 403 not available; 5xx fixed message | `run_state` words seen; `proposal_visible`/`decided` for front desk vs nurse; `has_more` |
| Referral detail | `GET /v1/staff/referrals/{document_id}` | id | 200; 404; 403 | `holds[]`, `proposal` shape, `mode` words, `decided` null vs false |
| Referral decision (nurse, doctor) | `POST /v1/staff/referrals/{document_id}/review` | `X-Idempotency-Key`; body `{decision}` (`approve` or `reject` only) | 200; 409 "not waiting for a decision"; 403; 503 | replay (`replayed: true`) on a retried key; 409 when already decided |
| Upload step 1 | `POST /v1/staff/documents/uploads` | key; `{clinic_id, content_type, kind, size_hint}` | 200; 403; 422; 503 | `upload_url` host is the Supabase project origin; 503 text when storage is off |
| Upload step 2 | `PUT <upload_url>` (storage host, no bearer) | file, `Content-Type` | 2xx; else retry with same key | the storage accepts a raw PUT with this content type |
| Upload step 3 | `POST /v1/staff/documents/register` | **same key**; `{clinic_id, kind}` | 200 shows `scan`; 422 "not accepted"; 409; 503 | `scan` words: only `clean` shown as clean; what a real scanner reports |
| Task inbox | `GET /v1/staff/review-tasks?status=` | `status` (open, acknowledged, resolved) | 200; 403 | task `kind` words seen; ordering (immediate first) |
| Task detail | `GET /v1/staff/review-tasks/{task_id}` | id | 200; 404; 403 | `provenance` keys per kind (only plain values are shown; `document_id` is linked) |
| Acknowledge | `POST .../{task_id}/acknowledge` | key, no body | 200; 409; 403 | replay for the same person |
| Resolve | `POST .../{task_id}/resolve` | key, no body | 200; 409 (kinds that do not close here); 403 | which kinds really close; `red_flag` gives 409 |
| Patient search (front desk) | `POST /v1/staff/patients/search` | `{clinic_id, name, dob, phone?}`, **no key** | 200; 403; 422; 429; 503 | **do by hand** (it needs a real person's details): `label`, `labels_withheld`, `truncated` exist? (requests item 5); 429 behaviour |
| Patient record | `GET /v1/staff/patients/{patient_id}` | id | 200; 404; 403; 429; 503 | `latest_identity_event` words; `merged_into` |
| Confirm identity | `POST .../{patient_id}/confirm-identity` | key, no body | 200; 409; 403 | no step-up demanded |
| Revoke identity | `POST .../{patient_id}/revoke-identity` | key; `{reason}` | 200 shows `review_task`; 409; 403 | `review_task` words and what `on_record` means; reasons accepted |
| Scheduling lists | `GET /v1/staff/scheduling/providers`, `/locations`, `/visit-types`, `/priority-levels` | `include_inactive` only when ticked | 200; 403 | fields present; inactive filter |
| Holds | `GET /v1/staff/scheduling/holds?status=` | `status` (default active) | 200; 403 | status words |
| Release a hold | `POST /v1/staff/scheduling/holds/{hold_id}/release` | key, no body | 200; 409 (booked or expired, shown calmly); 403 | 409 wording |
| Offer draft | `GET /v1/staff/scheduling/offers/draft` | `clinic_id`, `visit_type_id` | 200 shows only `status`; 403; 5xx | `status` words; `reason` and `data` shapes (requests item 6) |
| Possible duplicates | `GET /v1/staff/identity/possible-duplicates?status=` | `status` (default pending) | 200; 403 (non front desk) | `reason`, `matched_kinds` words |
| Merges | `GET /v1/staff/identity/merges?status=` | `status` only when filtered | 200; 403 | status words |
| Merge detail | `GET /v1/staff/identity/merges/{merge_id}` | id | 200; 404; 403 | `proposed_by`, `second_approved_by` ids |
| Merge approve / unmerge (**flag OFF**) | `POST /v1/staff/merges/{merge_id}/approve`, `.../unmerge` | key | 200; 401 step-up; 409; 403 | only after the owner turns the flag on: step-up challenge with a real token, one retry, same key |

Operations in the client that no screen calls: `GET /readyz` (used only by the live-run guard through the proxy) and
`POST /v1/staff/merges` (propose; not built). The snapshot has more routes (messages, bookings, waitlist, slots); no console screen
uses them yet.

## Also verify by hand (the walk cannot)

- The step-up challenge on a merge route with a real token carries `WWW-Authenticate: step-up` and the browser can read it (through
  the dev proxy it can; in production CORS must expose it).
- A 429 on patient search: does the backend send `Retry-After`? (The console shows a fixed message and ignores it.)
- An expired session in the middle of a screen sends the person to sign-in with "Please sign in again".
- The `X-CareFlow-Active-Role` header is NOT sent (flag off), and the backend ignores it if it were.
- `GET /readyz` reports the operating profile; the console shows "Operating profile unknown" until `/me` or `/readyz` carries it.

## Data the pass needs in the dev project (ask the backend session for a seed)

At least one of each, in a single clinic the test account can read: a referral document (and one with an open hold), a review task
that is open, one acknowledged and one resolved, a `referral_hold` task, a `red_flag` task (nurse or doctor account), a patient
record that is verified and one that is not, a pending possible duplicate, a merge awaiting second approval proposed by ANOTHER
account, an active merge, a visit type, a provider, a location, a priority level and an active hold. A nurse or doctor account
and a front-desk account, to see both sides of every role-dependent screen.

## What I need to start

1. The backend running, and `CAREFLOW_API_BASE_URL` in `frontend/.env.local` pointing at it (the dev proxy uses it).
2. A dev staff account for you to sign in with; I will not see credentials. A second account with another role is better.
3. The seed above (or tell me which rows exist).
4. Your OK to open the headed browser window on your machine, and to leave the dev server running.
