# Step-up re-authentication: design and the required spike (BUILD-SPEC 18.1, Appendix B)

**Status (updated 2026-11-04): step-up implemented; the claim shape was VERIFIED on a real dev-project token on 2026-10-30** (recorded in `docs/frontend-backend-requests.md` section 4: `aal` "aal2", `amr` entries with exactly `method` and `timestamp`, `totp` present, integer unix seconds, a refresh keeps the stamp).
The merge routes (`POST /v1/staff/merges`, `.../{merge_id}/approve`, `.../{merge_id}/unmerge`) refuse any request that is not stepped up.
**Still NOT covered by that capture:** the legacy string `amr` (not seen), a token from a later sign-in challenge, a token older than the window, and one live merge call end to end. The tables below keep the original assumptions; read the "Verified" column with this paragraph.
The same shape rules now also gate EVERY staff route (`careflow.auth.mfa_failure`, setting `CAREFLOW_REQUIRE_AAL2`, default on) (`careflow/app.py` `_require_mfa`). The three merge route docstrings in `careflow/app.py` still say "pending live verification" because they are copied into the OpenAPI document the frontend's generated types follow; they change with the next schema regeneration.

## What the code does

`careflow.auth.step_up_failure(claims, max_age_seconds, now)` is a pure function. A request is stepped up only if all hold:

1. `aal == "aal2"` (exact string);
2. `amr` is a non-empty list whose every entry is exactly `{"method": <str>, "timestamp": <int>}` (no other keys, no strings,
   no floats, no booleans);
3. at least one entry has `method == "totp"`; the newest such `timestamp` is not in the future (no skew allowance) and is no
   more than `CAREFLOW_STEP_UP_MAX_AGE_SECONDS` seconds old.

Anything else, including anything missing or malformed, is **not** stepped up. The dependency `require_step_up`
(`careflow/app.py`) runs after authentication and before any database work. A refusal is one fixed `401` body,
`{"detail": "step-up required"}`, with `WWW-Authenticate: step-up`. The reason (a code such as `aal_not_aal2`,
`amr_malformed`, `timestamp_expired`; never a claim value) goes to an info log line.

`CAREFLOW_STEP_UP_MAX_AGE_SECONDS` is required and has no code default. While it is unset, zero or negative, the merge routes
answer a visible `503` and log an error. `.env.example` sets it to `300` for dev and CI. It is a security setting, not a
clinical one.

`_totp_verified_at` in `careflow/auth.py` is the only function that knows the `amr` shape, so a correction after the spike is a
one-place change (plus the matching tests in `tests/test_auth_step_up.py`).

## What is unverified (Appendix B, a required spike)

From Supabase Auth's source and documentation (looked up through Context7), not from a real token:

| Claim | Source | Verified against a real token? |
|---|---|---|
| `aal` is `"aal1"` after a normal sign-in and `"aal2"` after a verified second factor | Supabase MFA guide | **No** |
| `amr` is a list of method entries with a verification timestamp | Supabase Auth source (`AMRClaim`) | **No**: the exact entry keys (`method`, `timestamp`) |
| `timestamp` is an integer count of unix seconds | assumed | **No**: could be another unit or type |
| The TOTP method is named `"totp"` | Supabase Auth source | **No** |
| A token refresh keeps the original `timestamp` (it does not move) | Supabase Auth source | **No** |
| Legacy tokens may carry plain strings in `amr` (the check rejects these) | Supabase Auth source | **No** |

### The spike (owner action)

1. On the **dev** project only, enrol a TOTP factor on a **dev staff account** (synthetic data only).
2. Sign in, complete the factor challenge, and decode the resulting access token **payload** (not the signature).
3. Paste only the `aal`, `amr` and `iat` claims into the conversation. Do not paste the token itself, and never a key.
4. Then refresh the session once and paste `amr` again, to confirm the timestamp does not move on refresh.

When that is recorded, correct `_totp_verified_at` if the shape differs, and update this page's table. Only then may the step-up
check be called verified.

## Not in this step (deferred, see `deferred-items.md`)

- Enforcing MFA enrolment at first staff login, and an `aal2` restrictive policy in the database.
- The other sensitive actions BUILD-SPEC 18.1 lists (publish card/rule, autonomy-mode change, erasure, role change). They
  reuse `require_step_up` when their routes exist.
- A clock-skew allowance for a freshly issued timestamp. It is zero today; decide after the live shape is known.
- The Supabase `reauthenticate` endpoint (an emailed nonce, used for password change) adds no `amr` entry, so it is not used.
