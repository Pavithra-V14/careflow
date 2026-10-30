import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { axe } from 'vitest-axe';
import * as matchers from 'vitest-axe/matchers';
import { initI18n, setLanguage } from '../src/shared/i18n';
import { App as PatientApp } from '../src/patient/App';
import { App as StaffApp } from '../src/staff/App';

expect.extend(matchers);

// jsdom cannot compute colours, so contrast is checked by tokens.test.ts and by axe in a real browser (e2e).
const axeOptions = { rules: { 'color-contrast': { enabled: false } } };

describe.each([
  ['patient', PatientApp],
  ['staff', StaffApp],
] as const)('%s shell accessibility', (_name, App) => {
  it.each(['en', 'ta'] as const)('has no axe violations in %s', async (lng) => {
    initI18n('en');
    await setLanguage(lng);
    const { container } = render(<App />);
    expect(await axe(container, axeOptions)).toHaveNoViolations();
    await setLanguage('en');
  });
});
