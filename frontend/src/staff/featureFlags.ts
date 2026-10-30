// Switches that are off until the backend supports them. Changing one is a deliberate, reviewed edit, and a test
// pins each default.

/**
 * Send the active-role header (X-CareFlow-Active-Role) on staff API calls. OFF: the backend does not read it yet
 * (docs/design/active-role.md is a proposal), and an unknown request header needs production CORS to allow it.
 * Turn on only after both are done.
 */
export const SEND_ACTIVE_ROLE_HEADER = false;
