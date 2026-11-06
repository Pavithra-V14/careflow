# Runbook: a suspected personal-data breach

BUILD-SPEC.md 16, 17.6, 20; `data-flow-register.yaml`; `docs/design/threat-model.md`. **Status: SKELETON for the owner and counsel. It deliberately contains no legal deadline, no regulator name or form, and no contact: those need the clinic's counsel and the privacy owner, and writing them from memory would be inventing obligations.** Every blank below must be filled in and signed off before real patient data.

**Who is on this runbook:** the incident lead (the owner), the privacy owner, engineering on call, counsel, the hospital's authorised signatory.

**Contacts (empty on purpose, for the owner to fill in):** incident lead: ____ . privacy owner: ____ . counsel: ____ . hosting provider support: ____ .

1. **Contain first, without destroying evidence.** Rotate the exposed credential (see `key_rotation.md`); disable the affected account (staff account management); turn the relevant feature flag off. Do **not** delete rows or logs. The audit log is append-only and is the evidence.
2. **Preserve.** Note the time, who found it, and what was seen. Export the relevant audit rows (ids and actions only), read-only. Keep the CI and deploy logs for the window.
3. **Scope it from the record, not from memory.** Use the audit reader (auditor role) to list reads and writes by the account or key in the window. `data-flow-register.yaml` lists every place data leaves the process; check each. State what was exposed in classes (ciphertext only? plaintext? which clinic? how many people?), never by pasting data into a message or ticket.
4. **Decide whether it is notifiable.** Counsel decides, with the privacy owner, whether and to whom a notification is due, within what time and in what form. The timeline and content are theirs to state here: ____ .
5. **Tell the people affected** only in wording counsel has approved. Patient messages that are not fixed templates are locked to a human (12.1) and go through the front-desk path with counsel's text.
6. **Fix forward.** A flaw found in a migration or policy is fixed by a NEW migration (merged migrations are never edited). Add the regression test that would have caught it.
7. **Record.** Write the post-incident note: timeline, cause, what was exposed, what was done, what changed. Add any new external call to the register.

**Never:** announce numbers or a cause before the scope is known; paste patient data into chat or tickets; delete evidence; promise an outcome.
