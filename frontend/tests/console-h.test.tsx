import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CLINIC_A, CLINIC_B, json } from './helpers/fakes';
import { ID, LEAK, fail, list, openConsole, writes, type Routes } from './helpers/console';

// MOCK-ONLY (see helpers/console.tsx): fakes written from openapi.snapshot.json, not from a live backend.
//
// Item 17 in docs/frontend-backend-requests.md: the scheduling lists return the rows of EVERY clinic the account can read, while the patient
// search covers only the ACTIVE clinic, so a slot of one clinic was offered with a patient of another and Hold / Book answered 404 with
// the detail "unknown patient for this clinic", which the console showed as "That could not be found.".

const OTHER = CLINIC_B; // another clinic the same account can read
const SLOT_OTHER = 'abababab-abab-4bab-8bab-abababababab';
const BOOKING_OTHER = 'cdcdcdcd-cdcd-4dcd-8dcd-cdcdcdcdcdcd';
const HOLD_OTHER = 'efefefef-efef-4fef-8fef-efefefefefef';
const WAIT_OTHER = '12121212-1212-4212-8212-121212121212';
const THREAD = 'ffffffff-ffff-4fff-8fff-ffffffffffff';
const MESSAGE = '34343434-3434-4434-8434-343434343434';
const MESSAGE_SAVED = '56565656-5656-4656-8656-565656565656';

const slot = (id: string, clinic: string, over: Record<string, unknown> = {}) => ({
  clinic_id: clinic, slot_id: id, provider_id: ID.doc, location_id: ID.slot, visit_type_id: ID.visitType,
  starts_at: '2026-11-10T04:00:00Z', ends_at: '2026-11-10T04:30:00Z', status: 'open', hold_expires_at: null, ...over,
});
const refs: Routes = [
  ['get', '/v1/staff/scheduling/providers', () => list([{ active: true, clinic_id: CLINIC_A, display_name: 'Provider A', provider_id: ID.doc }, { active: true, clinic_id: OTHER, display_name: 'Provider Other', provider_id: ID.doc2 }])],
  ['get', '/v1/staff/scheduling/locations', () => list([{ active: true, clinic_id: CLINIC_A, location_id: ID.slot, name: 'Room A' }])],
  ['get', '/v1/staff/scheduling/visit-types', () => list([{ active: true, clinic_id: CLINIC_A, duration_minutes: 30, name: 'Visit A', visit_type_id: ID.visitType }, { active: true, clinic_id: OTHER, duration_minutes: 20, name: 'Visit Other', visit_type_id: ID.hold }])],
  ['get', '/v1/staff/scheduling/priority-levels', () => list([{ active: true, clinic_id: CLINIC_A, code: 'P1', label: 'Level A', rank: 1 }, { active: true, clinic_id: OTHER, code: 'P9', label: 'Level Other', rank: 9 }])],
];
const searchRoute: Routes[number] = ['post', '/v1/staff/patients/search', () => json({ items: [{ patient_id: ID.patient, verified: true, registered_via: 'staff', matched_kinds: ['name'] }], labels_withheld: false, truncated: false })];

async function findPatient(user: ReturnType<typeof userEvent.setup>, dialog: HTMLElement) {
  await user.type(within(dialog).getByLabelText('Full name'), 'Test Person');
  await user.type(within(dialog).getByLabelText('Date of birth'), '1990-02-03');
  await user.click(within(dialog).getByRole('button', { name: 'Find' }));
  await user.click(await within(dialog).findByRole('radio'));
}

describe('item 17: the lists show the active clinic only', () => {
  it('slots: a slot of another clinic is not shown, so it cannot be held or booked with a patient of this clinic', async () => {
    await openConsole({ hash: '/scheduling/slots', routes: [...refs, ['get', '/v1/staff/scheduling/slots', () => list([slot(ID.hold, CLINIC_A), slot(SLOT_OTHER, OTHER)])]] });
    const rows = (await screen.findAllByRole('row')).slice(1);
    expect(rows).toHaveLength(1);
    expect(screen.queryByText('Provider Other')).toBeNull();
    // the filter dropdowns and the add-slot choices offer this clinic's providers and visit types only
    const provider = screen.getByLabelText('Provider') as HTMLSelectElement;
    expect(within(provider).getAllByRole('option').map((o) => o.textContent)).toEqual(['All', 'Provider A']);
  });

  it('bookings, holds, waitlist and offers: rows of another clinic are dropped', async () => {
    const booking = (id: string, clinic: string) => ({ booking_id: id, clinic_id: clinic, created_at: '2026-11-09T04:00:00Z', ends_at: '2026-11-10T04:30:00Z', location_id: ID.slot, patient_id: ID.patient, provider_id: ID.doc, slot_id: ID.slot, starts_at: '2026-11-10T04:00:00Z', visit_type_id: ID.visitType });
    const a = await openConsole({ hash: '/scheduling/bookings', routes: [...refs, ['get', '/v1/staff/scheduling/bookings', () => list([booking(ID.task, CLINIC_A), booking(BOOKING_OTHER, OTHER)])]] });
    expect((await screen.findAllByRole('row')).slice(1)).toHaveLength(1);
    a.unmount();
    const hold = (id: string, clinic: string) => ({ clinic_id: clinic, created_at: '2026-11-10T04:00:00Z', expires_at: '2026-11-10T05:00:00Z', hold_id: id, patient_id: null, slot_id: ID.slot, status: 'active' });
    const b = await openConsole({ hash: '/scheduling/holds', routes: [...refs, ['get', '/v1/staff/scheduling/holds', () => list([hold(ID.hold, CLINIC_A), hold(HOLD_OTHER, OTHER)])]] });
    expect((await screen.findAllByRole('row')).slice(1)).toHaveLength(1);
    b.unmount();
    const wait = (id: string, clinic: string, rank: number) => ({ clinic_id: clinic, clinical_priority: 'P1', joined_at: '2026-11-01T00:00:00Z', patient_id: ID.patient, rank, visit_type_id: ID.visitType, waitlist_id: id });
    const c = await openConsole({ hash: '/scheduling/waitlist', routes: [...refs, ['get', '/v1/staff/scheduling/waitlist', () => list([wait(ID.task, CLINIC_A, 1), wait(WAIT_OTHER, OTHER, 2)])]] });
    expect((await screen.findAllByRole('row')).slice(1)).toHaveLength(1);
    c.unmount();
    const offer = (id: string, clinic: string) => ({ channel: null, clinic_id: clinic, created_at: '2026-11-09T05:00:00Z', delivered_at: null, expires_at: '2026-11-10T05:00:00Z', hold_id: id, message_id: MESSAGE, patient_id: ID.patient, slot_id: ID.slot, state: 'awaiting_delivery' });
    await openConsole({ hash: '/scheduling/offers', routes: [...refs, ['get', '/v1/staff/scheduling/offers', () => list([offer(ID.hold, CLINIC_A), offer(HOLD_OTHER, OTHER)])]] });
    expect((await screen.findAllByRole('row')).slice(1)).toHaveLength(1);
  });

  it('asks the backend for the active clinic on the slot, booking, hold and waitlist lists', async () => {
    const asked = async (hash: string, path: string) => {
      const { calls, unmount } = await openConsole({ hash, routes: [...refs, ['get', path, () => list([])]] });
      await waitFor(() => expect(calls.some((c) => c.path === path)).toBe(true));
      const ids = calls.filter((c) => c.path === path).map((c) => c.query.get('clinic_id'));
      unmount();
      return ids;
    };
    expect(await asked('/scheduling/slots', '/v1/staff/scheduling/slots')).toEqual([CLINIC_A]);
    expect(await asked('/scheduling/bookings', '/v1/staff/scheduling/bookings')).toEqual([CLINIC_A]);
    expect(await asked('/scheduling/holds', '/v1/staff/scheduling/holds')).toEqual([CLINIC_A]);
    expect(await asked('/scheduling/waitlist', '/v1/staff/scheduling/waitlist')).toEqual([CLINIC_A]);
  });

  it('the reference lists show this clinic only', async () => {
    await openConsole({ hash: '/scheduling/providers', routes: [...refs] });
    await screen.findByText('Provider A');
    expect(screen.queryByText('Provider Other')).toBeNull();
  });
});

describe('item 17: Hold and Book say what the backend said, in plain words', () => {
  const slots: Routes[number] = ['get', '/v1/staff/scheduling/slots', () => list([slot(ID.hold, CLINIC_A)])];

  it('Book: "unknown patient for this clinic" is said plainly, not as "That could not be found."', async () => {
    const user = userEvent.setup();
    const book: Routes[number] = ['post', `/v1/staff/scheduling/slots/${ID.hold}/book`, () => json({ detail: 'unknown patient for this clinic' }, 404)];
    await openConsole({ hash: '/scheduling/slots', routes: [...refs, searchRoute, slots, book] });
    await user.click(await screen.findByRole('button', { name: 'Book' }));
    const dialog = screen.getByRole('dialog', { name: 'Book this slot?' });
    await findPatient(user, dialog);
    await user.click(within(dialog).getByRole('button', { name: 'Book' }));
    expect(await within(dialog).findByText('This patient is not registered at the clinic that owns this slot.')).toBeInTheDocument();
    expect(within(dialog).queryByText('That could not be found.')).toBeNull();
    expect(within(dialog).queryByRole('alert')).toBeNull();
  });

  it('Hold: the same phrase is said plainly; a 404 for any other reason (the slot itself) keeps the generic words and never shows the backend text', async () => {
    const user = userEvent.setup();
    let detail = 'unknown patient for this clinic';
    const hold: Routes[number] = ['post', `/v1/staff/scheduling/slots/${ID.hold}/hold`, () => json({ detail }, 404)];
    await openConsole({ hash: '/scheduling/slots', routes: [...refs, searchRoute, slots, hold] });
    await user.click(await screen.findByRole('button', { name: 'Hold' }));
    const dialog = screen.getByRole('dialog', { name: 'Hold this slot?' });
    await user.type(within(dialog).getByLabelText('Minutes to hold'), '20');
    await findPatient(user, dialog);
    await user.click(within(dialog).getByRole('button', { name: 'Hold' }));
    expect(await within(dialog).findByText('This patient is not registered at the clinic that owns this slot.')).toBeInTheDocument();
    detail = 'not found';
    await user.click(within(dialog).getByRole('button', { name: 'Hold' }));
    expect(await within(dialog).findByText('That could not be found.')).toBeInTheDocument();
    detail = LEAK;
    await user.click(within(dialog).getByRole('button', { name: 'Hold' }));
    await within(dialog).findByText('That could not be found.');
    expect(document.body.textContent).not.toContain(LEAK); // an unknown phrase is never shown as it came
  });
});

describe('the patient\'s reply, typed by front desk', () => {
  const run = (over: Record<string, unknown> = {}) => ({ candidate_patient_id: ID.patient2, candidate_rank: 1, finished: false, hold_id: ID.hold, slot_id: ID.slot, status: 'awaiting_reply', task_id: null, waiting_for: 'reply_entry', ...over });
  const header = (id: string) => ({ created_at: '2026-11-09T06:00:00Z', erased: false, message_id: id, sender_kind: 'patient', thread_id: THREAD, topic: 'appointments' });
  const reading = { calibrated: null, confidence: null, engine: null, message_id: MESSAGE_SAVED, needs_person: false, outcome: 'clear', reason: null, replayed: false, screening_id: ID.doc, suggestion: 'accept' };

  async function open(user: ReturnType<typeof userEvent.setup>, extra: Routes, stored: unknown[] = []) {
    const { calls } = await openConsole({ hash: '/scheduling/slots', routes: [
      ['get', '/v1/staff/scheduling/slots', () => list([slot(ID.slot, CLINIC_A)])],
      ...refs,
      ['get', `/v1/staff/scheduling/backfills/${ID.slot}`, () => json(run())],
      ['get', '/v1/staff/scheduling/waitlist', () => list([])],
      ['get', '/v1/staff/messages/threads', () => list([{ clinic_id: CLINIC_A, created_at: '2026-11-09T05:00:00Z', last_message_at: null, message_count: stored.length, patient_id: ID.patient2, status: 'open', thread_id: THREAD, topic: 'appointments' }])],
      ['get', `/v1/staff/messages/threads/${THREAD}/messages`, () => list(stored)],
      ...extra,
    ] });
    await user.click(await screen.findByRole('button', { name: new RegExp(`^Prepare offer ${ID.slot.slice(0, 8)}`) }));
    return calls;
  }

  it('saves only after a confirmation that says nothing is sent, with a key; drops the text from the page; and uses the new message id for the reading and for the entry', async () => {
    const user = userEvent.setup();
    const saveRoute: Routes[number] = ['post', '/v1/staff/scheduling/offers/replies', () => json({ created: true, message_id: MESSAGE_SAVED, outcome: 'clear', screening_id: ID.doc, thread_id: THREAD }, 201)];
    const entered: Routes[number] = ['post', `/v1/staff/scheduling/backfills/${ID.slot}/reply`, () => json(run({ waiting_for: 'none', finished: true }))];
    const calls = await open(user, [saveRoute, ['post', '/v1/staff/scheduling/offers/interpret-reply', () => json(reading)], entered]);
    const box = await screen.findByLabelText(/The patient's reply, as they gave it/);
    const saveButton = screen.getByRole('button', { name: 'Save the reply' });
    expect(saveButton).toBeDisabled();
    await user.type(box, 'Typed reply words');
    await user.click(saveButton);
    const dialog = screen.getByRole('dialog', { name: 'Save this reply?' });
    expect(dialog).toHaveTextContent('Nothing is sent to the patient.');
    expect(writes(calls).filter((c) => c.path.endsWith('/replies'))).toHaveLength(0);
    await user.click(within(dialog).getByRole('button', { name: 'Save the reply' }));
    expect(await screen.findByText(/The reply was saved and picked below/)).toBeInTheDocument();
    const saved = writes(calls).find((c) => c.path.endsWith('/replies'));
    expect(saved?.body).toEqual({ clinic_id: CLINIC_A, patient_id: ID.patient2, text: 'Typed reply words' });
    expect(saved?.key).toBeTruthy();
    expect((box as HTMLTextAreaElement).value).toBe(''); // the text is gone from the page
    expect(document.body.textContent).not.toContain('Typed reply words');
    await user.click(screen.getByRole('button', { name: 'Suggest a reading' }));
    await screen.findByText('Accepts');
    expect(writes(calls).find((c) => c.path.endsWith('/interpret-reply'))?.body).toEqual({ message_id: MESSAGE_SAVED });
    await user.click(screen.getByRole('button', { name: 'Enter this reply' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Enter this reply' }));
    await screen.findByText(/The reply was entered/);
    expect(writes(calls).find((c) => c.path.endsWith('/backfills/' + ID.slot + '/reply'))?.body).toEqual({ outcome: 'replied', message_id: MESSAGE_SAVED });
  });

  it('says a red-flag reply raised the nurse task, as soon as it is saved', async () => {
    const user = userEvent.setup();
    const saveRoute: Routes[number] = ['post', '/v1/staff/scheduling/offers/replies', () => json({ created: true, message_id: MESSAGE_SAVED, outcome: 'red_flag', screening_id: ID.doc, thread_id: THREAD }, 201)];
    await open(user, [saveRoute]);
    await user.type(await screen.findByLabelText(/The patient's reply, as they gave it/), 'words');
    await user.click(screen.getByRole('button', { name: 'Save the reply' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Save the reply' }));
    expect(await screen.findByText(/a red flag was found; a nurse task was raised/)).toBeInTheDocument();
  });

  it.each([
    [503, 'Replies cannot be stored yet (encryption is not set up). Nothing was saved.'],
    [409, 'A different reply was already saved with this request. Check what you typed and save again.'],
    [422, 'The reply is blank or too long. Nothing was saved.'],
    [404, 'This patient is not registered at this clinic. Nothing was saved.'],
  ])('shows a %s as a fixed calm message, keeps what was typed so it can be corrected, and never shows the backend text', async (status, words) => {
    const user = userEvent.setup();
    const saveRoute: Routes[number] = ['post', '/v1/staff/scheduling/offers/replies', () => fail(status)];
    await open(user, [saveRoute]);
    const box = await screen.findByLabelText(/The patient's reply, as they gave it/);
    await user.type(box, 'words to keep');
    await user.click(screen.getByRole('button', { name: 'Save the reply' }));
    const dialog = screen.getByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Save the reply' }));
    expect(await within(dialog).findByText(words)).toBeInTheDocument();
    expect(within(dialog).queryByRole('alert')).toBeNull();
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect((box as HTMLTextAreaElement).value).toBe('words to keep');
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('keeps the stored-reply picker for replies saved earlier (after a reload, say)', async () => {
    const user = userEvent.setup();
    await open(user, [], [header(MESSAGE)]);
    expect(await screen.findByText('Or pick a reply that was stored earlier')).toBeInTheDocument();
    await user.click(await screen.findByRole('radio'));
    expect(screen.getByRole('button', { name: 'Enter this reply' })).toBeEnabled();
  });
});
