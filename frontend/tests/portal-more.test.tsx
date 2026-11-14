import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { initI18n, setLanguage } from '../src/shared/i18n';
import type { PortalAuth } from '../src/patient/portal/auth';
import { PortalApp } from '../src/patient/portal/PortalApp';
import { CLINIC_A, fakeJwt, json, stubApi, type FakeApiRequest } from './helpers/fakes';

// MOCK-ONLY: a fake portal API written from openapi.snapshot.json. Every error body carries LEAK, which must never reach the page.
const LEAK = 'LEAK-SENTINEL-backend-text';
const NID = '44444444-4444-4444-8444-444444444444';
const RID = '55555555-5555-4555-8555-555555555555';

const auth = (signedIn = true): PortalAuth => ({
  requestCode: vi.fn(async () => true),
  verifyCode: vi.fn(async () => true),
  getAccessToken: vi.fn(async () => (signedIn ? fakeJwt({ sub: 'p' }) : null)),
  signOut: vi.fn(async () => undefined),
  onSignedOut: vi.fn(() => () => undefined),
});

interface State {
  profile?: Response;
  putProfile?: Response;
  notifications?: Response;
  readN?: Response;
  requests?: Response;
  postRequest?: Response;
  publicGrievance?: Response;
}
const profileBody = (over: Record<string, unknown> = {}) => ({ display_name: 'Placeholder Person', languages: ['en', 'ta'], preferred_language: null, sex: null, sex_words: ['female', 'male', 'other_word'], ...over });
function backend(s: State = {}) {
  return (req: FakeApiRequest): Response => {
    const key = `${req.method} ${req.path}`;
    if (key === 'get /public/clinic') return json({ clinic_id: CLINIC_A, name: 'Placeholder Hospital' });
    if (key === 'get /v1/portal/me') return json({ bound_to_a_patient_record: true, consents: {}, stage: 'active', status: 'x' });
    if (key === 'get /v1/portal/consents') return json({ notices: [] });
    if (key === 'get /v1/portal/grievances') return json({ grievances: [] });
    if (key === 'get /v1/portal/grievance-categories') return json({ categories: ['access'] });
    if (key === 'get /v1/portal/voice/preference') return json({ voice_enabled: false });
    if (key === 'get /v1/portal/profile') return s.profile?.clone() ?? json(profileBody());
    if (key === 'put /v1/portal/profile') return s.putProfile ?? json(profileBody({ sex: 'female', preferred_language: 'ta' }));
    if (key === 'get /v1/portal/notifications')
      return (
        s.notifications ??
        json({
          notifications: [
            { created_at: '2026-11-09T05:00:00Z', kind: 'request_update', notification_id: NID, read: false, ref: LEAK },
            { created_at: '2026-11-08T05:00:00Z', kind: 'offer_sent', notification_id: '66666666-6666-4666-8666-666666666666', read: true, ref: null },
          ],
        })
      );
    if (key === `post /v1/portal/notifications/${NID}/read`) return s.readN ?? json({ changed: true, notification_id: NID });
    if (key === 'get /v1/portal/requests') return s.requests ?? json({ requests: [{ clinic_id: CLINIC_A, created_at: '2026-11-09T05:00:00Z', field: 'phone', kind: 'correction', request_id: RID, status: 'open', updated_at: '2026-11-09T05:00:00Z' }] });
    if (key === 'post /v1/portal/requests') return s.postRequest ?? json({ created: true, request_id: RID }, 201);
    if (key === 'post /public/grievances') return s.publicGrievance ?? json({ created: true, grievance_id: RID }, 201);
    return json({ detail: LEAK }, 404);
  };
}
async function mount(lang: 'en' | 'ta' = 'en', signedIn = true) {
  initI18n('en');
  await setLanguage(lang);
  return render(<PortalApp auth={auth(signedIn)} apiBaseUrl="http://api.test" />);
}
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
const posts = (calls: FakeApiRequest[], path: string) => calls.filter((c) => c.method !== 'get' && c.path === path);

describe('your details', () => {
  it('shows the name read-only, offers the closed lists with nothing chosen, and saves with a key', async () => {
    const user = userEvent.setup();
    const calls = stubApi(backend());
    await mount();
    expect(await screen.findByText('Placeholder Person')).toBeInTheDocument();
    const sex = screen.getByLabelText('Sex');
    expect(sex).toHaveValue('');
    expect(within(sex).getAllByRole('option').map((o) => o.textContent)).toEqual(['Choose', 'female', 'male', 'other word']);
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    await user.selectOptions(sex, 'female');
    await user.selectOptions(screen.getByLabelText('Preferred language'), 'ta');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText('Your details were saved.')).toBeInTheDocument();
    const put = posts(calls, '/v1/portal/profile');
    expect(put).toHaveLength(1);
    expect(put[0]?.body).toEqual({ preferred_language: 'ta', sex: 'female' });
    expect(put[0]?.key).toBeTruthy();
  });

  it('sends only the field that changed and leaves the other out, and never offers to clear one', async () => {
    const user = userEvent.setup();
    const calls = stubApi(backend({ profile: json(profileBody({ sex: 'male', preferred_language: 'en' })) }));
    await mount();
    await user.selectOptions(await screen.findByLabelText('Preferred language'), 'ta');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByText('Your details were saved.');
    expect(posts(calls, '/v1/portal/profile')[0]?.body).toEqual({ preferred_language: 'ta' });
  });

  it('says so when the hospital has no list of choices yet', async () => {
    stubApi(backend({ profile: json(profileBody({ sex_words: [] })) }));
    await mount();
    expect(await screen.findByText("The hospital has not set up a list of choices yet.")).toBeInTheDocument();
  });

  it.each([
    [503, 'This is not set up yet. Nothing was saved.'],
    [422, "That choice is not on the hospital's list. Nothing was saved."],
  ])('tells a %s in fixed words and never the backend text', async (status, words) => {
    const user = userEvent.setup();
    stubApi(backend({ putProfile: json({ detail: LEAK }, status) }));
    await mount();
    await user.selectOptions(await screen.findByLabelText('Preferred language'), 'en');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText(words)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('shows the fixed "not available" line for a 403 on the read', async () => {
    stubApi(backend({ profile: json({ detail: LEAK }, 403) }));
    await mount();
    await waitFor(() => expect(screen.getAllByText('This is not available to you yet.').length).toBeGreaterThan(0));
    expect(document.body.textContent).not.toContain(LEAK);
  });
});

describe('notices', () => {
  it('lists them by closed kind, never shows the opaque reference, and marks one read with a key', async () => {
    const user = userEvent.setup();
    const calls = stubApi(backend());
    await mount();
    expect(await screen.findByText('Unread: 1')).toBeInTheDocument();
    expect(screen.getByText(/An update on one of your requests/)).toBeInTheDocument();
    expect(screen.getByText(/An appointment offer/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
    expect(screen.getAllByRole('button', { name: /Mark as read/ })).toHaveLength(1);
    await user.click(screen.getByRole('button', { name: /Mark as read/ }));
    await waitFor(() => expect(posts(calls, `/v1/portal/notifications/${NID}/read`)).toHaveLength(1));
    expect(posts(calls, `/v1/portal/notifications/${NID}/read`)[0]?.key).toBeTruthy();
  });

  it('has an empty state, and tells a 404 on mark-read calmly', async () => {
    stubApi(backend({ notifications: json({ notifications: [] }) }));
    await mount();
    expect(await screen.findByText('You have no notices.')).toBeInTheDocument();
  });

  it('tells a 404 on mark-read in fixed words', async () => {
    const user = userEvent.setup();
    stubApi(backend({ readN: json({ detail: LEAK }, 404) }));
    await mount();
    await user.click(await screen.findByRole('button', { name: /Mark as read/ }));
    expect(await screen.findByText('This notice is no longer there.')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
  });
});

describe('requests about the record', () => {
  it('chooses nothing for the person, asks for the detail only on a correction, and sends nothing before the confirmation', async () => {
    const user = userEvent.setup();
    const calls = stubApi(backend());
    await mount();
    const kind = await screen.findByLabelText('What do you want to ask?');
    expect(kind).toHaveValue('');
    expect(screen.getByRole('button', { name: 'Send request' })).toBeDisabled();
    expect(screen.queryByLabelText('Which detail?')).toBeNull();
    await user.selectOptions(kind, 'correction');
    expect(screen.getByRole('button', { name: 'Send request' })).toBeDisabled(); // a correction needs the detail named
    await user.selectOptions(screen.getByLabelText('Which detail?'), 'date_of_birth');
    await user.click(screen.getByRole('button', { name: 'Send request' }));
    expect(posts(calls, '/v1/portal/requests')).toHaveLength(0);
    const dialog = screen.getByRole('dialog', { name: 'Send this request?' });
    await user.click(within(dialog).getByRole('button', { name: 'Send request' }));
    expect(await screen.findByText('Your request was sent.')).toBeInTheDocument();
    const sent = posts(calls, '/v1/portal/requests');
    expect(sent).toHaveLength(1);
    expect(sent[0]?.body).toEqual({ clinic_id: CLINIC_A, field: 'date_of_birth', kind: 'correction' });
    expect(sent[0]?.key).toBeTruthy();
  });

  it('an erasure carries no field, and a repeat that was already on record is said calmly', async () => {
    const user = userEvent.setup();
    const calls = stubApi(backend({ postRequest: json({ created: false, request_id: RID }, 201) }));
    await mount();
    await user.selectOptions(await screen.findByLabelText('What do you want to ask?'), 'erasure');
    await user.click(screen.getByRole('button', { name: 'Send request' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Send request' }));
    expect(await screen.findByText('This request was already on record.')).toBeInTheDocument();
    expect(posts(calls, '/v1/portal/requests')[0]?.body).toEqual({ clinic_id: CLINIC_A, field: null, kind: 'erasure' });
  });

  it('lists the earlier requests with their status words, and cancelling the dialog writes nothing', async () => {
    const user = userEvent.setup();
    const calls = stubApi(backend());
    await mount();
    expect(await screen.findByText('Open', { selector: '.badge, span' })).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText('What do you want to ask?'), 'nominee');
    await user.click(screen.getByRole('button', { name: 'Send request' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }));
    expect(posts(calls, '/v1/portal/requests')).toHaveLength(0);
  });

  it.each([
    [403, 'This cannot be done for this account: it must be active and linked to a record at that hospital.'],
    [422, 'That request was not accepted. Check your choices. Nothing was sent.'],
  ])('tells a %s in fixed words and never the backend text', async (status, words) => {
    const user = userEvent.setup();
    stubApi(backend({ postRequest: json({ detail: LEAK }, status) }));
    await mount();
    await user.selectOptions(await screen.findByLabelText('What do you want to ask?'), 'nominee');
    await user.click(screen.getByRole('button', { name: 'Send request' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Send request' }));
    expect(await within(screen.getByRole('dialog')).findByText(words)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('has an empty state', async () => {
    stubApi(backend({ requests: json({ requests: [] }) }));
    await mount();
    const section = (await screen.findByRole('heading', { name: 'Your requests' })).parentElement as HTMLElement;
    expect(await within(section).findByText('You have not sent any.')).toBeInTheDocument();
  });
});

describe('a complaint without signing in', () => {
  it('is offered when signed out, needs the confirmation, sends no token and a long key, and drops the text', async () => {
    const user = userEvent.setup();
    const calls = stubApi(backend());
    await mount('en', false);
    await user.type(await screen.findByLabelText('Category'), 'access');
    await user.type(screen.getByLabelText('What you want to tell us'), 'Some words');
    await user.click(screen.getByRole('button', { name: 'Send' }));
    expect(posts(calls, '/public/grievances')).toHaveLength(0);
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Send' }));
    expect(await screen.findByText('Your message was sent.')).toBeInTheDocument();
    const sent = posts(calls, '/public/grievances');
    expect(sent).toHaveLength(1);
    expect(sent[0]?.body).toEqual({ category: 'access', text: 'Some words' });
    expect((sent[0]?.key ?? '').length).toBeGreaterThanOrEqual(32);
    expect(sent[0]?.auth).toBeNull();
    expect(screen.getByLabelText('What you want to tell us')).toHaveValue('');
  });

  it.each([
    [404, 'No hospital is set up to receive this right now. Nothing was sent.'],
    [422, "That category is not on the hospital's list. Nothing was sent."],
    [503, 'This is not available right now. Nothing was sent.'],
  ])('tells a %s in fixed words', async (status, words) => {
    const user = userEvent.setup();
    stubApi(backend({ publicGrievance: json({ detail: LEAK }, status) }));
    await mount('en', false);
    await user.type(await screen.findByLabelText('Category'), 'access');
    await user.type(screen.getByLabelText('What you want to tell us'), 'Some words');
    await user.click(screen.getByRole('button', { name: 'Send' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Send' }));
    expect(await within(screen.getByRole('dialog')).findByText(words)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('says a 429 in the fixed "too many requests" words', async () => {
    const user = userEvent.setup();
    stubApi(backend({ publicGrievance: json({ detail: LEAK }, 429) }));
    await mount('en', false);
    await user.type(await screen.findByLabelText('Category'), 'access');
    await user.type(screen.getByLabelText('What you want to tell us'), 'x');
    await user.click(screen.getByRole('button', { name: 'Send' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Send' }));
    expect(await within(screen.getByRole('dialog')).findByText('Too many requests. Wait a moment and try again.')).toBeInTheDocument();
  });
});

describe('Tamil', () => {
  it('shows the new sections in Tamil', async () => {
    stubApi(backend());
    await mount('ta');
    expect(await screen.findByRole('heading', { name: 'உங்கள் விவரங்கள்' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'உங்கள் அறிவிப்புகள்' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'உங்கள் பதிவைப் பற்றி மருத்துவமனையிடம் கேளுங்கள்' })).toBeInTheDocument();
  });
});
