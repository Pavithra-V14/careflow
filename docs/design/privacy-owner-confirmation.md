# What the privacy owner is asked to confirm in writing

Collected 2026-11-08. Each item states what the system does TODAY, in plain words, so the privacy owner can confirm it, or ask for a change. Nothing here is a legal opinion; the wording of notices and retention periods is counsel's. An item is closed only when a signed entry is recorded next to it (see `pre-launch-checklist.md`, rows 17, 18, 61, 62).

## 1. Two kinds of personal text that pass through the console

| What | Does the console STORE it? | Does the console SHOW it? |
|---|---|---|
| **The e-mail address typed when an admin invites a staff member** | **No.** It is sent to the Supabase Auth service (an existing, approved service) to create the sign-in, and goes nowhere else. It is never written to our tables, never logged, never in an audit row, never returned by any route. (Auth itself keeps it as the sign-in name; that is outside this system.) While no Auth admin adapter is wired, the invite route answers 503 and nothing is sent. | **No.** The invite answer carries only the new account id, the clinic, the role and a replay flag. |
| **The spoken words of a voice session (the transcript)** | **Only inside the retention window, and only as ciphertext.** Each chunk is encrypted before it is stored and carries its own expiry date, computed by the database from the retention setting (`CAREFLOW_VOICE_TRANSCRIPT_RETENTION_DAYS`, required, no default). After the window the chunk is no longer readable through the console. **Honest limit:** the expired ciphertext stays in the database until an admin runs the audited purge (`POST /v1/staff/voice/.../purge`, which deletes only expired chunks); there is no scheduled purge yet. Erasing a patient also deletes their voice chunks at once. | **Only to an admin, only inside the window, and every read is an audit row.** After the window the read returns nothing. No other role can read a transcript. Voice has no real engine in this build, so no real speech is captured today. |

Plain statement for the privacy owner: *the console neither stores the invite address nor shows it; it shows spoken words only to an admin, inside the retention window, with an audit row for each read, and does not show them after the window closes.*

What would close the honest limit: a scheduled, audited purge job with its own database role (design approved, not built; `retention-and-erasure-purge.md`, checklist row 4).

## 2. Other confirmations already on the checklist (not new)

- The patient-view field list for staff (`patient-view-fields.md`, row 17).
- What erasure keeps: result code, verified flag and times; identity tokens (keyed hashes); audit rows; message and thread rows without bodies (row 61).
- Retention periods for results, plan text, messages and voice transcripts (rows 4 and 62).
- Whether an automatically claimed patient account may ever see the care topic in its download (row 66).
- Whether patients may see clinical notes in their download: today they may not (decided 2026-11-08).

## 3. Signature block

| Item | Confirmed / change asked | Name | Date |
|---|---|---|---|
| 1. Invite address and voice transcript (above) | | | |
| 2. Rows 17, 61, 62, 66 | | | |
