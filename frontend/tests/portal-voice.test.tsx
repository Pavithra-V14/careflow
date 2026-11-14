import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { initI18n, setLanguage } from '../src/shared/i18n';
import type { PortalAuth } from '../src/patient/portal/auth';
import { PortalApp } from '../src/patient/portal/PortalApp';
import { CLINIC_A, fakeJwt, json, stubApi, type FakeApiRequest } from './helpers/fakes';

// MOCK-ONLY: a fake portal API written from openapi.snapshot.json. The "fake engine" is the backend's own answer to opening a session
// (`mic: "ok"`): nothing here opens a microphone or a socket, and a test pins that. Every error body carries LEAK.
const LEAK = 'LEAK-SENTINEL-backend-text';
const NOTICE = '11111111-1111-4111-8111-111111111111';
const SESSION = '33333333-3333-4333-8333-333333333333';

const auth = (): PortalAuth => ({
  requestCode: vi.fn(async () => true),
  verifyCode: vi.fn(async () => true),
  getAccessToken: vi.fn(async () => fakeJwt({ sub: 'p' })),
  signOut: vi.fn(async () => undefined),
  onSignedOut: vi.fn(() => () => undefined),
});

interface State {
  stage?: 'registered' | 'active';
  enabled?: boolean;
  open?: 'ok' | number;
  pref?: number;
  /** The API supplies a voice session notice (default yes). */
  voiceNotice?: boolean;
}
function backend(state: State) {
  let enabled = state.enabled ?? false;
  return (req: FakeApiRequest): Response => {
    const key = `${req.method} ${req.path}`;
    if (key === 'get /public/clinic') return json({ clinic_id: CLINIC_A, name: 'Placeholder Hospital' });
    if (key === 'get /v1/portal/me') return json({ bound_to_a_patient_record: true, consents: {}, stage: state.stage ?? 'active', status: 'x' });
    if (key === 'get /v1/portal/consents')
      return json({
        notices: [
          { body: 'x', decision: true, notice_id: NOTICE, purpose: 'care_delivery', version: 3 },
          ...(state.voiceNotice === false ? [] : [{ body: 'Voice notice text exactly as sent.', decision: null, label: 'Voice notice label', notice_id: '55555555-5555-4555-8555-555555555555', purpose: 'voice_session', version: 2 }]),
        ],
      });
    if (key === 'get /v1/portal/grievances') return json({ grievances: [] });
    if (key === 'get /v1/portal/grievance-categories') return json({ categories: ['access'] });
    if (key === 'get /v1/portal/voice/preference') return state.pref ? json({ detail: LEAK }, state.pref) : json({ voice_enabled: enabled });
    if (key === 'put /v1/portal/voice/preference') {
      enabled = (req.body as { voice_enabled: boolean }).voice_enabled;
      return json({ voice_enabled: enabled });
    }
    if (key === 'post /v1/portal/voice/sessions') return state.open && state.open !== 'ok' ? json({ detail: LEAK }, state.open) : json({ created: true, mic: 'ok', session_id: SESSION }, 201);
    if (key === 'get /v1/portal/my-data')
      return json({
        account_status: 'active',
        bookings: [],
        consents: [
          { action: 'granted', notice_version: 3, occurred_at: '2026-11-09T05:00:00Z', purpose: 'care_delivery' },
          { action: 'withdrawn', notice_version: 1, occurred_at: '2026-11-10T05:00:00Z', purpose: 'ai_processing' },
        ],
        grievances: [],
        messages: [],
        records: [],
        stage: 'active',
        truncated: false,
      });
    return json({ detail: LEAK }, 404);
  };
}

async function mount(lang: 'en' | 'ta' = 'en') {
  initI18n('en');
  await setLanguage(lang);
  return render(<PortalApp auth={auth()} apiBaseUrl="http://api.test" />);
}

const getUserMedia = vi.fn();
const socket = vi.fn();
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  getUserMedia.mockClear();
  socket.mockClear();
});
const watchHardware = () => {
  vi.stubGlobal('WebSocket', socket);
  Object.defineProperty(navigator, 'mediaDevices', { value: { getUserMedia }, configurable: true });
};
const writes = (calls: FakeApiRequest[]) => calls.filter((c) => c.method !== 'get');

describe('the voice setting', () => {
  it('is off until the person turns it on, offers no session while off, and writes nothing before the confirmation', async () => {
    const user = userEvent.setup();
    const calls = stubApi(backend({}));
    await mount();
    expect(await screen.findByText('Voice is off for your account.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Open voice session' })).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Turn voice on' }));
    expect(writes(calls)).toHaveLength(0);
    const dialog = screen.getByRole('dialog', { name: 'Turn voice on?' });
    await user.click(within(dialog).getByRole('button', { name: 'Turn voice on' }));
    expect(await screen.findByText('Voice is on for your account.')).toBeInTheDocument();
    expect(screen.getByText('Your voice setting was saved.')).toBeInTheDocument();
    expect(writes(calls)).toHaveLength(1);
    expect(writes(calls)[0]).toMatchObject({ method: 'put', path: '/v1/portal/voice/preference', body: { voice_enabled: true } });
    expect(writes(calls)[0]?.key).toBeTruthy();
    expect(await screen.findByRole('button', { name: 'Open voice session' })).toBeInTheDocument();
  });

  it('can be turned off again, after a confirmation', async () => {
    const user = userEvent.setup();
    const calls = stubApi(backend({ enabled: true }));
    await mount();
    await user.click(await screen.findByRole('button', { name: 'Turn voice off' }));
    await user.click(within(screen.getByRole('dialog', { name: 'Turn voice off?' })).getByRole('button', { name: 'Turn voice off' }));
    expect(await screen.findByText('Voice is off for your account.')).toBeInTheDocument();
    expect(writes(calls)[0]?.body).toEqual({ voice_enabled: false });
  });

  it('is not offered before the account is active', async () => {
    stubApi(backend({ stage: 'registered' }));
    await mount();
    await screen.findByText('Notices and your decisions');
    expect(screen.queryByText('Voice')).toBeNull();
  });

  it('says "not available to your role" on a 403 and a fixed message on a failure, never the backend text', async () => {
    stubApi(backend({ pref: 403 }));
    const a = await mount();
    expect(await screen.findByText(/not available to your role|not available/i)).toBeInTheDocument();
    a.unmount();
    stubApi(backend({ pref: 500 }));
    await mount();
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
  });
});

describe('opening a voice session', () => {
  it('starts with nothing chosen, shows the API notice exactly as sent (and none of its own), and needs a workflow and an agreement', async () => {
    const user = userEvent.setup();
    stubApi(backend({ enabled: true }));
    await mount();
    const open = await screen.findByRole('button', { name: 'Open voice session' });
    const radios = screen.getAllByRole('radio', { name: /Replying to an appointment offer|Answering a question before a visit|Answering an item on a visit checklist/ });
    expect(radios).toHaveLength(3); // the three workflows in the schema, nothing free-form
    expect(radios.some((r) => (r as HTMLInputElement).checked)).toBe(false);
    const agree = screen.getByRole('checkbox', { name: /I have read the notice above/ });
    expect((agree as HTMLInputElement).checked).toBe(false);
    expect(screen.getByText('Voice notice text exactly as sent.')).toBeInTheDocument();
    expect(document.querySelector('[data-placeholder]')).toBeNull(); // the frontend writes no notice text of its own
    expect(open).toBeDisabled();
    await user.click(radios[0]!);
    expect(open).toBeDisabled();
    await user.click(agree);
    expect(open).toBeEnabled();
    await user.click(radios[0]!); // still selected
    await user.click(agree);
    expect(open).toBeDisabled();
  });

  it('is not set up, and cannot be opened, until the API supplies a notice', async () => {
    watchHardware();
    const calls = stubApi(backend({ enabled: true, voiceNotice: false }));
    await mount();
    expect(await screen.findByText('Voice sessions are not set up yet. Nothing can be opened.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Open voice session' })).toBeDisabled();
    expect(screen.queryByRole('radio')).toBeNull();
    expect(writes(calls)).toHaveLength(0);
  });

  it('keeps the voice notice out of the account consent list, so it cannot be granted or withdrawn there', async () => {
    stubApi(backend({ enabled: true }));
    await mount();
    await screen.findByText('Voice notice text exactly as sent.');
    expect(screen.getAllByText('Voice notice text exactly as sent.')).toHaveLength(1);
  });

  it('sends the chosen workflow with consent and a key, says what happened, and never touches the microphone or a socket', async () => {
    const user = userEvent.setup();
    watchHardware();
    const calls = stubApi(backend({ enabled: true }));
    await mount();
    await user.click(await screen.findByRole('radio', { name: 'Answering a question before a visit' }));
    await user.click(screen.getByRole('checkbox', { name: /I have read the notice above/ }));
    await user.click(screen.getByRole('button', { name: 'Open voice session' }));
    expect(await screen.findByText('A voice session was opened.')).toBeInTheDocument();
    expect(screen.getByText(/does not record or play sound yet/)).toBeInTheDocument();
    const sent = writes(calls).filter((c) => c.path === '/v1/portal/voice/sessions');
    expect(sent).toHaveLength(1);
    expect(sent[0]?.body).toEqual({ clinic_id: CLINIC_A, workflow: 'intake_question', consent: true });
    expect(sent[0]?.key).toBeTruthy();
    expect(getUserMedia).not.toHaveBeenCalled();
    expect(socket).not.toHaveBeenCalled();
  });

  it('a different request gets a different key', async () => {
    const user = userEvent.setup();
    const calls = stubApi(backend({ enabled: true }));
    await mount();
    await user.click(await screen.findByRole('radio', { name: 'Replying to an appointment offer' }));
    await user.click(screen.getByRole('checkbox', { name: /I have read the notice above/ }));
    await user.click(screen.getByRole('button', { name: 'Open voice session' }));
    await screen.findByText('A voice session was opened.');
    await user.click(screen.getByRole('radio', { name: 'Answering an item on a visit checklist' }));
    expect(screen.queryByText('A voice session was opened.')).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Open voice session' }));
    await screen.findByText('A voice session was opened.');
    const sent = writes(calls).filter((c) => c.path === '/v1/portal/voice/sessions');
    expect(sent).toHaveLength(2);
    expect(sent[0]?.key).not.toBe(sent[1]?.key);
    expect(sent[1]?.body).toMatchObject({ workflow: 'readiness_item' });
  });

  it.each([
    [503, 'Voice is not available at this hospital right now. No session was opened.'],
    [404, 'No session was opened. You are not recorded as a patient of this hospital.'],
    [422, 'The session could not be opened as asked. No session was opened.'],
    [403, 'not available to your role'],
  ])('tells %s in fixed words and keeps the backend text off the page', async (status, text) => {
    const user = userEvent.setup();
    stubApi(backend({ enabled: true, open: status }));
    await mount();
    await user.click(await screen.findByRole('radio', { name: 'Replying to an appointment offer' }));
    await user.click(screen.getByRole('checkbox', { name: /I have read the notice above/ }));
    await user.click(screen.getByRole('button', { name: 'Open voice session' }));
    expect(await screen.findByText(new RegExp(text, 'i'))).toBeInTheDocument();
    expect(screen.queryByText('A voice session was opened.')).toBeNull();
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('is available in Tamil with the same controls', async () => {
    stubApi(backend({ enabled: true }));
    await mount('ta');
    expect(await screen.findByRole('button', { name: 'குரல் அமர்வைத் திற' })).toBeInTheDocument();
    expect(screen.getAllByRole('radio')).toHaveLength(3);
  });
});

describe('my consent history', () => {
  it('is shown from the file the person asked for, with no extra call', async () => {
    const user = userEvent.setup();
    const calls = stubApi(backend({}));
    const blobs: Blob[] = [];
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: (b: Blob) => (blobs.push(b), 'blob:x'), revokeObjectURL: vi.fn() }));
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    await mount();
    await screen.findByText('Voice is off for your account.');
    expect(screen.queryByText('Your consent history')).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Download my data' }));
    const table = await screen.findByRole('table', { name: 'Your consent history' });
    expect(within(table).getByText('Care delivery')).toBeInTheDocument();
    expect(within(table).getByText('AI-assisted processing')).toBeInTheDocument();
    expect(within(table).getByText('Withdrawn')).toBeInTheDocument();
    expect(calls.filter((c) => c.path === '/v1/portal/my-data')).toHaveLength(1);
  });
});
