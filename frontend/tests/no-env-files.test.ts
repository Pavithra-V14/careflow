import { execFileSync } from 'node:child_process';
import { describe, expect, it } from 'vitest';

// An environment file must never reach the repository (CLAUDE.md: no key, token or password in the repo). A file named
// `env.local` (no leading dot) was committed once because only `.env*` was ignored. These tests ask git itself, so they cover
// the real state of this checkout: nothing env-like is tracked, and nothing env-like is left unignored for `git add -A` to find.
// The names are listed, never the contents.

const git = (...args: string[]) => execFileSync('git', args, { cwd: import.meta.dirname, encoding: 'utf8' }).split('\n').filter(Boolean);
// .env, .env.local, .env.production, env.local, env.production.local, and so on; .env.example is the one allowed template.
const envLike = (path: string) => /(^|\/)\.?env(\.[^/]*)?$/.test(path) && !path.endsWith('.env.example');

describe('environment files', () => {
  it('none is tracked by git (anywhere in the repository)', () => {
    expect(git('ls-files', '--full-name', '--', '..').filter(envLike)).toEqual([]);
  });

  it('none is left unignored, so `git add -A` cannot pick one up', () => {
    expect(git('ls-files', '--others', '--exclude-standard', '--full-name', '--', '..').filter(envLike)).toEqual([]);
  });

  it('the only tracked file with env in its name is the empty template', () => {
    const named = git('ls-files', '--full-name', '--', '..').filter((p) => /(^|\/)\.?env(\.[^/]*)?$/.test(p));
    expect(named.every((p) => p.endsWith('.env.example'))).toBe(true);
  });
});
