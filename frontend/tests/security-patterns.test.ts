import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

// A source scan that pins the frontend security review (docs/frontend-security-review.md). It reads code, not comments.
const SRC = resolve(import.meta.dirname, '../src');

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) return files(p);
    return /\.(ts|tsx)$/.test(name) && !name.endsWith('.d.ts') ? [p] : [];
  });
}
function code(path: string): string {
  return readFileSync(path, 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .map((l) => l.replace(/(^|\s)\/\/.*$/, ''))
    .join('\n');
}
const all = files(SRC).map((p) => ({ path: relative(SRC, p).replaceAll('\\', '/'), text: code(p) }));
const hits = (re: RegExp, allowed: string[] = []) => all.filter((f) => re.test(f.text) && !allowed.includes(f.path)).map((f) => f.path);

describe('frontend security patterns', () => {
  it('has no way to inject markup or run a string as code', () => {
    expect(hits(/innerHTML|outerHTML|dangerouslySetInnerHTML|insertAdjacentHTML|document\.write|\beval\s*\(|new Function\s*\(/)).toEqual([]);
  });

  it('sets no inline style: the policy allows styles from the site only, so styles are classes', () => {
    expect(hits(/style=\{\{|\bstyle="|\.setAttribute\(\s*['"]style['"]/)).toEqual([]);
  });

  it('writes nothing to the console (no tokens or patient data in logs)', () => {
    expect(hits(/(?<![\w.'"`])console\.(log|info|warn|error|debug|trace)\s*\(/)).toEqual([]);
  });

  it('keeps browser storage to the language choice and the active role (the sign-in session is supabase-js\'s own)', () => {
    expect(hits(/localStorage|sessionStorage|indexedDB|document\.cookie/, ['shared/i18n/index.ts', 'staff/activeRole.ts'])).toEqual([]);
  });

  it('builds no URL by joining strings, and never navigates from data', () => {
    expect(hits(/window\.open\s*\(|location\.(href|assign|replace)\s*=|href=\{`/)).toEqual([]);
  });

  it('never puts text in the page title other than a fixed translated name', () => {
    const titles = all.flatMap((f) => [...f.text.matchAll(/document\.title\s*=\s*([^;\n]+)/g)].map((m) => `${f.path}: ${m[1]}`));
    expect(titles.every((t) => /i18n\.t\(/.test(t))).toBe(true);
  });

  it('shows an error only through its fixed wording: no screen reads a backend message or stack', () => {
    expect(hits(/\b(error|err|e|problem)\.(message|stack)\b/, ['staff/ErrorBoundary.tsx'])).toEqual([]);
  });

  it('drops data from memory when a screen closes: both query caches keep nothing after unmount', () => {
    const staff = all.find((f) => f.path === 'staff/App.tsx')?.text ?? '';
    const patient = all.find((f) => f.path === 'patient/portal/PortalApp.tsx')?.text ?? '';
    for (const text of [staff, patient]) {
      expect(text).toMatch(/new QueryClient\(\{[^)]*queries: \{[^}]*gcTime: 0/);
      expect(text).toMatch(/mutations: \{ gcTime: 0 \}/);
    }
  });

  it('clears the query cache on every sign-out path', () => {
    const staff = all.find((f) => f.path === 'staff/auth/AuthProvider.tsx')?.text ?? '';
    const patient = all.find((f) => f.path === 'patient/portal/PortalApp.tsx')?.text ?? '';
    expect(staff).toMatch(/queryClient\.clear\(\)/);
    expect(patient.match(/queryClient\.clear\(\)/g)?.length).toBeGreaterThanOrEqual(2); // signed out elsewhere, and a sign-out press
  });
});
