import { useCallback, useEffect, useRef, useState } from 'react';
import { readConfig } from './config';

/**
 * Idle sign-out. After the configured minutes with no pointer, key, touch or scroll activity the person is signed out and everything on
 * screen is dropped; one minute before that a "still there?" warning asks them to stay. The minutes are two BUILD-TIME settings with NO
 * default (CAREFLOW_IDLE_MINUTES_STAFF and CAREFLOW_IDLE_MINUTES_PATIENT): unset or invalid means off. The real values are the privacy
 * owner's. Only the demo and the browser-test builds fill in labelled placeholders (15 for staff, 10 for patients); see vite.config.ts.
 */
type IdleAudience = 'staff' | 'patient';

export function idleMinutes(audience: IdleAudience): number | null {
  return readConfig().idleMinutes[audience];
}

const ACTIVITY = ['pointerdown', 'keydown', 'touchstart', 'wheel', 'scroll'] as const;
const CHECK_EVERY_MS = 15_000;
const WARN_MS = 60_000;

interface IdleState {
  /** Whole seconds left before sign-out while the warning shows; null otherwise. */
  secondsLeft: number | null;
  /** The person says they are still there: the countdown starts again. */
  stay: () => void;
}

/**
 * Calls `onIdle` once when `minutes` pass with no activity, and reports the last minute as `secondsLeft`. Off when `minutes` is null or
 * not a positive finite number, and while `active` is false (nobody is signed in). It compares clock times rather than counting ticks,
 * because a browser slows the timers of a hidden tab: the check also runs the moment the tab becomes visible again. During the warning
 * only `stay()` keeps the person signed in; stray activity does not, so the answer is deliberate.
 */
export function useIdleSignOut(minutes: number | null, active: boolean, onIdle: () => void): IdleState {
  const callback = useRef(onIdle);
  useEffect(() => {
    callback.current = onIdle;
  });
  const [secondsLeft, setSecondsLeft] = useState<number | null>(null);
  const control = useRef<{ stay: () => void }>({ stay: () => undefined });

  useEffect(() => {
    if (!active || minutes === null || !Number.isFinite(minutes) || minutes <= 0) return;
    const limit = minutes * 60_000;
    const warnAt = limit - Math.min(WARN_MS, limit / 2);
    let last = Date.now();
    let warned = false;
    let fired = false;
    let timer = 0;
    const tick = () => {
      window.clearTimeout(timer);
      if (fired) return;
      const idle = Date.now() - last;
      if (idle >= limit) {
        fired = true;
        setSecondsLeft(null);
        callback.current();
        return;
      }
      if (idle >= warnAt) {
        warned = true;
        setSecondsLeft(Math.max(1, Math.ceil((limit - idle) / 1000)));
        timer = window.setTimeout(tick, 1000);
      } else {
        timer = window.setTimeout(tick, Math.min(warnAt - idle, CHECK_EVERY_MS));
      }
    };
    const mark = () => {
      if (!warned) last = Date.now();
    };
    const onVisible = () => {
      if (document.visibilityState === 'visible') tick();
    };
    const handle = control.current;
    handle.stay = () => {
      last = Date.now();
      warned = false;
      setSecondsLeft(null);
      tick();
    };
    for (const name of ACTIVITY) window.addEventListener(name, mark, { passive: true, capture: true });
    document.addEventListener('visibilitychange', onVisible);
    timer = window.setTimeout(tick, Math.min(warnAt, CHECK_EVERY_MS));
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisible);
      for (const name of ACTIVITY) window.removeEventListener(name, mark, { capture: true });
      handle.stay = () => undefined;
    };
  }, [minutes, active]);

  const stay = useCallback(() => control.current.stay(), []);
  return { secondsLeft: active ? secondsLeft : null, stay };
}
