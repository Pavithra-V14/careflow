import { useEffect, useRef } from 'react';

/**
 * Idle sign-out: after this many minutes with no pointer, key, touch or scroll activity in the page, the person is signed out and
 * everything on screen is dropped. BUILD-SPEC sections 18 to 21 give NO duration, and a duration is a privacy and safety decision, so
 * none is chosen here: `null` means OFF. The owner supplies an approved number for each audience (docs/frontend-backend-requests.md,
 * item 34) and it is set here as a deliberate, reviewed edit. A test pins both as null until then.
 */
export const IDLE_SIGN_OUT_MINUTES: { readonly staff: number | null; readonly patient: number | null } = { staff: null, patient: null };

const ACTIVITY = ['pointerdown', 'keydown', 'touchstart', 'wheel', 'scroll'] as const;
const CHECK_EVERY_MS = 15_000;

/**
 * Calls `onIdle` once when `minutes` pass with no activity. Off when `minutes` is null or not a positive finite number, and while
 * `active` is false (nobody is signed in). It compares clock times rather than counting timer ticks, because a browser slows the timers
 * of a hidden tab: the check also runs the moment the tab becomes visible again.
 */
export function useIdleSignOut(minutes: number | null, active: boolean, onIdle: () => void): void {
  const callback = useRef(onIdle);
  useEffect(() => {
    callback.current = onIdle;
  });
  useEffect(() => {
    if (!active || minutes === null || !Number.isFinite(minutes) || minutes <= 0) return;
    const limit = minutes * 60_000;
    let last = Date.now();
    let fired = false;
    const check = () => {
      if (fired || Date.now() - last < limit) return;
      fired = true;
      callback.current();
    };
    const mark = () => {
      last = Date.now();
    };
    const onVisible = () => {
      if (document.visibilityState === 'visible') check();
    };
    for (const name of ACTIVITY) window.addEventListener(name, mark, { passive: true, capture: true });
    document.addEventListener('visibilitychange', onVisible);
    const timer = window.setInterval(check, Math.min(CHECK_EVERY_MS, limit));
    return () => {
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
      for (const name of ACTIVITY) window.removeEventListener(name, mark, { capture: true });
    };
  }, [minutes, active]);
}
