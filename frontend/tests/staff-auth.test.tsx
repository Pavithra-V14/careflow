import { beforeEach, describe, expect, it } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { initI18n, setLanguage } from '../src/shared/i18n';
import { App } from '../src/staff/App';
import { AuthProvider, useAuth } from '../src/staff/auth/AuthProvider';
import { ACCOUNT_ID, CLINIC_A, CLINIC_B, GOOD_CODE, GOOD_PASSWORD, json, makeFakePort, meBody, stubApi } from './helpers/fakes';

// MOCK-ONLY: these drive the real screens and the real API layer against an in-memory auth fake and a fake API.
// They prove this app's logic. They are not evidence of how Supabase or the backend actually behave.

beforeEach(async () => {
  initI18n('en');
  await setLanguage('en');
});

const ME_OK = () => json(meBody([{ clinic_id: CLINIC_A, role: 'front_desk' }]));

async function signInThrough(user: ReturnType<typeof userEvent.setup>, password = GOOD_PASSWORD) {
  await user.type(await screen.findByLabelText('Work email'), 'staff@example.invalid');
  await user.type(screen.getByLabelText('Password'), password);
  await user.click(screen.getByRole('button', { name: 'Sign in' }));
}

describe('staff sign-in', () => {
  it('shows only a sign-in form: no sign-up link or screen, and does not steal focus on first load', async () => {
    const { port } = makeFakePort();
    render(<App port={port} apiBaseUrl="http://api.test" />);
    const heading = await screen.findByRole('heading', { level: 1, name: 'Staff sign in' });
    expect(heading).not.toHaveFocus();
    expect(screen.queryByText(/sign up|register|create (an )?account/i)).toBeNull();
    expect(screen.queryByRole('link', { name: /sign up|register/i })).toBeNull();
  });

  it('shows one generic message for a failed sign-in and never says what was wrong', async () => {
    const { port } = makeFakePort();
    const user = userEvent.setup();
    render(<App port={port} apiBaseUrl="http://api.test" />);
    await signInThrough(user, 'wrong password');
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Sign-in failed. Check your details and try again.');
    expect(alert.textContent).not.toMatch(/no such|not found|unknown (user|email|account)|invalid (email|password)|locked|disabled/i);
    expect(screen.getByLabelText('Password')).toHaveValue('');
    expect(screen.getByRole('heading', { level: 1, name: 'Staff sign in' })).toBeInTheDocument();
  });

  it('puts a user with no authenticator through enrolment, rejects a wrong code, then reaches the console on the right one', async () => {
    const { port } = makeFakePort();
    const user = userEvent.setup();
    stubApi(() => ME_OK());
    render(<App port={port} apiBaseUrl="http://api.test" />);
    await signInThrough(user);

    const enrolHeading = await screen.findByRole('heading', { level: 1, name: 'Set up your authenticator app' });
    await waitFor(() => expect(enrolHeading).toHaveFocus()); // later screens DO move focus to their heading
    expect(await screen.findByAltText('QR code for your authenticator app')).toHaveAttribute('src', expect.stringMatching(/^data:image\/svg\+xml/));
    expect(screen.getByText('FAKESECRETKEY234')).toBeInTheDocument();
    expect(port.enrollTotp).toHaveBeenCalledTimes(1);

    await user.type(screen.getByLabelText('6-digit code'), '000000');
    await user.click(screen.getByRole('button', { name: 'Verify and continue' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('That code was not accepted. Try again.');

    await user.type(screen.getByLabelText('6-digit code'), GOOD_CODE);
    await user.click(screen.getByRole('button', { name: 'Verify and continue' }));
    expect(await screen.findByRole('group', { name: 'Session' })).toBeInTheDocument();
  });

  it('asks a user who already has an authenticator for a code at every sign-in', async () => {
    const { port } = makeFakePort({ enrolled: true });
    const user = userEvent.setup();
    stubApi(() => ME_OK());
    render(<App port={port} apiBaseUrl="http://api.test" />);
    await signInThrough(user);

    expect(await screen.findByRole('heading', { level: 1, name: 'Enter your authenticator code' })).toBeInTheDocument();
    expect(port.enrollTotp).not.toHaveBeenCalled();
    await user.type(screen.getByLabelText('6-digit code'), GOOD_CODE);
    await user.click(screen.getByRole('button', { name: 'Verify' }));
    expect(await screen.findByRole('group', { name: 'Session' })).toBeInTheDocument();
    expect(port.verifyTotp).toHaveBeenCalledWith('factor-1', GOOD_CODE);
  });

  it('does not call the provider for a code that is not six digits', async () => {
    const { port } = makeFakePort({ enrolled: true });
    const user = userEvent.setup();
    render(<App port={port} apiBaseUrl="http://api.test" />);
    await signInThrough(user);
    await user.type(await screen.findByLabelText('6-digit code'), '12345');
    await user.click(screen.getByRole('button', { name: 'Verify' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Enter the 6-digit code.');
    expect(port.verifyTotp).not.toHaveBeenCalled();
  });

  it('shows an error state with a retry when enrolment cannot start', async () => {
    const { port } = makeFakePort();
    port.enrollTotp.mockResolvedValueOnce(null);
    const user = userEvent.setup();
    render(<App port={port} apiBaseUrl="http://api.test" />);
    await signInThrough(user);
    expect(await screen.findByRole('alert')).toHaveTextContent('Setup could not be started.');
    await user.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByAltText('QR code for your authenticator app')).toBeInTheDocument();
  });

  it('keeps a fully signed-in aal2 session on the console after a reload', async () => {
    const { port } = makeFakePort({ signedInAt: 'aal2' });
    stubApi(() => ME_OK());
    render(<App port={port} apiBaseUrl="http://api.test" />);
    expect(await screen.findByRole('group', { name: 'Session' })).toBeInTheDocument();
  });

  it('does not treat an aal1 session as signed in: it still demands the second factor', async () => {
    const { port } = makeFakePort({ signedInAt: 'aal1' });
    render(<App port={port} apiBaseUrl="http://api.test" />);
    expect(await screen.findByRole('heading', { level: 1, name: 'Set up your authenticator app' })).toBeInTheDocument();
  });

  it('returns to sign-in, with a notice and no reason, when the session ends elsewhere', async () => {
    const fake = makeFakePort({ signedInAt: 'aal2' });
    stubApi(() => ME_OK());
    render(<App port={fake.port} apiBaseUrl="http://api.test" />);
    await screen.findByRole('group', { name: 'Session' });
    fake.endSessionElsewhere();
    expect(await screen.findByRole('heading', { level: 1, name: 'Staff sign in' })).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Please sign in again.');
  });

  it('signs out cleanly, without the session-ended notice', async () => {
    const { port } = makeFakePort({ signedInAt: 'aal2' });
    const user = userEvent.setup();
    stubApi(() => ME_OK());
    render(<App port={port} apiBaseUrl="http://api.test" />);
    await user.click(await screen.findByRole('button', { name: 'Sign out' }));
    expect(await screen.findByRole('heading', { level: 1, name: 'Staff sign in' })).toBeInTheDocument();
    expect(screen.queryByText('Please sign in again.')).toBeNull();
    expect(port.signOut).toHaveBeenCalled();
  });

  it('says plainly that the site is not configured when there is no auth provider', () => {
    render(<App port={null} apiBaseUrl="" />);
    expect(screen.getByRole('alert')).toHaveTextContent('This site is not configured yet.');
    expect(screen.queryByLabelText('Password')).toBeNull();
  });
});

describe('staff shell: role badge, clinic, environment badge', () => {
  const open = async (roles: Record<string, unknown>[], extra: Record<string, unknown> = {}) => {
    const { port } = makeFakePort({ signedInAt: 'aal2' });
    stubApi(() => json({ ...meBody(roles), ...extra }));
    render(<App port={port} apiBaseUrl="http://api.test" />);
    return port;
  };

  it('shows the role, the shortened clinic id until a name exists, and "Environment unknown" until the backend says', async () => {
    await open([{ clinic_id: CLINIC_A, role: 'front_desk' }]);
    const bar = await screen.findByRole('group', { name: 'Session' });
    expect(within(bar).getByText('Front desk')).toBeInTheDocument();
    expect(within(bar).getByText('3f2b8c1e…')).toBeInTheDocument();
    expect(within(bar).getByRole('status', { name: 'Environment: Environment unknown' })).toHaveTextContent('Environment unknown');
    expect(within(bar).queryByText('LIVE')).toBeNull();
    expect(within(bar).queryByText('TEST')).toBeNull();
    expect(within(bar).getByText('Operating profile unknown')).toBeInTheDocument();
  });

  it.each([
    ['live', 'LIVE'],
    ['test', 'TEST'],
  ])('shows %s as %s only when the backend sends exactly that', async (origin, label) => {
    await open([{ clinic_id: CLINIC_A, role: 'nurse', clinic_name: 'Placeholder Clinic', clinic_origin: origin }]);
    const bar = await screen.findByRole('group', { name: 'Session' });
    expect(within(bar).getByRole('status', { name: `Environment: ${label}` })).toHaveTextContent(label);
    expect(within(bar).getByText('Placeholder Clinic')).toBeInTheDocument();
    expect(within(bar).queryByText('3f2b8c1e…')).toBeNull();
  });

  it.each(['prod', 'LIVE', '', null, 1, true])('treats clinic_origin %j as unknown, never as LIVE or TEST', async (bogus) => {
    await open([{ clinic_id: CLINIC_A, role: 'doctor', clinic_origin: bogus }]);
    const bar = await screen.findByRole('group', { name: 'Session' });
    expect(within(bar).getByRole('status', { name: 'Environment: Environment unknown' })).toBeInTheDocument();
  });

  it('shows the operating profile only when it is exactly demo or clinical', async () => {
    await open([{ clinic_id: CLINIC_A, role: 'admin' }], { operating_profile: 'clinical' });
    expect(await screen.findByText('Operating profile: Clinical')).toBeInTheDocument();
  });

  it('forces an explicit pick when there are several roles, with nothing preselected (least privilege)', async () => {
    await open([
      { clinic_id: CLINIC_A, role: 'nurse' },
      { clinic_id: CLINIC_B, role: 'admin' },
    ]);
    const user = userEvent.setup();
    expect(await screen.findByRole('heading', { level: 1, name: 'Choose your active role' })).toBeInTheDocument();
    for (const radio of screen.getAllByRole('radio')) expect(radio).not.toBeChecked();
    expect(screen.getByRole('button', { name: 'Continue' })).toBeDisabled();

    await user.click(screen.getByRole('radio', { name: /Admin/ }));
    await user.click(screen.getByRole('button', { name: 'Continue' }));
    const bar = await screen.findByRole('group', { name: 'Session' });
    expect(within(bar).getByText('Admin')).toBeInTheDocument();
    expect(within(bar).queryByText('Nurse')).toBeNull();

    await user.click(within(bar).getByRole('button', { name: 'Change role' }));
    expect(await screen.findByRole('heading', { level: 1, name: 'Choose your active role' })).toBeInTheDocument();
  });

  it('shows an unrecognised role by its raw name rather than hiding it', async () => {
    await open([{ clinic_id: CLINIC_A, role: 'new_role_from_backend' }]);
    expect(await screen.findByText('new_role_from_backend')).toBeInTheDocument();
  });

  it('says only that the account cannot use the console on a 403, and offers sign out', async () => {
    const { port } = makeFakePort({ signedInAt: 'aal2' });
    stubApi(() => json({ detail: 'forbidden' }, 403));
    render(<App port={port} apiBaseUrl="http://api.test" />);
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('This account cannot use the staff console. Contact your administrator.');
    expect(alert.textContent).not.toMatch(/forbidden|invited|suspended|closed|no account/i);
    expect(within(alert).getByRole('button', { name: 'Sign out' })).toBeInTheDocument();
  });

  it('treats an account with no role grants the same way as a 403', async () => {
    await open([]);
    expect(await screen.findByRole('alert')).toHaveTextContent('This account cannot use the staff console.');
  });

  it('shows a load error with retry when the API is down', async () => {
    const { port } = makeFakePort({ signedInAt: 'aal2' });
    let n = 0;
    stubApi(() => (++n === 1 ? json({}, 503) : json(meBody([{ clinic_id: CLINIC_A, role: 'nurse' }]))));
    const user = userEvent.setup();
    render(<App port={port} apiBaseUrl="http://api.test" />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Your account details could not be loaded.');
    await user.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByRole('group', { name: 'Session' })).toBeInTheDocument();
  });

  it('shows a loading state while the account loads', async () => {
    const { port } = makeFakePort({ signedInAt: 'aal2' });
    stubApi(() => ME_OK());
    render(<App port={port} apiBaseUrl="http://api.test" />);
    expect(await screen.findByText('Loading your account…')).toBeInTheDocument();
  });
});

describe('401 handling in the running app', () => {
  it('sends a plain 401 to sign-in with only a generic notice, and clears the local session', async () => {
    const { port } = makeFakePort({ signedInAt: 'aal2' });
    stubApi(() => json({ detail: 'token expired for user x' }, 401));
    render(<App port={port} apiBaseUrl="http://api.test" />);
    expect(await screen.findByRole('heading', { level: 1, name: 'Staff sign in' })).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Please sign in again.');
    expect(document.body.textContent).not.toMatch(/expired|token|user x/i);
    expect(port.signOut).toHaveBeenCalled();
  });

  function Trigger() {
    const { api } = useAuth();
    return (
      <button type="button" onClick={() => void api.approveMerge(ACCOUNT_ID)}>
        approve
      </button>
    );
  }
  const renderWithTrigger = (port: ReturnType<typeof makeFakePort>['port']) =>
    render(
      <QueryClientProvider client={new QueryClient()}>
        <AuthProvider port={port} apiBaseUrl="http://api.test">
          <Trigger />
        </AuthProvider>
      </QueryClientProvider>,
    );
  const stepUp401 = () => json({ detail: 'step-up required' }, 401, { 'WWW-Authenticate': 'step-up' });

  it('prompts for a fresh authenticator code on a step-up 401, then retries once with the new token and the same idempotency key', async () => {
    const fake = makeFakePort({ signedInAt: 'aal2' });
    const user = userEvent.setup();
    const calls = stubApi((_req, n) => (n === 1 ? stepUp401() : json({ merge_id: ACCOUNT_ID, status: 'active', master_patient_id: ACCOUNT_ID, subordinate_patient_id: ACCOUNT_ID, replayed: false })));
    renderWithTrigger(fake.port);
    const before = fake.currentToken();

    await user.click(await screen.findByRole('button', { name: 'approve' }));
    const dialog = await screen.findByRole('dialog', { name: 'Confirm it is you' });
    await user.type(within(dialog).getByLabelText('6-digit code'), GOOD_CODE);
    await user.click(within(dialog).getByRole('button', { name: 'Confirm' }));

    await waitFor(() => expect(calls).toHaveLength(2));
    expect(fake.port.verifyTotp).toHaveBeenCalledWith('factor-1', GOOD_CODE);
    expect(calls[1]?.auth).toBe(`Bearer ${fake.currentToken()}`);
    expect(calls[1]?.auth).not.toBe(`Bearer ${before}`);
    expect(calls[1]?.key).toBeTruthy();
    expect(calls[1]?.key).toBe(calls[0]?.key);
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('does not retry, and sends nobody to sign-in, when the step-up prompt is cancelled', async () => {
    const fake = makeFakePort({ signedInAt: 'aal2' });
    const user = userEvent.setup();
    const calls = stubApi(() => stepUp401());
    renderWithTrigger(fake.port);
    await user.click(await screen.findByRole('button', { name: 'approve' }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(calls).toHaveLength(1);
    expect(fake.port.signOut).not.toHaveBeenCalled();
  });

  it('keeps the dialog open after a wrong code and allows another attempt', async () => {
    const fake = makeFakePort({ signedInAt: 'aal2' });
    const user = userEvent.setup();
    stubApi((_r, n) => (n === 1 ? stepUp401() : json({})));
    renderWithTrigger(fake.port);
    await user.click(await screen.findByRole('button', { name: 'approve' }));
    const dialog = await screen.findByRole('dialog');
    await user.type(within(dialog).getByLabelText('6-digit code'), '999999');
    await user.click(within(dialog).getByRole('button', { name: 'Confirm' }));
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('That code was not accepted.');
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });
});
