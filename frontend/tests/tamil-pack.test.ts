import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

// The Tamil lists are generated: a new Tamil string must appear on the unreviewed list and in the reviewer's pack, which stay marked
// AI-prepared and unreviewed. `npm run i18n:list` and `npm run i18n:pack` regenerate them.
const root = resolve(import.meta.dirname, '..');
const run = (script: string) => execFileSync(process.execPath, [resolve(root, 'scripts', script), '--check'], { cwd: root, encoding: 'utf8', stdio: 'pipe' });

describe('Tamil review lists', () => {
  it('the unreviewed list is up to date', () => {
    expect(() => run('list-unreviewed-tamil.mjs')).not.toThrow();
  });
  it('the reviewer pack and its README are up to date with the current strings', () => {
    expect(run('tamil-pack.mjs')).toContain('matches the current strings');
  });
});

describe('Tamil machine check (a first pass, never a sign-off)', () => {
  const pack = readFileSync(resolve(root, '../docs/approval-pack/tamil-review.md'), 'utf8');
  const meta = JSON.parse(readFileSync(resolve(root, '../docs/approval-pack/tamil-machine-check.json'), 'utf8')) as { date: string; checked: number; fixed: unknown[] };
  const flags = readFileSync(resolve(root, '../docs/approval-pack/tamil-machine-check.tsv'), 'utf8').split('\n').filter(Boolean);
  it('says it is machine-checked, not human-reviewed, in the pack', () => {
    expect(pack).toContain(`Machine-checked on ${meta.date}; NOT human-reviewed; the Tamil reader must still approve.`);
    expect(pack).toContain('## Part 0. Machine first pass (back-translation)');
  });
  it('lists its counts and every flagged string, and the pack shows the same numbers', () => {
    expect(meta.checked).toBeGreaterThan(1700);
    expect(pack).toContain(`| Fixed (a clear defect) | ${meta.fixed.length} |`);
    expect(pack).toContain(`| Flagged for the human reviewer | ${flags.length} (`);
    for (const line of flags) {
      const [key, grade] = line.split('\t');
      expect(pack).toContain(`| \`${key}\` | ${grade} |`);
    }
  });
  it('never marks any string reviewed: the sign-off list is still empty', () => {
    const reviewed = JSON.parse(readFileSync(resolve(root, 'src/shared/i18n/ta-reviewed.json'), 'utf8')) as { reviewed: unknown[] };
    expect(reviewed.reviewed).toEqual([]);
  });
});
