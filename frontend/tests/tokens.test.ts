import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const css = readFileSync(resolve(import.meta.dirname, '../src/shared/tokens/tokens.css'), 'utf8');

function token(name: string): string {
  const m = new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})\\s*;`).exec(css);
  if (!m?.[1]) throw new Error(`token --${name} not found as a 6-digit hex`);
  return m[1];
}

function luminance(hex: string): number {
  const ch = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * (ch[0] ?? 0) + 0.7152 * (ch[1] ?? 0) + 0.0722 * (ch[2] ?? 0);
}

function ratio(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

// WCAG 2.2 AA: 4.5:1 for text, 3:1 for UI component boundaries and focus indicators.
const TEXT_PAIRS: [string, string][] = [
  ['color-text', 'color-bg'],
  ['color-text', 'color-surface'],
  ['color-text-muted', 'color-bg'],
  ['color-text-muted', 'color-surface'],
  ['color-on-primary', 'color-primary'],
  ['color-link', 'color-bg'],
  ['color-link', 'color-surface'],
  ['color-danger', 'color-bg'],
  ['color-emergency-text', 'color-emergency-bg'],
  ['color-test-text', 'color-test-bg'],
  ['color-live-text', 'color-live-bg'],
];
const UI_PAIRS: [string, string][] = [
  ['color-border', 'color-bg'],
  ['color-border', 'color-surface'],
  ['color-focus', 'color-bg'],
  ['color-focus', 'color-surface'],
];

describe('design token contrast (WCAG 2.2 AA)', () => {
  it.each(TEXT_PAIRS)('%s on %s is at least 4.5:1', (fg, bg) => {
    expect(ratio(token(fg), token(bg))).toBeGreaterThanOrEqual(4.5);
  });
  it.each(UI_PAIRS)('%s against %s is at least 3:1', (fg, bg) => {
    expect(ratio(token(fg), token(bg))).toBeGreaterThanOrEqual(3);
  });
  it('keeps staff touch targets at or above the 24px WCAG 2.2 minimum', () => {
    const m = /\[data-density='staff'\][^}]*--target-min:\s*([\d.]+)rem/.exec(css);
    expect(Number(m?.[1]) * 16).toBeGreaterThanOrEqual(24);
  });
  it('makes patient surfaces larger than staff surfaces', () => {
    const size = (d: string) => Number(new RegExp(`\\[data-density='${d}'\\][^}]*--font-size-base:\\s*([\\d.]+)rem`).exec(css)?.[1]);
    expect(size('patient')).toBeGreaterThan(size('staff'));
  });
});
