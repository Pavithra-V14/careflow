# Build plan (2026-11-04)

Short. The long form is `remaining-work.md` (items A and B) and BUILD-SPEC.md Appendix D (phases). **BUILDABLE NOW** = needs no decision, new grant or role, external service, or clinician. **BLOCKED** names exactly what it waits for (D owner, C clinician item, P provider, S spike/samples, H hosting). Size S under a day, M one to three days, L more. Every route carries its role-matrix case, audit row (if it records a decision) and OpenAPI snapshot in the same change; every migration gets an rls-security-reviewer pass and goes first in the push queue (option A in `ci-sharding.md`).

## State
Phases 0 to 3 are built through the routes with fake engines at the edges; the active role (0028) and the demo wiring are merged or in the queue. Phase 2 and 3 acceptance evidence (A4, A5) is written. Everything real (engines, scanner, channel, hosting) and everything clinical is blocked below.

## Buildable now, in order of value to the clinic

| # | Package | Size | Depends on | Tests |
|---|---|---|---|---|
| 1 | **A8** reply interpretation, fakes only (branch `a8-reply-interpretation`, built) | L | none | order test (rules first), spend ceiling, matrix, live harness |
| 2 | **A9** message thread and header lists (no bodies) for front desk | M | none | matrix per message policies; no body or decrypted text can appear |
| 3 | **A15** confirm the reply-screening path and the nurse task | S | none | one live test, no new code expected |
| 4 | **A12** fewer round trips per request (claims `set_config` merged with the first query) | M | active role merged | the two pool tests, a round-trip count test |
| 5 | **A11** gate scorecard runner (MEASURED / NOT MEASURED with reason / FAILED, fails closed) | M | none | offline runner tests; CI prints it |
| 6 | **A14** metrics catalog to dashboard and alert files | M | none | every metric defined once; every alert names one |
| 7 | **A10** runbooks that need no outside contact (dead letters, wrong merge, model outage, key drill) | S each | none | owner review; drill checklist |
| 8 | **A13** shard balance (done for shards 5 and 6; re-measure) | S | PR #18 run | `test_ci_shards.py`, measured times |
| 9 | Retention purge **design** is approved, **code is not** (see B) | - | - | - |

## Blocked (the full list is `remaining-work.md` section B)

| Package | Blocked on |
|---|---|
| Revoke reasons list and re-check task (wire the demo stand-ins); closing a red-flag task; editing a triage proposal | C items 23, 24, 25, 26. Also: no front-desk supervisor role and no admin alert exist; adding either needs a design note and the owner's OK |
| Retention and erasure purge code | D grants sign-off; C/D retention periods |
| Red-flag rules, cards, gold sets, thresholds, priority levels, offer wording | C (clinician sheet) |
| Real engines (vision, decision, translation), three-engine contract gate | P approvals (hosted providers are approved for synthetic demo only; branch `c1-providers` ships in a later PR) |
| Offer delivery to patients, offer expiry job | D design, P messaging channel |
| Staff invitation and role management, clinic settings | D (new grants, Auth admin API) |
| Showing demographics to staff, patient list, fuzzy search | D (decrypted reads with logging) |
| Phases 4 (observability, cost) to 10 (pre-launch); Phases 6 to 8 flagged | P accounts, C content, H hosting, a first week of real traffic |
| Real storage and scanner | done for storage policy (0027); scanner D where the worker machine lives |

## Rules for the queue
One PR per CI cycle, migration PRs first, the rest merge `main` in after. A package is not done until its tests and lint are green offline and (for routes and migrations) its matrix case and reviewer pass exist. Anything that would need a blocked input is stopped and asked, not worked around.
