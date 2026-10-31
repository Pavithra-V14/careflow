import { lazy, Suspense } from 'react';

// The claim-structure panel (the step-up claim spike) exists only in development builds: the import is behind
// import.meta.env.DEV, so the production bundle never contains it (checked by scripts/check-bundle-secrets.mjs).
// It needs only the session, not the backend, so it is also shown where the account could not be loaded, which lets the
// spike run before the API is up.
const DevClaimSpike = import.meta.env.DEV ? lazy(() => import('./DevClaimSpike')) : null;

export function DevSpikeSlot() {
  if (!DevClaimSpike) return null;
  return (
    <Suspense fallback={null}>
      <DevClaimSpike />
    </Suspense>
  );
}
