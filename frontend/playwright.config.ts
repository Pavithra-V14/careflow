import { defineConfig } from '@playwright/test';

// Builds with mock origins into dist-e2e and serves it (scripts/e2e-server.mjs); no manual build step.
export default defineConfig({
  testDir: 'e2e',
  outputDir: 'test-results',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30_000,
  reporter: [['list']],
  use: { baseURL: 'http://127.0.0.1:4173', browserName: 'chromium' },
  webServer: {
    command: 'node scripts/e2e-server.mjs',
    url: 'http://127.0.0.1:4173/patient/',
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
