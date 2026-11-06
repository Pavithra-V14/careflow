# Demo walkthrough: the synthetic end-to-end flow

Everything here is synthetic data in the `demo` profile. Run `python scripts/demo_walkthrough.py` for a printed version in which the three agents without HTTP routes (Care Journey, Visit Readiness, the voice monitor) actually run in memory with fakes. The routed steps below need the app and the CI/dev database.

**Start the app (demo only):** `CAREFLOW_PROFILE=demo CAREFLOW_DATA_POLICY=synthetic_only CAREFLOW_DEMO_PHASE3=1 CAREFLOW_DEMO_PHASE4=1`, plus the usual database and auth settings. The demo packs `config/demo-phase3.yaml` and `config/demo-phase4.yaml` supply labelled placeholders ("DEMO ONLY - NOT A CLINICAL DECISION"). Leave `FEATURE_CARE_JOURNEY`, `FEATURE_VISIT_READINESS` and `FEATURE_VOICE_CHANNEL` off unless you are showing those parts; the autonomy mode stays `shadow`.

| Step | Who | What to click or call | What you should see |
|---|---|---|---|
| 1. Referral in | Front desk | Upload page, or `POST /v1/staff/documents/uploads` then `POST /v1/staff/documents/register` | A registered document; a Front Door run is queued |
| 2. Run | Nurse, doctor | `POST /v1/staff/referrals/{document_id}/run`; `GET /v1/staff/referrals` | The rules read the letter first. A flag makes an immediate nurse task; otherwise a triage proposal waits for review |
| 3. Review | Nurse, doctor | `GET .../review-view`, then `POST .../review` approve or reject | A decision row and an audit row. Edit shows "Coming soon". A canary looks real; approving one quietly alerts admin |
| 4. Scheduling | Front desk | `GET /v1/staff/scheduling/slots`, `POST .../slots/{id}/hold`, `.../book` | A hold, then a booking; no double booking is possible |
| 5. Offer | Front desk | Cancel a booking, `POST .../backfills`, then `.../decide` send | The highest clinical priority waitlisted patient is offered first; delivery is by hand (`mark-delivered`) |
| 6. Reply | Front desk | `POST .../backfills/{slot_id}/reply`, then `POST .../offers/interpret-reply` | The reply is screened by the rules first; the suggestion is for a person to confirm; a booking follows only through the approval matrix |
| 7. Alerts and gaps | Nurse, doctor, admin | `GET /v1/staff/console/alerts`, `GET /v1/staff/care-gaps?clinic_id=...`, `POST /v1/staff/care-gaps/barriers` | Contentless alerts; the Care Gaps panel with ages; a recorded barrier appears under "unresolved_barrier" |
| 8. Care journey | Doctor | Not routed yet: `python scripts/demo_walkthrough.py` (section 8) | A confirmed plan waits for a result; a report with no value goes to a doctor; a verified match proposes a referral task |
| 9. Readiness | Patient (via front desk) | Not routed yet: script section 9 | Only missing facts asked; a danger phrase escalates before any model; a not-done answer reaches a nurse in every mode |
| 10. Voice | Patient | Not routed (no engine): script section 10; the session route answers 503 | The live monitor forces the safety response and the nurse task |

**Things that will look "off" on purpose:** no suggestion appears when a model route is not approved ("needs a person"); `GET /readyz` reports not ready in `clinical`; the Tamil screens are unreviewed; the care-gap panel is empty until episodes, rules or barriers exist (the owner or tests seed them).
