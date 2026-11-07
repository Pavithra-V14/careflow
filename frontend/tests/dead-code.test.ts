import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

// Nothing is left behind: no export that nothing imports, and no string in the language files that nothing uses. The report comes from
// scripts/find-dead.mjs (run it by hand to see the list). A string used through a key built at run time counts as used when its group
// is built that way (`group.${x}` or word('group', x)).
const report = execFileSync('node', [resolve(import.meta.dirname, '../scripts/find-dead.mjs')], { encoding: 'utf8' });

describe('dead code', () => {
  it('has no export that nothing imports', () => {
    expect(report).toContain('UNUSED EXPORTS (0)');
  });

  it('has no unused string in the language files', () => {
    expect(report).toContain('UNUSED STRINGS (0)');
  });
});
