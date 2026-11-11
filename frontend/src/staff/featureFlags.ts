// Switches that are off until the backend supports them. Changing one is a deliberate, reviewed edit, and a test
// pins each default.

/**
 * Send the active-role header (X-CareFlow-Active-Role: <clinic_id>:<role>) on staff API calls, for users with more than one
 * role only. OFF by default: it is switched on only when the owner says the dev backend has migration 0028 and a live check
 * works (the backend merged the header; production CORS already allows it). A deliberate, reviewed edit with its test.
 */
export const SEND_ACTIVE_ROLE_HEADER = false;

declare const __CAREFLOW_FLAGS__: { mergeActions: boolean; erasureActions?: boolean } | undefined;

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
