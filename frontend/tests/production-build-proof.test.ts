import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { buildCsp } from '../csp';
// @ts-expect-error plain .mjs script without types
import { CANARIES, buildProduction, verifyDist } from '../scripts/verify-production-build.mjs';

// The production-build proof (scripts/verify-production-build.mjs): what the check finds on small fixtures, and the real production bundle passing it.
const SUPABASE = 'https://mock-project.supabase.invalid';
const API = 'https://mock-api.invalid';
const PAGES = ['patient/index.html', 'patient/sign-in/index.html', 'patient/sign-up/index.html', 'patient/privacy/index.html', 'staff/index.html'];
const dirs: string[] = [];
afterEach(() => {
  for (const d of dirs.splice(0)) rmSync(d, { recursive: true, force: true });
});

function fixture(over: { js?: string; policy?: string; page?: (name: string, policy: string) => string } = {}): string {
  const dir = mkdtempSync(join(tmpdir(), 'prod-proof-'));
  dirs.push(dir);
  const policy = over.policy ?? buildCsp({ supabaseUrl: SUPABASE, apiBaseUrl: API });
  const put = (name: string, text: string) => {
    mkdirSync(join(dir, name, '..'), { recursive: true });
    writeFileSync(join(dir, name), text);
  };
  put('csp-policy.txt', policy + '\n');
  put('assets/main.js', over.js ?? 'var f={mergeActions:!1,erasureActions:!1,devPatientPasswordLogin:!1,devSkipStaffMfa:!1};');
  for (const p of PAGES) put(p, over.page ? over.page(p, policy) : `<!doctype html><html><head>\n    <meta http-equiv="Content-Security-Policy" content="${policy}" />\n<script type="module" src="/assets/main.js"></script></head><body></body></html>`);
  return dir;
}

describe('production build proof: the check itself', () => {
  it('passes a clean build', () => {
    expect(verifyDist(fixture())).toEqual([]);
  });

  it.each(['devSkipStaffMfa', 'devPatientPasswordLogin'])('fails when %s is compiled on, or is missing so it cannot be proven off', (flag) => {
    const base = { mergeActions: '!1', devPatientPasswordLogin: '!1', devSkipStaffMfa: '!1' };
    const on = Object.entries({ ...base, [flag]: '!0' }).map(([k, v]) => `${k}:${v}`).join(',');
    expect((verifyDist(fixture({ js: `var f={${on}};` })) as string[]).join(' ')).toContain(`${flag} is not compiled to false`);
    const missing = Object.entries(base).filter(([k]) => k !== flag).map(([k, v]) => `${k}:${v}`).join(',');
    expect((verifyDist(fixture({ js: `var f={${missing}};` })) as string[]).join(' ')).toContain(`${flag} is not in the compiled flags object`);
  });

  it('fails on a planted canary, a forbidden marker, a private key block and a connection string, without echoing them', () => {
    const canary = Object.values(CANARIES as Record<string, string>)[1] as string;
    const problems = verifyDist(fixture({ js: `var f={devPatientPasswordLogin:!1,devSkipStaffMfa:!1};var k="${canary}";` })) as string[];
    expect(problems.join(' ')).toContain('canary');
    expect(problems.join(' ')).not.toContain(canary);
    expect((verifyDist(fixture({ js: 'var f={devPatientPasswordLogin:!1,devSkipStaffMfa:!1};var u="DATABASE_URL";' })) as string[]).join(' ')).toContain('DATABASE_URL');
    const pem = ['-----BEGIN RSA ', 'PRIVATE KEY-----'].join('');
    expect((verifyDist(fixture({ js: `var f={devPatientPasswordLogin:!1,devSkipStaffMfa:!1};var k="${pem}";` })) as string[]).join(' ')).toContain('private key');
    const conn = ['postgres', '://u:p@h/d'].join('');
    expect((verifyDist(fixture({ js: `var f={devPatientPasswordLogin:!1,devSkipStaffMfa:!1};var c="${conn}";` })) as string[]).join(' ')).toContain('connection string');
  });

  it('fails when the policy is weakened, allows another origin, or a page lacks it or has an inline script', () => {
    const weak = buildCsp({ supabaseUrl: SUPABASE, apiBaseUrl: API }).replace("script-src 'self'", "script-src 'self' 'unsafe-inline'");
    expect((verifyDist(fixture({ policy: weak })) as string[]).join(' ')).toMatch(/script-src is not exactly|unsafe-inline/);
    const extra = buildCsp({ supabaseUrl: SUPABASE, apiBaseUrl: API }).replace("connect-src 'self'", "connect-src 'self' https://elsewhere.invalid");
    expect((verifyDist(fixture({ policy: extra })) as string[]).join(' ')).toContain('connect-src allows');
    expect((verifyDist(fixture({ page: () => '<!doctype html><html><head></head><body></body></html>' })) as string[]).join(' ')).toContain('does not carry the policy');
    expect((verifyDist(fixture({ page: (_n, policy) => `<html><head>\n    <meta http-equiv="Content-Security-Policy" content="${policy}" /><script>alert(1)</script></head></html>` })) as string[]).join(' ')).toContain('inline script');
  });
});

describe('production build proof: the real production bundle', () => {
  it('builds in the production mode with the demo switches and canary secrets set, and nothing ships', () => {
    const { dist, cleanup } = buildProduction() as { dist: string; cleanup: () => void };
    try {
      expect(verifyDist(dist)).toEqual([]);
    } finally {
      cleanup();
    }
  }, 180_000);
});
