import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
// @ts-expect-error plain .mjs script without types
import { FORBIDDEN, findLeaks } from '../scripts/check-bundle-secrets.mjs';

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
    const hits = findLeaks(fixture({ 'assets/deep/x.js': `var k="${marker}abc";` })) as { marker: string }[];
    expect(hits.map((h) => h.marker)).toEqual([marker]);
  });
  it('never returns the text around a match', () => {
    const hits = findLeaks(fixture({ 'x.js': 'sb_secret_REALVALUE123' })) as object[];
    expect(JSON.stringify(hits)).not.toContain('REALVALUE123');
  });
});
