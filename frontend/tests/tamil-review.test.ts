import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { RESOURCES } from '../src/shared/i18n';
import reviewedFile from '../src/shared/i18n/ta-reviewed.json';

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
  });

  it('marks the reason-code placeholder with the exact agreed words', () => {
    expect(en['placeholder.reasonCodes']).toBe('PLACEHOLDER - clinician to define');
  });
});
