import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { initI18n, setLanguage } from '../src/shared/i18n';
import type { PortalAuth } from '../src/patient/portal/auth';
import { PortalApp } from '../src/patient/portal/PortalApp';
import { CLINIC_A, fakeJwt, json, stubApi, type FakeApiRequest } from './helpers/fakes';

// MOCK-ONLY: a fake portal API written from openapi.snapshot.json. Every error body carries LEAK, which must never reach the page.
const LEAK = 'LEAK-SENTINEL-backend-text';
const BOOKING = '11111111-1111-4111-8111-aaaaaaaaaaaa';
const SLOT = '22222222-2222-4222-8222-bbbbbbbbbbbb';
const THREAD = '33333333-3333-4333-8333-cccccccccccc';

const auth = (): PortalAuth => ({
  requestCode: vi.fn(async () => true),
  verifyCode: vi.fn(async () => true),
  getAccessToken: vi.fn(async () => fakeJwt({ sub: 'p' })),
  signOut: vi.fn(async () => undefined),
  onSignedOut: vi.fn(() => () => undefined),
});

interface State {
  bookings?: Response;
  slots?: Response;
  cancel?: Response;
  book?: Response;
  threads?: Response;
  thread?: Response;
  send?: Response;
}
const booking = (over: Record<string, unknown> = {}) => ({ booking_id: BOOKING, cancellable: true, clinic_id: CLINIC_A, ends_at: '2026-12-01T05:30:00Z', location_name: 'Room 1', slot_id: SLOT, starts_at: '2026-12-01T05:00:00Z', status: 'confirmed', visit_type_name: 'Placeholder Visit', ...over });
const slot = { ends_at: '2026-12-02T05:30:00Z', location_name: 'Room 2', slot_id: SLOT, starts_at: '2026-12-02T05:00:00Z', visit_type_id: CLINIC_A, visit_type_name: 'Other Visit' };
function backend(s: State = {}) {
  return (req: FakeApiRequest): Response => {
    const key = `${req.method} ${req.path}`;
    if (key === 'get /public/clinic') return json({ clinic_id: CLINIC_A, name: 'Placeholder Hospital' });
    if (key === 'get /v1/portal/me') return json({ bound_to_a_patient_record: true, consents: {}, stage: 'active', status: 'x' });
    if (key === 'get /v1/portal/consents') return json({ notices: [] });
    if (key === 'get /v1/portal/grievances') return json({ grievances: [] });
    if (key === 'get /v1/portal/grievance-categories') return json({ categories: ['access'] });
    if (key === 'get /v1/portal/voice/preference') return json({ voice_enabled: false });
    if (key === 'get /v1/portal/booking/bookings') return s.bookings ?? json({ bookings: [booking(), booking({ booking_id: '99999999-9999-4999-8999-999999999999', cancellable: false, status: 'cancelled' })] });
    if (key === 'get /v1/portal/booking/slots') return s.slots ?? json({ slots: [slot] });
    if (key === `post /v1/portal/booking/bookings/${BOOKING}/cancel`) return s.cancel ?? json({ booking_id: BOOKING, changed: true });
    if (key === `post /v1/portal/booking/slots/${SLOT}/book`) return s.book ?? json({ booking_id: BOOKING, created: true, slot_id: SLOT }, 201);
    if (key === 'get /v1/portal/messages/threads') return s.threads ?? json({ threads: [{ clinic_id: CLINIC_A, last_message_at: '2026-11-09T05:00:00Z', message_count: 2, thread_id: THREAD, topic: 'appointments' }] });
    if (key === `get /v1/portal/messages/threads/${THREAD}`)
      return (
        s.thread ??
        json({
          thread_id: THREAD,
          messages: [
            { created_at: '2026-11-09T04:00:00Z', erased: false, message_id: 'a1', readable: true, sender: 'patient', text: 'Line one\nLine two <b>not markup</b>' },
            { created_at: '2026-11-09T05:00:00Z', erased: true, message_id: 'a2', readable: false, sender: 'staff', text: null },
          ],
        })
      );
    if (key === 'post /v1/portal/messages') return s.send ?? json({ created: true, message_id: 'a3', thread_id: THREAD }, 201);
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

describe('appointments', () => {
  it('lists the bookings, offers cancel only where the API says it can be cancelled, and cancels after a confirmation with a key', async () => {
    const user = userEvent.setup();
    const calls = stubApi(backend());
    await mount();
    expect((await screen.findAllByText(/Placeholder Visit/)).length).toBe(2);
    expect(screen.getAllByRole('button', { name: /Cancel appointment/ })).toHaveLength(1);
    await user.click(screen.getByRole('button', { name: /Cancel appointment/ }));
    expect(writes(calls, `/v1/portal/booking/bookings/${BOOKING}/cancel`)).toHaveLength(0);
    const dialog = screen.getByRole('dialog', { name: 'Cancel this appointment?' });
    await user.click(within(dialog).getByRole('button', { name: 'Cancel appointment' }));
    expect(await screen.findByText('Your appointment was cancelled.')).toBeInTheDocument();
    expect(writes(calls, `/v1/portal/booking/bookings/${BOOKING}/cancel`)[0]?.key).toBeTruthy();
  });

  it.each([
    [404, 'This appointment was not found.'],
    [409, 'This appointment cannot be cancelled any more: it was already cancelled or it has started.'],
    [503, 'Booking online is not turned on at this hospital yet.'],
  ])('tells a %s on cancel in fixed words and never the backend text', async (status, words) => {
    const user = userEvent.setup();
    stubApi(backend({ cancel: json({ detail: LEAK }, status) }));
    await mount();
    await user.click(await screen.findByRole('button', { name: /Cancel appointment/ }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel appointment' }));
    expect(await within(screen.getByRole('dialog')).findByText(words)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('shows the open times with nothing booked for the person, and books one after a confirmation with a key', async () => {
    const user = userEvent.setup();
    const calls = stubApi(backend());
    await mount();
    await user.click(await screen.findByRole('button', { name: /Book: Other Visit/ }));
    expect(writes(calls, `/v1/portal/booking/slots/${SLOT}/book`)).toHaveLength(0);
    const dialog = screen.getByRole('dialog', { name: 'Book this appointment?' });
    await user.click(within(dialog).getByRole('button', { name: 'Book' }));
    expect(await screen.findByText('Your appointment was booked.')).toBeInTheDocument();
    expect(writes(calls, `/v1/portal/booking/slots/${SLOT}/book`)[0]?.key).toBeTruthy();
    expect(calls.find((c) => c.path === '/v1/portal/booking/slots')?.query.get('clinic_id')).toBe(CLINIC_A);
  });

  it.each([
    [403, 'This time cannot be booked by this account. Nothing was booked.'],
    [409, "This time is no longer open, or you have reached the hospital's limit of future appointments. Nothing was booked."],
  ])('tells a %s on book in fixed words', async (status, words) => {
    const user = userEvent.setup();
    stubApi(backend({ book: json({ detail: LEAK }, status) }));
    await mount();
    await user.click(await screen.findByRole('button', { name: /Book: Other Visit/ }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Book' }));
    expect(await within(screen.getByRole('dialog')).findByText(words)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('says booking is not turned on (503) once, calmly, and shows no lists', async () => {
    stubApi(backend({ bookings: json({ detail: LEAK }, 503), slots: json({ detail: LEAK }, 503) }));
    await mount();
    expect(await screen.findByText('Booking online is not turned on at this hospital yet.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Book:/ })).toBeNull();
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('has empty states', async () => {
    stubApi(backend({ bookings: json({ bookings: [] }), slots: json({ slots: [] }) }));
    await mount();
    expect(await screen.findByText('You have no appointments.')).toBeInTheDocument();
    expect(await screen.findByText('There are no open times right now.')).toBeInTheDocument();
  });
});

describe('messages', () => {
  it('chooses nothing, sends nothing before the confirmation, drops the text after, and says the same thing whatever happened', async () => {
    const user = userEvent.setup();
    const calls = stubApi(backend());
    await mount();
    const topic = await screen.findByLabelText('What is it about?');
    expect(topic).toHaveValue('');
    expect(screen.getByRole('button', { name: 'Send message' })).toBeDisabled();
    await user.selectOptions(topic, 'care');
    await user.type(screen.getByLabelText('Your message'), 'Some words');
    await user.click(screen.getByRole('button', { name: 'Send message' }));
    expect(writes(calls, '/v1/portal/messages')).toHaveLength(0);
    await user.click(within(screen.getByRole('dialog', { name: 'Send this message?' })).getByRole('button', { name: 'Send message' }));
    expect(await screen.findByText('Your message was sent.')).toBeInTheDocument();
    const sent = writes(calls, '/v1/portal/messages');
    expect(sent).toHaveLength(1);
    expect(sent[0]?.body).toEqual({ clinic_id: CLINIC_A, text: 'Some words', topic: 'care' });
    expect(sent[0]?.key).toBeTruthy();
    expect(screen.getByLabelText('Your message')).toHaveValue('');
  });

  it.each([
    [403, 'This cannot be done for this account: it must be active and linked to a record at that hospital. Nothing was sent.'],
    [422, 'The message was not accepted. It may be empty or too long. Nothing was sent.'],
    [429, 'Too many requests. Wait a moment and try again.'],
    [503, 'Messages are not turned on at this hospital yet.'],
  ])('tells a %s in fixed words and never the backend text', async (status, words) => {
    const user = userEvent.setup();
    stubApi(backend({ send: json({ detail: LEAK }, status) }));
    await mount();
    await user.selectOptions(await screen.findByLabelText('What is it about?'), 'appointments');
    await user.type(screen.getByLabelText('Your message'), 'Some words');
    await user.click(screen.getByRole('button', { name: 'Send message' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Send message' }));
    expect(await within(screen.getByRole('dialog')).findByText(words)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('lists the conversations and reads one as plain text, with an erased message said as such', async () => {
    const user = userEvent.setup();
    stubApi(backend());
    await mount();
    await user.click(await screen.findByRole('button', { name: 'Read' }));
    const text = await screen.findByText(/Line one/);
    expect(text.textContent).toBe('Line one\nLine two <b>not markup</b>');
    expect(text.querySelector('b')).toBeNull();
    expect(await screen.findByText('You')).toBeInTheDocument();
    expect(screen.getByText('The hospital')).toBeInTheDocument();
  });

  it('says a conversation that is gone in fixed words, and has empty and off states', async () => {
    const user = userEvent.setup();
    stubApi(backend({ thread: json({ detail: LEAK }, 404) }));
    await mount();
    await user.click(await screen.findByRole('button', { name: 'Read' }));
    expect(await screen.findByText('This conversation was not found.')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('says messages are not turned on (503) calmly and offers no form', async () => {
    stubApi(backend({ threads: json({ detail: LEAK }, 503) }));
    await mount();
    expect(await screen.findByText('Messages are not turned on at this hospital yet.')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByLabelText('Your message')).toBeNull());
  });

  it('has an empty state', async () => {
    stubApi(backend({ threads: json({ threads: [] }) }));
    await mount();
    expect(await screen.findByText('You have no conversations.')).toBeInTheDocument();
  });
});

describe('Tamil', () => {
  it('shows both sections in Tamil', async () => {
    stubApi(backend());
    await mount('ta');
    expect(await screen.findByRole('heading', { name: 'சந்திப்புகள்' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'மருத்துவமனைக்குச் செய்திகள்' })).toBeInTheDocument();
  });
});
