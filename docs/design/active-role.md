# Active role: narrowing a caller's grants per request

**Status: design proposal. No code, no migration, no grant. Waiting for the owner's OK.**
Raised by the frontend (F2, `docs/frontend-backend-requests.md` item 3). BUILD-SPEC 19.3 says a staff user with several roles
"picks the active one rather than holding all permissions simultaneously" (least privilege). Today the staff console shows that
choice but the API ignores it: every request is authorised against **all** of the caller's live grants.

## 1. How authorisation works today (read from the code, so the design fits it)

- The token carries identity only (`sub`, `aal`, `amr`, ...). **No role or clinic is in the token.**
- `db_conn` (`careflow/app.py`) opens one transaction per request and calls `set_request_claims`, which runs
  `select set_config('request.jwt.claims', :c, true)` with the verified claims as a bound parameter. `auth.uid()` then resolves
  to the caller. Everything below reads grants from `iam.staff_roles` joined to `iam.accounts` (active, `account_type = 'staff'`,
  `revoked_at is null`).
- **Row-level security** reaches grants through two `SECURITY DEFINER` helpers: `iam.has_clinic_role(clinic, roles[])` (0001) and
  `iam.has_any_role(roles[])` (0007). About a hundred policy and function call sites across 14 migrations use them.
- **Direct readers that do not go through the helpers** (each would silently ignore a narrowing unless changed):
  `GET /v1/iam/me`, `_readable_clinics` (the scheduling lists), the `register_patient` gate, `_front_desk_gate` (the merge routes) and
  the role lookup in `messages.read_stored_message`, all in Python; and in SQL the `ai.model_registry_check_approval` trigger (0016).
  An inventory test (section 8) must keep this list honest.
- `audit.log` already has an `actor_role` column. Only `messages.py` fills it today (the highest-priority eligible role). The
  `msg.record_screening` audit insert (0013) does not set it.
- There is no CORS code in `careflow/` yet (the backend session is adding it).

## 2. Recommendation

Add one optional request header, **`X-CareFlow-Active-Role: <clinic_id>:<role>`**, that can only **narrow**.

1. A dependency parses the header strictly, then checks, **inside the request transaction and against the caller's actual live
   grants in the database**, that the caller holds exactly that grant (account active, staff, grant not revoked).
2. On success it writes a **second** claim into the claims JSON that `set_config` binds, `careflow_active = {"clinic_id", "role"}`.
   Identity (`sub`, so `auth.uid()`) is untouched, and the claim is overwritten by the server: any `careflow_active` already in the
   token is dropped first.
3. The two SQL helpers gain one extra condition, so every policy that uses them narrows with no per-policy change: when the claim
   key is present, a grant counts only if it equals the active `(clinic_id, role)`.
4. The five direct readers stop re-implementing the check and call the helpers instead (`select iam.has_clinic_role(:c, :roles)`;
   a small new `iam.effective_roles(clinic)` for the one that needs the role name). One definition of "effective role", in SQL.
5. Absent header means exactly today's behaviour (all grants). This is **phase 1**. **Phase 3** (a decision for the owner, section 7)
   makes the header required for accounts that hold more than one live grant, so least privilege stops being opt-in.

This keeps `auth.uid()` as the only identity mechanism, so it is not the custom session variable rejected in BUILD-SPEC Appendix A.
The claim never identifies anyone and never grants anything; it only removes.

## 3. Where it is applied: the `set_config` claims

Sequence in `db_conn`, still one transaction, every value a bound parameter:

1. `set_config('request.jwt.claims', <verified claims>, true)`: identity, exactly as now (RLS needs it to read `staff_roles`).
2. If the header is present: one query for the caller's live grants (a handful of rows). Header not in that set means the fixed
   refusal (section 4), before the route body runs, so nothing was written and the transaction rolls back.
3. `set_config('request.jwt.claims', <verified claims + careflow_active>, true)`: replaces step 1 for the rest of the transaction.
   Transaction-local, like today, so a pooled connection carries no active role into the next request.

The header value is never concatenated into SQL: it is parsed to a UUID and an allowlisted role name, and the claims JSON is built
with `json.dumps`.

**SQL semantics (fail closed).** Presence of the key `careflow_active` turns narrowing on, even if its value is `null` or malformed.
A malformed value matches no grant. The only way narrowing is off is the key being absent, and only the server's dependency
decides that.

**Routes that apply it:** the staff routers (`/v1/staff/*`). **`GET /v1/iam/me` deliberately ignores the header** and always
returns all live grants: the console needs the full list to offer the picker, and needs `/me` to keep working when its remembered
role has been revoked. Patient-portal routes (when they exist) do not read it.

## 4. A role the caller does not hold: one fixed refusal

Every failure collapses to one response, with no reason and no echo of the header: **`403`, body `{"detail": "active role not available"}`**.
Covered by that one body: header malformed, role not held, role held at another clinic, grant revoked, account invited or suspended,
account not staff, duplicate header. The log gets one line with the account id and a reason code (never the header value).
No audit row is written for a refusal (same as refused reads today).

It is a 403 and not a 401 on purpose: the console treats any other 401 as "session over" and signs the person out. The fixed body lets
the console recognise this case and send the user back to the role picker.

## 5. Single-role users

No header needed. If one is sent it is verified like any other. For phase 3, "needs the header" means "holds more than one live
grant", read from the same grants query, so there is no extra round trip. The console sends the header only when it knows an active role
(always for multi-role users, and may for single-role users; both are accepted).

## 6. Audit rows

- `audit.log.actor_role` records the **active role** (the effective role actually used), not "the highest priority role held". Today's
  `read_stored_message` already picks one role; with narrowing the eligible set shrinks, so it picks the active one when it qualifies.
- Add `details.role_scope = "active"` (header used) or `"all_grants"` (no header), so an auditor can tell a narrowed action from an
  unscoped one, and the owner can measure adoption before phase 3. `details` is the existing jsonb column; it holds no patient text.
- SQL writers (`msg.record_screening` and any later one) must set `actor_role` from a new `iam.active_role()` helper.
  A test fails if a staff write route inserts into `audit.log` with a null `actor_role`.
- The header is not part of any idempotency key, but **authorisation runs before a replay is returned**: a replay made under a
  different active role is gated like any other request (test it; do not assume it).

## 7. Step-up routes

- Order: authenticate, then **syntax check of the header** (no database), then `require_step_up` (the `aal` / `amr` check, still before
  any database work, still the same 401 with `WWW-Authenticate: step-up`), then `db_conn` (identity claims, grant verification, narrowed
  claims), then the route's own gate.
- Step-up is a property of the **token**, not of the role. Narrowing never changes it, and it is never used to infer a role.
- The merge routes' gate (`_front_desk_gate`) then applies to the **active** role. A user with `front_desk` and `doctor` at a clinic who
  sends `doctor` is refused on the merge routes even after stepping up. That is the point.
- The console's step-up retry clones the original request, so it resends the same header. Test that a retried request carries it.

## 8. Role-matrix cases (CLAUDE.md rule 8: same change as the code)

For every staff route in `MATRIX` and in `SEPARATE_HARNESS` (screen, read-message), add a header dimension, driven against the real
restricted role:

1. **No header:** results identical to today (regression pin).
2. **Valid header, grant that allows the route:** same result as no header.
3. **The narrowing proof:** a caller holding two roles at one clinic, one allowed on the route and one not. Header = the allowed role:
   allowed. Header = the other role: refused as a no-access caller is refused (that route's own 403 or 404). No header: allowed (phase 1).
4. **Cannot be held:** other role, other clinic, revoked grant, invited and suspended accounts, a patient and a guardian account, a
   `queue_worker`: all give **byte-identical** `403 active role not available`, no side effect, no audit row.
5. **Malformed:** empty, wrong separator, upper-case role, whitespace, a non-UUID, a very long value, SQL metacharacters, a duplicate
   header: the same fixed refusal.
6. **Cannot widen:** a caller with a narrow role names a broader role they do not hold (including one held at a different clinic): refused.
7. **Clinic narrowing:** a caller with `nurse@A` and `admin@B` and header `nurse:A` sees nothing of clinic B on any route.
8. **Step-up routes:** the four orderings (not stepped up with a bad header, not stepped up with a good one, stepped up with a bad one,
   stepped up with a role the gate refuses) each give the pinned status.
9. **`/me` ignores the header:** returns every grant, even with a header naming a revoked grant.
10. **No leak across pooled requests:** extend `test_identity_never_leaks_between_pooled_requests` so an active role set on one request is
    absent on the next on the same connection.
11. **A forged `careflow_active` claim inside a token** is overwritten, and a database test sets the claim directly: key present with
    `null`, with a wrong type, with a clinic the caller lacks, all grant nothing.
12. **Inventory guard:** a test greps `db/migrations` and `careflow/` for `iam.staff_roles` readers and fails on any not on a short
    allowlist (the helpers, `/me`, the audited exceptions such as the 0016 trigger). A new direct reader cannot bypass narrowing quietly.
13. `test_every_route_is_in_the_matrix` already fails on a missing route; extend it so a staff route without the header dimension fails too.

## 9. Anything that could make it widen access, and the guard for each

| Risk | Guard |
|---|---|
| Treating a malformed or unexpected claim as "no narrowing" (fail-open) | key presence turns narrowing on; malformed matches nothing; tested at the database (8.11) |
| Trusting the header without checking the database, or checking it against the token (which has no roles) | verification is a query against live `staff_roles`; refusal otherwise |
| A direct reader that bypasses the helpers, so narrowing silently does not apply | helpers used everywhere (section 2.4) plus the inventory guard (8.12) |
| Narrowing only in the Python layer, not in RLS | the claim lives in the same `request.jwt.claims` RLS already reads, via the two helpers |
| A forged `careflow_active` in the token, or a Supabase hook adding claims | server drops any incoming key and always writes its own; it could only narrow anyway |
| Header value reaching SQL as text | parsed to UUID plus allowlisted role; bound parameters only |
| Two `set_config` calls leaving the first value in force | the second replaces the first; transaction-local; covered by 8.10 |
| A `SECURITY DEFINER` function reading grants with its own query | each is listed in the inventory; `iam.effective_roles` and `iam.active_role` are the only way a function learns the role |
| Replay of an idempotent write under a different role | authorisation before replay (section 6, tested) |
| A role granted by a *different* account (for example the approver in the 0016 trigger) being narrowed by the caller's active role | the trigger keeps its meaning: narrowing applies only to checks about the caller (`auth.uid()`), not to a named other account. Listed explicitly in the inventory |
| `admin_of_account` / `has_clinic_role(..., 'admin')` now needs the **active** role to be admin | intended, and a visible behaviour change (section 10) |
| Background workers and the queue (no request, no claim) | unchanged: no key, no narrowing; they act as their own `queue_worker` account, not a human's grants |
| Header sent by an attacker | it can only reduce the sender's own access: an attacker holding the token already holds those grants |

## 10. Behaviour changes to confirm

- With a header, **list routes show only the active clinic** (today `_readable_clinics` unions every clinic where the caller holds a
  reader role). That is the intended "one clinic and role at a time" session, but it is a visible change for multi-clinic staff.
- An admin acting under a non-admin active role loses admin reads for that request (section 9, `admin_of_account`).
- Production CORS must list `X-CareFlow-Active-Role` in `Access-Control-Allow-Headers`, or the browser blocks every staff request
  at preflight. Add it to the CORS request already open (item 1 in `docs/frontend-backend-requests.md`).

## 11. Alternatives considered

- **Put the role in the token** (a Supabase custom access-token hook). Rejected: a role switch would need a token refresh; it ties
  authorisation to a hook configuration that is not verified (BUILD-SPEC Appendix B); and the token would then claim authority the
  database has not checked. The header plus database verification keeps the database the single source of grants.
- **A custom session variable** (`app.role`). Rejected: BUILD-SPEC Appendix A. The claim approach reuses `request.jwt.claims`.
- **Per-role tokens or a separate sign-in per role.** Rejected: multiplies second-factor prompts for no gain.
- **Enforce only in the API layer.** Rejected: RLS would still see all grants, so a missed Python gate would be an unnarrowed path.

## 12. Rollout and decisions for the owner

1. **Backend, one change:** a new migration (never edit a merged one) that `create or replace`s the two helpers and adds
   `iam.effective_roles` and `iam.active_role`; the dependency; the five readers moved onto the helpers; the audit fields; the tests
   in section 8. Phase 1 changes no result for a request without the header.
2. **Frontend, small:** send the header on staff API calls from the stored active role; on the fixed 403 body, return to the picker.
   Not started, and it cannot be useful before the backend change.
3. **Decisions needed:** (a) the header name and `<clinic_id>:<role>` format; (b) `403` with the body in section 4; (c) `/me` exempt;
   (d) list routes narrowing to the active clinic; (e) `details.role_scope` in audit rows; (f) **when to make the header required for
   multi-role accounts (phase 3)**. Recommendation: after the console ships it and `role_scope` shows real use, so a stale client
   cannot lock a user out on the day it lands.

**Recommended:** the design above. It adds no new identity mechanism, keeps the database as the source of grants, can only remove
access, fails closed, and is testable per route with the role matrix that already exists.
