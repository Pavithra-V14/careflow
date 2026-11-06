# Phase 10: pre-launch, 2026-11-06 (documentation and mechanism; nothing here is executed)

BUILD-SPEC.md Appendix D Phase 10, 14.6, 18.2, 2. This phase is mostly work that **people** do on real infrastructure. What engineering can do, and did, is make each item a runbook, a checker that refuses a hollow claim, or a documented decision for the owner. **Nothing below is closed.**

## What exists

| Item | State | Where |
|---|---|---|
| Runbooks: overdue red flag, dead-letter growth, model outage, wrong merge, breach | Drafted. The breach runbook is a skeleton: no legal deadline, regulator or contact is invented, counsel and the privacy owner fill those in | `docs/runbooks/` |
| Rollback drill | Procedure drafted and a checker built (`careflow/drills.py`): the drill counts only with every step, in order, all ok, under 300 seconds. **Not run.** | `docs/runbooks/rollback-drill.md` |
| Restore drill | Procedure and checker built; six named checks including that append-only triggers still fire after a restore. **Not run.** | `docs/runbooks/restore-drill.md` |
| DLT / SMS registration | Process documented; the template registry is empty; a send guard refuses unregistered templates in `clinical` (the carrier drops them silently), checks variable kinds (no free text), the +91 allowlist, a per-phone limit and a daily spend cap. **Nothing registered; no provider adapter.** | `docs/runbooks/sms-dlt-registration.md`, `careflow/sms.py`, `config/sms-templates.yaml` |
| Evidence path | Drill claims drop straight into `ops/predeploy-evidence.yaml`; the go/no-go still says NO-GO | `careflow/predeploy.py` |

## The `clinical`-profile capability gaps (BUILD-SPEC.md 2): sign-off sheet

For a launch, each row below is either **closed** (an approved route exists under an agreement, with a `phi_approved` registry row) or **accepted as off for a demo-only launch**, signed by the owner. The "behaviour without it" column is what the system already does today (it degrades visibly, never silently). **All answer cells are blank: engineering does not decide them.**

| Capability | Behaviour in `clinical` without an approved route | Closed (route + agreement) | Accepted off for a demo-only launch (owner, date) |
|---|---|---|---|
| Reading typed PDF referrals: extraction | Text read locally; front desk keys the facts in by hand | | |
| Photographed referrals (vision) | Off; manual task with the image shown | | |
| Card matching reranker | Off; fusion order used | | |
| Tamil translation | Off; native Tamil red-flag rules still run | | |
| Care-plan free-text parsing (Care Journey) | Off; the doctor enters tasks by hand | | |
| Pre-visit brief and adaptive intake (Visit Readiness) | Off; front desk runs intake from the card's question list | | |
| Voice channel | Off; the mic button does not appear | | |
| SMS notifications | Off; DLT not registered, nothing sent | | |

## Decisions that are the owner's

1. Start DLT entity, header and template registration now (weeks of lead time).
2. Choose the hosting for the rollback and restore drills, then schedule them. A drill on a laptop is not evidence.
3. Fill in the breach runbook's contacts with counsel.
4. Fill in the sign-off sheet above, row by row.
5. Decide the owner-delete rule for append-only ledgers before the restore drill (checklist item 40).
