import { defineConfig } from '@playwright/test';

// Visual regression baselines (docs/frontend-status.md, "Visual baselines"). Same mock-backed build and server as the ordinary browser tests
// (nothing real is contacted), but only e2e-visual/visual.spec.ts runs. Baselines live in e2e-visual/baselines (one folder, no platform suffix
// in the name) and are made and checked on the machine that made them: fonts and anti-aliasing differ between operating systems, so this is a
// LOCAL check, not a CI job. Everything that could move is held still: a fixed clock, UTC, no animation or caret, reduced motion, a fixed scale.
export default defineConfig({
  testDir: 'e2e-visual',
  outputDir: 'test-results-visual',
  snapshotPathTemplate: '{testDir}/baselines/{arg}{ext}',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  reporter: [['list']],
  expect: { toHaveScreenshot: { animations: 'disabled', caret: 'hide', scale: 'css', maxDiffPixelRatio: 0.002 } },
  use: {
    baseURL: 'http://127.0.0.1:4173',
    browserName: 'chromium',
    timezoneId: 'UTC',
    locale: 'en-GB',
    colorScheme: 'light',
    reducedMotion: 'reduce',
    deviceScaleFactor: 1,
  },
  webServer: {
    command: 'node scripts/e2e-server.mjs',
    url: 'http://127.0.0.1:4173/patient/',
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
