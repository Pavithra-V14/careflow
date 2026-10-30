import { defineConfig } from '@playwright/test';

// Runs against the production build served by `vite preview` (build first: npm run build).
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
    command: 'npx vite preview --host 127.0.0.1 --port 4173 --strictPort',
    url: 'http://127.0.0.1:4173/patient/',
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
