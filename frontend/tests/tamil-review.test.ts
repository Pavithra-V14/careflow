import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { RESOURCES } from '../src/shared/i18n';
import reviewedFile from '../src/shared/i18n/ta-reviewed.json';
import aiReview from '../src/shared/i18n/ta-ai-review.json';

const reviewed: { reviewed: string[] } = reviewedFile;

type Tree = { [k: string]: string | Tree };
const flatten = (tree: Tree, prefix = ''): [string, string][] => Object.entries(tree).flatMap(([k, v]) => (typeof v === 'string' ? [[prefix + k, v] as [string, string]] : flatten(v, `${prefix}${k}.`)));
const ta = Object.fromEntries(flatten(RESOURCES.ta as Tree));
const en = Object.fromEntries(flatten(RESOURCES.en as Tree));
const list = readFileSync(resolve(import.meta.dirname, '../TAMIL-UNREVIEWED.md'), 'utf8');

describe('Tamil review list', () => {
  it('names only keys that exist', () => {
    for (const k of reviewed.reviewed) expect(ta, k).toHaveProperty(k);
  });

  it('lists every Tamil string that is not signed off (the CI check also regenerates it)', () => {
    for (const k of Object.keys(ta)) if (!reviewed.reviewed.includes(k)) expect(list, k).toContain(`\`${k}\``);
  });

  it('keeps the clinical task kinds and every placeholder in English, identical in both languages', () => {
    for (const k of ['tasks.kind.red_flag', 'tasks.kind.screening_unassessable', 'tasks.kind.triage_proposal']) expect(ta[k], k).toBe(en[k]);
    for (const k of Object.keys(en).filter((x) => x.startsWith('placeholder.'))) expect(ta[k], k).toBe(en[k]);
    // The triage decision words and the identity-revoke reason words are clinical or policy terms: not translated until reviewed.
    for (const k of Object.keys(en).filter((x) => x.startsWith('referrals.decision.') || x.startsWith('patients.revoke.reasons.'))) expect(ta[k], k).toBe(en[k]);
  });

  it('marks the reason-code placeholder with the exact agreed words', () => {
    expect(en['placeholder.reasonCodes']).toBe('PLACEHOLDER - clinician to define');
  });

  describe('AI review (a second pair of eyes, never a sign-off)', () => {
    it('is labelled exactly "AI-reviewed, pending native review" in the data and on the list', () => {
      expect(aiReview.label).toBe('AI-reviewed, pending native review');
      expect(list).toContain('## AI-reviewed, pending native review');
    });

    it('does not mark any string reviewed: the signed-off list is still empty and every string stays on the unreviewed list', () => {
      expect(reviewed.reviewed).toEqual([]);
      expect(list).toMatch(/\d+ of \d+ strings are unreviewed/);
      const [done, total] = /(\d+) of (\d+) strings are unreviewed/.exec(list)?.slice(1).map(Number) ?? [];
      expect(done).toBe(total);
      expect(total).toBe(Object.keys(ta).length);
    });

    it('only talks about keys that exist, and every corrected string and every flag is shown in the list', () => {
      for (const k of [...Object.keys(aiReview.verdicts), ...aiReview.issues.map((i) => i.key), ...aiReview.flags.map((f) => f.key), ...aiReview.corrected.map((c) => c.key)]) expect(ta, k).toHaveProperty(k);
      for (const c of aiReview.corrected) expect(list, c.key).toContain(`\`${c.key}\``);
    });

    it('applied the seven corrections exactly as suggested, and a blind re-check found no meaning problem in any corrected string', () => {
      for (const i of aiReview.issues) {
        expect(i.applied, i.key).toBe(true);
        expect(ta[i.key], i.key).toBe(i.suggestedTamil);
      }
      expect(aiReview.issues).toHaveLength(7);
      expect(aiReview.corrected.every((c) => c.recheck === 'match' || c.recheck === 'minor-drift')).toBe(true);
      expect(aiReview.recheckCounts['meaning-changed']).toBe(0);
      expect(aiReview.recheckCounts.wrong).toBe(0);
    });

    it('standardised proposal on முன்மொழிவு and resolve on the தீர் root, and left hold, clinic and results-versus-decision flagged', () => {
      for (const k of ['referrals.proposal', 'referrals.proposalAbstained']) expect(ta[k], k).toContain('முன்மொழிவு');
      for (const k of ['tasks.status.resolved', 'tasks.resolvedAt', 'tasks.closed', 'tasks.resolve', 'tasks.closedElsewhere', 'tasks.resolveTitle', 'tasks.resolveBody', 'tasks.conflictAck', 'tasks.conflictResolve']) expect(ta[k], k).toContain('தீர்');
      expect(aiReview.glossary.map((g) => g.concept).sort()).toEqual(['clinic / hospital', 'decision / results', 'hold']);
    });

    it('says no native speaker was involved and that every string is still unreviewed', () => {
      expect(aiReview.method).toMatch(/none a native speaker/);
      expect(aiReview.method).toMatch(/every string is still unreviewed/);
    });
  });
});
