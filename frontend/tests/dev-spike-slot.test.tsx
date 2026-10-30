import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { initI18n, setLanguage } from '../src/shared/i18n';
import { App } from '../src/staff/App';
import { fakeJwt, makeFakePort, stubApi, json } from './helpers/fakes';

// The claim spike must be usable before the backend is up: it needs the session only. So the development panel is shown where
// the account could not be loaded as well. It must still never show the token.

describe('claim spike without a backend', () => {
  it('shows the development panel on the "account could not be loaded" screen and captures the structure from the session', async () => {
    initI18n('en');
    await setLanguage('en');
    const fake = makeFakePort({ signedInAt: 'aal2' });
    const token = fakeJwt({ aal: 'aal2', amr: [{ method: 'totp', timestamp: 1893456789 }], iat: 1893456789, sub: 'user-id-PLANTED' });
    fake.port.getAccessToken.mockImplementation(async () => token);
    stubApi(() => json({}, 503)); // the backend is down
    render(<App port={fake.port} apiBaseUrl="http://api.test" />);

    expect(await screen.findByRole('alert')).toHaveTextContent('Your account details could not be loaded.');
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Capture claim structure' }));
    const out = await screen.findByLabelText('Developer: claim structure', { selector: 'pre' });
    expect(out.textContent).toContain('<integer, 10 digits>');
    expect(document.body.textContent).not.toContain(token);
    expect(document.body.textContent).not.toContain('PLANTED');
  });

  it('keeps the panel outside the alert region', async () => {
    initI18n('en');
    const fake = makeFakePort({ signedInAt: 'aal2' });
    stubApi(() => json({}, 503));
    render(<App port={fake.port} apiBaseUrl="http://api.test" />);
    const alert = await screen.findByRole('alert');
    expect(alert.querySelector('[data-dev-only]')).toBeNull();
    expect(document.querySelector('[data-dev-only]')).not.toBeNull();
  });
});
