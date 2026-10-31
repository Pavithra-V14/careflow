// Switches that are off until the backend supports them. Changing one is a deliberate, reviewed edit, and a test
// pins each default.

/**
 * Send the active-role header (X-CareFlow-Active-Role) on staff API calls. OFF: the backend does not read it yet
 * (docs/design/active-role.md is a proposal), and an unknown request header needs production CORS to allow it.
 * Turn on only after both are done.
 */
export const SEND_ACTIVE_ROLE_HEADER = false;

declare const __CAREFLOW_FLAGS__: { mergeActions: boolean } | undefined;

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
