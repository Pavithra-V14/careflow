import { describe, expect, it } from 'vitest';
import { resolveConfig } from 'vite';
import { EXPOSED_ENV_NAMES, assertNoSecretShapedValues, pickExposedEnv, CONFIG_GLOBAL } from '../env-allowlist';

describe('env allowlist', () => {
  it('exposes exactly the three agreed names', () => {
    expect([...EXPOSED_ENV_NAMES]).toEqual(['SUPABASE_URL', 'SUPABASE_PUBLISHABLE_KEY', 'CAREFLOW_API_BASE_URL']);
  });

  it('drops every other name, including secrets that share a prefix', () => {
    const picked = pickExposedEnv({
      SUPABASE_URL: 'u',
      SUPABASE_PUBLISHABLE_KEY: 'k',
      CAREFLOW_API_BASE_URL: 'a',
      SUPABASE_SECRET_KEY: 'must-not-appear',
      DATABASE_URL: 'must-not-appear',
      CAREFLOW_ENC_SECRET: 'must-not-appear',
      SUPABASE_SERVICE_ROLE_KEY: 'must-not-appear',
    });
    expect(Object.keys(picked).sort()).toEqual([...EXPOSED_ENV_NAMES].sort());
    expect(JSON.stringify(picked)).not.toContain('must-not-appear');
  });

  it('yields empty strings, never undefined, for missing names', () => {
    expect(pickExposedEnv({})).toEqual({ SUPABASE_URL: '', SUPABASE_PUBLISHABLE_KEY: '', CAREFLOW_API_BASE_URL: '' });
  });
});

describe('vite config', () => {
  it('exposes only the allowlist through define, and never sets a broad envPrefix', async () => {
    const planted = ['DATABASE_URL', 'SUPABASE_SECRET_KEY', 'CAREFLOW_ENC_SECRET'];
    for (const name of planted) process.env[name] = 'must-not-appear';
    try {
      const cfg = await resolveConfig({ configFile: 'vite.config.ts', root: process.cwd() }, 'build');
      expect(Object.keys(cfg.define ?? {})).toEqual([CONFIG_GLOBAL]);
      const defined = JSON.parse(String(cfg.define?.[CONFIG_GLOBAL])) as Record<string, string>;
      expect(Object.keys(defined).sort()).toEqual([...EXPOSED_ENV_NAMES].sort());
      expect(JSON.stringify(cfg.define)).not.toContain('must-not-appear');
      const prefixes = [cfg.envPrefix ?? []].flat();
      for (const p of prefixes) {
        expect(['', 'SUPABASE_', 'CAREFLOW_']).not.toContain(p);
      }
      expect(prefixes).toEqual(['VITE_']);
    } finally {
      for (const name of planted) delete process.env[name];
    }
  });
});

describe('secret-shaped values', () => {
  const ok = { SUPABASE_URL: 'https://x.invalid', SUPABASE_PUBLISHABLE_KEY: 'sb_publishable_abc', CAREFLOW_API_BASE_URL: 'https://api.invalid' };
  it('lets a publishable key through', () => {
    expect(() => assertNoSecretShapedValues(ok)).not.toThrow();
  });
  it('stops the build for a secret key in any exposed slot, naming the variable but never the value', () => {
    // Assembled at run time so no secret-shaped literal is committed (GitHub push protection flags those).
    const secret = ['sb_sec', 'ret_'].join('') + '0123456789abcdef0123456789abcdef';
    for (const name of EXPOSED_ENV_NAMES) {
      let message = '';
      try {
        assertNoSecretShapedValues({ ...ok, [name]: secret });
      } catch (e) {
        message = (e as Error).message;
      }
      expect(message, name).toContain(name);
      expect(message, name).not.toContain(secret);
    }
  });
});
