import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { initI18n, setLanguage } from '../src/shared/i18n';
import type { PortalAuth } from '../src/patient/portal/auth';
import { PortalApp } from '../src/patient/portal/PortalApp';
import { CLINIC_A, fakeJwt, json, stubApi, type FakeApiRequest } from './helpers/fakes';

// MOCK-ONLY: a fake portal API written from openapi.snapshot.json. Every error body carries LEAK, which must never reach the page.
const LEAK = 'LEAK-SENTINEL-backend-text';
const BOOKING = '11111111-1111-4111-8111-aaaaaaaaaaaa';
const ITEM1 = '22222222-2222-4222-8222-bbbbbbbbbbbb';
const ITEM2 = '33333333-3333-4333-8333-cccccccccccc';
const LINK = '44444444-4444-4444-8444-dddddddddddd';
const NO_RULES = 'family links are not set up: the clinic has not entered its rules';

const auth = (): PortalAuth => ({
  requestCode: vi.fn(async () => true),
  verifyCode: vi.fn(async () => true),
  getAccessToken: vi.fn(async () => fakeJwt({ sub: 'p' })),
  signOut: vi.fn(async () => undefined),
  onSignedOut: vi.fn(() => () => undefined),
});
interface State {
  prep?: Response;
  mark?: Response;
  family?: Response;
  request?: Response;
  revoke?: Response;
}
const booking = { booking_id: BOOKING, cancellable: true, clinic_id: CLINIC_A, ends_at: '2026-12-01T05:30:00Z', location_name: 'Room 1', slot_id: ITEM1, starts_at: '2026-12-01T05:00:00Z', status: 'confirmed', visit_type_name: 'Placeholder Visit' };
const prepBody = (over: Record<string, unknown> = {}) => ({
  booking_id: BOOKING,
  items: [
    { done: null, item_id: ITEM1, marked_at: null, text: 'Clinic line one\nline two <b>not markup</b>' },
    { done: true, item_id: ITEM2, marked_at: '2026-11-09T05:00:00Z', text: 'Second item' },
  ],
  note: null,
  nothing_to_prepare: false,
  starts_at: '2026-12-01T05:00:00Z',
  visit_type: 'Placeholder Visit',
  ...over,
});
function backend(s: State = {}) {
  return (req: FakeApiRequest): Response => {
    const key = `${req.method} ${req.path}`;
    if (key === 'get /public/clinic') return json({ clinic_id: CLINIC_A, name: 'Placeholder Hospital' });
    if (key === 'get /v1/portal/me') return json({ bound_to_a_patient_record: true, consents: {}, stage: 'active', status: 'x' });
    if (key === 'get /v1/portal/consents') return json({ notices: [] });
    if (key === 'get /v1/portal/grievances') return json({ grievances: [] });
    if (key === 'get /v1/portal/grievance-categories') return json({ categories: ['access'] });
    if (key === 'get /v1/portal/voice/preference') return json({ voice_enabled: false });
    if (key === 'get /v1/portal/booking/bookings') return json({ bookings: [booking] });
    if (key === 'get /v1/portal/booking/slots') return json({ slots: [] });
    if (key === `get /v1/portal/visits/${BOOKING}/prep`) return s.prep?.clone() ?? json(prepBody());
    if (key === `post /v1/portal/visits/${BOOKING}/prep/${ITEM1}/done`) return s.mark?.clone() ?? json({ booking_id: BOOKING, created: true, done: true, item_id: ITEM1 });
    if (key === 'get /v1/portal/family-links') return s.family?.clone() ?? json({ links: [{ clinic_id: CLINIC_A, created_at: '2026-11-09T05:00:00Z', link_id: LINK, link_type: 'guardian_of_minor', status: 'requested' }] });
    if (key === 'post /v1/portal/family-links') return s.request?.clone() ?? json({ link_id: LINK, result: 'recorded' }, 201);
    if (key === `post /v1/portal/family-links/${LINK}/revoke`) return s.revoke?.clone() ?? json({ result: 'recorded' });
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
const writes = (calls: FakeApiRequest[], path: string) => calls.filter((c) => c.method !== 'get' && c.path === path);

describe('visit preparation (patient)', () => {
  it('shows the clinic checklist as sent (plain text), tells "not marked" from "not done", and marks an item with a key', async () => {
    const user = userEvent.setup();
    const calls = stubApi(backend());
    await mount();
    await user.click(await screen.findByRole('button', { name: /What to prepare: Placeholder Visit/ }));
    const first = await screen.findByText(/Clinic line one/);
    expect(first.textContent).toBe('Clinic line one\nline two <b>not markup</b>');
    expect(first.querySelector('b')).toBeNull();
    expect(screen.getByText('Not marked yet')).toBeInTheDocument(); // null is not "not done"
    expect(screen.getByText('You marked this done')).toBeInTheDocument();
    await user.click(within(first.closest('li') as HTMLElement).getByRole('button', { name: 'Mark done' }));
    await vi.waitFor(() => expect(writes(calls, `/v1/portal/visits/${BOOKING}/prep/${ITEM1}/done`)).toHaveLength(1));
    const sent = writes(calls, `/v1/portal/visits/${BOOKING}/prep/${ITEM1}/done`)[0];
    expect(sent?.body).toEqual({ done: true });
    expect(sent?.key).toBeTruthy();
  });

  it('says "nothing to prepare" for an empty checklist, and "not turned on" calmly for a 503', async () => {
    const user = userEvent.setup();
    stubApi(backend({ prep: json(prepBody({ items: [], nothing_to_prepare: true, note: 'Nothing to prepare for this visit.' })) }));
    const a = await mount();
    await user.click(await screen.findByRole('button', { name: /What to prepare/ }));
    expect(await screen.findByText('Nothing to prepare for this visit.')).toBeInTheDocument();
    a.unmount();
    stubApi(backend({ prep: json({ detail: LEAK }, 503) }));
    await mount();
    await user.click(await screen.findByRole('button', { name: /What to prepare/ }));
    expect(await screen.findByText('Visit preparation is not turned on at this hospital yet.')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it.each([
    [404, 'This item could not be marked: the visit has started, was cancelled, or the item is no longer on its list. Nothing was changed.'],
    [409, 'That mark conflicts with an earlier request. Refresh and try again. Nothing was changed.'],
  ])('tells a %s on marking in fixed words and never the backend text', async (status, words) => {
    const user = userEvent.setup();
    stubApi(backend({ mark: json({ detail: LEAK }, status) }));
    await mount();
    await user.click(await screen.findByRole('button', { name: /What to prepare/ }));
    await user.click((await screen.findAllByRole('button', { name: 'Mark done' }))[0]!);
    expect(await screen.findByText(words)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
  });
});

describe('family links (patient)', () => {
  it('is hidden when the owner switch is off (503) or the account may not use it (403)', async () => {
    stubApi(backend({ family: json({ detail: LEAK }, 503) }));
    const a = await mount();
    await screen.findByRole('heading', { name: 'Appointments' });
    expect(screen.queryByRole('heading', { name: 'Family links' })).toBeNull();
    a.unmount();
    stubApi(backend({ family: json({ detail: LEAK }, 403) }));
    await mount();
    await screen.findByRole('heading', { name: 'Appointments' });
    expect(screen.queryByRole('heading', { name: 'Family links' })).toBeNull();
  });

  it('lists the requests with closed words, says a verified link grants nothing, and chooses nothing for the person', async () => {
    stubApi(backend());
    await mount();
    expect(await screen.findByRole('heading', { name: 'Family links' })).toBeInTheDocument();
    expect(screen.getByText(/does not give you access to the other person's record/)).toBeInTheDocument();
    expect(screen.getByText('Waiting for a check')).toBeInTheDocument();
    expect(screen.getByLabelText('What kind of link?')).toHaveValue('');
    expect(screen.getByRole('button', { name: 'Ask for this link' })).toBeDisabled();
  });

  it('asks only after a confirmation, with a key, and says the result', async () => {
    const user = userEvent.setup();
    const calls = stubApi(backend());
    await mount();
    await user.selectOptions(await screen.findByLabelText('What kind of link?'), 'carer_of_adult');
    await user.click(screen.getByRole('button', { name: 'Ask for this link' }));
    expect(writes(calls, '/v1/portal/family-links')).toHaveLength(0);
    await user.click(within(screen.getByRole('dialog', { name: 'Ask for this link?' })).getByRole('button', { name: 'Ask for this link' }));
    expect(await screen.findByText('Your request was sent.')).toBeInTheDocument();
    const sent = writes(calls, '/v1/portal/family-links');
    expect(sent[0]?.body).toEqual({ clinic_id: CLINIC_A, link_type: 'carer_of_adult' });
    expect(sent[0]?.key).toBeTruthy();
  });

  it('says in fixed words that the clinic has not entered its rules, and never shows the backend sentence or text', async () => {
    const user = userEvent.setup();
    stubApi(backend({ request: json({ detail: NO_RULES }, 503) }));
    await mount();
    await user.selectOptions(await screen.findByLabelText('What kind of link?'), 'guardian_of_minor');
    await user.click(screen.getByRole('button', { name: 'Ask for this link' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Ask for this link' }));
    expect(await within(screen.getByRole('dialog')).findByText('Family links are not set up at this hospital yet: the hospital has not entered its rules. Nothing was sent.')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(NO_RULES);
  });

  it.each([
    [403, 'This cannot be done for this account: it must be active and linked to a record at that hospital. Nothing was sent.'],
    [422, 'That request was not accepted. Check your choices. Nothing was sent.'],
  ])('tells a %s on the request in fixed words', async (status, words) => {
    const user = userEvent.setup();
    stubApi(backend({ request: json({ detail: LEAK }, status) }));
    await mount();
    await user.selectOptions(await screen.findByLabelText('What kind of link?'), 'guardian_of_minor');
    await user.click(screen.getByRole('button', { name: 'Ask for this link' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Ask for this link' }));
    expect(await within(screen.getByRole('dialog')).findByText(words)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('withdraws after a confirmation with a key, and tells a 404 and an invalid state in fixed words', async () => {
    const user = userEvent.setup();
    const calls = stubApi(backend());
    await mount();
    await user.click(await screen.findByRole('button', { name: /^Withdraw: Guardian of a child/ }));
    expect(writes(calls, `/v1/portal/family-links/${LINK}/revoke`)).toHaveLength(0);
    await user.click(within(screen.getByRole('dialog', { name: 'Withdraw this?' })).getByRole('button', { name: 'Withdraw' }));
    expect(await screen.findByText('It was withdrawn.')).toBeInTheDocument();
    expect(writes(calls, `/v1/portal/family-links/${LINK}/revoke`)[0]?.key).toBeTruthy();
  });

  it('is available in Tamil', async () => {
    stubApi(backend());
    await mount('ta');
    expect(await screen.findByRole('heading', { name: 'குடும்ப இணைப்புகள்' })).toBeInTheDocument();
  });
});
