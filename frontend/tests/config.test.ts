import { describe, expect, it } from 'vitest';
import { readConfig } from '../src/shared/config';

const raw = { SUPABASE_URL: 'https://x.invalid', SUPABASE_PUBLISHABLE_KEY: 'pk', CAREFLOW_API_BASE_URL: 'https://api.invalid', CAREFLOW_IDLE_MINUTES_STAFF: '', CAREFLOW_IDLE_MINUTES_PATIENT: '' };

describe('readConfig', () => {
  it('uses the API base URL in production and same-origin in development', () => {
    expect(readConfig(raw, false).apiBaseUrl).toBe('https://api.invalid');
    expect(readConfig(raw, true).apiBaseUrl).toBe('');
  });
  it('has NO default for the idle minutes: unset, empty, zero, negative or not a number all mean off', () => {
    expect(readConfig(raw, false).idleMinutes).toEqual({ staff: null, patient: null });
    for (const bad of ['0', '-5', 'abc', ' ']) expect(readConfig({ ...raw, CAREFLOW_IDLE_MINUTES_STAFF: bad, CAREFLOW_IDLE_MINUTES_PATIENT: bad }, false).idleMinutes).toEqual({ staff: null, patient: null });
  });
  it('reads a positive number of minutes for each audience on its own', () => {
    expect(readConfig({ ...raw, CAREFLOW_IDLE_MINUTES_STAFF: '15' }, false).idleMinutes).toEqual({ staff: 15, patient: null });
    expect(readConfig({ ...raw, CAREFLOW_IDLE_MINUTES_PATIENT: '10' }, false).idleMinutes).toEqual({ staff: null, patient: 10 });
  });
  it('reports not-configured instead of throwing when a value is missing', () => {
    expect(readConfig({ ...raw, SUPABASE_URL: '' }, false).configured).toBe(false);
    expect(readConfig({ ...raw, CAREFLOW_API_BASE_URL: '' }, false).configured).toBe(false);
    expect(readConfig({ ...raw, CAREFLOW_API_BASE_URL: '' }, true).configured).toBe(true);
  });
});
