import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { App as StaffApp } from '../src/staff/App';
import { PortalApp } from '../src/patient/portal/PortalApp';
import { initI18n, setLanguage } from '../src/shared/i18n';
import type { PortalAuth } from '../src/patient/portal/auth';
import { CLINIC_A, fakeJwt, json, makeFakePort, meBody, stubApi } from './helpers/fakes';

// Idle sign-out has no default minutes. Here the minutes are switched on for the test only, to prove that
// when the owner supplies a number the staff and the patient are signed out, the screen is cleared and a plain note is shown.
vi.mock('../src/shared/idle', async (importOriginal) => {
  const original = await importOriginal<typeof import('../src/shared/idle')>();
  return { ...original, idleMinutes: () => 0.05 }; // 3 seconds: the warning shows for the last 1.5 seconds
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('idle sign-out wiring (durations switched on for the test)', () => {
  it('staff: an idle session is signed out locally, the console is cleared and the sign-in screen says the session ended', async () => {
    initI18n('en');
    await setLanguage('en');
    window.location.hash = '#/';
    const { port } = makeFakePort({ signedInAt: 'aal2' });
    stubApi((req) => (req.path === '/v1/iam/me' ? json(meBody([{ clinic_id: CLINIC_A, role: 'nurse' }])) : json({}, 404)));
    render(<StaffApp port={port} apiBaseUrl="http://api.test" />);
    expect(await screen.findByRole('group', { name: 'Session' })).toBeInTheDocument();
    const warning = await screen.findByRole('dialog', { name: 'Are you still there?' }, { timeout: 5000 });
    expect(warning).toHaveTextContent(/You will be signed out in \d+ seconds/);
    await waitFor(() => expect(port.signOut).toHaveBeenCalled(), { timeout: 5000 });
    expect(await screen.findByText('Please sign in again.')).toBeInTheDocument();
    expect(screen.queryByRole('group', { name: 'Session' })).toBeNull();
  });

  it('patient: an idle session is signed out, the account is cleared and a plain note is shown', async () => {
    initI18n('en');
    await setLanguage('en');
    const auth: PortalAuth = {
      requestCode: vi.fn(async () => true),
      verifyCode: vi.fn(async () => true),
      getAccessToken: vi.fn(async () => fakeJwt({ sub: 'p' })),
      signOut: vi.fn(async () => undefined),
      onSignedOut: vi.fn(() => () => undefined),
    };
    stubApi((req) => {
      if (req.path === '/public/clinic') return json({ clinic_id: CLINIC_A, name: 'Placeholder Hospital' });
      if (req.path === '/v1/portal/me') return json({ bound_to_a_patient_record: true, consents: {}, stage: 'registered', status: 'x' });
      if (req.path === '/v1/portal/consents') return json({ notices: [] });
      return json({ detail: 'x' }, 404);
    });
    render(<PortalApp auth={auth} apiBaseUrl="http://api.test" />);
    await screen.findByRole('heading', { name: /Notices and your decisions/ });
    await screen.findByRole('dialog', { name: 'Are you still there?' }, { timeout: 5000 });
    await waitFor(() => expect(auth.signOut).toHaveBeenCalled(), { timeout: 5000 });
    expect(await screen.findByText(/You were signed out because the page was not used for a while/)).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /Notices and your decisions/ })).toBeNull();
  });
});
