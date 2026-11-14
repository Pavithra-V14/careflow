import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { idleMinutes, useIdleSignOut } from '../src/shared/idle';

// Idle minutes have NO default (build-time settings): with nothing set the product signs nobody out for being idle.
describe('idle sign-out', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('is OFF for both audiences when the build sets nothing', () => {
    expect(idleMinutes('staff')).toBeNull();
    expect(idleMinutes('patient')).toBeNull();
  });

  it('does nothing when off, when nobody is signed in, or for a bad number', () => {
    const onIdle = vi.fn();
    renderHook(() => useIdleSignOut(null, true, onIdle));
    renderHook(() => useIdleSignOut(5, false, onIdle));
    renderHook(() => useIdleSignOut(0, true, onIdle));
    renderHook(() => useIdleSignOut(Number.NaN, true, onIdle));
    act(() => void vi.advanceTimersByTime(24 * 60 * 60_000));
    expect(onIdle).not.toHaveBeenCalled();
  });

  it('warns for the last minute, then signs out once', () => {
    const onIdle = vi.fn();
    const { result } = renderHook(() => useIdleSignOut(5, true, onIdle));
    act(() => void vi.advanceTimersByTime(3 * 60_000));
    expect(result.current.secondsLeft).toBeNull();
    act(() => void vi.advanceTimersByTime(60_000 + 500));
    expect(result.current.secondsLeft).toBe(60); // the whole last minute is left
    expect(onIdle).not.toHaveBeenCalled();
    act(() => void vi.advanceTimersByTime(60_000));
    expect(onIdle).toHaveBeenCalledTimes(1);
    expect(result.current.secondsLeft).toBeNull();
    act(() => void vi.advanceTimersByTime(60 * 60_000));
    expect(onIdle).toHaveBeenCalledTimes(1);
  });

  it('activity before the warning keeps the person signed in and the countdown starts again', () => {
    const onIdle = vi.fn();
    const { result } = renderHook(() => useIdleSignOut(5, true, onIdle));
    act(() => void vi.advanceTimersByTime(3 * 60_000 + 30_000));
    act(() => void window.dispatchEvent(new KeyboardEvent('keydown', { key: 'a' })));
    act(() => void vi.advanceTimersByTime(3 * 60_000 + 30_000));
    expect(onIdle).not.toHaveBeenCalled();
    expect(result.current.secondsLeft).toBeNull();
    act(() => void vi.advanceTimersByTime(2 * 60_000));
    expect(onIdle).toHaveBeenCalledTimes(1);
  });

  it('during the warning only "stay" keeps the person: stray activity does not', () => {
    const onIdle = vi.fn();
    const { result } = renderHook(() => useIdleSignOut(5, true, onIdle));
    act(() => void vi.advanceTimersByTime(4 * 60_000 + 5_000));
    expect(result.current.secondsLeft).not.toBeNull();
    act(() => void window.dispatchEvent(new KeyboardEvent('keydown', { key: 'a' })));
    act(() => void window.dispatchEvent(new Event('pointerdown')));
    act(() => void vi.advanceTimersByTime(60_000));
    expect(onIdle).toHaveBeenCalledTimes(1);
  });

  it('"stay" clears the warning and starts the whole countdown again', () => {
    const onIdle = vi.fn();
    const { result } = renderHook(() => useIdleSignOut(5, true, onIdle));
    act(() => void vi.advanceTimersByTime(4 * 60_000 + 10_000));
    expect(result.current.secondsLeft).not.toBeNull();
    act(() => result.current.stay());
    expect(result.current.secondsLeft).toBeNull();
    act(() => void vi.advanceTimersByTime(3 * 60_000));
    expect(onIdle).not.toHaveBeenCalled();
    act(() => void vi.advanceTimersByTime(3 * 60_000));
    expect(onIdle).toHaveBeenCalledTimes(1);
  });

  it('checks the clock when a hidden tab becomes visible again', () => {
    const onIdle = vi.fn();
    renderHook(() => useIdleSignOut(5, true, onIdle));
    act(() => {
      vi.setSystemTime(Date.now() + 10 * 60_000); // the timers did not run while the tab was hidden
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(onIdle).toHaveBeenCalledTimes(1);
  });

  it('stops listening when the person signs out or the screen closes', () => {
    const onIdle = vi.fn();
    const { rerender, unmount } = renderHook(({ active }) => useIdleSignOut(5, active, onIdle), { initialProps: { active: true } });
    rerender({ active: false });
    act(() => void vi.advanceTimersByTime(60 * 60_000));
    expect(onIdle).not.toHaveBeenCalled();
    rerender({ active: true });
    unmount();
    act(() => void vi.advanceTimersByTime(60 * 60_000));
    expect(onIdle).not.toHaveBeenCalled();
  });
});
