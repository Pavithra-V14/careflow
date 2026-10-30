**SIGNED OFF by the owner (2026-10-30) FOR ENGINEERING ON SYNTHETIC DATA ONLY, with one confirmation pending: written confirmation of this field list from the clinic's privacy / data-protection owner is a PRE-LAUNCH BLOCKER (no real data until it exists).** The code below is built behind `CAREFLOW_PROFILE=demo` AND `CAREFLOW_DATA_POLICY=synthetic_only`; anywhere else the routes answer 503 and nothing is decrypted.

## The decisions (owner, 2026-10-30), which replace the proposal's open questions
1. **Names on list rows:** yes for nurse and doctor, and for front desk, within the caller's own clinic.
2. **Phone:** the last four digits only in lists; the full number only on the opened record.
3. **Open access:** opening ONE record decrypts it for front desk, nurse and doctor of that clinic and writes an audit row (who, role, patient id; never the contents). No bulk export. A per-user open rate limit is a REQUIRED setting with NO default (`CAREFLOW_PATIENT_VIEW_MAX_OPENS_PER_MINUTE`; unset, opening is unavailable).
4. **Date of birth:** the year only in lists; the full date only on the opened record.
5. **Page limit:** a hard cap of 25 rows per page, no total-count shortcut, no export; adjustable DOWN as an engineering setting (`CAREFLOW_PATIENT_VIEW_PAGE_LIMIT`, never above 25).
6. **Privacy-lead confirmation:** not the engineer's or the owner's to give; recorded as a pre-launch blocker (item 17 in `phase2-status.md`).

Where a proposal question did not fit exactly: "initials and surname" (question 1) was replaced by the full name; "show contact" as a second audited action (question 2) was dropped (the full phone is on the opened record, which is audited); the per-row label read for lists (my proposal) is kept as the ONLY multi-record decryption, capped at 25 rows, with an audit row holding the COUNT only.

## What is built (branch b1-patient-view)
`careflow/patient_view.py` and three routes, all behind the gate above, all `Cache-Control: no-store`:
- `GET /v1/staff/patients/{id}/view`: the opened record (name, full date of birth, full phone). Front desk, nurse, doctor of its clinic; anyone who cannot see the record gets the same 404; 429 over the per-user limit; one `patient.view` audit row (who, role, patient id, `{"fields": "full"}`).
- `POST /v1/staff/patients/labels`: list rows (name, year of birth, last four digits) for up to 25 patients of one clinic; one `patient.view_labels` audit row with the COUNT only.
- `POST /v1/staff/patients/search` now carries the same list label on each hit (null outside the gate) and a `truncated` flag instead of a count.
The rate limiter is in memory and per process (several processes multiply the limit): fine for the demo, a shared limiter before real use.

## Original proposal (kept for the record)

# Patient view: which fields a role may see decrypted

## Decision of the owner (2026-10-30), recorded as the rule

1. **Least privilege.** A role sees the minimum needed for what it is doing, and full demographics only when ONE patient record is opened.
2. **Search results** show only the minimum needed to pick the right patient: for example name, year of birth, verified status and a masked contact. **Opening a single patient record** shows full demographics, and **that open writes an audit row**.
3. **No bulk decryption** until this note's exact field list per role is approved. Until then the patient search route returns only ids, the verified status and fields that are already non-sensitive (this is what is built: `POST /v1/staff/patients/search` and `GET /v1/staff/patients/{id}`).
4. **Every search writes one audit row** holding only who, role, the KIND of search and the result count: never the query text, never the results (built: action `patient.search`).

## What exists today

Demographics are stored encrypted (`mpi.patients.name_ct`, `dob_ct`, `phone_ct`, key version beside each; AES-GCM bound to the clinic and record, `careflow/crypto.py`). The only code that decrypts a stored value is key rotation (a migrator-run). **No route decrypts a name, date of birth or phone.** The tables hold three demographic fields only: name, date of birth, phone.

## Proposed field list per role

"Open" means opening ONE record by id. "List" means a search result row or any row in a list or queue.

| Field | Front desk (open) | Nurse / doctor (open) | Front desk (list) | Nurse / doctor (list) | Admin, auditor, business owner, queue worker, patients, guardians |
|---|---|---|---|---|---|
| Record id, clinic, source (`registered_via`), verified status, latest confirm/revoke, merged-into | yes | yes | yes | yes | no (admin: no) |
| Name | full | full | full name (needed to pick the right person) | **initials and surname only?** (DECISION 1) | never |
| Date of birth | full | full (and age) | **year only** | year only | never |
| Phone | full | **masked** (last 3 digits); full only through a second, audited "show contact" action (DECISION 2) | **masked** (last 3 digits) | none | never |
| Anything else | none exists in the schema | none | none | none | none |

Other rules proposed:
- **Lists and queues stay ids-only** (waitlist, bookings, holds, review tasks, referral queue). A screen that needs a person's name on a list row calls a per-row "label" read that is rate-limited to a small page (proposed: at most 20 rows per request) and audited as one `patient.view_label` row per request with the count, never the ids. This is the only form of multi-record decryption proposed.
- **The open writes `patient.view`**: who, role, the patient id, and the class of fields shown (`full` or `masked`); nothing from the record itself.
- **A record that is erased shows nothing** (the existing guard: erased records never match or open).
- **A record of another clinic is the same 404 as a missing one** (row-level security, as everywhere).
- **Responses carry `Cache-Control: no-store`**; nothing decrypted is logged, cached, or kept in an error message; the decrypted value is built per request and dropped.
- **Where the decrypt happens:** in the API process, under the caller's own session, using the vault already loaded for registration. No new service, no new grant (a read of `mpi.patients` by front desk, nurse and doctor is already allowed by `patients_select_clinic_staff`).
- **Nurse and doctor opening a record** is limited to records they reach through a task, a proposal or a booking they can already see? (DECISION 3: the database cannot express "reached through a task"; the policy today lets them read every patient of their clinic. Proposal: accept that, and rely on the audit row.)

## Decisions that were needed (answered above)

1. **Name on a list row for nurse and doctor:** full, initials and surname, or id only. Recommendation: initials and surname (enough to recognise a patient they already know, not a browsable directory).
2. **Phone for nurse and doctor:** masked with a separate audited "show contact" action, or never. Recommendation: masked, plus the audited action (a nurse sometimes has to call).
3. **Accept that nurse and doctor can open any patient of their clinic** (what the policy already allows), relying on the `patient.view` audit row. Recommendation: accept, and review the audit rows monthly.
4. **Date of birth precision on lists:** year only (proposed) or age band. Recommendation: year only.
5. **Per-row label page limit** (proposed 20) and whether front desk may use it on the waitlist screen. Recommendation: yes, 20.
6. **A clinician or the clinic's privacy lead should confirm** the list above is the clinic's reading of "minimum necessary" under the privacy law that applies (not an engineering judgement).

## What will be built once this is approved (and not before)

`GET /v1/staff/patients/{id}/view` (decrypted fields by role, audited), the optional per-row label read, the masking function, `Cache-Control: no-store`, role-matrix cases for every role in the table (each field present for the roles that may see it and absent, not null, for the roles that may not), a test that no decrypted value reaches a log record, and the audit-row tests. Size: medium. No migration and no grant are expected.
