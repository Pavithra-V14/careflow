import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ACCOUNT_ID, CLINIC_A, json } from './helpers/fakes';
import { ID, LEAK, fail, list, openConsole, writes, type Routes } from './helpers/console';

// MOCK-ONLY (see helpers/console.tsx): fakes written from openapi.snapshot.json, not from a live backend.
// The audit reader, register a patient, the Run button, the decrypted record view, and the erasure list with its write buttons OFF.

const row = (n: number, over: Record<string, unknown> = {}) => ({
  action: 'patient.view', actor_id: ID.user, actor_role: 'nurse', audit_id: n, clinic_id: CLINIC_A, details: {}, occurred_at: '2026-11-10T04:00:00Z', origin: 'live', resource_id: ID.patient, resource_type: 'patient', ...over,
});
const page = (items: unknown[], more: number | null = null) => json({ items, limit: 50, has_more: more !== null, next_before_id: more });

describe('audit reader', () => {
  it('is in the menu for an auditor only', async () => {
    const a = await openConsole({ role: 'auditor' });
    expect(within(screen.getByRole('navigation')).getByRole('link', { name: 'Audit log' })).toBeInTheDocument();
    a.unmount();
    await openConsole({ role: 'front_desk' });
    expect(within(screen.getByRole('navigation')).queryByRole('link', { name: 'Audit log' })).toBeNull();
  });

  it('reads nothing until asked, sends the filters, shows details only on a click and pages with before_id', async () => {
    const user = userEvent.setup();
    let n = 0;
    const { calls } = await openConsole({
      role: 'auditor',
      hash: '/audit',
      routes: [['get', '/v1/staff/audit', () => (++n === 1 ? page([row(9, { details: { count: 3, note: '<b>x</b>' } }), row(8, { actor_role: null, actor_id: null, clinic_id: null, resource_id: null, resource_type: null })], 8) : page([row(7)]))]],
    });
    expect(calls.some((c) => c.path === '/v1/staff/audit')).toBe(false); // nothing read on arrival
    await user.type(screen.getByLabelText('Action'), 'patient.view');
    await user.type(screen.getByLabelText('Person (account id)'), ID.user);
    await user.click(screen.getByRole('button', { name: 'Read the log' }));
    expect(await screen.findAllByRole('row')).toHaveLength(3);
    const first = calls.find((c) => c.path === '/v1/staff/audit');
    expect(first?.query.get('action')).toBe('patient.view');
    expect(first?.query.get('actor_id')).toBe(ID.user);
    expect(first?.query.get('limit')).toBe('50');
    expect(first?.query.get('clinic_id')).toBe(CLINIC_A); // the active clinic, on every page
    expect(first?.query.has('before_id')).toBe(false);
    // details only after a click, shown as plain text (never as markup)
    await user.click(screen.getAllByText('Show details')[0] as HTMLElement);
    expect(screen.getByText('<b>x</b>')).toBeInTheDocument();
    expect(screen.getByText('No role recorded')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Older rows' }));
    await waitFor(() => expect(screen.getAllByRole('row')).toHaveLength(4));
    const second = calls.filter((c) => c.path === '/v1/staff/audit')[1];
    expect(second?.query.get('before_id')).toBe('8');
    expect(second?.query.get('action')).toBe('patient.view'); // the same filter
    expect(second?.query.get('clinic_id')).toBe(CLINIC_A);
    expect(screen.queryByRole('button', { name: 'Older rows' })).toBeNull();
    expect(writes(calls)).toHaveLength(0);
  });

  it('says so when nothing matches, and rejects an account id that is not one without calling the route', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ role: 'auditor', hash: '/audit', routes: [['get', '/v1/staff/audit', () => page([])]] });
    await user.type(screen.getByLabelText('Person (account id)'), 'nope');
    await user.click(screen.getByRole('button', { name: 'Read the log' }));
    expect(await screen.findByText(/An account id looks like/)).toBeInTheDocument();
    expect(calls.some((c) => c.path === '/v1/staff/audit')).toBe(false);
    await user.clear(screen.getByLabelText('Person (account id)'));
    await user.click(screen.getByRole('button', { name: 'Read the log' }));
    expect(await screen.findByText('No rows match.')).toBeInTheDocument();
  });

  it('shows a fixed message for 503 and "not available to your role" for 403, never the backend text', async () => {
    const user = userEvent.setup();
    const a = await openConsole({ role: 'auditor', hash: '/audit', routes: [['get', '/v1/staff/audit', () => fail(503)]] });
    await user.click(screen.getByRole('button', { name: 'Read the log' }));
    await screen.findByRole('alert');
    expect(screen.queryByText(LEAK)).toBeNull();
    a.unmount();
    const b = await openConsole({ role: 'auditor', hash: '/audit', routes: [['get', '/v1/staff/audit', () => fail(403)]] });
    await user.click(screen.getByRole('button', { name: 'Read the log' }));
    expect(await screen.findByText('This is not available to your role.')).toBeInTheDocument();
    expect(screen.queryByText(LEAK)).toBeNull();
    b.unmount();
  });

  it('shows "not available to your role" to a role that is not an auditor, without calling the route', async () => {
    const { calls } = await openConsole({ role: 'admin', hash: '/audit' });
    expect(await screen.findByText('This is not available to your role.')).toBeInTheDocument();
    expect(calls.some((c) => c.path === '/v1/staff/audit')).toBe(false);
  });
});

describe('register a patient', () => {
  const route = (body: unknown): Routes[number] => ['post', '/v1/staff/patients', () => json(body)];
  const form = () => screen.getByRole('heading', { name: 'Register a patient' }).nextElementSibling as HTMLElement;

  it('starts empty, asks first, writes once with a key, then drops the typed details and links the record', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ hash: '/patients', routes: [route({ candidate_ids: [], outcome: 'created', patient_id: ID.patient })] });
    const reg = form();
    expect(within(reg).getByLabelText('Name to register')).toHaveValue('');
    expect(within(reg).getByRole('button', { name: 'Register' })).toBeDisabled();
    await user.type(within(reg).getByLabelText('Name to register'), 'Test Person');
    await user.type(within(reg).getByLabelText('Date of birth to register'), '1990-02-03');
    await user.type(within(reg).getByLabelText('Phone number to register (optional)'), '9876543210');
    await user.click(within(reg).getByRole('button', { name: 'Register' }));
    const dialog = await screen.findByRole('dialog');
    expect(writes(calls)).toHaveLength(0); // nothing written before the confirmation
    await user.click(within(dialog).getByRole('button', { name: 'Register' }));
    expect(await screen.findByText('A new record was created.')).toBeInTheDocument();
    const [w] = writes(calls);
    expect(w?.path).toBe('/v1/staff/patients');
    expect(w?.key).toBeTruthy();
    expect(w?.body).toEqual({ clinic_id: CLINIC_A, name: 'Test Person', dob: '1990-02-03', phone: '9876543210' });
    expect(within(reg).getByLabelText('Name to register')).toHaveValue(''); // dropped from the page
    expect(screen.queryByDisplayValue('Test Person')).toBeNull();
    expect(screen.getByRole('link', { name: /Open record/ })).toHaveAttribute('href', `#/patients/${ID.patient}`);
  });

  it('lists the possible matches when the outcome is "review", and never reads it as created', async () => {
    const user = userEvent.setup();
    await openConsole({ hash: '/patients', routes: [route({ candidate_ids: [ID.patient2], outcome: 'review', patient_id: ID.patient })] });
    const reg = form();
    await user.type(within(reg).getByLabelText('Name to register'), 'A B');
    await user.type(within(reg).getByLabelText('Date of birth to register'), '1990-02-03');
    await user.click(within(reg).getByRole('button', { name: 'Register' }));
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Register' }));
    expect(await screen.findByText(/Possible matches were found/)).toBeInTheDocument();
    expect(screen.queryByText('A new record was created.')).toBeNull();
    expect(screen.getAllByRole('link', { name: /Open record/ })).toHaveLength(2);
  });

  it.each([
    [409, 'A different person was already saved with this request.'],
    [503, 'Registering is not set up yet'],
    [422, 'One of the fields is not valid.'],
    [404, 'This clinic could not be found'],
  ])('answers %i with a fixed message, keeps what was typed, and never shows the backend text', async (status, text) => {
    const user = userEvent.setup();
    await openConsole({ hash: '/patients', routes: [['post', '/v1/staff/patients', () => fail(status)]] });
    const reg = form();
    await user.type(within(reg).getByLabelText('Name to register'), 'A B');
    await user.type(within(reg).getByLabelText('Date of birth to register'), '1990-02-03');
    await user.click(within(reg).getByRole('button', { name: 'Register' }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Register' }));
    expect(await within(dialog).findByText(new RegExp(text))).toBeInTheDocument();
    expect(screen.queryByText(LEAK)).toBeNull();
    expect(within(reg).getByLabelText('Name to register')).toHaveValue('A B');
  });

  it('is not offered to a nurse', async () => {
    await openConsole({ role: 'nurse', hash: '/patients' });
    expect(screen.queryByRole('heading', { name: 'Register a patient' })).toBeNull();
  });
});

describe('Run on the referral detail', () => {
  const detail = { clinic_id: CLINIC_A, content_type: 'application/pdf', created_at: '2026-10-30T05:00:00Z', decided: false, document_id: ID.doc, holds: [], open_holds: 0, proposal: null, proposal_id: null, proposal_visible: false, registered_by: ID.user, run_state: 'received', size_bytes: 2048 };
  const open: Routes[number] = ['get', `/v1/staff/referrals/${ID.doc}`, () => json(detail)];
  const runPath = `/v1/staff/referrals/${ID.doc}/run`;
  // Nurse and doctor are offered Run only when the reviewer view says there is no proposal (404). Front desk cannot read it.
  const noProposal: Routes[number] = ['get', `/v1/staff/referrals/${ID.doc}/review-view`, () => fail(404)];

  it.each(['front_desk', 'nurse', 'doctor'])('is offered to %s, asks first, queues once with a key', async (role) => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ role, hash: `/referrals/${ID.doc}`, routes: [open, noProposal, ['post', runPath, () => json({ job_id: ID.task, replayed: false })]] });
    await user.click(await screen.findByRole('button', { name: 'Run the referral' }));
    expect(writes(calls)).toHaveLength(0);
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Run the referral' }));
    expect(await screen.findByText(/The run was queued/)).toBeInTheDocument();
    expect(writes(calls)).toHaveLength(1);
    expect(writes(calls)[0]?.key).toBeTruthy();
  });

  it('says a replay is a replay, and answers 503 in fixed words', async () => {
    const user = userEvent.setup();
    const a = await openConsole({ hash: `/referrals/${ID.doc}`, routes: [open, ['post', runPath, () => json({ job_id: ID.task, replayed: true })]] });
    await user.click(await screen.findByRole('button', { name: 'Run the referral' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Run the referral' }));
    expect(await screen.findByText(/already queued a run/)).toBeInTheDocument();
    a.unmount();
    const b = await openConsole({ hash: `/referrals/${ID.doc}`, routes: [open, ['post', runPath, () => fail(503)]] });
    await user.click(await screen.findByRole('button', { name: 'Run the referral' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Run the referral' }));
    expect(await screen.findByText(/Runs cannot be started yet/)).toBeInTheDocument();
    expect(screen.queryByText(LEAK)).toBeNull();
    b.unmount();
  });

  it.each(['nurse', 'doctor'])('is NOT offered to %s once the reviewer view shows a proposal (Run does nothing there)', async (role) => {
    const view = { alternatives: [], confidence: null, engine: null, evidence: [], kind: 'triage_proposal', on_approve: 'a', on_edit: 'e', on_reject: 'r', proposal: { abstained: false, mode: 'shadow', candidate_count: 1 }, reason_codes: [], task_id: ID.task, triggering_rule: null };
    const { calls } = await openConsole({ role, hash: `/referrals/${ID.doc}`, routes: [open, ['get', `/v1/staff/referrals/${ID.doc}/review-view`, () => json(view)]] });
    await screen.findByText('Registered by');
    await waitFor(() => expect(calls.some((c) => c.path.endsWith('/review-view'))).toBe(true));
    await new Promise((r) => setTimeout(r, 100)); // let the answer land: the button must stay away, not merely not have arrived yet
    expect(screen.queryByRole('button', { name: 'Run the referral' })).toBeNull();
  });

  it('does not carry a message or form state from one referral to the next (regression, live pass 2026-11-13)', async () => {
    const user = userEvent.setup();
    const other: Routes[number] = ['get', `/v1/staff/referrals/${ID.doc2}`, () => json({ ...detail, document_id: ID.doc2 })];
    await openConsole({ role: 'front_desk', hash: `/referrals/${ID.doc}`, routes: [open, other, ['post', runPath, () => json({ job_id: ID.task, replayed: false })]] });
    // Visit both first, so each is cached: that is when the first one's state used to survive the move (a cached page does not reload).
    await screen.findByText(ID.doc);
    window.location.hash = `#/referrals/${ID.doc2}`;
    await screen.findByText(ID.doc2);
    window.location.hash = `#/referrals/${ID.doc}`;
    await screen.findByText(ID.doc);
    await user.click(await screen.findByRole('button', { name: 'Run the referral' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Run the referral' }));
    expect(await screen.findByText(/The run was queued/)).toBeInTheDocument();
    window.location.hash = `#/referrals/${ID.doc2}`;
    await screen.findByText(ID.doc2);
    await waitFor(() => expect(screen.queryByText(/The run was queued/)).toBeNull());
    expect(await screen.findByRole('button', { name: 'Run the referral' })).toBeInTheDocument();
  });

  it('is not offered to an admin', async () => {
    await openConsole({ role: 'admin', hash: `/referrals/${ID.doc}`, routes: [open] });
    await screen.findByText('Registered by');
    expect(screen.queryByRole('button', { name: 'Run the referral' })).toBeNull();
  });
});

describe('the decrypted record view (pending the privacy owner)', () => {
  const status = { clinic_id: CLINIC_A, latest_identity_event: null, latest_identity_event_at: null, merged_into: null, patient_id: ID.patient, registered_via: 'front_desk', verified: true };
  const statusRoute: Routes[number] = ['get', `/v1/staff/patients/${ID.patient}`, () => json(status)];
  const viewPath = `/v1/staff/patients/${ID.patient}/view`;

  it('reads nothing until a click, shows the details, never refetches, and drops them on Hide', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({
      role: 'nurse',
      hash: `/patients/${ID.patient}`,
      routes: [statusRoute, ['get', viewPath, () => json({ clinic_id: CLINIC_A, date_of_birth: '1990-02-03', name: 'Test Person', patient_id: ID.patient, phone: null })]],
    });
    await screen.findByRole('heading', { name: 'Full details' });
    expect(screen.getByText(/awaiting the privacy owner/)).toBeInTheDocument();
    expect(calls.some((c) => c.path === viewPath)).toBe(false);
    await user.click(screen.getByRole('button', { name: 'Open the full details' }));
    expect(await screen.findByText('Test Person')).toBeInTheDocument();
    expect(screen.getByText('1990-02-03')).toBeInTheDocument();
    expect(screen.getByText('Not recorded')).toBeInTheDocument();
    window.dispatchEvent(new Event('focus'));
    window.dispatchEvent(new Event('online'));
    await user.click(screen.getByRole('button', { name: 'Hide the details' }));
    expect(screen.queryByText('Test Person')).toBeNull();
    expect(calls.filter((c) => c.path === viewPath)).toHaveLength(1);
  });

  it.each([
    [503, /cannot be opened in this setup/],
    [429, /Too many records/],
  ])('answers %i in fixed words and shows nothing', async (code, text) => {
    const user = userEvent.setup();
    await openConsole({ hash: `/patients/${ID.patient}`, routes: [statusRoute, ['get', viewPath, () => fail(code)]] });
    await user.click(await screen.findByRole('button', { name: 'Open the full details' }));
    expect(await screen.findByText(text)).toBeInTheDocument();
    expect(screen.queryByText(LEAK)).toBeNull();
  });

  it('is not offered to an admin', async () => {
    await openConsole({ role: 'admin', hash: `/patients/${ID.patient}`, routes: [statusRoute] });
    await screen.findByText('Latest identity event');
    expect(screen.queryByRole('heading', { name: 'Full details' })).toBeNull();
  });
});

describe('erasure requests with the write buttons OFF', () => {
  const req = (over: Record<string, unknown> = {}) => ({ approved_by: null, created_at: '2026-11-10T04:00:00Z', decided_at: null, patient_id: ID.patient, reason: 'reason_x', request_id: ID.task, requested_by: ID.user, status: 'requested', ...over });

  it('shows the list read-only with a plain note, and no approve, cancel or ask buttons', async () => {
    const { calls } = await openConsole({ hash: '/erasure', routes: [['get', '/v1/staff/erasure-requests', () => list([req(), req({ request_id: ID.task2, requested_by: ACCOUNT_ID })])]] });
    expect((await screen.findAllByRole('row')).length).toBe(3);
    expect(screen.getAllByText(/switched off until the second-factor check is confirmed/).length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', { name: /Approve/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Cancel the request/ })).toBeNull();
    expect(screen.getByText('You')).toBeInTheDocument();
    expect(writes(calls)).toHaveLength(0);
  });

  it('is not available to a role that is not front desk, and never calls the route', async () => {
    const { calls } = await openConsole({ role: 'nurse', hash: '/erasure' });
    expect(await screen.findByText('This is not available to your role.')).toBeInTheDocument();
    expect(calls.some((c) => c.path === '/v1/staff/erasure-requests')).toBe(false);
  });

  it('shows an empty state and a fixed error', async () => {
    const a = await openConsole({ hash: '/erasure', routes: [['get', '/v1/staff/erasure-requests', () => list([])]] });
    expect(await screen.findByText('No requests.')).toBeInTheDocument();
    a.unmount();
    await openConsole({ hash: '/erasure', routes: [['get', '/v1/staff/erasure-requests', () => fail(503)]] });
    await screen.findByRole('alert');
    expect(screen.queryByText(LEAK)).toBeNull();
  });
});
