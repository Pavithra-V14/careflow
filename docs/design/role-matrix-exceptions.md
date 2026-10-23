# Role-matrix exceptions

`CLAUDE.md` rule 8 says every new route adds its own case to the role-matrix test (`tests/test_api_role_matrix.py`) in the same change,
and that the matrix runs against the real restricted database role. `CLAUDE.md` itself is not edited; this page records the one
**documented, owner-approved exception** (decision of 2026-10-24).

## The exception: `POST /v1/staff/messages/{message_id}/screen`

**Why.** `tests/test_api_role_matrix.py` commits its fixture rows and deletes them afterwards. This route screens encrypted messages and
writes screenings and audit rows, and `msg.messages`, `msg.screenings` and `audit.log` are append-only for every role, including the table
owner, so a committed fixture could never be cleaned up.

**What replaces it.** `tests/test_api_screen_message_live.py` drives the real app over HTTP with real signed tokens and the real
`verified_claims`. Its database dependency is replaced by one shared connection inside a never-committed transaction: each request runs in a
savepoint with `SET LOCAL ROLE careflow_app` and the verified claims set (as `db_conn` does), so row-level security and `msg.record_screening`
are genuinely in the path. It covers 14 identities (every staff role, a worker, another clinic's staff, a nurse here who is a doctor
elsewhere, a patient, a guardian, invited and suspended accounts) across the three topics, plus authentication failures, validation,
replay, an erased message, a tampered body and an unloaded key version.

**The guard that keeps it honest.** The route is registered in `SEPARATE_HARNESS` in `tests/test_api_role_matrix.py`.
`test_every_route_is_in_the_matrix` fails if any served `/v1` route is in neither `MATRIX` nor `SEPARATE_HARNESS`, and
`test_separately_tested_routes_have_a_dedicated_matrix_that_covers_them` imports the dedicated module, requires it to export the roles it
covers (all of them), and requires the driving test to be a real parametrized function over exactly those roles.

**CI.** Both modules must run with **zero skips** (CI already fails on any skipped test). The dedicated module's only skip condition is a
missing `DATABASE_URL_MIGRATOR`, which CI provides. One test in `tests/test_rls_screenings_live.py` skips itself until migration 0013 is
applied; CI applies migrations before the tests.

## What the dedicated harness does NOT cover, and where that is covered instead

| Not covered by the dedicated harness | Covered by |
|---|---|
| The real transaction-mode pooler engine and the real restricted login (it uses `SET LOCAL ROLE` on a shared connection) | `tests/test_db_engine_config.py`, `scripts/verify_app_role.py`, and the committed-fixture matrix in `tests/test_api_role_matrix.py` for every route that can use it |
| Commit-before-response (a 200 means the write is durable; nothing commits here) | the scheduling write tests in `tests/test_api_role_matrix.py` (`test_a_write_is_committed_before_its_response_is_returned`) and the `TxConn` dependency (`scope="function"`) |
| The deferred "a patient-kind message needs a screening by commit" trigger (it only fires at commit) | the 0013 live tests in `tests/test_rls_screenings_live.py` (`SET CONSTRAINTS ... IMMEDIATE` inside the rolled-back transaction) |

## Rule for future routes

A route uses the normal committed matrix unless every row it writes can be deleted by the owner. A route that writes to an append-only table
(`msg.*`, `audit.log`) may use the dedicated-harness pattern, and must be added to `SEPARATE_HARNESS` with its roles listed in the module's
`COVERED` mapping. Nothing else may be exempted.
