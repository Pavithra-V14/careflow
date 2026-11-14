import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CLINIC_A, json } from './helpers/fakes';
import { ID, LEAK, fail, list, openConsole, writes, type Routes } from './helpers/console';

// MOCK-ONLY (see helpers/console.tsx): the fakes are written from openapi.snapshot.json, not from a live backend.

afterEach(() => vi.unstubAllGlobals());

const BOOKING = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd';
const LOCATION = 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee';
const THREAD = 'ffffffff-ffff-4fff-8fff-ffffffffffff';
const MESSAGE = '12121212-1212-4212-8212-121212121212';

const slot = (over: Record<string, unknown> = {}) => ({
  clinic_id: CLINIC_A, slot_id: ID.slot, provider_id: ID.doc, location_id: LOCATION, visit_type_id: ID.visitType,
  starts_at: '2026-11-10T04:00:00Z', ends_at: '2026-11-10T04:30:00Z', status: 'open', hold_expires_at: null, ...over,
});
const provider = { active: true, clinic_id: CLINIC_A, display_name: 'Placeholder Provider', provider_id: ID.doc };
const location = { active: true, clinic_id: CLINIC_A, location_id: LOCATION, name: 'Placeholder Room' };
const visitType = { active: true, clinic_id: CLINIC_A, duration_minutes: 30, name: 'Placeholder Visit', visit_type_id: ID.visitType };
const refs: Routes = [
  ['get', '/v1/staff/scheduling/providers', () => list([provider])],
  ['get', '/v1/staff/scheduling/locations', () => list([location])],
  ['get', '/v1/staff/scheduling/visit-types', () => list([visitType])],
  ['get', '/v1/staff/scheduling/priority-levels', () => list([{ active: true, clinic_id: CLINIC_A, code: 'P1', label: 'Placeholder One', rank: 1 }])],
];
const searchRoute: Routes[number] = ['post', '/v1/staff/patients/search', () => json({ items: [{ patient_id: ID.patient, verified: true, registered_via: 'staff', matched_kinds: ['name'] }], labels_withheld: false, truncated: false })];
const booking = { booking_id: BOOKING, clinic_id: CLINIC_A, created_at: '2026-11-09T04:00:00Z', ends_at: '2026-11-10T04:30:00Z', location_id: LOCATION, patient_id: ID.patient, provider_id: ID.doc, slot_id: ID.slot, starts_at: '2026-11-10T04:00:00Z', visit_type_id: ID.visitType };
const offer = (over: Record<string, unknown> = {}) => ({
  channel: null, clinic_id: CLINIC_A, created_at: '2026-11-09T05:00:00Z', delivered_at: null, expires_at: '2026-11-10T05:00:00Z', hold_id: ID.hold, message_id: MESSAGE, patient_id: ID.patient, slot_id: ID.slot, state: 'awaiting_delivery', ...over,
});

async function findPatient(user: ReturnType<typeof userEvent.setup>, dialog: HTMLElement) {
  await user.type(within(dialog).getByLabelText('Full name'), 'Test Person');
  await user.type(within(dialog).getByLabelText('Date of birth'), '1990-02-03');
  await user.click(within(dialog).getByRole('button', { name: 'Find' }));
  await user.click(await within(dialog).findByRole('radio'));
}

describe('slots', () => {
  it('lists slots with provider, location and visit type names, filters by query, and shows loading/empty/error states', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ hash: '/scheduling/slots', routes: [...refs, ['get', '/v1/staff/scheduling/slots', () => list([slot()])]] });
    const row = (await screen.findAllByRole('row'))[1] as HTMLElement;
    expect(within(row).getByText('Placeholder Provider')).toBeInTheDocument();
    expect(within(row).getByText('Placeholder Room')).toBeInTheDocument();
    expect(within(row).getByText('Placeholder Visit')).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText('Provider'), ID.doc);
    await waitFor(() => expect(calls.some((c) => c.path === '/v1/staff/scheduling/slots' && c.query.get('provider_id') === ID.doc)).toBe(true));
  });

  it('says so when there are none, and shows a fixed message (never the backend text) on failure', async () => {
    const a = await openConsole({ hash: '/scheduling/slots', routes: [...refs, ['get', '/v1/staff/scheduling/slots', () => list([])]] });
    expect(await screen.findByText('There are no open or held slots.')).toBeInTheDocument();
    a.unmount();
    await openConsole({ hash: '/scheduling/slots', routes: [...refs, ['get', '/v1/staff/scheduling/slots', () => fail(502)]] });
    expect(await screen.findByRole('alert')).toHaveTextContent('This is not available right now.');
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('shows no hold, book or add control to a nurse (the backend decides, this only hides dead ends)', async () => {
    await openConsole({ role: 'nurse', hash: '/scheduling/slots', routes: [...refs, ['get', '/v1/staff/scheduling/slots', () => list([slot()])]] });
    await screen.findAllByRole('row');
    expect(screen.queryByRole('button', { name: 'Hold' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Book' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Add a slot' })).toBeNull();
  });

  it('holds a slot: nothing is written before the confirmation, nothing is filled in for the person, and the key is reused on a retry', async () => {
    const user = userEvent.setup();
    let attempts = 0;
    const hold: Routes[number] = ['post', `/v1/staff/scheduling/slots/${ID.slot}/hold`, () => (++attempts === 1 ? fail(503) : json({ expires_at: '2026-11-10T05:00:00Z', hold_id: ID.hold, replayed: attempts > 2, slot_id: ID.slot }))];
    const { calls } = await openConsole({ hash: '/scheduling/slots', routes: [...refs, ['get', '/v1/staff/scheduling/slots', () => list([slot()])], hold] });
    await user.click(await screen.findByRole('button', { name: 'Hold' }));
    const dialog = screen.getByRole('dialog', { name: 'Hold this slot?' });
    const minutes = within(dialog).getByLabelText('Minutes to hold') as HTMLInputElement;
    expect(minutes.value).toBe('');
    expect(within(dialog).getByRole('button', { name: 'Hold' })).toBeDisabled();
    expect(writes(calls)).toHaveLength(0);
    await user.type(minutes, '20');
    expect(writes(calls)).toHaveLength(0);
    await user.click(within(dialog).getByRole('button', { name: 'Hold' }));
    expect(await within(dialog).findByRole('alert')).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Hold' }));
    await screen.findByText('The slot is held.');
    const sent = writes(calls).filter((c) => c.path.endsWith('/hold'));
    expect(sent).toHaveLength(2);
    expect(sent[0]?.key).toBeTruthy();
    expect(sent[1]?.key).toBe(sent[0]?.key);
    expect(sent[0]?.body).toEqual({ duration_minutes: 20 });
  });

  it('books a slot for a patient chosen from a search; a 409 is a calm status and the patient is not pre-selected', async () => {
    const user = userEvent.setup();
    let taken = true;
    const book: Routes[number] = ['post', `/v1/staff/scheduling/slots/${ID.slot}/book`, () => (taken ? fail(409) : json({ booking_id: BOOKING, patient_id: ID.patient, replayed: false, slot_id: ID.slot, status: 'confirmed' }))];
    const { calls } = await openConsole({ hash: '/scheduling/slots', routes: [...refs, searchRoute, ['get', '/v1/staff/scheduling/slots', () => list([slot()])], book] });
    await user.click(await screen.findByRole('button', { name: 'Book' }));
    const dialog = screen.getByRole('dialog', { name: 'Book this slot?' });
    expect(within(dialog).getByRole('button', { name: 'Book' })).toBeDisabled();
    await findPatient(user, dialog);
    expect(writes(calls).filter((c) => c.path.endsWith('/book'))).toHaveLength(0);
    await user.click(within(dialog).getByRole('button', { name: 'Book' }));
    expect(await within(dialog).findByText('That changed in the meantime. Refresh and look again.')).toBeInTheDocument();
    expect(within(dialog).queryByRole('alert')).toBeNull();
    taken = false;
    await user.click(within(dialog).getByRole('button', { name: 'Book' }));
    await screen.findByText('The slot is booked.');
    expect(writes(calls).filter((c) => c.path.endsWith('/book')).at(-1)?.body).toEqual({ patient_id: ID.patient });
  });
});

describe('setup (add a provider or a slot)', () => {
  it('writes nothing until the confirmation, and sends the clinic id and the entered name with a key', async () => {
    const user = userEvent.setup();
    const create: Routes[number] = ['post', '/v1/staff/scheduling/providers', () => json({ clinic_id: CLINIC_A, id: ID.doc2, replayed: false })];
    const { calls } = await openConsole({ hash: '/scheduling/providers', routes: [...refs, create] });
    await user.click(await screen.findByRole('button', { name: 'Add a provider' }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByRole('button', { name: 'Add' })).toBeDisabled();
    await user.type(within(dialog).getByLabelText('Name'), 'New Provider');
    expect(writes(calls)).toHaveLength(0);
    await user.click(within(dialog).getByRole('button', { name: 'Add' }));
    await screen.findByText('Created.');
    expect(writes(calls)[0]).toMatchObject({ path: '/v1/staff/scheduling/providers', body: { clinic_id: CLINIC_A, display_name: 'New Provider' } });
    expect(writes(calls)[0]?.key).toBeTruthy();
  });

  it('a visit type needs a length typed by the person (nothing guesses one)', async () => {
    const user = userEvent.setup();
    await openConsole({ hash: '/scheduling/visit-types', routes: [...refs] });
    await user.click(await screen.findByRole('button', { name: 'Add a visit type' }));
    const dialog = screen.getByRole('dialog');
    await user.type(within(dialog).getByLabelText('Name'), 'X');
    expect(within(dialog).getByRole('button', { name: 'Add' })).toBeDisabled();
    expect((within(dialog).getByLabelText('Length in minutes') as HTMLInputElement).value).toBe('');
  });
});

describe('bookings and cancel', () => {
  it('cancels only after a confirmation with a marked placeholder, then shows the ranked waitlist and says nobody is contacted', async () => {
    const user = userEvent.setup();
    const cancel: Routes[number] = ['post', `/v1/staff/scheduling/bookings/${BOOKING}/cancel`, () => json({ booking_id: BOOKING, candidates: [{ joined_at: '2026-11-01T00:00:00Z', patient_id: ID.patient2, priority_code: 'P1', rank: 1, waitlist_id: ID.task }], replayed: false, slot_id: ID.slot })];
    const { calls } = await openConsole({ hash: '/scheduling/bookings', routes: [...refs, ['get', '/v1/staff/scheduling/bookings', () => list([booking])], cancel] });
    await user.click(await screen.findByRole('button', { name: /Cancel booking/ }));
    const dialog = screen.getByRole('dialog', { name: 'Cancel this booking?' });
    expect(dialog).toHaveTextContent('Nobody is contacted and nobody is booked.');
    expect(dialog.querySelector('[data-placeholder]')).not.toBeNull();
    expect(writes(calls)).toHaveLength(0);
    await user.click(within(dialog).getByRole('button', { name: 'Cancel booking' }));
    expect(await screen.findByText('The booking was cancelled and the slot is free.')).toBeInTheDocument();
    expect(screen.getByText('Ranked waitlist for the freed slot')).toBeInTheDocument();
    expect(writes(calls)).toHaveLength(1);
    expect(writes(calls)[0]?.key).toBeTruthy();
  });

  it('has an empty state and a fixed error message', async () => {
    const a = await openConsole({ hash: '/scheduling/bookings', routes: [...refs, ['get', '/v1/staff/scheduling/bookings', () => list([])]] });
    expect(await screen.findByText('There are no confirmed bookings.')).toBeInTheDocument();
    a.unmount();
    await openConsole({ hash: '/scheduling/bookings', routes: [...refs, ['get', '/v1/staff/scheduling/bookings', () => fail(500)]] });
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
  });
});

describe('waitlist', () => {
  it('lists in the order the backend gives (rank), and joins a patient only after choosing every field and confirming', async () => {
    const user = userEvent.setup();
    const join: Routes[number] = ['post', '/v1/staff/scheduling/waitlist/join', () => json({ clinical_priority: 'P1', patient_id: ID.patient, replayed: false, visit_type_id: ID.visitType, waitlist_id: ID.task })];
    const rows = [
      { clinic_id: CLINIC_A, clinical_priority: 'P1', joined_at: '2026-11-01T00:00:00Z', patient_id: ID.patient, rank: 1, visit_type_id: ID.visitType, waitlist_id: ID.task },
      { clinic_id: CLINIC_A, clinical_priority: 'P2', joined_at: '2026-11-02T00:00:00Z', patient_id: ID.patient2, rank: 2, visit_type_id: ID.visitType, waitlist_id: ID.task2 },
    ];
    const { calls } = await openConsole({ hash: '/scheduling/waitlist', routes: [...refs, searchRoute, ['get', '/v1/staff/scheduling/waitlist', () => list(rows)], join] });
    const table = await screen.findByRole('table');
    expect(within(table).getAllByRole('row').slice(1).map((r) => within(r).getAllByRole('cell')[2]?.textContent)).toEqual(['P1', 'P2']);
    await user.click(screen.getByRole('button', { name: 'Add to waitlist' }));
    const dialog = screen.getByRole('dialog');
    const confirm = within(dialog).getByRole('button', { name: 'Add to waitlist' });
    expect(confirm).toBeDisabled();
    await findPatient(user, dialog);
    await user.selectOptions(within(dialog).getByLabelText('Visit type'), ID.visitType);
    expect(confirm).toBeDisabled(); // the priority is still the person's to choose
    await user.selectOptions(within(dialog).getByLabelText('Priority'), 'P1');
    expect(writes(calls).filter((c) => c.path.endsWith('/join'))).toHaveLength(0);
    await user.click(confirm);
    await screen.findByText('Added to the waitlist.');
    expect(writes(calls).find((c) => c.path.endsWith('/join'))?.body).toEqual({ patient_id: ID.patient, visit_type_id: ID.visitType, clinical_priority: 'P1' });
  });

  it('is empty when nobody waits', async () => {
    await openConsole({ hash: '/scheduling/waitlist', routes: [...refs, ['get', '/v1/staff/scheduling/waitlist', () => list([])]] });
    expect(await screen.findByText('Nobody is waiting.')).toBeInTheDocument();
  });
});

describe('offers: list, copy the text, mark delivered (the system sends nothing)', () => {
  const textRoute = (answer: () => Response): Routes[number] => ['get', `/v1/staff/messages/${MESSAGE}`, answer];
  const text = () => json({ body: 'Placeholder offer text', message_id: MESSAGE, sender_kind: 'staff', thread_id: THREAD, topic: 'appointments' });

  it('shows every state in words, filters by state, and has empty and error states', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ hash: '/scheduling/offers', routes: [...refs, ['get', '/v1/staff/scheduling/offers', (req) => list(req.query.get('state') ? [] : [offer(), offer({ hold_id: ID.slot, state: 'delivered', delivered_at: '2026-11-09T06:00:00Z', channel: 'manual' })])]] });
    expect((await screen.findAllByText('Awaiting delivery')).length).toBeGreaterThan(0);
    expect(screen.getAllByText('Delivered').length).toBeGreaterThan(0);
    await user.click(screen.getByRole('button', { name: 'Expired' }));
    expect(await screen.findByText('There are no offers.')).toBeInTheDocument();
    expect(calls.filter((c) => c.path === '/v1/staff/scheduling/offers').map((c) => c.query.get('state'))).toEqual([null, 'expired']);
  });

  it('copies the text by the offer\'s message id on a click only (never before), and then says to send it yourself', async () => {
    const user = userEvent.setup();
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    const { calls } = await openConsole({ hash: '/scheduling/offers', routes: [...refs, textRoute(text), ['get', '/v1/staff/scheduling/offers', () => list([offer()])]] });
    await screen.findAllByRole('row');
    expect(calls.some((c) => c.path.startsWith('/v1/staff/messages'))).toBe(false); // no message is read, and so none audited, until asked
    await user.click(screen.getByRole('button', { name: /Copy offer text/ }));
    await screen.findByText('The offer text was copied. Send it yourself, then mark it delivered.');
    expect(writeText).toHaveBeenCalledWith('Placeholder offer text');
    expect(calls.filter((c) => c.path.startsWith('/v1/staff/messages')).map((c) => c.path)).toEqual([`/v1/staff/messages/${MESSAGE}`]); // exactly one read, by id: no thread search, no guess
    expect(writes(calls)).toHaveLength(0);
    expect(document.body.textContent).not.toContain('Placeholder offer text'); // the text is not left on the page
  });

  it('offers no copy button when the offer has no message id, and says so plainly when the message cannot be found', async () => {
    const user = userEvent.setup();
    const a = await openConsole({ hash: '/scheduling/offers', routes: [...refs, ['get', '/v1/staff/scheduling/offers', () => list([offer({ message_id: null })])]] });
    await screen.findAllByRole('row');
    expect(screen.queryByRole('button', { name: /Copy offer text/ })).toBeNull();
    a.unmount();
    await openConsole({ hash: '/scheduling/offers', routes: [...refs, textRoute(() => fail(404)), ['get', '/v1/staff/scheduling/offers', () => list([offer()])]] });
    await user.click(await screen.findByRole('button', { name: /Copy offer text/ }));
    expect(await screen.findByText('The offer text could not be found.')).toBeInTheDocument();
  });

  it('shows the text for manual copying when the browser refuses the clipboard', async () => {
    const user = userEvent.setup();
    Object.defineProperty(navigator, 'clipboard', { value: { writeText: async () => Promise.reject(new Error('blocked')) }, configurable: true });
    await openConsole({ hash: '/scheduling/offers', routes: [...refs, textRoute(text), ['get', '/v1/staff/scheduling/offers', () => list([offer()])]] });
    await user.click(await screen.findByRole('button', { name: /Copy offer text/ }));
    expect(await screen.findByText(/Copying was blocked by the browser/)).toBeInTheDocument();
    expect(screen.getByLabelText('Copy offer text')).toHaveValue('Placeholder offer text');
  });

  it('marks delivered only after a confirmation; a second press is a replay; 409 is calm; and there is no send button that works', async () => {
    const user = userEvent.setup();
    let conflict = true;
    const mark: Routes[number] = ['post', `/v1/staff/scheduling/offers/${ID.hold}/mark-delivered`, () => (conflict ? fail(409) : json({ channel: 'manual', delivered_at: '2026-11-09T06:00:00Z', hold_id: ID.hold, replayed: false }))];
    const { calls } = await openConsole({ hash: '/scheduling/offers', routes: [...refs, ['get', '/v1/staff/scheduling/offers', () => list([offer()])], mark] });
    await user.click(await screen.findByRole('button', { name: /Mark delivered/ }));
    const dialog = screen.getByRole('dialog', { name: 'Mark this offer delivered?' });
    expect(dialog).toHaveTextContent('The system sent nothing to the patient.');
    expect(writes(calls)).toHaveLength(0);
    await user.click(within(dialog).getByRole('button', { name: 'Mark delivered' }));
    expect(await within(dialog).findByText(/no longer live/)).toBeInTheDocument();
    conflict = false;
    await user.click(within(dialog).getByRole('button', { name: 'Mark delivered' }));
    await screen.findByText('The offer is marked delivered.');
    expect(writes(calls).every((c) => c.path.endsWith('/mark-delivered') && !!c.key)).toBe(true);
    expect(screen.queryByRole('button', { name: 'Send offer' })).toBeNull();
  });

  it('offers no mark-delivered button to a nurse', async () => {
    await openConsole({ role: 'nurse', hash: '/scheduling/offers', routes: [...refs, ['get', '/v1/staff/scheduling/offers', () => list([offer()])]] });
    await screen.findAllByRole('row');
    expect(screen.queryByRole('button', { name: /Mark delivered/ })).toBeNull();
  });
});

describe('reviewer view and reason codes', () => {
  const proposal = { abstained: false, candidates: [{ card_id: 'card-x', version: 3 }], created_at: '2026-10-30T05:00:00Z', mode: 'shadow', patient_id: ID.patient, proposal_id: ID.doc2, review_task_id: ID.task };
  const detail = { clinic_id: CLINIC_A, content_type: 'application/pdf', created_at: '2026-10-30T05:00:00Z', decided: false, document_id: ID.doc, holds: [], open_holds: 0, proposal, proposal_id: ID.doc2, proposal_visible: true, registered_by: ID.user, run_state: 'awaiting_decision', size_bytes: 2048 };
  const view = (over: Record<string, unknown> = {}) => ({
    alternatives: [{ label: 'card-x@3', probability: null }, { label: 'card-y@1', probability: 0.25 }],
    confidence: null, engine: null, evidence: [{ field: 'reason', source: 'letter', crop_ref: null }],
    kind: 'triage_proposal', on_approve: 'Approve text from the API', on_edit: 'Edit text from the API', on_reject: 'Reject text from the API',
    proposal: { abstained: false, mode: 'shadow', candidate_count: 2 }, reason_codes: [], task_id: ID.task, triggering_rule: null, ...over,
  });
  const base = (over: Record<string, unknown> = {}, codes: unknown = { configured: false, codes: [] }): Routes => [
    ['get', `/v1/staff/referrals/${ID.doc}`, () => json(detail)],
    ['get', `/v1/staff/referrals/${ID.doc}/review-view`, () => json(view(over))],
    ['get', '/v1/staff/console/reason-codes', () => json(codes)],
    ['post', `/v1/staff/referrals/${ID.doc}/review`, (req) => json({ decision: (req.body as { decision: string }).decision, document_id: ID.doc, replayed: false })],
  ];

  it('shows an unknown confidence, probability, engine and rule as "Not recorded", never as zero', async () => {
    await openConsole({ role: 'nurse', hash: `/referrals/${ID.doc}`, routes: base() });
    await screen.findByText('Approve text from the API');
    const section = screen.getByRole('heading', { name: 'Reviewer view' }).closest('section') as HTMLElement;
    const facts = within(section);
    expect(facts.getAllByText('Not recorded').length).toBeGreaterThanOrEqual(4);
    expect(facts.getByText('25%')).toBeInTheDocument();
    expect(section.textContent).not.toMatch(/\b0%/);
    expect(facts.getByText('Approve text from the API')).toBeInTheDocument();
  });

  it('labels the proposal a suggestion, and shows the mode, engine and figures exactly as sent', async () => {
    await openConsole({ role: 'nurse', hash: `/referrals/${ID.doc}`, routes: base({ confidence: 0.876, engine: 'model-label-x', proposal: { abstained: false, mode: 'shadow', candidate_count: 2 }, alternatives: [{ label: 'card-x@3', probability: 0.4321 }] }) });
    await screen.findByText('Approve text from the API');
    const section = screen.getByRole('heading', { name: 'Reviewer view' }).closest('section') as HTMLElement;
    expect(within(section).getByText(/This is a suggestion made by the system.*It is not a decision: a person decides\./)).toBeInTheDocument();
    expect(within(section).getByText('shadow')).toBeInTheDocument(); // the mode word as sent, not reworded
    expect(within(section).getByText('model-label-x')).toBeInTheDocument();
    expect(within(section).getByText('87.6%')).toBeInTheDocument(); // not rounded to 88%
    expect(within(section).getByText('43.2%')).toBeInTheDocument();
    const detailSection = screen.getByRole('heading', { level: 2, name: 'Triage proposal' }).parentElement as HTMLElement;
    expect(within(detailSection).getAllByText(/This is a suggestion made by the system/).length).toBeGreaterThanOrEqual(1);
  });

  it('shows an unknown mode as "Not recorded" in the reviewer view', async () => {
    await openConsole({ role: 'nurse', hash: `/referrals/${ID.doc}`, routes: base({ proposal: { abstained: false, candidate_count: 2 } }) });
    await screen.findByText('Approve text from the API');
    const section = screen.getByRole('heading', { name: 'Reviewer view' }).closest('section') as HTMLElement;
    const mode = within(section).getByText('Mode').nextElementSibling as HTMLElement;
    expect(mode).toHaveTextContent('Not recorded');
  });

  it('shows a known confidence as a percentage', async () => {
    await openConsole({ role: 'nurse', hash: `/referrals/${ID.doc}`, routes: base({ confidence: 0.8, engine: 'engine-x', triggering_rule: 'rule-x' }) });
    expect(await screen.findByText('80%')).toBeInTheDocument();
    expect(screen.getByText('engine-x')).toBeInTheDocument();
  });

  it('says there is no proposal on a 404 and a fixed message (no backend text) on a failure', async () => {
    const a = await openConsole({ role: 'nurse', hash: `/referrals/${ID.doc}`, routes: [['get', `/v1/staff/referrals/${ID.doc}`, () => json(detail)], ['get', `/v1/staff/referrals/${ID.doc}/review-view`, () => fail(404)]] });
    expect(await screen.findByText('There is no proposal to show for this referral.')).toBeInTheDocument();
    a.unmount();
    await openConsole({ role: 'nurse', hash: `/referrals/${ID.doc}`, routes: [['get', `/v1/staff/referrals/${ID.doc}`, () => json(detail)], ['get', `/v1/staff/referrals/${ID.doc}/review-view`, () => fail(500)]] });
    await waitFor(() => expect(screen.getAllByRole('alert').length).toBeGreaterThan(0));
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('is not shown to front desk', async () => {
    await openConsole({ role: 'front_desk', hash: `/referrals/${ID.doc}`, routes: base() });
    await screen.findByRole('heading', { level: 2, name: 'Triage proposal' });
    expect(screen.queryByRole('heading', { name: 'Reviewer view' })).toBeNull();
  });

  it('when the clinic has a list of reasons, a reject needs one chosen from it and sends it; nothing is pre-selected', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ role: 'nurse', hash: `/referrals/${ID.doc}`, routes: base({}, { configured: true, codes: ['reason_a', 'reason_b'] }) });
    const group = await screen.findByRole('group', { name: 'Decision' });
    await user.click(within(group).getByRole('radio', { name: 'Reject' }));
    const record = screen.getByRole('button', { name: 'Record decision' });
    expect(record).toBeDisabled();
    const select = screen.getByLabelText('Reason') as HTMLSelectElement;
    expect(select.value).toBe('');
    await user.selectOptions(select, 'reason_b');
    expect(record).toBeEnabled();
    await user.click(record);
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Record decision' }));
    await screen.findByText('The decision was recorded.');
    expect(writes(calls)[0]?.body).toEqual({ decision: 'reject', reason_code: 'reason_b' });
  });

  it('when the list is not set, no reason is asked for or sent and the marked placeholder stays', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ role: 'nurse', hash: `/referrals/${ID.doc}`, routes: base() });
    const group = await screen.findByRole('group', { name: 'Decision' });
    await user.click(within(group).getByRole('radio', { name: 'Reject' }));
    expect(screen.queryByLabelText('Reason')).toBeNull();
    expect(document.querySelector('[data-placeholder]')).not.toBeNull();
    await user.click(screen.getByRole('button', { name: 'Record decision' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Record decision' }));
    await screen.findByText('The decision was recorded.');
    expect(writes(calls)[0]?.body).toEqual({ decision: 'reject' });
  });
});
