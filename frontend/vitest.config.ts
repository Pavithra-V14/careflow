import { defineConfig } from 'vitest/config';

export default defineConfig({
  define: {
    __CAREFLOW_FLAGS__: JSON.stringify({ mergeActions: false }),
    __CAREFLOW_PUBLIC_CONFIG__: JSON.stringify({
      SUPABASE_URL: 'https://example.invalid',
      SUPABASE_PUBLISHABLE_KEY: 'test-publishable-key',
      CAREFLOW_API_BASE_URL: 'http://localhost:8000',
    }),
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['tests/setup.ts'],
    include: ['tests/**/*.test.{ts,tsx}'],
    // No test needs a stylesheet applied (tokens.test.ts reads the file directly), and processing CSS for every file cost about a third of
    // each cold-start render. Measured: the first test of a file pays about 0.7 s of one-off start-up and the rest are 0.1 to 0.8 s.
    css: false,
  },
});
