import '@testing-library/jest-dom/vitest';
import { afterEach, vi } from 'vitest';
import { cleanup, configure } from '@testing-library/react';

// A test that passes alone must not fail because the machine is busy (the first render of a file pays a cold start): findBy and waitFor wait up to 5 s instead of 1 s.
configure({ asyncUtilTimeout: 5000 });

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
