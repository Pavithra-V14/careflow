import { resolve } from 'node:path';
import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import { CONFIG_GLOBAL, FLAGS_GLOBAL, assertNoSecretShapedValues, pickExposedEnv } from './env-allowlist.ts';
import { buildCsp, injectCspMeta } from './csp.ts';

// Two entry points (patient, staff), one token package, one i18n setup, one API client.
export default defineConfig(({ mode, command }) => {
  // The empty prefix makes loadEnv return every name; pickExposedEnv then keeps ONLY the allowlist.
  // Do not turn this into envPrefix: a broad prefix would expose every matching name.
  const exposed = pickExposedEnv(loadEnv(mode, process.cwd(), ''));
  assertNoSecretShapedValues(exposed);
  // Idle sign-out minutes have NO default. Only the demo and the browser-test builds fill in LABELLED PLACEHOLDERS (15 staff, 10 patients);
  // the real values are the privacy owner's (docs/frontend-live-checklist.md, pre-launch steps). A real build leaves them empty = off.
  const placeholders = mode === 'demo' || mode === 'e2e';
  const idlePlaceholder = placeholders && (exposed.CAREFLOW_IDLE_MINUTES_STAFF === '' || exposed.CAREFLOW_IDLE_MINUTES_PATIENT === '');
  if (placeholders) {
    exposed.CAREFLOW_IDLE_MINUTES_STAFF ||= '15';
    exposed.CAREFLOW_IDLE_MINUTES_PATIENT ||= '10';
  }
  const apiTarget = exposed.CAREFLOW_API_BASE_URL;
  const csp = buildCsp({ supabaseUrl: exposed.SUPABASE_URL, apiBaseUrl: exposed.CAREFLOW_API_BASE_URL });
  return {
    plugins: [
      react(),
      {
        // Production build only (apply: 'build'), ENFORCED: the policy is a <meta> tag in every built page and also
        // ships as dist/csp-policy.txt for a host that can send it as a header (frame-ancestors, report-uri).
        name: 'careflow-csp-policy',
        apply: 'build',
        transformIndexHtml: { order: 'post', handler: (html: string) => injectCspMeta(html, csp) },
        generateBundle() {
          this.emitFile({ type: 'asset', fileName: 'csp-policy.txt', source: csp + '\n' });
          // A build with placeholder idle minutes says so in a file next to the policy, so nobody mistakes it for a real configuration.
          if (idlePlaceholder) {
            this.emitFile({
              type: 'asset',
              fileName: 'idle-settings.txt',
              source: `PLACEHOLDER (${mode} build): idle sign-out is ${exposed.CAREFLOW_IDLE_MINUTES_STAFF} minutes for staff and ${exposed.CAREFLOW_IDLE_MINUTES_PATIENT} for patients. These are not the privacy owner's values. A real build sets CAREFLOW_IDLE_MINUTES_STAFF and CAREFLOW_IDLE_MINUTES_PATIENT.\n`,
            });
          }
        },
      },
    ],
    // Explicit and narrow on purpose: nothing in this project uses VITE_ names, and a broad prefix would leak.
    envPrefix: 'VITE_',
    // Flags are compile-time constants, never environment variables. mergeActions is on ONLY in `--mode e2e` (mock browser tests).
    // devSkipStaffMfa: DEV ONLY. True only for the dev server (command 'serve', mode 'development') AND only when the starting shell set
    // VITE_DEV_SKIP_STAFF_MFA=1. It reads process.env, never a file (loadEnv is not used for it), and is false in every build.
    define: {
      [CONFIG_GLOBAL]: JSON.stringify(exposed),
      [FLAGS_GLOBAL]: JSON.stringify({
        mergeActions: mode === 'e2e',
        erasureActions: mode === 'e2e',
        devSkipStaffMfa: command === 'serve' && mode === 'development' && process.env.VITE_DEV_SKIP_STAFF_MFA === '1',
      }),
    },
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
    server: {
      // Development only: same-origin calls to the API avoid CORS. Production CORS is a backend matter
      // (docs/frontend-backend-requests.md).
      proxy: apiTarget ? { '/v1': apiTarget, '/readyz': apiTarget } : {},
    },
  };
});
