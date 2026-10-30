import { describe, expect, it } from 'vitest';
import { RESOURCES, initI18n, setLanguage } from '../src/shared/i18n';
import { formatDate, formatNumber } from '../src/shared/i18n/format';

type Tree = { [k: string]: string | Tree };

function flatten(tree: Tree, prefix = ''): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(tree)) {
    if (typeof v === 'string') out[prefix + k] = v;
    else Object.assign(out, flatten(v, `${prefix}${k}.`));
  }
  return out;
}

const vars = (s: string) => [...s.matchAll(/\{\{\s*(\w+)\s*\}\}/g)].map((m) => m[1]).sort();

describe('translations', () => {
  // Both files per language, merged exactly as the app merges them.
  const E = flatten(RESOURCES.en as Tree);
  const T = flatten(RESOURCES.ta as Tree);

  it('have identical key sets in English and Tamil', () => {
    expect(Object.keys(T).sort()).toEqual(Object.keys(E).sort());
  });
  it('have no empty strings', () => {
    for (const [k, v] of [...Object.entries(E), ...Object.entries(T)]) expect(v.trim(), k).not.toBe('');
  });
  it('use the same interpolation variables in both languages', () => {
    for (const k of Object.keys(E)) expect(vars(T[k] ?? ''), k).toEqual(vars(E[k] ?? ''));
  });
  it('keeps the emergency number identical in both languages', () => {
    expect(E['common.emergencyBanner']).toContain('108');
    expect(T['common.emergencyBanner']).toContain('108');
  });
});

describe('i18n runtime', () => {
  it('switches language and sets the document lang attribute', async () => {
    initI18n('en');
    expect(document.documentElement.lang).toBe('en');
    await setLanguage('ta');
    expect(document.documentElement.lang).toBe('ta');
    await setLanguage('en');
  });
});

describe('formatting', () => {
  it('formats numbers with Indian grouping', () => {
    expect(formatNumber(1234567, 'en')).toBe('12,34,567');
    expect(formatNumber(1234567, 'ta')).toBe('12,34,567');
  });
  it('formats dates in Asia/Kolkata, not the machine time zone', () => {
    // 20:00 UTC on 31 Dec is already 1 Jan in India.
    const d = '2026-12-31T20:00:00Z';
    expect(formatDate(d, 'en', { day: 'numeric', month: 'numeric', year: 'numeric' })).toBe('1/1/2027');
    expect(formatDate(d, 'ta', { day: 'numeric', month: 'numeric', year: 'numeric' })).toContain('2027');
  });
  it('renders Tamil month names in Tamil script', () => {
    expect(formatDate('2026-10-24T05:00:00Z', 'ta', { month: 'long' })).toMatch(/[஀-௿]/);
  });
  it('returns an empty string, not "Invalid Date", for an unparseable value', () => {
    expect(formatDate('not a date', 'en')).toBe('');
  });
});
