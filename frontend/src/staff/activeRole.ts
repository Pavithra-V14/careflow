import { grantKey, type Grant } from './me';

const KEY = 'careflow.activeRole';

// The active role is a per-tab convenience (sessionStorage, not localStorage): it holds a clinic id and a role
// name, never a token. Every access may throw (blocked storage), so everything is wrapped.
export function readStoredGrant(grants: readonly Grant[]): Grant | null {
  try {
    const stored = globalThis.sessionStorage?.getItem(KEY);
    return grants.find((g) => grantKey(g) === stored) ?? null;
  } catch {
    return null;
  }
}

export function storeGrant(grant: Grant | null): void {
  try {
    if (grant) globalThis.sessionStorage?.setItem(KEY, grantKey(grant));
    else globalThis.sessionStorage?.removeItem(KEY);
  } catch {
    /* ignore */
  }
}
