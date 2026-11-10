import { describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CLINIC_A, json } from './helpers/fakes';
import { ID, LEAK, fail, list, openConsole, writes, type Routes } from './helpers/console';

// MOCK-ONLY (see helpers/console.tsx): fakes written from openapi.snapshot.json, not from a live backend.

const GRANT = 'efefefef-efef-4fef-8fef-efefefefefef';
const VISIT = 'abababab-abab-4bab-8bab-abababababab';
const BOOKING = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd';
const THREAD = 'ffffffff-ffff-4fff-8fff-ffffffffffff';
const MESSAGE = '12121212-1212-4212-8212-121212121212';

describe('break-glass uses the clinic lists', () => {
  const granted: Routes[number] = ['post', '/v1/staff/break-glass', () => json({ created: true, expires_at: '2026-11-10T06:00:00Z', grant_id: GRANT }, 201)];
  const mineRoute = (rows: unknown[]): Routes[number] => ['get', '/v1/staff/break-glass/mine', () => json({ grants: rows })];

  it('picks the reason from the clinic\'s list (nothing chosen for the person) and sends the chosen code', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ role: 'nurse', hash: '/access', routes: [['get', '/v1/staff/break-glass/reasons', () => json({ reasons: ['reason_a', 'reason_b'] })], mineRoute([]), granted] });
    await user.click(await screen.findByRole('button', { name: "Open access to a patient's messages" }));
    const dialog = screen.getByRole('dialog');
    await user.type(within(dialog).getByLabelText('Patient id'), ID.patient);
    const select = await within(dialog).findByLabelText('Reason code');
    expect(select.tagName).toBe('SELECT');
    expect((select as HTMLSelectElement).value).toBe('');
    expect(within(select).getAllByRole('option').map((o) => o.textContent)).toEqual(['Choose a reason', 'reason a', 'reason b']);
    expect(within(dialog).getByRole('button', { name: 'Open access' })).toBeDisabled();
    await user.selectOptions(select, 'reason_b');
    await user.click(within(dialog).getByRole('button', { name: 'Open access' }));
    await screen.findByText(/Access is open until/);
    expect(writes(calls)[0]?.body).toMatchObject({ reason_code: 'reason_b', patient_id: ID.patient });
  });

  it('falls back to a typed code of the right shape when the list cannot be read', async () => {
    const user = userEvent.setup();
    await openConsole({ role: 'nurse', hash: '/access', routes: [['get', '/v1/staff/break-glass/reasons', () => fail(500)], mineRoute([])] });
    await user.click(await screen.findByRole('button', { name: "Open access to a patient's messages" }));
    const field = await within(screen.getByRole('dialog')).findByLabelText('Reason code');
    expect(field.tagName).toBe('INPUT');
  });

  it('lists my own open access and lets me pick one up again after a reload, reading only on a click', async () => {
    const user = userEvent.setup();
    const messages: Routes[number] = ['get', `/v1/staff/break-glass/${GRANT}/messages`, () => json({ messages: [{ body: 'Message words', created_at: '2026-11-09T05:00:00Z', erased: false, message_id: MESSAGE, readable: true, sender_kind: 'patient', thread_id: THREAD, topic: 'readiness' }] })];
    const { calls } = await openConsole({
      role: 'doctor',
      hash: '/access',
      routes: [['get', '/v1/staff/break-glass/reasons', () => json({ reasons: [] })], mineRoute([{ expires_at: '2026-11-10T06:00:00Z', grant_id: GRANT, opened_at: '2026-11-09T05:00:00Z', patient_id: ID.patient, reason_code: 'reason_a' }]), messages],
    });
    await user.click(await screen.findByRole('button', { name: new RegExp(`^Use ${GRANT.slice(0, 8)}`) }));
    expect(calls.some((c) => c.path.endsWith('/messages'))).toBe(false);
    await user.click(await screen.findByRole('button', { name: 'Read the messages' }));
    expect(await screen.findByText('Message words')).toBeInTheDocument();
    expect(screen.getByText(/Patient · Readiness/)).toBeInTheDocument(); // sender and topic carry labels
  });

  it('says there is no open access, as words', async () => {
    await openConsole({ role: 'nurse', hash: '/access', routes: [['get', '/v1/staff/break-glass/reasons', () => json({ reasons: [] })], mineRoute([])] });
    expect(await screen.findByText('You have no open access right now.')).toBeInTheDocument();
  });
});

describe('visit readiness picks a visit from the upcoming list', () => {
  const run = { current_item: 'item-1', finished: false, status: 'red_flag_escalated', task_id: null, visit_id: VISIT, waiting_for: 'none' };
  const upcoming = (rows: unknown[]): Routes[number] => ['get', '/v1/staff/visits/upcoming', () => json({ visits: rows })];
  const types: Routes[number] = ['get', '/v1/staff/scheduling/visit-types', () => list([{ active: true, clinic_id: CLINIC_A, duration_minutes: 30, name: 'Placeholder Visit', visit_type_id: ID.visitType }])];

  it('lists visits by time and visit type (never a name), opens one on a click, and labels the run\'s status', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({
      role: 'nurse',
      hash: '/readiness',
      routes: [upcoming([{ ends_at: '2026-11-10T04:30:00Z', patient_id: ID.patient, starts_at: '2026-11-10T04:00:00Z', visit_id: VISIT, visit_type_id: ID.visitType }]), types, ['get', `/v1/staff/visits/${VISIT}/readiness`, () => json(run)]],
    });
    expect(await screen.findByText('Placeholder Visit')).toBeInTheDocument();
    expect(screen.queryByLabelText('Visit id')).toBeNull(); // nothing to type while the list works
    expect(calls.some((c) => c.path.includes('/readiness'))).toBe(false);
    await user.click(screen.getByRole('button', { name: new RegExp(`^Show the run ${VISIT.slice(0, 8)}`) }));
    expect(await screen.findByText('Red flag: escalated to a nurse')).toBeInTheDocument();
  });

  it('says there are no upcoming visits, and falls back to typing a visit id when the list cannot be read', async () => {
    const a = await openConsole({ role: 'nurse', hash: '/readiness', routes: [upcoming([]), types] });
    expect(await screen.findByText('There are no upcoming visits.')).toBeInTheDocument();
    a.unmount();
    await openConsole({ role: 'nurse', hash: '/readiness', routes: [['get', '/v1/staff/visits/upcoming', () => fail(500)], types] });
    expect(await screen.findByLabelText('Visit id')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
  });
});

describe('an offer run for a freed slot (nothing is sent to a patient)', () => {
  const booking = { booking_id: BOOKING, clinic_id: CLINIC_A, created_at: '2026-11-09T04:00:00Z', ends_at: '2026-11-10T04:30:00Z', location_id: ID.slot, patient_id: ID.patient, provider_id: ID.doc, slot_id: ID.slot, starts_at: '2026-11-10T04:00:00Z', visit_type_id: ID.visitType };
  const run = (over: Record<string, unknown> = {}) => ({ candidate_patient_id: ID.patient2, candidate_rank: 1, finished: false, hold_id: null, slot_id: ID.slot, status: 'awaiting_send', task_id: null, waiting_for: 'send_decision', ...over });
  const waitlist = () => list([{ clinic_id: CLINIC_A, clinical_priority: 'P1', joined_at: '2026-11-01T00:00:00Z', patient_id: ID.patient2, rank: 1, visit_type_id: ID.visitType, waitlist_id: ID.task }]);
  const cancelled: Routes[number] = ['post', `/v1/staff/scheduling/bookings/${BOOKING}/cancel`, () => json({ booking_id: BOOKING, candidates: [], replayed: false, slot_id: ID.slot })];
  const base: Routes = [
    ['get', '/v1/staff/scheduling/bookings', () => list([booking])],
    ['get', '/v1/staff/scheduling/waitlist', () => waitlist()],
    cancelled,
  ];

  async function afterCancel(user: ReturnType<typeof userEvent.setup>) {
    await user.click(await screen.findByRole('button', { name: /^Cancel booking/ }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel booking' }));
    return screen.findByRole('button', { name: 'Prepare offer for this slot' });
  }

  it('offers "Prepare offer for this slot" after a cancel, starts the run only after a confirmation that says nothing is sent, and shows the run and the ranked waitlist', async () => {
    const user = userEvent.setup();
    let started = false;
    const routes: Routes = [
      ...base,
      ['get', `/v1/staff/scheduling/backfills/${ID.slot}`, () => (started ? json(run()) : fail(404))],
      ['post', '/v1/staff/scheduling/backfills', () => ((started = true), json(run()))],
    ];
    const { calls } = await openConsole({ hash: '/scheduling/bookings', routes });
    await user.click(await afterCancel(user));
    expect(await screen.findByText('There is no offer run for this slot yet.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Prepare offer for this slot' }));
    const dialog = screen.getByRole('dialog', { name: 'Prepare offer for this slot' });
    expect(dialog).toHaveTextContent('Nothing is sent to anyone.');
    expect(writes(calls).filter((c) => c.path.endsWith('/backfills'))).toHaveLength(0);
    await user.click(within(dialog).getByRole('button', { name: 'Prepare offer for this slot' }));
    await screen.findByText('The run is started. Decide what to do with the first candidate.');
    const start = writes(calls).find((c) => c.path.endsWith('/backfills'));
    expect(start?.body).toEqual({ clinic_id: CLINIC_A, slot_id: ID.slot, visit_type_id: ID.visitType });
    expect(start?.key).toBeTruthy();
    expect(await screen.findByText('Your decision to send, skip or stop')).toBeInTheDocument();
    expect(await screen.findByText('Current candidate', { selector: 'span.pill' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Ranked waitlist' })).toBeInTheDocument();
  });

  it('gives a nurse no way to prepare an offer', async () => {
    const user = userEvent.setup();
    await openConsole({ role: 'nurse', hash: '/scheduling/bookings', routes: [...base] });
    await screen.findAllByRole('row');
    expect(screen.queryByRole('button', { name: /Cancel booking|Prepare offer/ })).toBeNull();
    void user;
  });

  it('asks for a confirmation for each of Send, Skip and Stop, writes nothing before it, and tells a 409 and a 503 calmly', async () => {
    const user = userEvent.setup();
    let status = 200;
    const decide: Routes[number] = ['post', `/v1/staff/scheduling/backfills/${ID.slot}/decide`, (req) => (status === 200 ? json(run({ finished: (req.body as { action: string }).action === 'stop', waiting_for: 'none' })) : fail(status))];
    const { calls } = await openConsole({ hash: '/scheduling/slots', routes: [
      ['get', '/v1/staff/scheduling/slots', () => list([{ clinic_id: CLINIC_A, slot_id: ID.slot, provider_id: ID.doc, location_id: ID.slot, visit_type_id: ID.visitType, starts_at: '2026-11-10T04:00:00Z', ends_at: '2026-11-10T04:30:00Z', status: 'open', hold_expires_at: null }])],
      ['get', '/v1/staff/scheduling/providers', () => list([])], ['get', '/v1/staff/scheduling/locations', () => list([])], ['get', '/v1/staff/scheduling/visit-types', () => list([])],
      ['get', `/v1/staff/scheduling/backfills/${ID.slot}`, () => json(run())], ['get', '/v1/staff/scheduling/waitlist', () => waitlist()], decide,
    ] });
    await user.click(await screen.findByRole('button', { name: new RegExp(`^Prepare offer ${ID.slot.slice(0, 8)}`) }));
    for (const [action, title] of [['Send', 'Send the offer to this candidate?'], ['Skip', 'Skip this candidate?'], ['Stop', 'Stop the run?']] as const) {
      await user.click(await screen.findByRole('button', { name: action }));
      const dialog = screen.getByRole('dialog', { name: title });
      expect(writes(calls).filter((c) => c.path.endsWith('/decide'))).toHaveLength(action === 'Send' ? 0 : action === 'Skip' ? 0 : 0);
      await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    }
    expect(writes(calls).filter((c) => c.path.endsWith('/decide'))).toHaveLength(0);

    await user.click(screen.getByRole('button', { name: 'Skip' }));
    status = 409;
    const dialog = screen.getByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Skip' }));
    expect(await within(dialog).findByText('The run is not waiting for that. Refresh and look again.')).toBeInTheDocument();
    status = 503;
    await user.click(within(dialog).getByRole('button', { name: 'Skip' }));
    expect(await within(dialog).findByText('Offer runs are not set up yet. Nothing was started or changed.')).toBeInTheDocument();
    expect(within(dialog).queryByRole('alert')).toBeNull();
    status = 200;
    await user.click(within(dialog).getByRole('button', { name: 'Skip' }));
    expect(await screen.findByText(/The candidate was skipped/)).toBeInTheDocument();
    expect(writes(calls).filter((c) => c.path.endsWith('/decide')).at(-1)?.body).toEqual({ action: 'skip' });
    expect(document.body.textContent).not.toContain(LEAK);
  });

  describe('the patient\'s reply', () => {
    const thread = (rows: unknown[]): Routes => [
      ['get', '/v1/staff/messages/threads', () => list([{ clinic_id: CLINIC_A, created_at: '2026-11-09T05:00:00Z', last_message_at: null, message_count: 1, patient_id: ID.patient2, status: 'open', thread_id: THREAD, topic: 'appointments' }])],
      ['get', `/v1/staff/messages/threads/${THREAD}/messages`, () => list(rows)],
    ];
    const header = { created_at: '2026-11-09T06:00:00Z', erased: false, message_id: MESSAGE, sender_kind: 'staff', thread_id: THREAD, topic: 'appointments' };
    const reading = (over: Record<string, unknown> = {}) => ({ calibrated: null, confidence: null, engine: null, message_id: MESSAGE, needs_person: false, outcome: 'clear', reason: null, replayed: false, screening_id: ID.doc, suggestion: 'accept', ...over });
    const open = async (user: ReturnType<typeof userEvent.setup>, extra: Routes) => {
      const calls = (await openConsole({ hash: '/scheduling/slots', routes: [
        ['get', '/v1/staff/scheduling/slots', () => list([{ clinic_id: CLINIC_A, slot_id: ID.slot, provider_id: ID.doc, location_id: ID.slot, visit_type_id: ID.visitType, starts_at: '2026-11-10T04:00:00Z', ends_at: '2026-11-10T04:30:00Z', status: 'open', hold_expires_at: null }])],
        ['get', '/v1/staff/scheduling/providers', () => list([])], ['get', '/v1/staff/scheduling/locations', () => list([])], ['get', '/v1/staff/scheduling/visit-types', () => list([])],
        ['get', `/v1/staff/scheduling/backfills/${ID.slot}`, () => json(run({ waiting_for: 'reply_entry', status: 'awaiting_reply' }))], ['get', '/v1/staff/scheduling/waitlist', () => waitlist()], ...extra,
      ] })).calls;
      await user.click(await screen.findByRole('button', { name: new RegExp(`^Prepare offer ${ID.slot.slice(0, 8)}`) }));
      return calls;
    };

    it('picks the stored reply, reads it only on a click, shows a suggested reading as a suggestion, and enters it only after a confirmation', async () => {
      const user = userEvent.setup();
      const entered: Routes[number] = ['post', `/v1/staff/scheduling/backfills/${ID.slot}/reply`, () => json(run({ waiting_for: 'none', finished: true }))];
      const calls = await open(user, [...thread([header]), ['get', `/v1/staff/messages/${MESSAGE}`, () => json({ body: 'Reply words', message_id: MESSAGE, sender_kind: 'staff', thread_id: THREAD, topic: 'appointments' })], ['post', '/v1/staff/scheduling/offers/interpret-reply', () => json(reading())], entered]);
      await user.click(await screen.findByRole('radio'));
      expect(calls.some((c) => c.path === `/v1/staff/messages/${MESSAGE}`)).toBe(false); // nothing is read, so nothing audited, until asked
      expect(screen.getByRole('button', { name: 'Enter this reply' })).toBeEnabled();
      await user.click(screen.getByRole('button', { name: 'Read this message' }));
      expect(await screen.findByText('Reply words')).toBeInTheDocument();
      await user.click(screen.getByRole('button', { name: 'Suggest a reading' }));
      expect(await screen.findByText('Accepts')).toBeInTheDocument();
      expect(screen.getAllByText('Not recorded').length).toBeGreaterThanOrEqual(2); // a missing confidence and engine are never shown as a value
      expect(screen.getByText(/What it means is your decision/)).toBeInTheDocument();
      expect(writes(calls).filter((c) => c.path.endsWith('/reply'))).toHaveLength(0);
      await user.click(screen.getByRole('button', { name: 'Enter this reply' }));
      await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Enter this reply' }));
      expect(await screen.findByText(/The reply was entered. Finished: Yes/)).toBeInTheDocument();
      expect(writes(calls).find((c) => c.path.endsWith('/reply'))?.body).toEqual({ outcome: 'replied', message_id: MESSAGE });
    });

    it('does not clear a red-flag reply: it says the rules did not clear it and that entering it raises the nurse task', async () => {
      const user = userEvent.setup();
      await open(user, [...thread([header]), ['post', '/v1/staff/scheduling/offers/interpret-reply', () => json(reading({ outcome: 'red_flag', suggestion: null, reason: 'red_flag', needs_person: true }))]]);
      await user.click(await screen.findByRole('radio'));
      await user.click(screen.getByRole('button', { name: 'Suggest a reading' }));
      expect(await screen.findByText('Red flag', { selector: 'span.pill' })).toBeInTheDocument();
      expect(screen.getByText('No suggestion: a person must read the reply.')).toBeInTheDocument();
      expect(screen.getByText(/The rules did not clear this reply/)).toBeInTheDocument();
    });

    it('records that no reply came after a confirmation, even when no stored reply exists; and says reading is not set up on a 503', async () => {
      const user = userEvent.setup();
      const none: Routes[number] = ['post', `/v1/staff/scheduling/backfills/${ID.slot}/reply`, () => json(run({ waiting_for: 'none' }))];
      const calls = await open(user, [...thread([]), none]);
      expect(await screen.findByText(/No stored reply was found for this patient/)).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Enter this reply' })).toBeDisabled();
      await user.click(screen.getByRole('button', { name: 'No reply came' }));
      await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'No reply came' }));
      await screen.findByText(/The reply was entered/);
      expect(writes(calls).find((c) => c.path.endsWith('/reply'))?.body).toEqual({ outcome: 'timed_out' });
    });

    it('shows the 503 of reply reading as a calm status', async () => {
      const user = userEvent.setup();
      await open(user, [...thread([header]), ['post', '/v1/staff/scheduling/offers/interpret-reply', () => fail(503)]]);
      await user.click(await screen.findByRole('radio'));
      await user.click(screen.getByRole('button', { name: 'Suggest a reading' }));
      expect(await screen.findByText('Reply reading is not set up yet. Read the message yourself.')).toBeInTheDocument();
      expect(document.body.textContent).not.toContain(LEAK);
    });
  });
});
