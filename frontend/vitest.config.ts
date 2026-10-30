import { defineConfig } from 'vitest/config';

export default defineConfig({
  define: {
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
    css: true,
  },
});
