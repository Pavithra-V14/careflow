import { resolve } from 'node:path';
import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import { CONFIG_GLOBAL, pickExposedEnv } from './env-allowlist.ts';
import { buildCsp } from './csp.ts';

// Two entry points (patient, staff), one token package, one i18n setup, one API client.
export default defineConfig(({ mode }) => {
  // The empty prefix makes loadEnv return every name; pickExposedEnv then keeps ONLY the allowlist.
  // Do not turn this into envPrefix: a broad prefix would expose every matching name.
  const exposed = pickExposedEnv(loadEnv(mode, process.cwd(), ''));
  const apiTarget = exposed.CAREFLOW_API_BASE_URL;
  const csp = buildCsp({ supabaseUrl: exposed.SUPABASE_URL, apiBaseUrl: exposed.CAREFLOW_API_BASE_URL });
  return {
    plugins: [
      react(),
      {
        // Production build only (apply: 'build'): the policy also ships as dist/csp-policy.txt for a host that sets headers.
        name: 'careflow-csp-policy',
        apply: 'build',
        generateBundle() {
          this.emitFile({ type: 'asset', fileName: 'csp-policy.txt', source: csp + '\n' });
        },
      },
    ],
    // Explicit and narrow on purpose: nothing in this project uses VITE_ names, and a broad prefix would leak.
    envPrefix: 'VITE_',
    define: { [CONFIG_GLOBAL]: JSON.stringify(exposed) },
    build: {
      rollupOptions: {
        input: {
          patient: resolve(import.meta.dirname, 'patient/index.html'),
          'patient-sign-in': resolve(import.meta.dirname, 'patient/sign-in/index.html'),
          'patient-sign-up': resolve(import.meta.dirname, 'patient/sign-up/index.html'),
          'patient-privacy': resolve(import.meta.dirname, 'patient/privacy/index.html'),
          staff: resolve(import.meta.dirname, 'staff/index.html'),
        },
      },
    },
    // Stage 1 of the CSP rollout: the production policy is served REPORT-ONLY by `vite preview`, and
    // e2e/csp.spec.ts fails on any violation. Stage 2 enforces it in the built HTML.
    preview: { headers: { 'Content-Security-Policy-Report-Only': csp } },
    server: {
      // Development only: same-origin calls to the API avoid CORS. Production CORS is a backend matter
      // (docs/frontend-backend-requests.md).
      proxy: apiTarget ? { '/v1': apiTarget, '/readyz': apiTarget } : {},
    },
  };
});
