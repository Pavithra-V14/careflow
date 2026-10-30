import { describe, expect, it } from 'vitest';
import { resolveConfig } from 'vite';
import { FLAGS_GLOBAL } from '../env-allowlist';
import { SEND_ACTIVE_ROLE_HEADER, mergeActionsEnabled } from '../src/staff/featureFlags';

// The merge approve / unmerge buttons stay OFF until the step-up claim spike is recorded. These tests pin that.

async function flagsFor(mode: string): Promise<{ mergeActions: boolean }> {
  const cfg = await resolveConfig({ configFile: 'vite.config.ts', root: process.cwd(), mode }, 'build', mode);
  return JSON.parse(String(cfg.define?.[FLAGS_GLOBAL])) as { mergeActions: boolean };
}

describe('feature flags', () => {
  it('has merge actions OFF in the unit-test build', () => {
    expect(mergeActionsEnabled()).toBe(false);
  });

  it.each(['production', 'development', 'staging', 'test', ''])('compiles merge actions to false for mode "%s"', async (mode) => {
    expect((await flagsFor(mode || 'production')).mergeActions).toBe(false);
  });

  it('turns merge actions on only for the mock-backed e2e mode', async () => {
    expect((await flagsFor('e2e')).mergeActions).toBe(true);
  });

  it('cannot be switched on by an environment variable of any name', async () => {
    const planted = ['CAREFLOW_MERGE_ACTIONS', 'MERGE_ACTIONS', 'VITE_MERGE_ACTIONS', 'CAREFLOW_FLAGS'];
    for (const n of planted) process.env[n] = 'true';
    try {
      expect((await flagsFor('production')).mergeActions).toBe(false);
    } finally {
      for (const n of planted) delete process.env[n];
    }
  });

  it('keeps the active-role header off as well', () => {
    expect(SEND_ACTIVE_ROLE_HEADER).toBe(false);
  });
});
