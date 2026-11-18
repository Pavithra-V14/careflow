import { describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CLINIC_A, json } from './helpers/fakes';
import { ID, LEAK, fail, list, openConsole, writes, type Routes } from './helpers/console';

// MOCK-ONLY (see helpers/console.tsx): the staff message screen and the patient labels. Every error body carries LEAK.
const TH = 'abababab-abab-4bab-8bab-abababababab';
const M1 = 'cdcdcdcd-cdcd-4dcd-8dcd-cdcdcdcdcdcd';
const M2 = 'efefefef-efef-4fef-8fef-efefefefefef';
const thread = (over: Record<string, unknown> = {}) => ({ clinic_id: CLINIC_A, created_at: '2026-11-09T04:00:00Z', last_message_at: '2026-11-09T05:00:00Z', message_count: 2, patient_id: ID.patient, status: 'open', thread_id: TH, topic: 'appointments', ...over });
const header = (over: Record<string, unknown> = {}) => ({ created_at: '2026-11-09T05:00:00Z', erased: false, message_id: M1, sender_kind: 'patient', thread_id: TH, topic: 'appointments', ...over });
const threads = (items: unknown[] = [thread()]): Routes[number] => ['get', '/v1/staff/messages/threads', () => list(items)];
const headers = (items: unknown[] = [header(), header({ message_id: M2, sender_kind: 'staff', erased: true })]): Routes[number] => ['get', `/v1/staff/messages/threads/${TH}/messages`, () => list(items)];
const read = (body = 'Line one\nLine two <b>not markup</b>'): Routes[number] => ['get', `/v1/staff/messages/${M1}`, () => json({ body, message_id: M1, sender_kind: 'patient', thread_id: TH, topic: 'appointments' })];
const screenRoute = (outcome = 'clear', reply: string | null = 'not_required', replayed = false): Routes[number] => ['post', `/v1/staff/messages/${M1}/screen`, () => json({ message_id: M1, outcome, patient_reply: reply, replayed, screening_id: ID.doc })];

describe('staff messages', () => {
  it('lists conversations with closed words and ids only, filters by topic, and shows no text', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ hash: '/messages', routes: [threads()] });
    const table = await screen.findByRole('table');
    expect(within(table).getByText('Appointments')).toBeInTheDocument();
    expect(within(table).getAllByText('Open').length).toBeGreaterThanOrEqual(1);
    expect(document.body.textContent).not.toContain(LEAK);
    await user.selectOptions(screen.getByLabelText('Topic'), 'safety');
    await screen.findByRole('table');
    expect(calls.filter((c) => c.path === '/v1/staff/messages/threads').map((c) => c.query.get('topic'))).toEqual([null, 'safety']);
  });

  it('opens a conversation to message headers (newest first), with an erased message marked and no read button for it', async () => {
    const user = userEvent.setup();
    await openConsole({ hash: '/messages', routes: [threads(), headers()] });
    await user.click(await screen.findByRole('button', { name: /^Open/ }));
    const rows = within((await screen.findAllByRole('table'))[1]!).getAllByRole('row');
    expect(rows).toHaveLength(3);
    expect(within(rows[1]!).getByRole('button', { name: /^Read/ })).toBeInTheDocument();
    expect(within(rows[2]!).getByText('Erased')).toBeInTheDocument();
    expect(within(rows[2]!).queryByRole('button')).toBeNull();
  });

  it('reads a body only after a confirmation, as plain text, and drops it when hidden', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ hash: '/messages', routes: [threads(), headers(), read()] });
    await user.click(await screen.findByRole('button', { name: /^Open/ }));
    await user.click(await screen.findByRole('button', { name: /^Read/ }));
    expect(calls.some((c) => c.path === `/v1/staff/messages/${M1}`)).toBe(false);
    const dialog = screen.getByRole('dialog', { name: 'Read this message?' });
    await user.click(within(dialog).getByRole('button', { name: 'Read' }));
    const quote = await screen.findByText(/Line one/);
    expect(quote.textContent).toBe('Line one\nLine two <b>not markup</b>');
    expect(quote.querySelector('b')).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Hide the text' }));
    expect(screen.queryByText(/Line one/)).toBeNull();
  });

  it.each([
    [404, 'This message was not found, or its text was erased. Nothing was shown.'],
    [410, 'This message was not found, or its text was erased. Nothing was shown.'],
    [403, 'You may not read this message. Nothing was shown.'],
    [503, 'This is not available right now. Try again in a moment.'],
  ])('tells a %s on read in fixed words and never the backend text', async (status, words) => {
    const user = userEvent.setup();
    await openConsole({ hash: '/messages', routes: [threads(), headers(), ['get', `/v1/staff/messages/${M1}`, () => fail(status)]] });
    await user.click(await screen.findByRole('button', { name: /^Open/ }));
    await user.click(await screen.findByRole('button', { name: /^Read/ }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Read' }));
    expect(await within(screen.getByRole('dialog')).findByText(words)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('screens a message only after a confirmation, with a key, and says the outcome in fixed words', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ role: 'nurse', hash: '/messages', routes: [threads(), headers(), screenRoute('red_flag', 'pending_clinician_template')] });
    await user.click(await screen.findByRole('button', { name: /^Open/ }));
    await user.click(await screen.findByRole('button', { name: /^Screen for red flags/ }));
    expect(writes(calls)).toHaveLength(0);
    await user.click(within(screen.getByRole('dialog', { name: 'Screen this message for red flags?' })).getByRole('button', { name: 'Screen for red flags' }));
    expect(await screen.findByText(/The rules found a red flag\. A nurse task was raised\./)).toBeInTheDocument();
    expect(screen.getByText(/No reply is sent: the patient-facing wording has not been written by a clinician yet\./)).toBeInTheDocument();
    expect(writes(calls)).toHaveLength(1);
    expect(writes(calls)[0]?.key).toBeTruthy();
  });

  it.each([
    [409, 'The text of this message was erased, so it cannot be screened. Nothing was recorded.'],
    [403, 'You may not screen this message. Nothing was recorded.'],
    [404, 'This message was not found, or its text was erased. Nothing was shown.'],
    [503, 'Screening is not available now (encryption is not set up). Nothing was recorded.'],
  ])('tells a %s on screen in fixed words', async (status, words) => {
    const user = userEvent.setup();
    await openConsole({ hash: '/messages', routes: [threads(), headers(), ['post', `/v1/staff/messages/${M1}/screen`, () => fail(status)]] });
    await user.click(await screen.findByRole('button', { name: /^Open/ }));
    await user.click(await screen.findByRole('button', { name: /^Screen for red flags/ }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Screen for red flags' }));
    expect(await within(screen.getByRole('dialog')).findByText(words)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('has an empty state, is not available to another role, and offers no send button', async () => {
    const a = await openConsole({ hash: '/messages', routes: [threads([])] });
    expect(await screen.findByText('There are no conversations.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Send/ })).toBeNull();
    a.unmount();
    await openConsole({ role: 'admin', hash: '/messages', routes: [['get', '/v1/staff/messages/threads', () => fail(403)]] });
    await screen.findByText('This is not available to your role.');
  });

  it('is in Tamil', async () => {
    await openConsole({ hash: '/messages', lang: 'ta', routes: [threads()] });
    expect(await screen.findByRole('heading', { name: 'செய்திகள்' })).toBeInTheDocument();
  });
});

describe('patient labels', () => {
  const labelRoute = (items: unknown[] = [{ birth_year: 1980, name: 'Demo Person', patient_id: ID.patient, phone_last4: '4321' }]): Routes[number] => ['post', '/v1/staff/patients/labels', () => json({ items })];

  it('reads nothing until confirmed, then shows name, year of birth and the last four digits beside the id; no key is sent', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ hash: '/messages', routes: [threads(), labelRoute()] });
    await user.click(await screen.findByRole('button', { name: 'Show names' }));
    expect(calls.some((c) => c.path === '/v1/staff/patients/labels')).toBe(false);
    const dialog = screen.getByRole('dialog', { name: 'Show names for these patients?' });
    expect(within(dialog).getByText(/one audit entry that holds only the count/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Show names' }));
    expect(await screen.findByText('Demo Person')).toBeInTheDocument();
    expect(screen.getByText(/born 1980/)).toBeInTheDocument();
    expect(screen.getByText(/phone ends 4321/)).toBeInTheDocument();
    const sent = calls.find((c) => c.path === '/v1/staff/patients/labels');
    expect(sent?.body).toEqual({ clinic_id: CLINIC_A, patient_ids: [ID.patient] });
    expect(sent?.key).toBeNull();
  });

  it('asks for at most 25 patients at a time and says so', async () => {
    const user = userEvent.setup();
    const many = Array.from({ length: 30 }, (_, i) => thread({ thread_id: `${String(i).padStart(8, '0')}-0000-4000-8000-000000000000`, patient_id: `${String(i + 100).padStart(8, '0')}-0000-4000-8000-000000000000` }));
    const { calls } = await openConsole({ hash: '/messages', routes: [threads(many), labelRoute([])] });
    await user.click(await screen.findByRole('button', { name: 'Show names' }));
    expect(screen.getByText('Only the first 25 on the list are shown at a time.')).toBeInTheDocument();
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Show names' }));
    await screen.findByText(/Names shown for 0 patients/);
    expect((calls.find((c) => c.path === '/v1/staff/patients/labels')?.body as { patient_ids: string[] }).patient_ids).toHaveLength(25);
  });

  it.each([
    [403, 'You may not see names here. Nothing was shown.'],
    [404, 'This clinic was not found. Nothing was shown.'],
    [503, 'Names cannot be shown in this environment. Nothing was shown.'],
    [422, 'Too many patients at once. Nothing was shown.'],
    [429, 'Too many requests. Wait a moment and try again.'],
  ])('tells a %s in fixed words and never the backend text', async (status, words) => {
    const user = userEvent.setup();
    await openConsole({ hash: '/messages', routes: [threads(), ['post', '/v1/staff/patients/labels', () => fail(status)]] });
    await user.click(await screen.findByRole('button', { name: 'Show names' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Show names' }));
    expect(await within(screen.getByRole('dialog')).findByText(words)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('is offered on the bookings list too, and cancelling reads nothing', async () => {
    const user = userEvent.setup();
    const booking = { booking_id: ID.doc, clinic_id: CLINIC_A, ends_at: '2026-12-01T05:30:00Z', location_id: ID.slot, patient_id: ID.patient, provider_id: ID.doc2, slot_id: ID.slot, starts_at: '2026-12-01T05:00:00Z', status: 'confirmed', visit_type_id: ID.visitType };
    const { calls } = await openConsole({ hash: '/scheduling/bookings', routes: [['get', '/v1/staff/scheduling/bookings', () => list([booking])]] });
    await user.click(await screen.findByRole('button', { name: 'Show names' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }));
    expect(calls.some((c) => c.path === '/v1/staff/patients/labels')).toBe(false);
  });
});
