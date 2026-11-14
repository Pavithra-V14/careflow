import { execFileSync } from 'node:child_process';
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
