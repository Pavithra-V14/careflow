import '@testing-library/jest-dom/vitest';
import { afterEach, vi } from 'vitest';
import { cleanup } from '@testing-library/react';

import { resetScreenFocus } from '../src/staff/components/ScreenHeading';

afterEach(() => {
  cleanup();
  resetScreenFocus();
  vi.unstubAllGlobals();
  try {
    sessionStorage.clear();
  } catch {
    /* ignore */
  }
});

// jsdom has no <dialog> modal support; this minimal shim lets the step-up dialog open and close in unit tests.
if (typeof HTMLDialogElement !== 'undefined' && !HTMLDialogElement.prototype.showModal) {
  HTMLDialogElement.prototype.showModal = function showModal(this: HTMLDialogElement) {
    this.setAttribute('open', '');
  };
  HTMLDialogElement.prototype.close = function close(this: HTMLDialogElement) {
    this.removeAttribute('open');
  };
}
