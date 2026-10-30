import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

// Static guards over the application source. They encode rules from CLAUDE.md and the F2 brief so a later
// change cannot quietly break them.
const SRC = resolve(import.meta.dirname, '../src');

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? files(p) : [p];
  });
}
const sources = files(SRC)
  .filter((f) => /\.(ts|tsx|css)$/.test(f) && !f.endsWith('schema.d.ts'))
  .map((f) => ({ file: relative(SRC, f).replaceAll('\\', '/'), text: readFileSync(f, 'utf8') }));

const offenders = (re: RegExp, only: (file: string) => boolean = () => true) => sources.filter((s) => only(s.file) && re.test(s.text)).map((s) => s.file);

describe('source guards', () => {
  it('never uses a secret key or a service-role key', () => {
    expect(offenders(/sb_secret_|SUPABASE_SECRET_KEY|service_role|SERVICE_ROLE|DATABASE_URL|CAREFLOW_ENC_SECRET/)).toEqual([]);
  });

  it('has no staff sign-up, OTP or OAuth path (staff are invitation-only)', () => {
    expect(offenders(/\.signUp\s*\(|signInWithOtp|signInWithOAuth|signInWithIdToken|resetPasswordForEmail|inviteUserByEmail/)).toEqual([]);
    expect(offenders(/sign-?up/i, (f) => f.startsWith('staff/'))).toEqual([]);
  });

  it('writes to localStorage only for the language choice, and never touches tokens in storage directly', () => {
    expect(offenders(/(?<!\w)localStorage\??[.[]/, (f) => f !== 'shared/i18n/index.ts')).toEqual([]);
    expect(offenders(/(?<!\w)sessionStorage\??[.[]/, (f) => f !== 'staff/activeRole.ts')).toEqual([]);
    // supabase-js keeps its own session in its default storage; nothing here reads or writes it by hand.
    expect(offenders(/sb-.*-auth-token|getItem\(['"]sb-|access_token|refresh_token/, (f) => f !== 'staff/auth/supabasePort.ts')).toEqual([]);
  });

  it('never logs', () => {
    expect(offenders(/(?<![\w.])console\.(log|info|warn|error|debug|trace)\s*\(/)).toEqual([]);
  });

  it('never injects raw HTML or evaluates strings', () => {
    expect(offenders(/dangerouslySetInnerHTML|innerHTML\s*=|\beval\s*\(|new Function\s*\(|document\.write/)).toEqual([]);
  });

  it('references no external host: nothing loads from, or posts to, a third party', () => {
    const urls = sources.flatMap((s) => [...s.text.matchAll(/https?:\/\/[^\s'"`)<]+/g)].map((m) => `${s.file}: ${m[0]}`));
    // The only absolute URLs allowed are in comments/examples about the Supabase project and the SVG namespace.
    expect(urls.filter((u) => !/w3\.org\/2000\/svg/.test(u))).toEqual([]);
  });

  it('uses no VITE_-prefixed or other environment variable beyond the DEV flag', () => {
    expect(offenders(/import\.meta\.env\.(?!DEV\b)/)).toEqual([]);
    expect(offenders(/process\.env/)).toEqual([]);
  });

  it('keeps analytics, fonts and CDNs out', () => {
    expect(offenders(/google-analytics|googletagmanager|gtag\(|segment\.|mixpanel|hotjar|sentry|fonts\.googleapis|fonts\.gstatic|cdn\./i)).toEqual([]);
  });
});
