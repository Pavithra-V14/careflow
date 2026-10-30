import { useEffect, useRef, type ReactNode } from 'react';

// Each screen replaces the previous one in place, so focus moves to its heading: a screen-reader or
// keyboard user is told the screen changed instead of being left on a control that no longer exists.
// The very first screen after a page load is not focused: a keyboard user then starts at the top of the page and
// meets the skip link and the language switcher first. Only later changes of screen move focus.
let firstScreenShown = false;
export function resetScreenFocus(): void {
  firstScreenShown = false;
}

export function ScreenHeading({ id, children }: { id?: string; children: ReactNode }) {
  const ref = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (firstScreenShown) ref.current?.focus();
    firstScreenShown = true;
  }, []);
  return (
    <h1 ref={ref} tabIndex={-1} id={id}>
      {children}
    </h1>
  );
}
