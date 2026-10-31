import { defineConfig } from '@playwright/test';

// The LIVE run: a real browser window, against a dev server and a RUNNING backend. Started only by `npm run e2e:live`
// (scripts/e2e-live.mjs), which checks both are up. Never part of CI and never picked up by the default config, whose
// testDir is `e2e`. No webServer here: it uses the dev server you already have running.
export default defineConfig({
  testDir: 'e2e-live',
  outputDir: 'test-results-live',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 20 * 60_000,
  reporter: [['list']],
  use: { baseURL: process.env.LIVE_BASE_URL ?? 'http://127.0.0.1:5173', browserName: 'chromium', headless: false },
});
