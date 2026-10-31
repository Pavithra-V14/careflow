import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { OPERATIONS } from '../src/shared/api/client';

// The live-run checklist (docs/frontend-live-checklist.md) must name every call the client can make, so a new client method
// cannot ship without a line saying what to check against the real backend.

const doc = readFileSync(resolve(import.meta.dirname, '../../docs/frontend-live-checklist.md'), 'utf8');

// Mentioned in the "calls no screen makes" paragraph instead of the table.
const NO_SCREEN = new Set(['/readyz', '/v1/staff/merges']);

describe('live checklist', () => {
  const paths = [...new Set(Object.values(OPERATIONS).map((o) => o.path))];

  it.each(paths)('names %s', (path) => {
    // Paths are written with their own parameter names in some rows and as `.../{x}/action` in others: match on the last two segments.
    const tail = path.split('/').filter(Boolean).slice(-2).join('/');
    const short = path.split('/').slice(-1)[0] as string;
    const named = doc.includes(path) || doc.includes(tail) || (short !== '' && !short.startsWith('{') && doc.includes(`/${short}`)) || NO_SCREEN.has(path);
    expect(named, `docs/frontend-live-checklist.md does not mention ${path}`).toBe(true);
  });

  it('says it has not been run, and what it needs to start', () => {
    expect(doc).toMatch(/Not started/);
    expect(doc).toMatch(/What I need to start/);
  });

  it('says the walk is read-only and that nothing recorded is a value', () => {
    expect(doc).toMatch(/read-only/);
    expect(doc).toMatch(/no values/i);
  });
});
