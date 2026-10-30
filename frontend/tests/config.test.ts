import { describe, expect, it } from 'vitest';
import { readConfig } from '../src/shared/config';

const raw = { SUPABASE_URL: 'https://x.invalid', SUPABASE_PUBLISHABLE_KEY: 'pk', CAREFLOW_API_BASE_URL: 'https://api.invalid' };

describe('readConfig', () => {
  it('uses the API base URL in production and same-origin in development', () => {
    expect(readConfig(raw, false).apiBaseUrl).toBe('https://api.invalid');
    expect(readConfig(raw, true).apiBaseUrl).toBe('');
  });
  it('reports not-configured instead of throwing when a value is missing', () => {
    expect(readConfig({ ...raw, SUPABASE_URL: '' }, false).configured).toBe(false);
    expect(readConfig({ ...raw, CAREFLOW_API_BASE_URL: '' }, false).configured).toBe(false);
    expect(readConfig({ ...raw, CAREFLOW_API_BASE_URL: '' }, true).configured).toBe(true);
  });
});
