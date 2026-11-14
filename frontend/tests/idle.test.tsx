import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import { IDLE_SIGN_OUT_MINUTES, useIdleSignOut } from '../src/shared/idle';

// BUILD-SPEC gives no idle duration, so none is chosen: both audiences are OFF (null) until the owner supplies an approved number.
describe('idle sign-out', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('is OFF for both audiences until the owner supplies a number', () => {
    expect(IDLE_SIGN_OUT_MINUTES).toEqual({ staff: null, patient: null });
  });

  it('does nothing when off, when nobody is signed in, or for a bad number', () => {
    const onIdle = vi.fn();
    renderHook(() => useIdleSignOut(null, true, onIdle));
    renderHook(() => useIdleSignOut(5, false, onIdle));
    renderHook(() => useIdleSignOut(0, true, onIdle));
    renderHook(() => useIdleSignOut(Number.NaN, true, onIdle));
    vi.advanceTimersByTime(24 * 60 * 60_000);
    expect(onIdle).not.toHaveBeenCalled();
  });

  it('signs out once after the idle time with no activity', () => {
    const onIdle = vi.fn();
    renderHook(() => useIdleSignOut(5, true, onIdle));
    vi.advanceTimersByTime(4 * 60_000);
    expect(onIdle).not.toHaveBeenCalled();
    vi.advanceTimersByTime(2 * 60_000);
    expect(onIdle).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(60 * 60_000);
    expect(onIdle).toHaveBeenCalledTimes(1);
  });

  it('activity keeps the person signed in, and the countdown starts again', () => {
    const onIdle = vi.fn();
    renderHook(() => useIdleSignOut(5, true, onIdle));
    vi.advanceTimersByTime(4 * 60_000);
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'a' }));
    vi.advanceTimersByTime(4 * 60_000);
    expect(onIdle).not.toHaveBeenCalled();
    vi.advanceTimersByTime(2 * 60_000);
    expect(onIdle).toHaveBeenCalledTimes(1);
  });

  it('checks the clock when a hidden tab becomes visible again', () => {
    const onIdle = vi.fn();
    renderHook(() => useIdleSignOut(5, true, onIdle));
    vi.setSystemTime(Date.now() + 10 * 60_000); // the timers did not run while the tab was hidden
    document.dispatchEvent(new Event('visibilitychange'));
    expect(onIdle).toHaveBeenCalledTimes(1);
  });

  it('stops listening when the person signs out or the screen closes', () => {
    const onIdle = vi.fn();
    const { rerender, unmount } = renderHook(({ active }) => useIdleSignOut(5, active, onIdle), { initialProps: { active: true } });
    rerender({ active: false });
    vi.advanceTimersByTime(60 * 60_000);
    expect(onIdle).not.toHaveBeenCalled();
    rerender({ active: true });
    unmount();
    vi.advanceTimersByTime(60 * 60_000);
    expect(onIdle).not.toHaveBeenCalled();
  });
});
