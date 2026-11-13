import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { initI18n, setLanguage } from '../src/shared/i18n';
import { App } from '../src/staff/App';
import { CLINIC_A, GOOD_PASSWORD, json, makeFakePort, meBody, stubApi } from './helpers/fakes';

// MOCK-ONLY. DEV ONLY switch: with it ON the staff console is reached with the password alone, and every page shows a banner.
// tests/flags.test.ts pins that the switch cannot be on in any build. Here the flag is mocked on for the unit environment only.
vi.mock('../src/staff/featureFlags', async (original) => ({ ...(await original<typeof import('../src/staff/featureFlags')>()), devSkipStaffMfa: () => true }));

beforeEach(async () => {
  initI18n('en');
  await setLanguage('en');
});

describe('dev skip of the staff second factor (flag mocked ON)', () => {
  it('shows the banner on the sign-in page, and reaches the console with the password only: no enrolment, no challenge', async () => {
    const { port } = makeFakePort();
    const user = userEvent.setup();
    stubApi(() => json(meBody([{ clinic_id: CLINIC_A, role: 'front_desk' }])));
    render(<App port={port} apiBaseUrl="http://api.test" />);
    expect(await screen.findByText(/DEVELOPMENT ONLY: the second factor/)).toBeInTheDocument();
    await user.type(await screen.findByLabelText('Work email'), 'staff@example.invalid');
    await user.type(screen.getByLabelText('Password'), GOOD_PASSWORD);
    await user.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByText('Front desk')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Set up your authenticator app' })).toBeNull();
    expect(port.enrollTotp).not.toHaveBeenCalled();
    expect(screen.getByText(/DEVELOPMENT ONLY: the second factor/)).toBeInTheDocument(); // still shown on the console
  });
});
