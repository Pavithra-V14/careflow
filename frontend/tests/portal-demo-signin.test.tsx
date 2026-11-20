import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { initI18n, setLanguage } from '../src/shared/i18n';
import type { PortalAuth } from '../src/patient/portal/auth';
import { PortalApp } from '../src/patient/portal/PortalApp';
import { CLINIC_A, fakeJwt, json, stubApi, type FakeApiRequest } from './helpers/fakes';

// MOCK-ONLY. The DEMO email + password sign-in on the patient portal: shown only when the build-time switch is on, beside (never instead of)
// the phone-code sign-in, with a visible banner, and never keeping or echoing what was typed.
const LEAK = 'LEAK-SENTINEL-backend-text';
const flag = vi.hoisted(() => ({ on: false }));
vi.mock('../src/staff/featureFlags', async (original) => ({ ...(await original<typeof import('../src/staff/featureFlags')>()), devPatientPasswordLogin: () => flag.on }));

function fakeAuth() {
  let token: string | null = null;
  const auth = {
    requestCode: vi.fn(async () => true),
    verifyCode: vi.fn(async () => false),
    signInWithPassword: vi.fn(async (email: string, password: string) => {
      if (email !== 'demo@example.test' || password !== 'right-one') return false;
      token = fakeJwt({ sub: 'p' });
      return true;
    }),
    getAccessToken: vi.fn(async () => token),
    signOut: vi.fn(async () => {
      token = null;
    }),
    onSignedOut: vi.fn(() => () => undefined),
  } satisfies PortalAuth;
  return auth;
}

function backend(req: FakeApiRequest): Response {
  const key = `${req.method} ${req.path}`;
  if (key === 'get /public/clinic') return json({ clinic_id: CLINIC_A, name: 'Placeholder Hospital' });
  if (key === 'get /public/grievance-categories' || key === 'get /v1/portal/grievance-categories') return json({ categories: ['access'] });
  if (key === 'get /v1/portal/me') return json({ bound_to_a_patient_record: false, consents: {}, stage: 'active', status: 'x' });
  return json({ detail: LEAK }, 404);
}

async function mount(auth: PortalAuth, lang: 'en' | 'ta' = 'en') {
  initI18n('en');
  await setLanguage(lang);
  return render(<PortalApp auth={auth} apiBaseUrl="http://api.test" />);
}

beforeEach(() => {
  flag.on = false;
  stubApi(backend);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('demo patient password sign-in', () => {
  it('is absent while the switch is off: only the phone-code form is offered', async () => {
    await mount(fakeAuth());
    expect(await screen.findByRole('button', { name: 'Send me a code' })).toBeInTheDocument();
    expect(screen.queryByLabelText('Password')).toBeNull();
    expect(screen.queryByText(/Demo sign-in/)).toBeNull();
  });

  it('is absent when the switch is on but the sign-in service offers no password method', async () => {
    flag.on = true;
    const auth = fakeAuth();
    const { requestCode, verifyCode, getAccessToken, signOut, onSignedOut } = auth;
    const withoutPassword: PortalAuth = { requestCode, verifyCode, getAccessToken, signOut, onSignedOut };
    await mount(withoutPassword);
    expect(await screen.findByRole('button', { name: 'Send me a code' })).toBeInTheDocument();
    expect(screen.queryByLabelText('Password')).toBeNull();
  });

  it('with the switch on shows a visible banner and the form BESIDE the phone-code form, which stays', async () => {
    flag.on = true;
    await mount(fakeAuth());
    expect(await screen.findByRole('button', { name: 'Send me a code' })).toBeInTheDocument(); // still the default
    expect(screen.getByText(/Demo sign-in\. Email and password sign-in is for demonstrations/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Sign in (demo)' })).toBeDisabled(); // nothing typed, nothing sent
  });

  it('signs in with the right pair, then shows the account; the password is never kept', async () => {
    flag.on = true;
    const user = userEvent.setup();
    const auth = fakeAuth();
    await mount(auth);
    await user.type(await screen.findByLabelText('Email'), 'demo@example.test');
    await user.type(screen.getByLabelText('Password'), 'right-one');
    await user.click(screen.getByRole('button', { name: 'Sign in (demo)' }));
    await waitFor(() => expect(auth.signInWithPassword).toHaveBeenCalledWith('demo@example.test', 'right-one'));
    await waitFor(() => expect(screen.queryByLabelText('Password')).toBeNull()); // signed in: the sign-in screen is gone
    expect(document.body.textContent).not.toContain('right-one');
  });

  it('says one fixed sentence for a wrong pair, clears the password, and never shows backend text', async () => {
    flag.on = true;
    const user = userEvent.setup();
    const auth = fakeAuth();
    await mount(auth);
    await user.type(await screen.findByLabelText('Email'), 'demo@example.test');
    await user.type(screen.getByLabelText('Password'), 'wrong');
    await user.click(screen.getByRole('button', { name: 'Sign in (demo)' }));
    expect(await screen.findByText('The demo sign-in did not work. Check the email and password and try again.')).toBeInTheDocument();
    expect(screen.getByLabelText('Password')).toHaveValue('');
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('is also said in Tamil, with the banner', async () => {
    flag.on = true;
    await mount(fakeAuth(), 'ta');
    expect(await screen.findByText(/டெமோ உள்நுழைவு\. மின்னஞ்சல்/)).toBeInTheDocument();
    expect(screen.getByLabelText('கடவுச்சொல்')).toBeInTheDocument();
  });
});
