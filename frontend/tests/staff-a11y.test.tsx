import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'vitest-axe';
import * as matchers from 'vitest-axe/matchers';
import { initI18n, setLanguage } from '../src/shared/i18n';
import { App } from '../src/staff/App';
import { CLINIC_A, CLINIC_B, GOOD_PASSWORD, json, makeFakePort, meBody, stubApi } from './helpers/fakes';

expect.extend(matchers);
// jsdom cannot compute colours: contrast is covered by tokens.test.ts and by axe in a real browser (e2e).
const axeOptions = { rules: { 'color-contrast': { enabled: false } } };

async function reach(name: string): Promise<void> {
  const user = userEvent.setup();
  if (name === 'sign-in') {
    const { port } = makeFakePort();
    render(<App port={port} apiBaseUrl="http://api.test" />);
    await screen.findByLabelText(/^(Work email|பணி மின்னஞ்சல்)$/);
    return;
  }
  if (name === 'enrolment' || name === 'challenge') {
    const { port } = makeFakePort({ enrolled: name === 'challenge' });
    render(<App port={port} apiBaseUrl="http://api.test" />);
    await user.type(await screen.findByLabelText(/^(Work email|பணி மின்னஞ்சல்)$/), 'a@b.invalid');
    await user.type(screen.getByLabelText(/^(Password|கடவுச்சொல்)$/), GOOD_PASSWORD);
    await user.click(screen.getByRole('button', { name: /^(Sign in|உள்நுழைக)$/ }));
    await screen.findByLabelText(/^(6-digit code|6 இலக்கக் குறியீடு)$/);
    return;
  }
  const { port } = makeFakePort({ signedInAt: 'aal2' });
  const roles = name === 'role-picker' ? [{ clinic_id: CLINIC_A, role: 'nurse' }, { clinic_id: CLINIC_B, role: 'admin' }] : [{ clinic_id: CLINIC_A, role: 'doctor' }];
  stubApi(() => (name === 'forbidden' ? json({}, 403) : json(meBody(roles))));
  render(<App port={port} apiBaseUrl="http://api.test" />);
  if (name === 'forbidden') await screen.findByRole('alert');
  else if (name === 'role-picker') await screen.findAllByRole('radio');
  else await screen.findByRole('group', { name: /Session|அமர்வு/ });
}


describe.each(['sign-in', 'enrolment', 'challenge', 'role-picker', 'console', 'forbidden'])('staff %s screen', (name) => {
  it.each(['en', 'ta'] as const)('has no axe violations in %s and a single h1', async (lng) => {
    initI18n('en');
    await setLanguage(lng);
    await reach(name);
    expect(await axe(document.body, axeOptions)).toHaveNoViolations();
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
  });
});

describe('staff screens use the whole viewport sensibly', () => {
  it('labels every form control', async () => {
    await reach('sign-in');
    for (const input of document.querySelectorAll('input')) expect(input.labels?.length ?? 0, input.outerHTML).toBeGreaterThan(0);
  });
});
