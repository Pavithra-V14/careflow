# Retention schedule: proposal

> **AI DRAFT. Not legal advice, not clinical advice. For owner, counsel and clinical lead review. Not approved.**
> The defaults below are copied from `BUILD-SPEC.md` section 9.4, which calls them "starting values, not fixed law". Nothing here has been checked against any state or professional-council rule.

**Who acts:** counsel and the privacy owner confirm or change each row; the clinical lead signs the clinical-record rows (`# CLINICAL-PLACEHOLDER: needs clinician sign-off`). The approved periods become per-clinic settings. The voice transcript period is already a required setting (`CAREFLOW_VOICE_TRANSCRIPT_RETENTION_DAYS`) and is enforced by a scheduled purge. The other time-limited rows are **not enforced by any scheduled job yet** (the spec requires one, so this is a pre-launch item).

| What | Spec default | Your decision | Enforced today? |
|---|---|---|---|
| Clinical record, adult | 8 years after last encounter | [ ] confirm [ ] change to ___ | No |
| Clinical record, minor | Until age 21, at least 8 years | [ ] confirm [ ] change | No |
| Pregnancy / childbirth record | 21+ years | [ ] confirm [ ] change | No |
| Consent records and notices | Permanent | [ ] confirm | Yes (never deleted) |
| Audit log | Permanent (or at least 8 years) | [ ] confirm | Yes (append-only) |
| Protocol and rule versions | Permanent | [ ] confirm | Yes |
| Agent checkpoints and run traces (metadata) | 90 days | [ ] confirm [ ] change | No |
| Prompt and response bodies with patient text | 30 days | [ ] confirm [ ] change | No |
| Voice transcript bodies | 30 days (spec 11.6) | [ ] confirm [ ] change | Yes, once the setting is set |
| De-identified evaluation samples | Indefinite | [ ] confirm | n/a |
| Erasure request | Identifying fields removed, audit kept with identity replaced | [ ] confirm | Yes |

## Questions for counsel
1. Which rule sets the adult and minor periods for [STATE/COUNCIL]?
2. Does a legal hold or a pending grievance suspend deletion, and who records that?
3. Is "permanent" acceptable for consent records and the audit log under data-minimisation duties?
4. Backups: how long do deleted rows survive in backups, and what do we tell a patient who asks? [OWNER/COUNSEL]
