import { describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ACCOUNT_ID, CLINIC_A, json } from './helpers/fakes';
import { ID, LEAK, fail, list, openConsole, writes, type Routes } from './helpers/console';

// MOCK-ONLY (see helpers/console.tsx): fakes written from openapi.snapshot.json, not from a live backend.

const VISIT = 'abababab-abab-4bab-8bab-abababababab';
const REQUEST = 'cdcdcdcd-cdcd-4dcd-8dcd-cdcdcdcdcdcd';
const GRANT = 'efefefef-efef-4fef-8fef-efefefefefef';
const NOTE = '12121212-1212-4212-8212-121212121212';

const run = (over: Record<string, unknown> = {}) => ({ current_item: 'item-1', finished: false, status: 'waiting', task_id: null, visit_id: VISIT, waiting_for: 'reply', ...over });

describe('visit readiness (nurse and doctor)', () => {
  const open = async (user: ReturnType<typeof userEvent.setup>) => {
    await user.type(await screen.findByLabelText('Visit id'), VISIT);
    await user.click(screen.getByRole('button', { name: 'Show the run' }));
  };

  it('shows where the run is without showing any clinic content, and flags an escalation task', async () => {
    const user = userEvent.setup();
    await openConsole({ role: 'nurse', hash: '/readiness', routes: [['get', `/v1/staff/visits/${VISIT}/readiness`, () => json(run({ task_id: ID.task }))]] });
    await open(user);
    expect(await screen.findByText('The patient\'s reply')).toBeInTheDocument();
    expect(screen.getByText('item-1')).toBeInTheDocument();
    expect(screen.getByText('Review task')).toBeInTheDocument();
    expect(screen.queryByText('None raised')).toBeNull();
  });

  it('says there is no run yet on a 404 and starts one only after a confirmation, with a key; 409 and 503 are calm', async () => {
    const user = userEvent.setup();
    let answer = 409;
    const start: Routes[number] = ['post', `/v1/staff/visits/${VISIT}/readiness/start`, () => (answer === 200 ? json(run()) : fail(answer))];
    const { calls } = await openConsole({ role: 'nurse', hash: '/readiness', routes: [['get', `/v1/staff/visits/${VISIT}/readiness`, () => fail(404)], start] });
    await open(user);
    expect(await screen.findByText('There is no readiness run for this visit yet.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Start readiness' }));
    const dialog = screen.getByRole('dialog');
    expect(writes(calls)).toHaveLength(0);
    await user.click(within(dialog).getByRole('button', { name: 'Start readiness' }));
    expect(await within(dialog).findByText(/no readiness content set up/)).toBeInTheDocument();
    answer = 503;
    await user.click(within(dialog).getByRole('button', { name: 'Start readiness' }));
    expect(await within(dialog).findByText(/Visit readiness is not set up yet/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
    answer = 200;
    await user.click(within(dialog).getByRole('button', { name: 'Start readiness' }));
    expect(await screen.findByText('The run is started.')).toBeInTheDocument();
    expect(writes(calls).at(-1)?.key).toBeTruthy();
  });

  it('relays a reply only after choosing what happened; a reply needs text; "no reply" sends no body', async () => {
    const user = userEvent.setup();
    const reply: Routes[number] = ['post', `/v1/staff/visits/${VISIT}/readiness/reply`, () => json(run({ waiting_for: 'none' }))];
    const { calls } = await openConsole({ role: 'doctor', hash: '/readiness', routes: [['get', `/v1/staff/visits/${VISIT}/readiness`, () => json(run())], reply] });
    await open(user);
    await user.click(await screen.findByRole('button', { name: "Relay the patient's reply" }));
    const dialog = screen.getByRole('dialog');
    const send = within(dialog).getByRole('button', { name: 'Relay' });
    expect(send).toBeDisabled();
    for (const r of within(dialog).getAllByRole('radio')) expect(r).not.toBeChecked();
    await user.click(within(dialog).getByRole('radio', { name: 'The patient replied' }));
    expect(send).toBeDisabled(); // a reply needs text
    await user.type(within(dialog).getByLabelText("The patient's reply"), 'typed reply');
    await user.click(within(dialog).getByRole('radio', { name: 'No reply came' }));
    expect(within(dialog).queryByLabelText("The patient's reply")).toBeNull();
    await user.click(send);
    await screen.findByText('The reply was relayed.');
    expect(writes(calls)[0]?.body).toEqual({ outcome: 'no_reply' });
  });

  it('is not available to a role the backend refuses', async () => {
    const user = userEvent.setup();
    await openConsole({ role: 'nurse', hash: '/readiness', routes: [['get', `/v1/staff/visits/${VISIT}/readiness`, () => fail(403)]] });
    await open(user);
    await screen.findByText('This is not available to your role.');
  });
});

describe('portal claims (front desk)', () => {
  const searchRoute: Routes[number] = ['post', '/v1/staff/patients/search', () => json({ items: [{ patient_id: ID.patient, verified: true, registered_via: 'staff', matched_kinds: ['name'] }], labels_withheld: false, truncated: false })];
  const claims: Routes[number] = ['get', '/v1/staff/portal-claims', () => json({ claims: [{ created_at: '2026-11-09T05:00:00Z', request_id: REQUEST }] })];

  it('approves onto a confirmed record chosen by the person, after a confirmation, and says what the backend answered', async () => {
    const user = userEvent.setup();
    const resolve: Routes[number] = ['post', `/v1/staff/portal-claims/${REQUEST}/resolve`, () => json({ result: 'consent_required' })];
    const { calls } = await openConsole({ hash: '/claims', routes: [claims, searchRoute, resolve] });
    await user.click(await screen.findByRole('button', { name: /^Approve/ }));
    const dialog = screen.getByRole('dialog', { name: 'Approve this claim?' });
    const confirm = within(dialog).getByRole('button', { name: 'Approve' });
    expect(confirm).toBeDisabled();
    await user.type(within(dialog).getByLabelText('Full name'), 'Test Person');
    await user.type(within(dialog).getByLabelText('Date of birth'), '1990-02-03');
    await user.click(within(dialog).getByRole('button', { name: 'Find' }));
    await user.click(await within(dialog).findByRole('radio'));
    const resolves = () => writes(calls).filter((c) => c.path.endsWith('/resolve')); // the patient search is a POST too, but a read
    expect(resolves()).toHaveLength(0);
    await user.click(confirm);
    expect(await screen.findByText('The person has not given the required consent yet. Nothing was linked.')).toBeInTheDocument();
    expect(resolves()[0]).toMatchObject({ body: { decision: 'approve', patient_id: ID.patient } });
    expect(resolves()[0]?.key).toBeTruthy();
  });

  it('rejects without naming a record', async () => {
    const user = userEvent.setup();
    const resolve: Routes[number] = ['post', `/v1/staff/portal-claims/${REQUEST}/resolve`, () => json({ result: 'rejected' })];
    const { calls } = await openConsole({ hash: '/claims', routes: [claims, resolve] });
    await user.click(await screen.findByRole('button', { name: /^Reject/ }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Reject' }));
    await screen.findByText('Rejected.');
    expect(writes(calls)[0]?.body).toEqual({ decision: 'reject', patient_id: null });
  });

  it('shows the list as an id and a time only, an empty state, and is not available to other roles', async () => {
    const a = await openConsole({ hash: '/claims', routes: [['get', '/v1/staff/portal-claims', () => json({ claims: [] })]] });
    expect(await screen.findByText('There are no pending claims.')).toBeInTheDocument();
    a.unmount();
    await openConsole({ role: 'nurse', hash: '/claims', routes: [['get', '/v1/staff/portal-claims', () => fail(403)]] });
    await screen.findByText('This is not available to your role.');
  });
});

describe('break-glass access', () => {
  const open = (answer: () => Response): Routes[number] => ['post', '/v1/staff/break-glass', answer];
  const messages = (answer: () => Response): Routes[number] => ['get', `/v1/staff/break-glass/${GRANT}/messages`, answer];
  const granted = () => json({ created: true, expires_at: '2026-11-10T06:00:00Z', grant_id: GRANT }, 201);
  const msg = (over: Record<string, unknown> = {}) => ({ body: 'Message words', created_at: '2026-11-09T05:00:00Z', erased: false, message_id: NOTE, readable: true, sender_kind: 'patient', thread_id: ID.hold, topic: 'care', ...over });

  async function openGrant(user: ReturnType<typeof userEvent.setup>) {
    await user.click(await screen.findByRole('button', { name: "Open access to a patient's messages" }));
    const dialog = screen.getByRole('dialog');
    await user.type(within(dialog).getByLabelText('Patient id'), ID.patient);
    return dialog;
  }

  it('needs a reason CODE of the right shape (no free text), nothing is written before the confirmation, then reads only on a click', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ role: 'nurse', hash: '/access', routes: [open(granted), messages(() => json({ messages: [msg(), msg({ message_id: ID.doc, erased: true, body: null }), msg({ message_id: ID.doc2, readable: false, body: null })] }))] });
    const dialog = await openGrant(user);
    const confirm = within(dialog).getByRole('button', { name: 'Open access' });
    await user.type(within(dialog).getByLabelText('Reason code'), 'Not a code, free text!');
    expect(confirm).toBeDisabled();
    await user.clear(within(dialog).getByLabelText('Reason code'));
    await user.type(within(dialog).getByLabelText('Reason code'), 'reason_a');
    expect(writes(calls)).toHaveLength(0);
    await user.click(confirm);
    await screen.findByText(/Access is open until/);
    expect(writes(calls)[0]).toMatchObject({ path: '/v1/staff/break-glass', body: { clinic_id: CLINIC_A, patient_id: ID.patient, reason_code: 'reason_a' } });
    expect(calls.some((c) => c.path.endsWith('/messages'))).toBe(false); // opening reads nothing, so nothing is audited yet
    await user.click(screen.getByRole('button', { name: 'Read the messages' }));
    expect(await screen.findByText('Message words')).toBeInTheDocument();
    expect(screen.getByText('Erased')).toBeInTheDocument();
    expect(screen.getByText('Not readable')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Close' }));
    expect(document.body.textContent).not.toContain('Message words'); // dropped with the panel
  });

  it('says a reason code that is not on the clinic\'s list was refused (422) and that nothing was opened; 503 is calm', async () => {
    const user = userEvent.setup();
    let status = 422;
    await openConsole({ role: 'nurse', hash: '/access', routes: [open(() => fail(status))] });
    const dialog = await openGrant(user);
    await user.type(within(dialog).getByLabelText('Reason code'), 'unknown_code');
    await user.click(within(dialog).getByRole('button', { name: 'Open access' }));
    expect(await within(dialog).findByText("That reason code is not on the clinic's list. Nothing was opened.")).toBeInTheDocument();
    status = 503;
    await user.click(within(dialog).getByRole('button', { name: 'Open access' }));
    expect(await within(dialog).findByText(/Break-glass access is not set up yet/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('says the window has closed when the backend refuses the read', async () => {
    const user = userEvent.setup();
    await openConsole({ role: 'doctor', hash: '/access', routes: [open(granted), messages(() => fail(403))] });
    const dialog = await openGrant(user);
    await user.type(within(dialog).getByLabelText('Reason code'), 'reason_a');
    await user.click(within(dialog).getByRole('button', { name: 'Open access' }));
    await user.click(await screen.findByRole('button', { name: 'Read the messages' }));
    expect(await screen.findByText('The access window has closed, or this access is not yours.')).toBeInTheDocument();
  });

  it('lets an admin review a grant afterwards and tells every refusal in fixed words; an admin has no open button here', async () => {
    const user = userEvent.setup();
    const row = { expires_at: '2026-11-10T06:00:00Z', grant_id: GRANT, opened_at: '2026-11-09T05:00:00Z', opened_by: ID.user, patient_id: ID.patient, reason_code: 'reason_a', review_outcome: null, reviewed_at: null, reviewed_by: null };
    const review: Routes[number] = ['post', `/v1/staff/break-glass/${GRANT}/review`, () => json({ result: 'same_person' })];
    const { calls } = await openConsole({ role: 'admin', hash: '/access', routes: [['get', '/v1/staff/break-glass', () => json({ grants: [row] })], review] });
    expect(screen.queryByRole('button', { name: "Open access to a patient's messages" })).toBeNull();
    await user.click(await screen.findByRole('button', { name: /^Inappropriate/ }));
    expect(writes(calls)).toHaveLength(0);
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Inappropriate' }));
    expect(await screen.findByText('You opened this access, so someone else must review it.')).toBeInTheDocument();
    expect(writes(calls)[0]?.body).toEqual({ outcome: 'inappropriate' });
  });
});

describe('staff recovery (admin)', () => {
  const row = (over: Record<string, unknown> = {}) => ({ account_id: ID.user, approved_by: null, created_at: '2026-11-09T05:00:00Z', decided_at: null, request_id: REQUEST, requested_by: ID.user2, status: 'requested', ...over });
  const recovery = (rows: unknown[]): Routes[number] => ['get', '/v1/staff/recovery', () => json({ requests: rows })];

  it('offers approval only to someone who is neither the requester nor the account being recovered, and shows the result in fixed words', async () => {
    const user = userEvent.setup();
    const approve: Routes[number] = ['post', `/v1/staff/recovery/${REQUEST}/approve`, () => json({ result: 'approved' })];
    const mine = row({ request_id: ID.hold, requested_by: ACCOUNT_ID });
    const about = row({ request_id: ID.slot, account_id: ACCOUNT_ID });
    const { calls } = await openConsole({ role: 'admin', hash: '/admin/recovery', routes: [recovery([row(), mine, about]), approve] });
    await screen.findAllByRole('row');
    expect(screen.getAllByRole('button', { name: /^Approve/ })).toHaveLength(1);
    await user.click(screen.getByRole('button', { name: /^Approve/ }));
    expect(writes(calls)).toHaveLength(0);
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Approve' }));
    expect(await screen.findByText('Approved. It still has to be completed.')).toBeInTheDocument();
  });

  it('requests a recovery for another account only, choosing it yourself; refusals and the 503 on completing are calm', async () => {
    const user = userEvent.setup();
    const accounts: Routes[number] = ['get', '/v1/staff/accounts', () => list([{ account_id: ID.user, roles: ['nurse'], status: 'active' }, { account_id: ACCOUNT_ID, roles: ['admin'], status: 'active' }])];
    const request: Routes[number] = ['post', '/v1/staff/recovery', () => json({ created: true, request_id: REQUEST }, 201)];
    const complete: Routes[number] = ['post', `/v1/staff/recovery/${REQUEST}/complete`, () => fail(503)];
    const { calls } = await openConsole({ role: 'admin', hash: '/admin/recovery', routes: [recovery([row({ status: 'approved' })]), accounts, request, complete] });
    await user.click(await screen.findByRole('button', { name: 'Request a recovery' }));
    const dialog = screen.getByRole('dialog');
    const select = within(dialog).getByLabelText('Account') as HTMLSelectElement;
    expect(select.value).toBe('');
    expect(within(select).getAllByRole('option').map((o) => (o as HTMLOptionElement).value)).toEqual(['', ID.user]); // not your own account
    await user.selectOptions(select, ID.user);
    await user.click(within(dialog).getByRole('button', { name: 'Request' }));
    await screen.findByText('The recovery was requested.');
    expect(writes(calls)[0]?.body).toEqual({ account_id: ID.user, clinic_id: CLINIC_A });
    await user.click(screen.getByRole('button', { name: /^Complete/ }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Complete' }));
    expect(await screen.findByText(/Recovery is not set up yet/)).toBeInTheDocument();
  });
});

describe('notes (nurse and doctor; append-only)', () => {
  const note = (over: Record<string, unknown> = {}) => ({ author_id: ID.user, created_at: '2026-11-09T05:00:00Z', erased: false, note_id: NOTE, parent_id: null, text: 'First note words', ...over });
  const notesRoute = (rows: unknown[]): Routes[number] => ['get', '/v1/staff/notes', () => json({ notes: rows })];

  it('shows the notes oldest first with addenda naming their parent, and says erased or unreadable instead of a blank; there is no edit or delete', async () => {
    const user = userEvent.setup();
    await openConsole({ role: 'nurse', hash: `/notes?patient=${ID.patient}`, routes: [notesRoute([note(), note({ note_id: ID.doc, parent_id: NOTE, text: 'Addendum words' }), note({ note_id: ID.doc2, erased: true, text: null }), note({ note_id: ID.task, text: null })])] });
    await user.click(await screen.findByRole('button', { name: 'Show notes' }));
    expect(await screen.findByText('First note words')).toBeInTheDocument();
    expect(screen.getByText('Addendum words')).toBeInTheDocument();
    expect(screen.getByText('Erased')).toBeInTheDocument();
    expect(screen.getByText('Not readable')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /edit|delete/i })).toBeNull();
    expect(screen.getAllByText(/addendum to/).length).toBe(1);
  });

  it('writes a note and an addendum only after typing and confirming, with a key; 503 says nothing was written', async () => {
    const user = userEvent.setup();
    let status = 503;
    const write: Routes[number] = ['post', '/v1/staff/notes', () => (status === 201 ? json({ created: true, note_id: ID.hold }, 201) : fail(status))];
    const { calls } = await openConsole({ role: 'doctor', hash: `/notes?patient=${ID.patient}`, routes: [notesRoute([note()]), write] });
    await user.click(await screen.findByRole('button', { name: 'Show notes' }));
    await user.click(await screen.findByRole('button', { name: /^Add an addendum/ }));
    const dialog = screen.getByRole('dialog');
    const save = within(dialog).getByRole('button', { name: 'Save' });
    expect(save).toBeDisabled();
    await user.type(within(dialog).getByLabelText('Note'), 'Addendum typed');
    expect(writes(calls)).toHaveLength(0);
    await user.click(save);
    expect(await within(dialog).findByText('Encryption keys are not configured. Nothing was written.')).toBeInTheDocument();
    status = 201;
    await user.click(save);
    await screen.findByText('The note was saved.');
    expect(writes(calls).at(-1)).toMatchObject({ body: { clinic_id: CLINIC_A, patient_id: ID.patient, parent_id: NOTE, text: 'Addendum typed' } });
    expect(writes(calls).at(-1)?.key).toBeTruthy();
  });

  it('is not available to front desk', async () => {
    const user = userEvent.setup();
    await openConsole({ role: 'front_desk', hash: `/notes?patient=${ID.patient}`, routes: [['get', '/v1/staff/notes', () => fail(403)]] });
    await user.click(await screen.findByRole('button', { name: 'Show notes' }));
    await screen.findByText('This is not available to your role.');
  });
});

describe('grievances (admin)', () => {
  const g = (over: Record<string, unknown> = {}) => ({ category: 'service', clinic_id: CLINIC_A, created_at: '2026-11-09T05:00:00Z', erased: false, grievance_id: REQUEST, status: 'open', text: 'Patient words', ...over });

  it('shows the patient\'s words only while readable, acknowledges then resolves behind confirmations, and tells every result', async () => {
    const user = userEvent.setup();
    const set: Routes[number] = ['post', `/v1/staff/grievances/${REQUEST}/status`, (req) => json({ result: (req.body as { status: string }).status === 'resolved' ? 'invalid_transition' : 'recorded' })];
    const { calls } = await openConsole({
      role: 'admin',
      hash: '/admin/grievances',
      routes: [['get', '/v1/staff/grievances', () => json({ grievances: [g(), g({ grievance_id: ID.doc, erased: true, text: null, status: 'resolved' }), g({ grievance_id: ID.doc2, text: null, status: 'acknowledged' })] })], set],
    });
    expect(await screen.findByText('Patient words')).toBeInTheDocument();
    expect(screen.getByText('Erased')).toBeInTheDocument();
    expect(screen.getByText('Not readable')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: new RegExp(`^Acknowledge ${REQUEST.slice(0, 8)}`) }));
    expect(writes(calls)).toHaveLength(0);
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Acknowledge' }));
    expect(await screen.findByText('Recorded.')).toBeInTheDocument();
    expect(writes(calls)[0]?.body).toEqual({ status: 'acknowledged' });
    await user.click(screen.getByRole('button', { name: new RegExp(`^Resolve ${REQUEST.slice(0, 8)}`) }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Resolve' }));
    expect(await screen.findByText('That change is not allowed from its current status. Nothing was changed.')).toBeInTheDocument();
    expect(screen.getAllByRole('row').slice(1).some((r) => within(r).queryAllByRole('button').length > 0)).toBe(true);
  });

  it('has an empty state and a fixed error', async () => {
    const a = await openConsole({ role: 'admin', hash: '/admin/grievances', routes: [['get', '/v1/staff/grievances', () => json({ grievances: [] })]] });
    expect(await screen.findByText('There are no grievances.')).toBeInTheDocument();
    a.unmount();
    await openConsole({ role: 'admin', hash: '/admin/grievances', routes: [['get', '/v1/staff/grievances', () => fail(500)]] });
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
  });
});

describe('result corrections', () => {
  it('shows the kind of change only when the result replaces another, and sends it only then', async () => {
    const user = userEvent.setup();
    const record: Routes[number] = ['post', '/v1/staff/results', () => json({ created: true, result_id: ID.doc2, wake_queued: false }, 201)];
    const { calls } = await openConsole({ role: 'doctor', hash: `/care/results?patient=${ID.patient}`, routes: [record] });
    await user.click(await screen.findByRole('button', { name: 'Record a result' }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).queryByLabelText('What kind of change is this?')).toBeNull();
    await user.type(within(dialog).getByLabelText('Result code'), 'c1');
    await user.type(within(dialog).getByLabelText('Measure 1 name'), 'm1');
    await user.type(within(dialog).getByLabelText('Valid from'), '2026-11-10T09:30');
    await user.click(within(dialog).getByRole('radio', { name: 'Yes' }));
    await user.type(within(dialog).getByLabelText('Replaces result id (optional)'), ID.doc);
    const change = within(dialog).getByLabelText('What kind of change is this?') as HTMLSelectElement;
    expect(change.value).toBe(''); // not stated: the system works it out
    await user.selectOptions(change, 'correction');
    await user.click(within(dialog).getByRole('button', { name: 'Save result' }));
    await screen.findByText('The result was recorded.');
    expect(writes(calls)[0]?.body).toMatchObject({ supersedes_id: ID.doc, change: 'correction' });
  });

  it('shows a result-correction task by name, and offers no resolve when the API says it cannot be closed here', async () => {
    const task = { acknowledged_at: null, acknowledged_by: null, assigned_role: 'doctor', clinic_id: CLINIC_A, created_at: '2026-11-09T05:00:00Z', kind: 'result_correction', priority: 'standard', resolvable: false, resolved_at: null, status: 'acknowledged', task_id: ID.task, provenance: { new_result: ID.doc }, source_message_id: 'result:x' };
    await openConsole({ role: 'doctor', hash: `/tasks/${ID.task}`, routes: [['get', `/v1/staff/review-tasks/${ID.task}`, () => json(task)]] });
    expect(await screen.findByText('Result correction')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Resolve/ })).toBeNull();
  });
});
