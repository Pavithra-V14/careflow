# Red-flag rules: authoring, approval and withdrawal (migrations 0072, 0076, 0077)

Mechanism only. No rule content is written by the build; the words in the tests are meaningless markers. A rule's content is the clinicians' (`# CLINICAL-PLACEHOLDER: needs clinician sign-off`).

**Life of a rule version.** A doctor of the clinic writes a DRAFT (`POST /v1/staff/console/red-flag-rules`) in a restricted language: literal phrases and token sets (every word present as a whole word, any order), bounded, no regular expression, no invisible or unusual space characters. A draft counts only in the demo profile, never in `clinical`. Two DIFFERENT doctors, neither the author, approve it (`POST .../{rule_id}/decision`, a second factor each, an append-only ledger with a hash of the reviewed content); one reject closes it. A doctor can withdraw a DRAFT (`POST .../{rule_id}/withdraw`): status `withdrawn`, which is not `retired`, because a retired newest version would switch off an older APPROVED version of the same rule code. A change to an approved rule is a new version, approved the same way.

**Decided by the owner (2026-11-13).** Doctors only approve. Platform-wide rules (no clinic) are refused by this door. The ordinary second-factor check applies to authoring, approving and withdrawing (no extra fresh-factor prompt). One person holding two doctor accounts can supply both approvals: a launch condition (checklist 58a).

**Not built.** Retiring an APPROVED version has no route today: the guard allows `approved -> retired` only for the migrator, and the decision route knows only `approve` and `reject`. Making a retirement a two-person decision needs that route to be extended first; until then an approved rule can only be superseded by a newer approved version.

**Known harmless quirk.** The reviewed-content hash gained a token-sets segment in 0076. A version whose first approval was recorded under 0072 and whose second under 0076 has two ledger rows with different hashes for identical content. The decision function does not compare them, so nothing breaks; no real rule exists yet, so none is affected.

**Deploy ordering.** The app refuses to start on a database that has not applied the migrations its code needs (`careflow/schema_check.py`, through 0077 today): migrate first, then start (`docs/first-run-guide.md`).
