# Tamil review: README for the reviewer

**Status: AI-prepared. No native speaker has reviewed any of this yet.** You are the first. Nothing in the pack is marked reviewed, and nothing becomes "reviewed" until you say so, string by string.

## What this is

CareFlow is a clinic system with a staff console (nurses, doctors, front desk, administrators) and a patient portal. Every screen has an English and a Tamil version. The Tamil was written by an AI model. This pack lists every Tamil string (1614) next to its English so a native Tamil speaker can check it before anyone relies on it. The strings are interface text only. There is no patient data in this file.

The pack was regenerated on 2026-11-14 (`npm run i18n:pack`) and covers every string the screens use today. The file to review is [tamil-review.md](tamil-review.md). Part 7 is the full table. Parts 1 to 3 are the strings the AI was least sure about, safety-related ones first.

## What to do

1. Start with **Part 1** (64 strings). These are the red-flag, result, decision, erasure, consent and identity-merge words, where a wrong word could mislead a nurse or a patient. If you work in a clinic, these are the ones to read first.
2. Then **Part 2** (94 strings: wrong meaning, wrong register, inconsistent term) and **Part 4** (terms used inconsistently: please pick one Tamil word for each English term).
3. Then read **Part 7**, the full table, as far as your time allows. In the last column write **OK** if the Tamil is right, or write the **full corrected Tamil**. Leave a row empty if you did not look at it. Do not write OK for something you only skimmed.
4. In **Part 5** (register), tell us which polite form you want on the patient pages. Patients should be addressed respectfully and plainly.

## Rules for corrections

- Keep every `{{placeholder}}` exactly as written (for example `{{count}}`). Do not translate it or change its spelling.
- Do not edit the Key or English columns.
- Part 6 lists strings that are English on purpose (hospital or lawyer text, and some clinical words). You do not need to fix those, but if you know the right Tamil for a clinical word there (for example "red flag" or "triage"), please say so.
- If a term has no good Tamil word, say so and suggest keeping the English or a short explanation.

## How long it should take

This is an estimate by the preparer, not a measured time. Part 1 and Part 2 together: about 2 to 3 hours. The full table: roughly 9 to 11 hours in all, best done in sittings of about 2 hours, because most of it is short labels (about 15 minutes per 100 labels) and about 664 sentences (about 40 seconds each). If you can only give two to three hours, do Parts 1, 2, 4 and 5.

## How to send corrections back

Edit a copy of `tamil-review.md` (any text editor; the table can also be pasted into a spreadsheet, filled in, and pasted back) and send the file back to the person who gave it to you, with your name and the date at the top. Say whether you reviewed all of it or only some parts (for example "Parts 1 and 2 only"). Only the rows you marked OK or corrected will be signed off; the rest stay unreviewed.

## What happens next

An engineer applies your corrections and records the strings you marked OK in the sign-off list. Until a string is on that list, the system keeps it on its unreviewed list and the project does not call it reviewed. You can ask for the pack again after the corrections are applied to check them.
