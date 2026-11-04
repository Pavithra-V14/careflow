import { useSyncExternalStore } from 'react';

// Whether the browser reports a connection. navigator.onLine can say "online" when nothing is reachable, so this only ever
// UPGRADES caution: a banner and held-back writes when it says offline, nothing claimed when it says online.
export const isOffline = (): boolean => typeof navigator !== 'undefined' && navigator.onLine === false;

function subscribe(onChange: () => void): () => void {
  window.addEventListener('online', onChange);
  window.addEventListener('offline', onChange);
  return () => {
    window.removeEventListener('online', onChange);
    window.removeEventListener('offline', onChange);
  };
}

export const useOffline = (): boolean => useSyncExternalStore(subscribe, isOffline, () => false);
