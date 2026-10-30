import { describe, expect, it } from 'vitest';
import { buildCsp, injectCspMeta, originOf } from '../csp';

const policy = buildCsp({ supabaseUrl: 'https://abc.supabase.co/auth/v1', apiBaseUrl: 'https://api.example.invalid/' });
const directive = (name: string) => new RegExp(`(?:^|; )${name} ([^;]*)`).exec(policy)?.[1]?.split(' ') ?? [];

describe('content security policy', () => {
  it('allows connections only to self, the Supabase origin and the API origin', () => {
    expect(directive('connect-src').sort()).toEqual(["'self'", 'https://abc.supabase.co', 'https://api.example.invalid'].sort());
  });

  it('starts from default-src none and allows scripts and styles only from self', () => {
    expect(directive('default-src')).toEqual(["'none'"]);
    expect(directive('script-src')).toEqual(["'self'"]);
    expect(directive('style-src')).toEqual(["'self'"]);
    expect(directive('object-src')).toEqual(["'none'"]);
  });

  it('never allows inline or eval code, wildcards or http: sources', () => {
    expect(policy).not.toMatch(/unsafe-inline|unsafe-eval|unsafe-hashes|\*|\shttp:(\s|;|$)/);
  });

  it('allows data: images only (for the authenticator QR code), not data: scripts', () => {
    expect(directive('img-src')).toEqual(["'self'", 'data:']);
    expect(directive('script-src')).not.toContain('data:');
  });

  it('omits an unconfigured origin instead of emitting an empty or invalid one', () => {
    const bare = buildCsp({ supabaseUrl: '', apiBaseUrl: 'not a url' });
    expect(bare).toContain("connect-src 'self'");
    expect(bare).not.toMatch(/connect-src[^;]*(undefined|null|invalid)/);
  });

  it('refuses non-http origins', () => {
    expect(originOf('javascript:alert(1)')).toBeNull();
    expect(originOf('ftp://x.invalid')).toBeNull();
    expect(originOf('https://a.invalid/path?q=1')).toBe('https://a.invalid');
  });

  it('injects the policy as the first element of head', () => {
    const html = injectCspMeta('<!doctype html><html><head><title>x</title></head></html>', policy);
    expect(html).toMatch(/<head>\s*<meta http-equiv="Content-Security-Policy" content="[^"]+" \/>\s*<title>/);
  });
});
