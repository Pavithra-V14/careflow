# Role-matrix exceptions

`CLAUDE.md` rule 8 says every new route adds its own case to the role-matrix test (`tests/test_api_role_matrix.py`) in the same change,
and that the matrix runs against the real restricted database role. `CLAUDE.md` itself is not edited; this page records the one
**documented, owner-approved exception** (decision of 2026-10-23).

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

## Not an exception: the merge routes

`POST /v1/staff/merges`, `.../{id}/approve` and `.../{id}/unmerge` write only `mpi.merges` and `agents.review_tasks`, which the owner can
delete, so they use the normal committed matrix in `tests/test_api_role_matrix.py` (cleanup deletes `mpi.merges` and `agents.review_tasks`
before `mpi.match_candidates` and `mpi.patients`). Every identity is tried with a stepped-up and a not-stepped-up token.

## The split matrix: `GET /v1/staff/messages/{message_id}` (staff message read, decision of 2026-10-23)

**Why a split.** Reading needs stored messages, and `msg.threads` and `msg.messages` are append-only for every role, including the table
owner, so a committed fixture could never be cleaned up. The audit row the route writes is a second append-only table, but it is not what
forces the split. This is NOT the D1 exception reused: the route is tested in two halves and both must exist.

**Committed half** (`tests/test_api_role_matrix.py`, entry `("GET", READ)` in `MATRIX`; real pooler role, real commits). It needs no stored
message: every seeded identity plus a valid user with no account asking for a message that does not exist gets the one fixed `404`;
authentication failures are `401`; a malformed id is `422`; and `test_refused_message_reads_write_no_audit_row` proves a refusal writes no
`audit.log` row.

**Dedicated half** (`tests/test_api_read_message_live.py`; one shared connection in a never-committed transaction, `SET LOCAL ROLE
careflow_app`, real signed tokens, the real auth dependency; it reuses the world fixture of the screening module, not its exception). It
covers every required role on all three topics (nurse, doctor and a nurse-here-doctor-elsewhere read everything, front desk reads
appointments and safety, every other role, inactive account and other clinic gets the same `404`), the audit row of each `200` (ids only,
the authorising role, a server-made `message-read:<uuid4>` key, none for a refusal), `no-store`, `410` for an erased message, the fixed
`500` and `503` for a tampered or unreadable body, and that no body text reaches a log or an error.

**The guard.** `SPLIT_HARNESS` in `tests/test_api_role_matrix.py`. `test_every_route_is_in_the_matrix` requires a split route to also be in
`MATRIX`, and `test_split_routes_have_both_halves_and_the_dedicated_half_covers_every_role_on_every_topic` imports the dedicated module and
requires it to export `COVERED_GRID` covering every required role on every topic, the driving test to be a real parametrized function over
exactly those roles, and the audit, refusal, erased and authentication tests to exist.

**What the dedicated half does NOT cover, and where that IS covered**

| Not covered by the dedicated half | Covered by |
|---|---|
| The real transaction-mode pooler engine and restricted login, **for the success path** (the role lookup, the decrypt and the audit INSERT on the real pooler login, and a `200`) | **Not covered by any test.** The committed half runs this route on the real pooler role but only reaches the first SELECT, which returns no rows (random message ids), so it proves authentication, validation, the fixed 404 and that a refusal writes no audit row, not the success path. The role and its grants are checked separately by `tests/test_db_engine_config.py` and `scripts/verify_app_role.py`. Closing this gap needs a committed message fixture, which append-only `msg.*` tables do not allow. |
| Commit-before-response (a `200` means the audit row is durable) | Not tested for this route. It rests on the `TxConn` `scope="function"` dependency, which `test_a_write_is_committed_before_its_response_is_returned` proves for the scheduling writes. `tests/test_message_read.py` uses a fake connection, so it proves only the ORDER of the calls (find, role, decrypt, audit INSERT last), not any commit. |
| The `audit.log` append-only trigger | `tests/test_rls_iam_audit_live.py` |

Both halves must run in CI with zero skips.

## Rule for future routes

A route uses the normal committed matrix unless every row it writes can be deleted by the owner. A route that writes to an append-only table
(`msg.*`, `audit.log`) may use the dedicated-harness pattern, and must be added to `SEPARATE_HARNESS` with its roles listed in the module's
`COVERED` mapping. Nothing else may be exempted.
