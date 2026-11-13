// Switches that are off until the backend supports them. Changing one is a deliberate, reviewed edit, and a test
// pins each default.

/**
 * Send the active-role header (X-CareFlow-Active-Role: <clinic_id>:<role>) on staff API calls, for users with more than one
 * role only. OFF by default: it is switched on only when the owner says the dev backend has migration 0028 and a live check
 * works (the backend merged the header; production CORS already allows it). A deliberate, reviewed edit with its test.
 */
export const SEND_ACTIVE_ROLE_HEADER = false;

declare const __CAREFLOW_FLAGS__: { mergeActions: boolean; erasureActions?: boolean; devSkipStaffMfa?: boolean } | undefined;

/**
 * DEV ONLY. Lets a staff member reach the console with the password alone (no authenticator enrolment, challenge or step-up
 * prompt) on a DEVELOPMENT project. Compile-time constant set by vite.config.ts ONLY for the dev server (`vite serve`, mode
 * "development") AND only when the shell that started it set VITE_DEV_SKIP_STAFF_MFA=1; never from a file, and never in any
 * build (`vite build`) whatever its mode. The extra `import.meta.env.DEV` check keeps it false in a production bundle even if the
 * constant were somehow present. While on, every staff page shows a banner. The backend still enforces whatever it enforces.
 * Switch it off (just do not set the variable) before any real use. Pinned by tests/flags.test.ts.
 */
export function devSkipStaffMfa(): boolean {
  return import.meta.env.DEV === true && typeof __CAREFLOW_FLAGS__ !== 'undefined' && __CAREFLOW_FLAGS__.devSkipStaffMfa === true;
}

/**
 * Merge APPROVE and UNMERGE buttons. OFF in every real build. These routes need a recently verified second factor, and the
 * `aal` / `amr` claim shape the backend checks has still not been observed in a real token (docs/design/step-up-spike.md,
 * the F2 claim spike). Until that is recorded the buttons stay off. It is a build-time constant, not an environment variable:
 * vite.config.ts sets it true only for `--mode e2e` (the mock-backed browser build), never for development or production,
 * and tests/env-allowlist.test.ts pins that. A function so unit tests can switch it on with vi.mock.
 */
export function mergeActionsEnabled(): boolean {
  return typeof __CAREFLOW_FLAGS__ !== 'undefined' && __CAREFLOW_FLAGS__.mergeActions === true;
}

/**
 * Erasure request, approve and cancel buttons. OFF in every real build, for the same reason as the merge actions: each of these writes
 * needs a recently verified second factor and the `aal` / `amr` claim shape the backend checks has not been observed in a real token.
 * The list of requests is read-only and always shown. Compile-time only, on in `--mode e2e` (the mock browser build), pinned by tests.
 */
export function erasureActionsEnabled(): boolean {
  return typeof __CAREFLOW_FLAGS__ !== 'undefined' && __CAREFLOW_FLAGS__.erasureActions === true;
}
