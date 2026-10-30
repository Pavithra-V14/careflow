import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
// @ts-expect-error plain .mjs script without types
import { DEV_ONLY_MARKERS, FORBIDDEN, findLeaks } from '../scripts/check-bundle-secrets.mjs';

const dirs: string[] = [];
function fixture(files: Record<string, string>): string {
  const dir = mkdtempSync(join(tmpdir(), 'bundle-check-'));
  dirs.push(dir);
  for (const [name, content] of Object.entries(files)) {
    mkdirSync(join(dir, name, '..'), { recursive: true });
    writeFileSync(join(dir, name), content);
  }
  return dir;
}
afterEach(() => {
  for (const d of dirs.splice(0)) rmSync(d, { recursive: true, force: true });
});

describe('bundle secret check', () => {
  it('forbids exactly the four agreed markers', () => {
    expect([...(FORBIDDEN as string[])].sort()).toEqual(['CAREFLOW_ENC_SECRET', 'DATABASE_URL', 'SUPABASE_SECRET_KEY', 'sb_secret_'].sort());
  });
  it('passes a clean bundle', () => {
    expect(findLeaks(fixture({ 'a.js': 'console.log("hi")', 'assets/b.css': 'body{}' }))).toEqual([]);
  });
  it.each(FORBIDDEN as string[])('fails when %s appears, including in a nested file', (marker) => {
    const hits = findLeaks(fixture({ 'assets/deep/x.js': `var k="${marker}${'A'.repeat(32)}";` })) as { marker: string }[];
    expect(hits.map((h) => h.marker)).toEqual([marker]);
  });
  it('never returns the text around a match', () => {
    // Assembled at run time so no secret-shaped literal is committed (GitHub push protection flags those).
    const hits = findLeaks(fixture({ 'x.js': ['sb_sec', 'ret_'].join('') + 'REALVALUE1234567890abcdef' })) as object[];
    expect(JSON.stringify(hits)).not.toContain('REALVALUE123');
  });
  it('does not fail on library code that merely mentions the sb_secret_ prefix (supabase-js classifies key types)', () => {
    const library = 'e.startsWith(`sb_publishable_`)||e.startsWith(`sb_secret_`)';
    expect(findLeaks(fixture({ 'assets/staff.js': library }))).toEqual([]);
  });
  it('still fails on a short but key-shaped value only from 16 characters, not below', () => {
    expect(findLeaks(fixture({ 'a.js': 'sb_secret_' + 'x'.repeat(15) }))).toEqual([]);
    expect((findLeaks(fixture({ 'b.js': 'sb_secret_' + 'x'.repeat(16) })) as unknown[]).length).toBe(1);
  });
  it('also fails if the development-only claim panel marker reaches a bundle', () => {
    const hits = findLeaks(fixture({ 'assets/staff.js': 'x="CAREFLOW_DEV_CLAIM_SPIKE"' })) as { marker: string }[];
    expect(hits.map((h) => h.marker)).toEqual(['CAREFLOW_DEV_CLAIM_SPIKE']);
    expect(DEV_ONLY_MARKERS).toEqual(['CAREFLOW_DEV_CLAIM_SPIKE']);
  });
});
