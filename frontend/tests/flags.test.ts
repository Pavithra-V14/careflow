import { describe, expect, it } from 'vitest';
import { resolveConfig } from 'vite';
import { FLAGS_GLOBAL } from '../env-allowlist';
import { SEND_ACTIVE_ROLE_HEADER, devSkipStaffMfa, erasureActionsEnabled, mergeActionsEnabled } from '../src/staff/featureFlags';

// The merge approve / unmerge buttons stay OFF until the step-up claim spike is recorded. These tests pin that.

async function flagsFor(mode: string): Promise<{ mergeActions: boolean; erasureActions: boolean }> {
  const cfg = await resolveConfig({ configFile: 'vite.config.ts', root: process.cwd(), mode }, 'build', mode);
  return JSON.parse(String(cfg.define?.[FLAGS_GLOBAL])) as { mergeActions: boolean; erasureActions: boolean };
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

  it('has erasure actions OFF in every real build and on only in the mock-backed e2e mode', async () => {
    expect(erasureActionsEnabled()).toBe(false);
    for (const mode of ['production', 'development', 'staging', 'test']) expect((await flagsFor(mode)).erasureActions).toBe(false);
    expect((await flagsFor('e2e')).erasureActions).toBe(true);
  });

  // DEV ONLY switch that skips the staff second factor (VITE_DEV_SKIP_STAFF_MFA). It must be impossible in any build.
  describe('dev skip of the staff second factor', () => {
    async function devSkip(command: 'build' | 'serve', mode: string): Promise<boolean> {
      const cfg = await resolveConfig({ configFile: 'vite.config.ts', root: process.cwd(), mode }, command, mode);
      return (JSON.parse(String(cfg.define?.[FLAGS_GLOBAL])) as { devSkipStaffMfa: boolean }).devSkipStaffMfa;
    }
    const withVar = async (value: string | undefined, run: () => Promise<void>) => {
      const before = process.env.VITE_DEV_SKIP_STAFF_MFA;
      if (value === undefined) delete process.env.VITE_DEV_SKIP_STAFF_MFA;
      else process.env.VITE_DEV_SKIP_STAFF_MFA = value;
      try {
        await run();
      } finally {
        if (before === undefined) delete process.env.VITE_DEV_SKIP_STAFF_MFA;
        else process.env.VITE_DEV_SKIP_STAFF_MFA = before;
      }
    };

    it('is off in the unit-test build', () => {
      expect(devSkipStaffMfa()).toBe(false);
    });

    it('is off by default in the dev server and in every build', async () => {
      await withVar(undefined, async () => {
        expect(await devSkip('serve', 'development')).toBe(false);
        for (const mode of ['production', 'development', 'staging', 'e2e']) expect(await devSkip('build', mode)).toBe(false);
      });
    });

    it('is on only for the dev server when the shell variable is exactly 1', async () => {
      await withVar('1', async () => expect(await devSkip('serve', 'development')).toBe(true));
      for (const v of ['0', 'true', 'yes', '']) await withVar(v, async () => expect(await devSkip('serve', 'development')).toBe(false));
    });

    it('is NEVER on in a production build, or any build, even with the variable set', async () => {
      await withVar('1', async () => {
        for (const mode of ['production', 'development', 'staging', 'e2e', 'test']) expect(await devSkip('build', mode)).toBe(false);
        expect(await devSkip('serve', 'production')).toBe(false);
        expect(await devSkip('serve', 'e2e')).toBe(false);
      });
    });
  });

  it('keeps the active-role header off as well', () => {
    expect(SEND_ACTIVE_ROLE_HEADER).toBe(false);
  });
});
