import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { initI18n, setLanguage } from '../src/shared/i18n';
import type { PortalAuth } from '../src/patient/portal/auth';
import { PortalApp } from '../src/patient/portal/PortalApp';
import { CLINIC_A, fakeJwt, json, stubApi, type FakeApiRequest } from './helpers/fakes';

// MOCK-ONLY: a fake portal API written from openapi.snapshot.json. BUILD-SPEC 20.1: withdrawal is one click. Granting keeps its confirmation.
const LEAK = 'LEAK-SENTINEL-backend-text';
const NOTICE = '11111111-1111-4111-8111-111111111111';
const auth = (): PortalAuth => ({
  requestCode: vi.fn(async () => true),
  verifyCode: vi.fn(async () => true),
  getAccessToken: vi.fn(async () => fakeJwt({ sub: 'p' })),
  signOut: vi.fn(async () => undefined),
  onSignedOut: vi.fn(() => () => undefined),
});
function backend(post?: Response) {
  return (req: FakeApiRequest): Response => {
    const key = `${req.method} ${req.path}`;
    if (key === 'get /public/clinic') return json({ clinic_id: CLINIC_A, name: 'Placeholder Hospital' });
    if (key === 'get /v1/portal/me') return json({ bound_to_a_patient_record: true, consents: {}, stage: 'active', status: 'x' });
    if (key === 'get /v1/portal/consents') return json({ notices: [{ body: 'Notice text.', decision: true, notice_id: NOTICE, purpose: 'care_delivery', version: 3 }] });
    if (key === 'post /v1/portal/consents') return post ?? json({ recorded: true }, 201);
    if (key === 'get /v1/portal/grievances') return json({ grievances: [] });
    if (key === 'get /v1/portal/grievance-categories') return json({ categories: ['access'] });
    if (key === 'get /v1/portal/voice/preference') return json({ voice_enabled: false });
    return json({ detail: LEAK }, 404);
  };
}
async function mount(lang: 'en' | 'ta' = 'en') {
  initI18n('en');
  await setLanguage(lang);
  return render(<PortalApp auth={auth()} apiBaseUrl="http://api.test" />);
}
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
const consentPosts = (calls: FakeApiRequest[]) => calls.filter((c) => c.method === 'post' && c.path === '/v1/portal/consents');

describe('consent: withdrawal is one click, granting keeps its confirmation', () => {
  it('withdraws at once with a key, shows the result, and offers to grant again with a confirmation', async () => {
    const user = userEvent.setup();
    const calls = stubApi(backend());
    await mount();
    await user.click(await screen.findByRole('button', { name: 'Withdraw' }));
    expect(screen.queryByRole('dialog')).toBeNull(); // no confirmation step
    expect(await screen.findByText(/Your consent was withdrawn/)).toBeInTheDocument();
    expect(consentPosts(calls)).toHaveLength(1);
    expect(consentPosts(calls)[0]?.body).toMatchObject({ action: 'withdrawn', notice_id: NOTICE, purpose: 'care_delivery' });
    expect(consentPosts(calls)[0]?.key).toBeTruthy();
    await user.click(screen.getByRole('button', { name: 'Grant again' }));
    const dialog = screen.getByRole('dialog', { name: 'Grant this?' });
    expect(consentPosts(calls)).toHaveLength(1); // granting still waits for its confirmation
    await user.click(within(dialog).getByRole('button', { name: 'Grant' }));
    await waitFor(() => expect(consentPosts(calls)).toHaveLength(2));
    expect(consentPosts(calls)[1]?.body).toMatchObject({ action: 'granted' });
  });

  it('tells a 409 in fixed words and never shows the backend text', async () => {
    const user = userEvent.setup();
    stubApi(backend(json({ detail: LEAK }, 409)));
    await mount();
    await user.click(await screen.findByRole('button', { name: 'Withdraw' }));
    expect(await screen.findByText(/The notice changed while you were reading/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('is available in Tamil', async () => {
    stubApi(backend());
    await mount('ta');
    expect(await screen.findByRole('button', { name: 'திரும்பப் பெறு' })).toBeInTheDocument();
  });
});
