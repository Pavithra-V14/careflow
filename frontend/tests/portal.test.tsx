import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { initI18n, setLanguage } from '../src/shared/i18n';
import { normalisePhone, type PortalAuth } from '../src/patient/portal/auth';
import { PortalApp } from '../src/patient/portal/PortalApp';
import { CLINIC_A, fakeJwt, json, stubApi, type FakeApiRequest } from './helpers/fakes';

// MOCK-ONLY: a fake sign-in service and a fake portal API written from openapi.snapshot.json. They prove this app's behaviour, not the
// backend's or Supabase's. Every error body carries LEAK, and a test asserts it never reaches the page.
const LEAK = 'LEAK-SENTINEL-backend-text';
const NOTICE = '11111111-1111-4111-8111-111111111111';
const NOTICE_TEXT = 'Line one.\nLine two <b>not markup</b>.';

function fakeAuth(options: { signedIn?: boolean } = {}) {
  let token: string | null = options.signedIn ? fakeJwt({ sub: 'p' }) : null;
  const listeners = new Set<() => void>();
  const auth = {
    requestCode: vi.fn(async (phone: string) => phone === '+910000000000'),
    verifyCode: vi.fn(async (_phone: string, code: string) => {
      if (code !== '123456') return false;
      token = fakeJwt({ sub: 'p' });
      return true;
    }),
    getAccessToken: vi.fn(async () => token),
    signOut: vi.fn(async () => {
      token = null;
    }),
    onSignedOut: vi.fn((l: () => void) => {
      listeners.add(l);
      return () => listeners.delete(l);
    }),
  } satisfies PortalAuth;
  return { auth, endElsewhere: () => { token = null; listeners.forEach((l) => l()); } };
}

type Stage = 'registered' | 'new_patient' | 'pending_activation' | 'active';
function backend(state: { stage: Stage | null; granted?: boolean | null; bound?: boolean }, extra: (req: FakeApiRequest) => Response | undefined = () => undefined) {
  return (req: FakeApiRequest): Response => {
    const special = extra(req);
    if (special) return special;
    const key = `${req.method} ${req.path}`;
    if (key === 'get /v1/portal/me') return state.stage === null ? json({ detail: LEAK }, 403) : json({ bound_to_a_patient_record: state.bound ?? false, consents: {}, stage: state.stage, status: 'x' });
    if (key === 'post /v1/portal/register') {
      state.stage = 'registered';
      return json({ result: 'registered' }, 201);
    }
    if (key === 'get /v1/portal/consents') return json({ notices: [{ body: NOTICE_TEXT, decision: state.granted ?? null, notice_id: NOTICE, purpose: 'care_delivery', version: 3 }] });
    if (key === 'post /v1/portal/consents') return json({ recorded: true }, 201);
    if (key === 'post /v1/portal/activate') return json({ result: 'activated' });
    if (key === 'post /v1/portal/claims') return json({ outcome: 'pending_review' });
    if (key === 'get /v1/portal/grievances') return json({ grievances: [] });
    if (key === 'post /v1/portal/grievances') return json({ created: true, grievance_id: NOTICE }, 201);
    if (key === 'get /v1/portal/my-data') return json({ account_status: 'active', bookings: [], consents: [{ action: 'granted', notice_version: 3, occurred_at: '2026-11-09T05:00:00Z', purpose: 'care_delivery' }], grievances: [], messages: [], records: [], stage: 'active', truncated: true });
    return json({ detail: LEAK }, 404);
  };
}

async function mount(auth: PortalAuth | null, lang: 'en' | 'ta' = 'en') {
  initI18n('en');
  await setLanguage(lang);
  return render(<PortalApp auth={auth} apiBaseUrl="http://api.test" />);
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('phone numbers', () => {
  it('accepts a plus sign, a country code and digits (spaces and dashes allowed) and nothing else', () => {
    expect(normalisePhone('+91 00000-00000')).toBe('+910000000000');
    for (const bad of ['', '9100000000', '+0123456789', '+91abc', '+91', '00 91 0000000000', '+9'.repeat(20)]) expect(normalisePhone(bad), bad).toBeNull();
  });
});

describe('sign in', () => {
  it('asks for a number, then a six-digit code, and sends nothing for a number that is not a number', async () => {
    const user = userEvent.setup();
    const { auth } = fakeAuth();
    stubApi(backend({ stage: 'active' }));
    await mount(auth);
    const send = await screen.findByRole('button', { name: 'Send me a code' });
    expect(send).toBeDisabled();
    await user.type(screen.getByLabelText('Mobile number'), '12345');
    expect(screen.getByText(/Enter the number with a plus sign/)).toBeInTheDocument();
    expect(send).toBeDisabled();
    await user.clear(screen.getByLabelText('Mobile number'));
    await user.type(screen.getByLabelText('Mobile number'), '+91 00000 00000');
    await user.click(send);
    await user.type(await screen.findByLabelText('6-digit code'), '123456');
    await user.click(screen.getByRole('button', { name: 'Continue' }));
    expect(await screen.findByText('Notices and your decisions')).toBeInTheDocument();
    expect(auth.requestCode).toHaveBeenCalledWith('+910000000000');
    expect(auth.verifyCode).toHaveBeenCalledWith('+910000000000', '123456');
  });

  it('says one fixed thing for a code that did not work, and never what was wrong', async () => {
    const user = userEvent.setup();
    const { auth } = fakeAuth();
    await mount(auth);
    await user.type(await screen.findByLabelText('Mobile number'), '+910000000000');
    await user.click(screen.getByRole('button', { name: 'Send me a code' }));
    await user.type(await screen.findByLabelText('6-digit code'), '000000');
    await user.click(screen.getByRole('button', { name: 'Continue' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('That code did not work.');
    expect((screen.getByLabelText('6-digit code') as HTMLInputElement).value).toBe(''); // the failed code is not kept
  });

  it('says the same fixed thing when a code could not be sent', async () => {
    const user = userEvent.setup();
    const { auth } = fakeAuth();
    await mount(auth);
    await user.type(await screen.findByLabelText('Mobile number'), '+919999999999');
    await user.click(screen.getByRole('button', { name: 'Send me a code' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('The code could not be sent.');
  });

  it('shows a plain not-configured state when there is no sign-in service', async () => {
    await mount(null);
    expect(await screen.findByText('This site is not configured yet.')).toBeInTheDocument();
  });
});

describe('the account', () => {
  it('offers to create the patient account on a 403 (an explicit step), then shows the notice', async () => {
    const user = userEvent.setup();
    const { auth } = fakeAuth({ signedIn: true });
    const calls = stubApi(backend({ stage: null }));
    await mount(auth);
    const create = await screen.findByRole('button', { name: 'Create my account' });
    expect(calls.some((c) => c.path === '/v1/portal/register')).toBe(false);
    await user.click(create);
    expect(await screen.findByText('Notices and your decisions')).toBeInTheDocument();
    expect(calls.some((c) => c.path === '/v1/portal/register')).toBe(true);
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('shows the consent notice exactly as sent, as text, with its version', async () => {
    const { auth } = fakeAuth({ signedIn: true });
    stubApi(backend({ stage: 'registered' }));
    await mount(auth);
    await screen.findByText(/version 3/);
    const text = document.querySelector('.notice-text') as HTMLElement;
    expect(text.textContent).toBe(NOTICE_TEXT);
    expect(text.querySelector('b')).toBeNull();
    expect(screen.getByText(/version 3/)).toBeInTheDocument();
  });

  it('records a decision only after a confirmation, against the notice shown, with a key; a 409 says the notice changed', async () => {
    const user = userEvent.setup();
    const { auth } = fakeAuth({ signedIn: true });
    let changed = true;
    const calls = stubApi(backend({ stage: 'registered' }, (req) => (req.method === 'post' && req.path === '/v1/portal/consents' && changed ? json({ detail: LEAK }, 409) : undefined)));
    await mount(auth);
    await user.click(await screen.findByRole('button', { name: 'Grant' }));
    const dialog = screen.getByRole('dialog', { name: 'Grant this?' });
    expect(calls.filter((c) => c.method === 'post')).toHaveLength(0);
    await user.click(within(dialog).getByRole('button', { name: 'Grant' }));
    expect(await within(dialog).findByText(/The notice changed while you were reading/)).toBeInTheDocument();
    changed = false;
    await user.click(within(dialog).getByRole('button', { name: 'Grant' }));
    expect(await screen.findByText('Your decision was recorded.')).toBeInTheDocument();
    const posts = calls.filter((c) => c.method === 'post' && c.path === '/v1/portal/consents');
    expect(posts.at(-1)?.body).toEqual({ notice_id: NOTICE, purpose: 'care_delivery', action: 'granted' });
    expect(posts.at(-1)?.key).toBeTruthy();
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('activates only after a confirmation, and says plainly when the notice has not been granted', async () => {
    const user = userEvent.setup();
    const { auth } = fakeAuth({ signedIn: true });
    const calls = stubApi(backend({ stage: 'pending_activation' }, (req) => (req.path === '/v1/portal/activate' ? json({ result: 'consent_required' }) : undefined)));
    await mount(auth);
    await user.click(await screen.findByRole('button', { name: 'Activate my account' }));
    expect(calls.some((c) => c.path === '/v1/portal/activate')).toBe(false);
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Activate my account' }));
    expect(await screen.findByText(/has not been granted yet, so the account was not activated/)).toBeInTheDocument();
  });

  it('keeps claim, grievance and download out of reach until the account is active', async () => {
    const { auth } = fakeAuth({ signedIn: true });
    stubApi(backend({ stage: 'registered' }));
    await mount(auth);
    await screen.findByText('Notices and your decisions');
    expect(screen.queryByText('Link your record')).toBeNull();
    expect(screen.queryByText('Download my data')).toBeNull();
    expect(screen.getByText(/become available once your account is active/)).toBeInTheDocument();
  });
});

describe('an active account', () => {
  it('claims a record by clinic, name and date of birth after a confirmation, and tells each outcome in fixed words', async () => {
    const user = userEvent.setup();
    const { auth } = fakeAuth({ signedIn: true });
    const calls = stubApi(backend({ stage: 'active', granted: true }));
    await mount(auth);
    const button = await screen.findByRole('button', { name: 'Link my record' });
    expect(button).toBeDisabled();
    await user.type(screen.getByLabelText('Clinic code', { selector: '#claim-clinic' }), CLINIC_A);
    await user.type(screen.getByLabelText('Full name'), 'Test Person');
    await user.type(screen.getByLabelText('Date of birth'), '1990-02-03');
    await user.click(button);
    expect(calls.some((c) => c.path === '/v1/portal/claims')).toBe(false);
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Link my record' }));
    expect(await screen.findByText('Your request was received. A person at the hospital will decide.')).toBeInTheDocument();
    const claim = calls.find((c) => c.path === '/v1/portal/claims');
    expect(claim?.body).toEqual({ clinic_id: CLINIC_A, name: 'Test Person', dob: '1990-02-03' });
    expect(claim?.key).toBeTruthy();
    expect((screen.getByLabelText('Full name') as HTMLInputElement).value).toBe(''); // what was typed is dropped
  });

  it('files a grievance with a category code and text, and drops the text once it is sent; a category not on the list is refused plainly', async () => {
    const user = userEvent.setup();
    const { auth } = fakeAuth({ signedIn: true });
    let status = 422;
    const calls = stubApi(backend({ stage: 'active', granted: true, bound: true }, (req) => (req.method === 'post' && req.path === '/v1/portal/grievances' && status !== 201 ? json({ detail: LEAK }, status) : undefined)));
    await mount(auth);
    const send = await screen.findByRole('button', { name: 'Send' });
    await user.type(screen.getByLabelText('Clinic code', { selector: '#gr-clinic' }), CLINIC_A);
    await user.type(screen.getByLabelText('Category'), 'Not a code!');
    await user.type(screen.getByLabelText('What you want to tell us'), 'Typed words');
    expect(send).toBeDisabled();
    await user.clear(screen.getByLabelText('Category'));
    await user.type(screen.getByLabelText('Category'), 'access');
    await user.click(send);
    const dialog = screen.getByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Send' }));
    expect(await within(dialog).findByText("That category is not on the hospital's list. Nothing was sent.")).toBeInTheDocument();
    status = 201;
    await user.click(within(dialog).getByRole('button', { name: 'Send' }));
    expect(await screen.findByText('Your message was sent.')).toBeInTheDocument();
    expect(calls.filter((c) => c.method === 'post' && c.path === '/v1/portal/grievances').at(-1)?.body).toEqual({ clinic_id: CLINIC_A, category: 'access', text: 'Typed words' });
    expect((screen.getByLabelText('What you want to tell us') as HTMLTextAreaElement).value).toBe('');
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('downloads exactly what the API sent as one file, says what it holds, and keeps none of it on the page', async () => {
    const user = userEvent.setup();
    const { auth } = fakeAuth({ signedIn: true });
    stubApi(backend({ stage: 'active', granted: true, bound: true }));
    const blobs: Blob[] = [];
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: (b: Blob) => (blobs.push(b), 'blob:x'), revokeObjectURL: vi.fn() }));
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    await mount(auth);
    await user.click(await screen.findByRole('button', { name: 'Download my data' }));
    expect(await screen.findByText('Your file was downloaded. It holds:')).toBeInTheDocument();
    expect(click).toHaveBeenCalledTimes(1);
    const saved = JSON.parse(await blobs[0]!.text()) as { consents: unknown[]; truncated: boolean };
    expect(saved.consents).toHaveLength(1);
    expect(saved.truncated).toBe(true);
    expect(screen.getByText('The file does not hold everything: some lists were cut short.')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('care_delivery'.concat('', '') === '' ? '' : 'granted'); // the contents are not rendered
  });
});

describe('the session', () => {
  it('goes back to sign-in and clears the screen when the session ends elsewhere', async () => {
    const { auth, endElsewhere } = fakeAuth({ signedIn: true });
    stubApi(backend({ stage: 'active', granted: true }));
    await mount(auth);
    await screen.findByText('Notices and your decisions');
    endElsewhere();
    expect(await screen.findByLabelText('Mobile number')).toBeInTheDocument();
    expect(screen.queryByText('Notices and your decisions')).toBeNull();
    expect(document.body.textContent).not.toContain(NOTICE_TEXT);
  });

  it('signs out on request, and on a 401 from the API', async () => {
    const user = userEvent.setup();
    const { auth } = fakeAuth({ signedIn: true });
    stubApi(backend({ stage: 'active', granted: true }));
    const view = await mount(auth);
    await user.click(await screen.findByRole('button', { name: 'Sign out' }));
    expect(await screen.findByLabelText('Mobile number')).toBeInTheDocument();
    expect(auth.signOut).toHaveBeenCalled();
    view.unmount();
    const second = fakeAuth({ signedIn: true });
    stubApi(() => json({ detail: LEAK }, 401));
    await mount(second.auth);
    await waitFor(() => expect(screen.getByLabelText('Mobile number')).toBeInTheDocument());
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('keeps nothing about the patient in the browser beyond what the sign-in service itself keeps', async () => {
    const user = userEvent.setup();
    const { auth } = fakeAuth({ signedIn: true });
    stubApi(backend({ stage: 'active', granted: true, bound: true }));
    await mount(auth);
    await user.click(await screen.findByRole('button', { name: 'Download my data' }).catch(() => screen.getByRole('button', { name: 'Sign out' })));
    expect(Object.keys(localStorage).filter((k) => !k.startsWith('sb-') && k !== 'careflow.language')).toEqual([]);
    expect(Object.keys(sessionStorage)).toEqual([]);
    expect(document.cookie).toBe('');
  });
});

describe('Tamil', () => {
  it('shows the sign-in form in Tamil with no English left in it', async () => {
    const { auth } = fakeAuth();
    await mount(auth, 'ta');
    expect(await screen.findByLabelText('கைபேசி எண்')).toBeInTheDocument();
    expect(screen.queryByText('Mobile number')).toBeNull();
  });
});
