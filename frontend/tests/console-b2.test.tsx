import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CLINIC_A, json } from './helpers/fakes';
import { ID, LEAK, fail, list, openConsole, writes, type Routes } from './helpers/console';

// MOCK-ONLY (see helpers/console.tsx): fakes written from openapi.snapshot.json, not from a live backend.

const ALERT = 'abababab-abab-4bab-8bab-abababababab';
const PLAN = 'cdcdcdcd-cdcd-4dcd-8dcd-cdcdcdcdcdcd';

const alert = (over: Record<string, unknown> = {}) => ({
  acknowledged: false, alert_class: 'task_overdue', alert_id: ALERT, audience_role: 'doctor', clinic_id: CLINIC_A, count: 3, created_at: '2026-11-09T05:00:00Z', ref: ID.task, ...over,
});

describe('alerts', () => {
  it('asks for the active clinic, shows the class, who it is aimed at (the escalation state) and the count, and filters acknowledged ones', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({
      role: 'nurse',
      hash: '/alerts',
      routes: [['get', '/v1/staff/console/alerts', (req) => list(req.query.get('include_acknowledged') ? [alert(), alert({ alert_id: ID.doc2, acknowledged: true })] : [alert()])]],
    });
    const row = (await screen.findAllByRole('row'))[1] as HTMLElement;
    expect(within(row).getByText('task overdue')).toBeInTheDocument();
    expect(within(row).getByText('Doctor')).toBeInTheDocument();
    expect(within(row).getByText('3')).toBeInTheDocument();
    expect(calls.find((c) => c.path === '/v1/staff/console/alerts')?.query.get('clinic_id')).toBe(CLINIC_A);
    await user.click(screen.getByRole('checkbox', { name: 'Include acknowledged alerts' }));
    await waitFor(() => expect(screen.getAllByText('Acknowledged').length).toBeGreaterThan(0));
    expect(calls.filter((c) => c.path === '/v1/staff/console/alerts').map((c) => c.query.get('include_acknowledged'))).toEqual([null, 'true']);
  });

  it('acknowledges only after a confirmation; a 409 is a calm status; an acknowledged alert has no button', async () => {
    const user = userEvent.setup();
    let taken = true;
    const ack: Routes[number] = ['post', `/v1/staff/console/alerts/${ALERT}/acknowledge`, () => (taken ? fail(409) : json({ acknowledged: true, alert_id: ALERT }))];
    const { calls } = await openConsole({ role: 'nurse', hash: '/alerts', routes: [['get', '/v1/staff/console/alerts', () => list([alert(), alert({ alert_id: ID.doc2, acknowledged: true })])], ack] });
    await user.click(await screen.findByRole('button', { name: /^Acknowledge/ }));
    expect(screen.getAllByRole('button', { name: /^Acknowledge/ })).toHaveLength(2); // the row's and the dialog's: the acknowledged row has none
    const dialog = screen.getByRole('dialog', { name: 'Acknowledge this alert?' });
    expect(writes(calls)).toHaveLength(0);
    await user.click(within(dialog).getByRole('button', { name: 'Acknowledge' }));
    expect(await within(dialog).findByText(/already acknowledged/)).toBeInTheDocument();
    expect(within(dialog).queryByRole('alert')).toBeNull();
    taken = false;
    await user.click(within(dialog).getByRole('button', { name: 'Acknowledge' }));
    await screen.findByText('The alert was acknowledged.');
    const sent = writes(calls);
    expect(sent).toHaveLength(2);
    expect(sent[0]?.key).toBeTruthy();
    expect(sent[1]?.key).toBeTruthy(); // a 409 will not change on a retry, so the second press is a new intent with its own key
  });

  it('has an empty state, a fixed error message, and "not available" for a role the backend refuses', async () => {
    const a = await openConsole({ role: 'nurse', hash: '/alerts', routes: [['get', '/v1/staff/console/alerts', () => list([])]] });
    expect(await screen.findByText('There are no alerts.')).toBeInTheDocument();
    a.unmount();
    const b = await openConsole({ role: 'nurse', hash: '/alerts', routes: [['get', '/v1/staff/console/alerts', () => fail(500)]] });
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
    b.unmount();
    await openConsole({ role: 'nurse', hash: '/alerts', routes: [['get', '/v1/staff/console/alerts', () => fail(403)]] });
    await screen.findByText('This is not available to your role.');
  });
});

describe('dashboard (admin)', () => {
  it('shows an unreadable metric as "Not readable", never as zero, and lists what is not measured', async () => {
    await openConsole({
      role: 'admin',
      hash: '/dashboard',
      routes: [['get', '/v1/staff/console/dashboard', () => json({ clinic_id: CLINIC_A, metrics: [{ metric_id: 'm_one', state: 'measured', value: 12 }, { metric_id: 'm_two', state: 'unreadable', value: null }], not_measured: ['m_three'] })]],
    });
    const rows = (await screen.findAllByRole('row')).slice(1);
    expect(within(rows[0] as HTMLElement).getByText('12')).toBeInTheDocument();
    expect(within(rows[1] as HTMLElement).getAllByText('Not readable')).toHaveLength(2);
    expect(within(rows[1] as HTMLElement).queryByText('0')).toBeNull();
    expect(screen.getByText('m_three')).toBeInTheDocument();
  });

  it('is not available to a role the backend refuses, and an error shows a fixed message', async () => {
    const a = await openConsole({ role: 'nurse', hash: '/dashboard', routes: [['get', '/v1/staff/console/dashboard', () => fail(403)]] });
    await screen.findByText('This is not available to your role.');
    a.unmount();
    await openConsole({ role: 'admin', hash: '/dashboard', routes: [['get', '/v1/staff/console/dashboard', () => fail(502)]] });
    expect(await screen.findByRole('alert')).toHaveTextContent('This is not available right now.');
    expect(document.body.textContent).not.toContain(LEAK);
  });
});

describe('care gaps (read-only)', () => {
  const panel = (over: Record<string, unknown> = {}) => ({
    after_the_fact_error_rate: null, alert_age_configured: false, clinic_id: CLINIC_A, recurrence_rules_with_unknown_last_done: 2,
    rows: [{ age_days: null, flagged_overdue: false, item_id: ID.task, source: 'barrier' }, { age_days: 40, flagged_overdue: true, item_id: ID.task2, source: 'recurrence' }],
    summaries: [{ median_age_days: null, open_count: null, source: 'barrier', undated: 1 }, { median_age_days: 12, open_count: 4, source: 'recurrence', undated: 0 }],
    unreadable: ['barrier'], ...over,
  });

  it('never shows a source that could not be read as zero, and says when nothing can be flagged', async () => {
    await openConsole({ role: 'nurse', hash: '/care/gaps', routes: [['get', '/v1/staff/care-gaps', () => json(panel())]] });
    const summary = (await screen.findAllByRole('table'))[0] as HTMLElement;
    const barrier = within(summary).getAllByRole('row')[1] as HTMLElement;
    expect(within(barrier).getAllByText('Not readable')).toHaveLength(2);
    expect(within(barrier).queryByText('0')).toBeNull();
    expect(screen.getByText(/is not set, so nothing is flagged/)).toBeInTheDocument();
    expect(screen.getByText('Overdue')).toBeInTheDocument();
    expect(screen.getAllByText('Unknown').length).toBeGreaterThan(0); // an unknown age is "Unknown", not a number
    expect(screen.getByText('Not measured')).toBeInTheDocument(); // the error rate
  });

  it('asks for the active clinic and is not available to front desk (no menu entry, and the backend refuses)', async () => {
    const { calls } = await openConsole({ role: 'front_desk', hash: '/care/gaps', routes: [['get', '/v1/staff/care-gaps', () => fail(403)]] });
    await screen.findByText('This is not available to your role.');
    expect(calls.find((c) => c.path === '/v1/staff/care-gaps')?.query.get('clinic_id')).toBe(CLINIC_A);
  });
});

describe('care plans', () => {
  const plans = (status = 'confirmation_requested') => [{ created_at: '2026-11-09T05:00:00Z', patient_id: ID.patient, plan_id: PLAN, status }];
  const confirm = (body: unknown): Routes[number] => ['post', `/v1/staff/care-plans/${PLAN}/confirm`, () => body as Response];

  it('lists ids and status only; a doctor can approve a plan that waits for confirmation, after a confirmation, with a key', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({
      role: 'doctor',
      hash: '/care/plans',
      routes: [['get', '/v1/staff/care-plans', () => json({ plans: plans() })], confirm(json({ created: false, plan_id: PLAN, status: 'active', task_id: null, waiting_for: 'result' }))],
    });
    await user.click(await screen.findByRole('button', { name: /^Approve/ }));
    const dialog = screen.getByRole('dialog', { name: 'Approve this plan?' });
    expect(writes(calls)).toHaveLength(0);
    await user.click(within(dialog).getByRole('button', { name: 'Approve' }));
    expect(await screen.findByText('Recorded. The plan is now waiting for: a result.')).toBeInTheDocument();
    expect(writes(calls)[0]).toMatchObject({ path: `/v1/staff/care-plans/${PLAN}/confirm`, body: { action: 'approve' } });
    expect(writes(calls)[0]?.key).toBeTruthy();
  });

  it('offers a nurse no approve, reject or write button, and offers a doctor none on a plan that is not waiting', async () => {
    const a = await openConsole({ role: 'nurse', hash: '/care/plans', routes: [['get', '/v1/staff/care-plans', () => json({ plans: plans() })]] });
    await screen.findAllByRole('row');
    expect(screen.queryByRole('button', { name: /Approve|Reject|Write a care plan/ })).toBeNull();
    a.unmount();
    await openConsole({ role: 'doctor', hash: '/care/plans', routes: [['get', '/v1/staff/care-plans', () => json({ plans: plans('active') })]] });
    await screen.findAllByRole('row');
    expect(screen.queryByRole('button', { name: /^Approve|^Reject/ })).toBeNull();
    expect(screen.getByRole('button', { name: 'Write a care plan' })).toBeInTheDocument();
  });

  it('a 409 on a decision is calm, and 503 on writing says care plans are not set up and that nothing was written', async () => {
    const user = userEvent.setup();
    const create: Routes[number] = ['post', '/v1/staff/care-plans', () => fail(503)];
    await openConsole({ role: 'doctor', hash: '/care/plans', routes: [['get', '/v1/staff/care-plans', () => json({ plans: plans() })], confirm(fail(409)), create] });
    await user.click(await screen.findByRole('button', { name: /^Reject/ }));
    const d1 = screen.getByRole('dialog');
    await user.click(within(d1).getByRole('button', { name: 'Reject' }));
    expect(await within(d1).findByText('That changed in the meantime. Refresh and look again.')).toBeInTheDocument();
    await user.click(within(d1).getByRole('button', { name: 'Cancel' }));
    await user.click(screen.getByRole('button', { name: 'Write a care plan' }));
    const d2 = screen.getByRole('dialog');
    const submit = within(d2).getByRole('button', { name: 'Submit plan' });
    expect(submit).toBeDisabled();
    await user.type(within(d2).getByLabelText('Patient id'), 'not-an-id');
    expect(within(d2).getByText('That is not a patient id.')).toBeInTheDocument();
    await user.clear(within(d2).getByLabelText('Patient id'));
    await user.type(within(d2).getByLabelText('Patient id'), ID.patient);
    await user.type(within(d2).getByLabelText('Plan text'), 'Typed by the doctor');
    await user.click(submit);
    expect(await within(d2).findByText('Care plans are not set up yet. Nothing was written.')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
  });
});

describe('the clinic time zone from /v1/iam/me', () => {
  it('uses the own zone of the clinic when the backend sends one, with no placeholder label', async () => {
    const user = userEvent.setup();
    const record: Routes[number] = ['post', '/v1/staff/results', () => json({ created: true, result_id: ID.doc2, wake_queued: false })];
    const { calls } = await openConsole({ role: 'nurse', hash: '/care/results', routes: [record], me: { clinic_time_zone: 'UTC' } });
    await user.click(await screen.findByRole('button', { name: 'Record a result' }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('Times are in the clinic time zone: UTC.')).toBeInTheDocument();
    await user.type(within(dialog).getByLabelText('Patient id'), ID.patient);
    await user.type(within(dialog).getByLabelText('Result code'), 'c1');
    await user.type(within(dialog).getByLabelText('Measure 1 name'), 'm1');
    await user.type(within(dialog).getByLabelText('Valid from'), '2026-11-10T09:30');
    await user.click(within(dialog).getByRole('radio', { name: 'Yes' }));
    await user.click(within(dialog).getByRole('button', { name: 'Save result' }));
    await screen.findByText('The result was recorded.');
    expect((writes(calls)[0]?.body as { valid_from: string }).valid_from).toBe('2026-11-10T09:30:00.000Z');
  });

  it('keeps the labelled demo placeholder when the setting is null', async () => {
    const user = userEvent.setup();
    await openConsole({ role: 'nurse', hash: '/care/results', me: { clinic_time_zone: null } });
    await user.click(await screen.findByRole('button', { name: 'Record a result' }));
    expect(within(screen.getByRole('dialog')).getByText(/Asia\/Calcutta \(demo placeholder/)).toBeInTheDocument();
  });
});

describe('results', () => {
  it('records nothing until every field is valid and confirmed; an empty measure is sent as unknown; times are the clinic zone', async () => {
    const user = userEvent.setup();
    const record: Routes[number] = ['post', '/v1/staff/results', () => json({ created: true, result_id: ID.doc2, wake_queued: true })];
    const { calls } = await openConsole({ role: 'nurse', hash: '/care/results', routes: [record] });
    await user.click(await screen.findByRole('button', { name: 'Record a result' }));
    const dialog = screen.getByRole('dialog');
    const save = within(dialog).getByRole('button', { name: 'Save result' });
    expect(save).toBeDisabled();
    for (const r of within(dialog).getAllByRole('radio')) expect(r).not.toBeChecked(); // verified is not chosen for the person
    await user.type(within(dialog).getByLabelText('Patient id'), ID.patient);
    await user.type(within(dialog).getByLabelText('Result code'), 'code-1');
    await user.type(within(dialog).getByLabelText('Measure 1 name'), 'm1');
    await user.type(within(dialog).getByLabelText('Valid from'), '2026-11-10T09:30');
    expect(save).toBeDisabled(); // verified still not chosen
    await user.click(within(dialog).getByRole('radio', { name: 'No' }));
    expect(save).toBeEnabled();
    expect(writes(calls)).toHaveLength(0);
    await user.click(save);
    await screen.findByText('The result was recorded. A waiting care plan will be woken.');
    expect(writes(calls)[0]).toMatchObject({
      path: '/v1/staff/results',
      body: { clinic_id: CLINIC_A, patient_id: ID.patient, code: 'code-1', measures: { m1: null }, valid_from: '2026-11-10T04:00:00.000Z', valid_to: null, verified: false, supersedes_id: null },
    });
    expect(writes(calls)[0]?.key).toBeTruthy();
  });

  it('refuses a value that is not a number, and is not available to front desk', async () => {
    const user = userEvent.setup();
    const a = await openConsole({ role: 'doctor', hash: '/care/results' });
    await user.click(await screen.findByRole('button', { name: 'Record a result' }));
    await user.type(screen.getByLabelText('Measure 1 name'), 'm1');
    await user.type(screen.getByLabelText('Measure 1 value'), 'abc');
    expect(screen.getByText('A value must be a number or empty.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save result' })).toBeDisabled();
    a.unmount();
    await openConsole({ role: 'front_desk', hash: '/care/results' });
    await screen.findByText('This is not available to your role.');
  });
});
