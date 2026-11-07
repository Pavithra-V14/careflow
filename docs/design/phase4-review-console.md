# Phase 4: the review console, canaries, monitoring, learning labels, observability, watchdog, cost canvas (as built 2026-11-06)

MECHANISM level on synthetic data, with fakes and required settings. Spec: 12.2-12.6, 13.1-13.5, 15.1. Migrations 0039, 0040 (each reviewed; ledger rows added). No external service was added: Langfuse, Grafana, Slack and e-mail are ports with in-memory fakes only (rule 13).

## What exists

| Piece | Where | Real or fake |
|---|---|---|
| Reviewer view (proposal, evidence, alternatives, approve/reject effects, engine, confidence, rule), unknown shown as null | `careflow/review_console.py`, `GET /v1/staff/referrals/{id}/review-view` | real |
| Reason-code picklist | `CAREFLOW_REVIEW_REASON_CODES`; `reason_code` on a review decision (0039); `GET /v1/staff/console/reason-codes` | real; the list is the clinic's |
| Deadlines and escalation nurse -> doctor -> admin; nothing approved by timeout | `review_console.escalations`, watchdog | real; the deadlines are a required setting |
| Canary tasks (rate, fabricated letter, silent admin alert on approval) | `careflow/canary.py`, `_canary_check` in the review route, `ops.alerts` (0039) | the alert path is real; DEMO PROFILE ONLY, `careflow/canary_injection.py` (worker-tick sweep) registers a canary document through `docs.register_canary_document` (0042) and enqueues a normal Front Door run with origin `canary`, at the configured rate per live review task |
| Reviewer statistics and rubber-stamp verdict | `agents.reviewer_stats` (0040), `careflow/reviewer_stats.py`, `GET /v1/staff/console/reviewer-stats` | real; thresholds are a required setting |
| Two-person rules table | `careflow/two_person.py` | the four rules; merge and erasure are enforced in the database already |
| Learning labels (human decisions only), pseudonymized export, ~5% after-the-fact sample | `careflow/learning_labels.py` | pure functions; no export route or file is written |
| Observability ports, metadata-only capture, label allowlist, bounded non-blocking exporter, sampling | `careflow/observability.py` | fakes only |
| In-app watchdog and alert routing | `careflow/watchdog.py`, `careflow/phase4_sql.py`, a sweep on the worker tick | in-app channel real; e-mail and Slack are ports with fakes (a missing channel raises a `delivery_failed` admin alert) |
| Alerts list and acknowledge | `GET /v1/staff/console/alerts`, `POST .../alerts/{id}/acknowledge` (audited as `alert.acknowledge`) | real |
| Cost canvas W x A x U + P + F | `careflow/cost_canvas.py`, `GET /v1/staff/console/cost-canvas` | real arithmetic from the spend ledger and completed jobs; retry cost and reviewer minutes are not measured yet, so F and the total are null with the reason |

## Rules honoured

- Nothing is invented clinically: reason codes, deadlines, watchdog limits, canary rate, rubber-stamp thresholds, sampling rates, the pseudonym key, platform floor and reviewer rates are all required settings with no default; unset means the feature says `not configured` and does nothing.
- Unknown is not a value: a missing probability or confidence is null, a task kind with no deadline never escalates and is reported, a term that cannot be measured is null.
- The canary is invisible to the reviewer: the view omits the flag, the alert is aimed at admin only, and the policy gives the reviewer no way to read it.
- The watchdog reads state and writes alerts only; the escalation module imports no writer (a structural test pins it).
- Every new route has role-matrix cases in the same change; the live tables are in `tests/test_alerts_live.py`.

## Residual / open

1. Nothing yet decides to INJECT a canary into the live queue (the generator, the rate and the alert path exist; the producer needs the clinic to set the rate and a place in the Front Door to enqueue a canary document).
2. The free-text note beside a reason code is not stored (it could hold patient text).
3. Reviewer minutes and retry cost are not measured, so the cost canvas total stays null until they are.
4. `acknowledged_at` on a review task is set by whoever acknowledged it, so a time-to-decide figure can be shortened (floored at 0).
5. Langfuse, Grafana, Slack and e-mail each need an owner decision, a data-flow-register entry and a real-service spike before any code depends on them.


## Follow-ups of 2026-11-06 (owner decisions 1-6)

- **Demo values.** `config/demo-phase4.yaml`, loaded by `careflow/demo_phase4.py` only under `CAREFLOW_PROFILE=demo`, `CAREFLOW_DATA_POLICY=synthetic_only` and `CAREFLOW_DEMO_PHASE4=1`; the app refuses to start if the switch is set anywhere else. Every value is labelled `DEMO ONLY - NOT A CLINICAL DECISION`; an explicit environment value wins; none is a default in `.env` or code. The cost figures are zero and the pseudonym key is a public placeholder.
- **Canary injection.** Demo only, and only when a canary rate is configured. Per tick and clinic: one draw per live review task of the last minute, at most one canary. The demo text source makes the fabricated letter for origin `canary`; the demo retriever then proposes a card id that does not exist (the planted error). LIMIT: the reviewer view shows card ids, not the letter, so the planted error is as visible as the card list. A real canary needs the clinic to decide what evidence the console shows.
- **Reason-code free text** is not stored (decision 3). **Cost canvas** F and total stay null (decision 4). **Langfuse, Grafana, Slack, e-mail** stay fakes (decision 5).
- **Checkpoint gap closed** by 0041 (decision 6). Residual: front desk can still write the thread of ANY slot of its own clinic.
