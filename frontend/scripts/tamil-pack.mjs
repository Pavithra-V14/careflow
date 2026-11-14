// Regenerates the Tamil review pack for a native reviewer: docs/approval-pack/tamil-review.md and tamil-review-README.md.
//   npm run i18n:pack            writes both files
//   npm run i18n:pack -- --check exits 1 if the committed files do not match what the current strings would produce
// What goes in: every Tamil string and its English (the same file list as `npm run i18n:list`), a mechanical check of all of them
// (placeholders, empty strings, untranslated Latin words, informal pronouns), the earlier AI pass (src/shared/i18n/ta-ai-review.json),
// and the second-pass notes in docs/approval-pack/tamil-review-notes.tsv (key, grade A/B/C, note). The notes are written by an AI model,
// not a native speaker. A string with no note says "no issue found", which never means a person has cleared it. Nothing here is ever
// marked reviewed: that is only ever done by adding a key to src/shared/i18n/ta-reviewed.json.
// A string with no note says "no issue found". When strings are added, add a note for each one that deserves it to the notes file.
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

const root = resolve(import.meta.dirname, '..');
const repo = resolve(root, '..');
const check = process.argv.includes('--check');
const i18n = (f) => resolve(root, 'src/shared/i18n', f);
const read = (f) => JSON.parse(readFileSync(i18n(f), 'utf8'));
function merge(base, extra) {
  const out = { ...base };
  for (const [k, v] of Object.entries(extra)) out[k] = v && typeof v === 'object' && out[k] && typeof out[k] === 'object' ? merge(out[k], v) : v;
  return out;
}
const flatten = (tree, prefix = '') => Object.entries(tree).flatMap(([k, v]) => (typeof v === 'string' ? [[prefix + k, v]] : flatten(v, `${prefix}${k}.`)));

// One list of files: the one `npm run i18n:list` uses.
const listScript = readFileSync(resolve(root, 'scripts/list-unreviewed-tamil.mjs'), 'utf8');
const FILES = [...(listScript.match(/const FILES = \[([^\]]*)\]/)?.[1] ?? '').matchAll(/'([^']*)'/g)].map((m) => m[1]);
if (FILES.length === 0) throw new Error('could not read the file list from scripts/list-unreviewed-tamil.mjs');
const all = (lang) => FILES.map((p) => read(`${p}${lang}.json`)).reduce((a, b) => merge(a, b));
const en = Object.fromEntries(flatten(all('en')));
const ta = Object.fromEntries(flatten(all('ta')));
const keys = Object.keys(ta).sort();
const rows = keys.map((k) => ({ k, e: en[k] ?? '', t: ta[k] ?? '' }));
const byKey = new Map(rows.map((r) => [r.k, r]));
const ai = read('ta-ai-review.json');

// ---- mechanical checks ------------------------------------------------------------------------------------------------------------
const ph = (s) => (s.match(/\{\{\s*\w+\s*\}\}/g) ?? []).map((x) => x.replace(/\s/g, '')).sort().join(',');
const phBad = rows.filter((r) => ph(r.e) !== ph(r.t));
const empty = rows.filter((r) => r.t.trim() === '');
const informal = rows.filter((r) => /(\bநீ\b|உனக்கு|உன்னை|உன்\b)/.test(r.t));
const kept = rows.filter((r) => r.e === r.t).map((r) => r.k);

// ---- second-pass notes ------------------------------------------------------------------------------------------------------------
const notes = readFileSync(resolve(repo, 'docs/approval-pack/tamil-review-notes.tsv'), 'utf8')
  .split('\n')
  .filter(Boolean)
  .map((l) => {
    const [k, sev, ...rest] = l.split('\t');
    return { k, sev, text: rest.join('\t') };
  });
const stale = notes.filter((n) => !byKey.has(n.k));
const live = notes.filter((n) => byKey.has(n.k));
const noteBy = new Map(live.map((n) => [n.k, n]));
const A = live.filter((n) => n.sev === 'A');
const B = live.filter((n) => n.sev === 'B');
const C = live.filter((n) => n.sev === 'C');
const esc = (s) => String(s).replaceAll('|', '\\|').replaceAll('\n', ' ');
const earlier = (k) => {
  const c = ai.corrected.find((x) => x.key === k);
  if (c) return `earlier AI pass: corrected (${c.reason})`;
  const i = ai.issues.find((x) => x.key === k);
  if (i) return `earlier AI pass: ${i.verdict}: ${i.note}`;
  const f = ai.flags.find((x) => x.key === k);
  if (f) return `earlier AI pass flag (${f.kind}): ${f.note}`;
  return '';
};

const theme = [
  ['1. Red flags, screening outcomes and escalation (patient safety)', /^(backfill\.reply\.(enterBody|reasons|savedOutcome|outcome|screenedNote|screening)|alerts\.class\.(red_flag|task_escalated)|readiness\.statuses\.|dashboard\.metric\.(red_flag|urgent|safety|escalation)|care\.plans\.status\.screening)/],
  ['2. Clinical results, decisions, medications and red-flag rules', /^(care\.results|care\.gaps\.(recheck|sameDecider)|referrals\.proposal|medications\.|admin\.rules\.|admin\.tab\.rules)/],
  ['3. Consent, privacy, erasure and patient rights', /^(erasure\.keepsBody|portal\.activate\.consent|portal\.consent\.|portal\.voice\.|portal\.myData\.(history|purpose)|portal\.grievance\.body|placeholder\.)/],
  ['4. Identity: merge, link, revoke and undo', /^(identity\.|patients\.revoke|alerts\.class\.identity_revoked_suspect)/],
  ['5. "Acknowledge" versus "approve", and irreversible actions', /^(alerts\.acknowledge|admin\.voice\.purgeBody)/],
];
const placed = new Set();
const groups = theme.map(([title, re]) => ({ title, items: A.filter((n) => re.test(n.k)) }));
groups.forEach((g) => g.items.forEach((n) => placed.add(n.k)));
groups.push({ title: '6. Other safety-related strings', items: A.filter((n) => !placed.has(n.k)) });
const table = (items) => ['| Key | English | Tamil (AI-prepared) | Why a native speaker is needed |', '|---|---|---|---|', ...items.map((n) => `| \`${n.k}\` | ${esc(byKey.get(n.k).e)} | ${esc(byKey.get(n.k).t)} | ${esc(n.text)} |`)].join('\n');

const gloss = [
  ['approve', 'ஒப்புக்கொள்|ஒப்புதல்', 'ஒப்புக்கொள் (verb), ஒப்புதல் (noun)', 'The verb also means "admit/agree"; some places use ஒப்புதல் அளி.'],
  ['acknowledge', 'ஏற்றுக்கொள்|ஏற்கப்பட்ட', 'ஏற்றுக்கொள் ("accept")', 'Same verb as approve; the action only records that you have seen something.'],
  ['record / log / transcript / register', 'பதிவு', 'பதிவு everywhere', 'One word for a patient record, the audit log, a voice transcript, "recorded" and "register".'],
  ['result / decision / end', 'முடிவு', 'முடிவு everywhere', 'One word for a clinical result, a person\'s decision and an end time.'],
  ['role', 'பங்கு', 'பங்கு', 'Also means "share"; பணி is used for "task".'],
  ['merge / link / connect / attach', 'இணை', 'இணை / இணைப்பு', 'Merging two patient records, linking a portal account, attaching a note: one word.'],
  ['red flag', 'சிவப்புக் கொடி', 'சிவப்புக் கொடி (literal)', 'Check the wording clinicians use.'],
  ['hold (a slot or a referral)', 'நிறுத்|ஒதுக்கீடு', 'நிறுத்து / நிறுத்தம் (stop) and ஒதுக்கீடு (allocation)', 'Two or three words for one idea.'],
  ['retire (a rule version) and withdraw (a draft)', 'நிறுத்|திரும்பப் பெற', 'நிறுத்து (retire) and திரும்பப் பெறு (withdraw)', 'The rule screens need two different words; "withdraw" shares its Tamil with "revoke" and "cannot be undone".'],
  ['task / job', 'பணி', 'பணி', 'A review task and a queued job share the word.'],
  ['approved (a status) and approve (a button)', 'அங்கீகர', 'அங்கீகரிக்கப்பட்டது (status) and ஒப்புக்கொள் (button)', 'Two word families for one idea in the red-flag rule screens.'],
  ['patient', 'நோயாளி|நோயாளர்', 'நோயாளி and நோயாளர்', 'Two spellings.'],
  ['clinic / hospital', 'மருத்துவமனை|கிளினிக்', 'மருத்துவமனை (most strings), கிளினிக் (1)', 'Check which is meant where.'],
  ['front desk', 'வரவேற்பு மேசை|முன்பணியாளர்', 'வரவேற்பு மேசை and முன்பணியாளர்', 'Two renderings.'],
  ['confidence', 'நம்பிக்கை', 'நம்பிக்கை', 'Means belief or hope; the strings mean how sure the system is.'],
  ['sample (a sampled decision)', 'மாதிரி', 'மாதிரி', 'Reads as a laboratory specimen.'],
  ['revoke / take back, and "cannot be undone"', 'திரும்பப் பெற', 'திரும்பப் பெறு for both', 'The same verb for "revoke" and "cannot be reversed".'],
  ['grant / withdraw (a consent)', 'வழங்கு|திரும்பப் பெறு', 'வழங்கு (grant) and திரும்பப் பெறு (withdraw)', 'Consent withdrawal is one click in the portal, so the word must be unmistakable.'],
  ['fresh (code)', 'புதிய', 'புதிய ("new")', 'Means a recently entered code, not a new one.'],
  ['screen (the rules read text)', 'பரிசோதி|பரிசோதனை', 'பரிசோதனை', 'The word for a medical examination or test.'],
  ['escalate', 'உயர்நிலை', 'உயர்நிலைக்கு அனுப்பு ("send to a higher level")', 'Loses the sense of raising urgency.'],
  ['authenticator', 'அங்கீகார', 'அங்கீகார செயலி (app) / அங்கீகார குறியீடு (code)', 'Consistent; check it is the word staff use.'],
];
const cnt = (re) => rows.filter((r) => new RegExp(re).test(r.t)).length;
const glossTable = ['| English term | Tamil used (number of strings, approximate: substring match) | Problem |', '|---|---|---|', ...gloss.map(([e, re, t, p]) => `| ${e} | ${t} (${cnt(re)}) | ${p} |`)].join('\n');

const full = [
  '| Key | English | Tamil (AI-prepared) | AI second pass | Reviewer: write OK, or the corrected Tamil |',
  '|---|---|---|---|---|',
  ...rows.map((r) => {
    const n = noteBy.get(r.k);
    const ai2 = r.e === r.t ? 'kept in English by design' : [n ? `${n.sev}: ${n.text}` : 'no issue found', earlier(r.k)].filter(Boolean).join('; ');
    return `| \`${r.k}\` | ${esc(r.e)} | ${esc(r.t)} | ${esc(ai2)} | |`;
  }),
].join('\n');

const date = process.env.PACK_DATE ?? new Date().toISOString().slice(0, 10);
const words = (s) => s.trim().split(/\s+/).length;
const longRows = rows.filter((r) => words(r.e) >= 4).length;

const md = `# Tamil review pack (AI-prepared, NOT reviewed by a native speaker)

Prepared ${date}. Every Tamil string in the staff console and the patient portal, next to its English, with a second-pass check by an AI model (not a native speaker) and a column for a native reviewer. **No string in this file is marked as reviewed.** The sign-off list (\`frontend/src/shared/i18n/ta-reviewed.json\`) is still empty, and every one of the ${rows.length} Tamil strings stays on the unreviewed list (\`frontend/TAMIL-UNREVIEWED.md\`) until a native reviewer signs it off. Read the README first: [tamil-review-README.md](tamil-review-README.md).

**Regenerated ${date}** by \`npm run i18n:pack\` from the strings as they are now, so it covers every current string, including the newest screens (patient voice setting, consent withdrawal and history, activating a staff account, withdrawing and retiring a red-flag rule). Each is written and second-passed in the same way and is just as unreviewed.

## What the second pass looked at

Meaning errors, register for patients, medical or safety terms, unsafe ambiguity, inconsistent terms. Findings are graded:

- **A** safety, privacy or patient-rights meaning could be misread. Needs a native speaker, ideally one who works in a clinic.
- **B** wrong or odd meaning, wrong register, or an inconsistent term. Needs a native speaker.
- **C** minor wording, or an administrator-only technical label.

How it was done, honestly: all ${rows.length} pairs were checked by script (placeholders, empty strings, untranslated Latin words, informal pronouns), and the strings that carry meaning (sentences of four words or more, and short strings that differ from their English) were read by an AI model. The AI model is not a native speaker, so the lists below are the places where it was unsure or found a likely problem; a string marked "no issue found" has not been cleared by a person.

Automatic checks: ${rows.length} strings; ${empty.length} empty; the {{placeholders}} are ${phBad.length === 0 ? 'identical in English and Tamil in every string' : `DIFFERENT in ${phBad.length} strings: ${phBad.map((r) => r.k).join(', ')}`}; ${informal.length} informal pronouns (நீ, உன்) found; ${kept.length} Tamil strings equal their English on purpose (Part 6).

Notes: ${A.length} grade A, ${B.length} grade B, ${C.length} grade C (${live.length} rows with a note; some notes cover a group of similar strings, named in the note).

## Part 1. Strings that most need a native speaker (safety first)

${groups.filter((g) => g.items.length).map((g) => `### ${g.title}\n\n${table(g.items)}`).join('\n\n')}

## Part 2. Meaning, register and term problems (grade B)

${table(B)}

## Part 3. Minor wording and technical labels (grade C)

${table(C)}

## Part 4. Terms used inconsistently

${glossTable}

## Part 5. Register

Staff-console buttons and menu labels use the bare singular imperative (for example காட்டு, சேர், திற, அனுப்பு), which is common for interface labels. Sentences use the polite -ங்கள் form (for example தேர்ந்தெடுங்கள்). Patient-facing pages mix three forms: -ங்கள் (உருவாக்குங்கள்), the archaic -க (உள்நுழைக, உருவாக்குக) and the bare imperative (எனக்குக் குறியீட்டை அனுப்பு, வேறு எண்ணைப் பயன்படுத்து). A native reviewer should choose one polite register for every patient-facing string. No informal pronouns (நீ, உன்) were found.

## Part 6. Left in English on purpose (${kept.length} strings)

These are the same in English and Tamil by design, and a Tamil-speaking patient sees English for them until real Tamil text is supplied. Every \`placeholder.*\` string is hospital or counsel content (privacy notice, privacy promise, grievance contact, FAQ answers, erasure wording, identity checks); they are not translation work. The others are the clinical task kinds (\`tasks.kind.red_flag\`, \`tasks.kind.screening_unassessable\`, \`tasks.kind.triage_proposal\`), the referral decision words (\`referrals.decision.*\`), the identity-revoke reason words (\`patients.revoke.reasons.*\`) and the environment and language labels.

${kept.map((k) => `- \`${k}\``).join('\n')}

## Part 7. All ${rows.length} strings

Fill in the last column. Write **OK**, or write the full corrected Tamil (keep every {{placeholder}} exactly as it is). Leave a row empty if you have not looked at it.

${full}
`;

const readme = `# Tamil review: README for the reviewer

**Status: AI-prepared. No native speaker has reviewed any of this yet.** You are the first. Nothing in the pack is marked reviewed, and nothing becomes "reviewed" until you say so, string by string.

## What this is

CareFlow is a clinic system with a staff console (nurses, doctors, front desk, administrators) and a patient portal. Every screen has an English and a Tamil version. The Tamil was written by an AI model. This pack lists every Tamil string (${rows.length}) next to its English so a native Tamil speaker can check it before anyone relies on it. The strings are interface text only. There is no patient data in this file.

The pack was regenerated on ${date} (\`npm run i18n:pack\`) and covers every string the screens use today. The file to review is [tamil-review.md](tamil-review.md). Part 7 is the full table. Parts 1 to 3 are the strings the AI was least sure about, safety-related ones first.

## What to do

1. Start with **Part 1** (${A.length} strings). These are the red-flag, result, decision, erasure, consent and identity-merge words, where a wrong word could mislead a nurse or a patient. If you work in a clinic, these are the ones to read first.
2. Then **Part 2** (${B.length} strings: wrong meaning, wrong register, inconsistent term) and **Part 4** (terms used inconsistently: please pick one Tamil word for each English term).
3. Then read **Part 7**, the full table, as far as your time allows. In the last column write **OK** if the Tamil is right, or write the **full corrected Tamil**. Leave a row empty if you did not look at it. Do not write OK for something you only skimmed.
4. In **Part 5** (register), tell us which polite form you want on the patient pages. Patients should be addressed respectfully and plainly.

## Rules for corrections

- Keep every \`{{placeholder}}\` exactly as written (for example \`{{count}}\`). Do not translate it or change its spelling.
- Do not edit the Key or English columns.
- Part 6 lists strings that are English on purpose (hospital or lawyer text, and some clinical words). You do not need to fix those, but if you know the right Tamil for a clinical word there (for example "red flag" or "triage"), please say so.
- If a term has no good Tamil word, say so and suggest keeping the English or a short explanation.

## How long it should take

This is an estimate by the preparer, not a measured time. Part 1 and Part 2 together: about 2 to 3 hours. The full table: roughly 9 to 11 hours in all, best done in sittings of about 2 hours, because most of it is short labels (about 15 minutes per 100 labels) and about ${longRows} sentences (about 40 seconds each). If you can only give two to three hours, do Parts 1, 2, 4 and 5.

## How to send corrections back

Edit a copy of \`tamil-review.md\` (any text editor; the table can also be pasted into a spreadsheet, filled in, and pasted back) and send the file back to the person who gave it to you, with your name and the date at the top. Say whether you reviewed all of it or only some parts (for example "Parts 1 and 2 only"). Only the rows you marked OK or corrected will be signed off; the rest stay unreviewed.

## What happens next

An engineer applies your corrections and records the strings you marked OK in the sign-off list. Until a string is on that list, the system keeps it on its unreviewed list and the project does not call it reviewed. You can ask for the pack again after the corrections are applied to check them.
`;

const outputs = [
  [resolve(repo, 'docs/approval-pack/tamil-review.md'), md],
  [resolve(repo, 'docs/approval-pack/tamil-review-README.md'), readme],
];
if (stale.length) console.warn(`notes for ${stale.length} keys that no longer exist were left out: ${stale.map((n) => n.k).join(', ')}`);
if (check) {
  // The date line is the only thing that changes on its own, so it is ignored in the comparison.
  const norm = (s) => s.replace(/Prepared \d{4}-\d{2}-\d{2}/, 'Prepared D').replace(/Regenerated \d{4}-\d{2}-\d{2}/, 'Regenerated D').replace(/regenerated on \d{4}-\d{2}-\d{2}/, 'regenerated on D');
  const stale2 = outputs.filter(([f, text]) => norm(readFileSync(f, 'utf8')) !== norm(text));
  if (stale2.length) {
    console.error(`The Tamil review pack is out of date: ${stale2.map(([f]) => f).join(', ')}. Run: npm run i18n:pack`);
    process.exit(1);
  }
  console.log('The Tamil review pack matches the current strings.');
} else {
  for (const [f, text] of outputs) writeFileSync(f, text, 'utf8');
  console.log(`wrote the pack: ${rows.length} strings; notes A ${A.length}, B ${B.length}, C ${C.length}`);
}
