import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ACCOUNT_ID, CLINIC_A, json } from './helpers/fakes';
import { ID, LEAK, fail, list, openConsole, writes, type Routes } from './helpers/console';

// MOCK-ONLY (see helpers/console.tsx): fakes written from openapi.snapshot.json, not from a live backend.

const ITEM = 'abababab-abab-4bab-8bab-abababababab';
const PROPOSAL = 'cdcdcdcd-cdcd-4dcd-8dcd-cdcdcdcdcdcd';
const SESSION = 'efefefef-efef-4fef-8fef-efefefefefef';

const gaps = (rows: unknown[]) => json({
  after_the_fact_error_rate: null, alert_age_configured: true, clinic_id: CLINIC_A, recurrence_rules_with_unknown_last_done: 0,
  rows, summaries: [], unreadable: [],
});
const row = (source: string, over: Record<string, unknown> = {}) => ({ age_days: 3, flagged_overdue: false, item_id: ITEM, source, ...over });

describe('care-gap writes (nurse and doctor only)', () => {
  it('records a barrier: nothing chosen for the person, nothing written before the confirmation, a key sent', async () => {
    const user = userEvent.setup();
    const barrier: Routes[number] = ['post', '/v1/staff/care-gaps/barriers', () => json({ barrier_id: ITEM, created: true }, 201)];
    const { calls } = await openConsole({ role: 'nurse', hash: '/care/gaps', routes: [['get', '/v1/staff/care-gaps', () => gaps([])], barrier] });
    await user.click(await screen.findByRole('button', { name: 'Record a barrier' }));
    const dialog = screen.getByRole('dialog');
    const record = within(dialog).getByRole('button', { name: 'Record' });
    expect(record).toBeDisabled();
    expect((within(dialog).getByLabelText('Barrier') as HTMLSelectElement).value).toBe('');
    await user.type(within(dialog).getByLabelText('Patient id'), ID.patient);
    expect(record).toBeDisabled(); // the barrier type is still the person's to choose
    await user.selectOptions(within(dialog).getByLabelText('Barrier'), 'transport');
    expect(writes(calls)).toHaveLength(0);
    await user.click(record);
    await screen.findByText('The barrier was recorded.');
    expect(writes(calls)[0]).toMatchObject({ path: '/v1/staff/care-gaps/barriers', body: { clinic_id: CLINIC_A, patient_id: ID.patient, barrier: 'transport' } });
    expect(writes(calls)[0]?.key).toBeTruthy();
  });

  it('starts an episode, and says plainly when the rules setting is off (503) that nothing was started', async () => {
    const user = userEvent.setup();
    const episode: Routes[number] = ['post', '/v1/staff/care-gaps/episodes', () => fail(503)];
    await openConsole({ role: 'doctor', hash: '/care/gaps', routes: [['get', '/v1/staff/care-gaps', () => gaps([])], episode] });
    await user.click(await screen.findByRole('button', { name: 'Start an episode' }));
    const dialog = screen.getByRole('dialog');
    await user.type(within(dialog).getByLabelText('Patient id'), ID.patient);
    await user.type(within(dialog).getByLabelText('Episode code'), 'code-a');
    await user.type(within(dialog).getByLabelText('Started on'), '2026-11-01');
    await user.click(within(dialog).getByRole('button', { name: 'Start' }));
    expect(await within(dialog).findByText('Episode rules are not set up yet. Nothing was started.')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('offers each item the one action its source has, and none for an unknown source', async () => {
    await openConsole({ role: 'nurse', hash: '/care/gaps', routes: [['get', '/v1/staff/care-gaps', () => gaps([row('unresolved_barrier'), row('after_the_fact_review', { item_id: ID.task }), row('overdue_recurrence', { item_id: ID.task2 }), row('something_new', { item_id: ID.task3 })])]] });
    const rows = (await screen.findAllByRole('row')).slice(1);
    expect(within(rows[0] as HTMLElement).getByRole('button', { name: /^Resolve/ })).toBeInTheDocument();
    expect(within(rows[1] as HTMLElement).getByRole('button', { name: /^Re-check/ })).toBeInTheDocument();
    expect(within(rows[2] as HTMLElement).getByRole('button', { name: /^Mark done/ })).toBeInTheDocument();
    expect(within(rows[3] as HTMLElement).queryByRole('button')).toBeNull();
  });

  it('shows the database refusal in fixed words when the re-checker is the person who made the original decision', async () => {
    const user = userEvent.setup();
    const recheck: Routes[number] = ['post', `/v1/staff/care-gaps/samples/${ITEM}/recheck`, () => fail(409)];
    const { calls } = await openConsole({ role: 'nurse', hash: '/care/gaps', routes: [['get', '/v1/staff/care-gaps', () => gaps([row('after_the_fact_review')])], recheck] });
    await user.click(await screen.findByRole('button', { name: /^Re-check/ }));
    const dialog = screen.getByRole('dialog');
    expect(writes(calls)).toHaveLength(0);
    await user.click(within(dialog).getByRole('button', { name: 'Re-check' }));
    expect(await within(dialog).findByText(/You made the decision this sample was drawn from, so someone else must re-check it/)).toBeInTheDocument();
    expect(within(dialog).queryByRole('alert')).toBeNull();
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('resolves a barrier, and refuses a rule date in the future with a calm message', async () => {
    const user = userEvent.setup();
    const routes: Routes = [
      ['get', '/v1/staff/care-gaps', () => gaps([row('unresolved_barrier'), row('overdue_recurrence', { item_id: ID.task2 })])],
      ['post', `/v1/staff/care-gaps/barriers/${ITEM}/resolve`, () => json({ barrier_id: ITEM, resolved: true })],
      ['post', `/v1/staff/care-gaps/episode-rules/${ID.task2}/done`, () => fail(409)],
    ];
    const { calls } = await openConsole({ role: 'nurse', hash: '/care/gaps', routes });
    await user.click(await screen.findByRole('button', { name: /^Resolve/ }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Resolve' }));
    await screen.findByText('The barrier was resolved.');
    await user.click(screen.getByRole('button', { name: /^Mark done/ }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByRole('button', { name: 'Record' })).toBeDisabled();
    await user.type(within(dialog).getByLabelText('Done on'), '2099-01-01');
    await user.click(within(dialog).getByRole('button', { name: 'Record' }));
    expect(await within(dialog).findByText(/That date is refused/)).toBeInTheDocument();
    expect(writes(calls).find((c) => c.path.endsWith('/done'))?.body).toEqual({ done_on: '2099-01-01' });
  });

  it('gives front desk and admin no write controls on the panel', async () => {
    const a = await openConsole({ role: 'admin', hash: '/care/gaps', routes: [['get', '/v1/staff/care-gaps', () => gaps([row('unresolved_barrier')])]] });
    await screen.findAllByRole('row');
    expect(screen.queryByRole('button', { name: /Record a barrier|Start an episode|^Resolve/ })).toBeNull();
    a.unmount();
  });

  it('fills the patient id in from the record or referral that was on screen', async () => {
    const user = userEvent.setup();
    await openConsole({ role: 'nurse', hash: `/care/results?patient=${ID.patient}` });
    await user.click(await screen.findByRole('button', { name: 'Record a result' }));
    expect(within(screen.getByRole('dialog')).getByLabelText('Patient id')).toHaveValue(ID.patient);
  });

  it('ignores a patient hint that is not an id, and offers the care links on a patient record to nurse and doctor only', async () => {
    const user = userEvent.setup();
    const a = await openConsole({ role: 'nurse', hash: '/care/results?patient=not-an-id' });
    await user.click(await screen.findByRole('button', { name: 'Record a result' }));
    expect(within(screen.getByRole('dialog')).getByLabelText('Patient id')).toHaveValue('');
    a.unmount();
    const patient = { clinic_id: CLINIC_A, created_at: '2026-10-30T05:00:00Z', merged_into: null, patient_id: ID.patient, registered_via: 'staff', verified: true };
    const routes: Routes = [['get', `/v1/staff/patients/${ID.patient}`, () => json(patient)]];
    const b = await openConsole({ role: 'nurse', hash: `/patients/${ID.patient}`, routes });
    const links = await screen.findByRole('navigation', { name: 'Care for this patient' });
    expect(within(links).getByRole('link', { name: 'Care plans' })).toHaveAttribute('href', `#/care/plans?patient=${ID.patient}`);
    b.unmount();
    await openConsole({ role: 'front_desk', hash: `/patients/${ID.patient}`, routes });
    await screen.findByRole('heading', { level: 1 });
    expect(screen.queryByRole('navigation', { name: 'Care for this patient' })).toBeNull();
  });
});

describe('admin: staff accounts', () => {
  const account = (over: Record<string, unknown> = {}) => ({ account_id: ID.user, roles: ['nurse'], status: 'active', ...over });
  const accountsRoute = (items: unknown[]): Routes[number] => ['get', '/v1/staff/accounts', () => list(items)];

  it('lists ids, a status word and role words only, and the invite is behind a confirmation with nothing chosen for the person', async () => {
    const user = userEvent.setup();
    const invite: Routes[number] = ['post', '/v1/staff/accounts/invite', () => json({ account_id: ID.user2, clinic_id: CLINIC_A, replayed: false, role: 'nurse' })];
    const { calls } = await openConsole({ role: 'admin', hash: '/admin/accounts', routes: [accountsRoute([account()]), invite] });
    const table = await screen.findByRole('table');
    expect(within(table).getByText('Nurse')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Invite a staff member' }));
    const dialog = screen.getByRole('dialog');
    const confirm = within(dialog).getByRole('button', { name: 'Invite a staff member' });
    expect(confirm).toBeDisabled();
    expect((within(dialog).getByLabelText('Role') as HTMLSelectElement).value).toBe('');
    await user.type(within(dialog).getByLabelText('E-mail address'), 'new@example.invalid');
    await user.selectOptions(within(dialog).getByLabelText('Role'), 'nurse');
    expect(writes(calls)).toHaveLength(0);
    await user.click(confirm);
    await screen.findByText('The invitation was created.');
    expect(writes(calls)[0]).toMatchObject({ path: '/v1/staff/accounts/invite', body: { clinic_id: CLINIC_A, email: 'new@example.invalid', role: 'nurse' } });
    expect(document.body.textContent).not.toContain('new@example.invalid'); // the address is not shown back
  });

  it('shows the 503 state of an invite as a calm status that says nothing was sent', async () => {
    const user = userEvent.setup();
    const invite: Routes[number] = ['post', '/v1/staff/accounts/invite', () => fail(503)];
    await openConsole({ role: 'admin', hash: '/admin/accounts', routes: [accountsRoute([]), invite] });
    await user.click(await screen.findByRole('button', { name: 'Invite a staff member' }));
    const dialog = screen.getByRole('dialog');
    await user.type(within(dialog).getByLabelText('E-mail address'), 'new@example.invalid');
    await user.selectOptions(within(dialog).getByLabelText('Role'), 'front_desk');
    await user.click(within(dialog).getByRole('button', { name: 'Invite a staff member' }));
    expect(await within(dialog).findByText('Inviting is not set up yet (no sign-in administration is configured). Nothing was sent.')).toBeInTheDocument();
    expect(within(dialog).queryByRole('alert')).toBeNull();
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('suspends an active account and reinstates a suspended one; revoke refuses to remove the last administrator', async () => {
    const user = userEvent.setup();
    const routes: Routes = [
      accountsRoute([account(), account({ account_id: ID.user2, status: 'suspended', roles: ['admin'] })]),
      ['post', `/v1/staff/accounts/${ID.user}/suspend`, () => json({ account_id: ID.user, changed: true, status: 'suspended' })],
      ['post', `/v1/staff/accounts/${ID.user2}/roles/revoke`, () => fail(409)],
    ];
    const { calls } = await openConsole({ role: 'admin', hash: '/admin/accounts', routes });
    await screen.findAllByRole('row');
    expect(screen.getAllByRole('button', { name: /^Reinstate/ })).toHaveLength(1);
    await user.click(screen.getByRole('button', { name: /^Suspend/ }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Suspend' }));
    await screen.findByText('The account was suspended.');
    expect(writes(calls)[0]?.key).toBeTruthy();
    await user.click(screen.getByRole('button', { name: new RegExp(`^Revoke a role ${ID.user2.slice(0, 8)}`) }));
    const dialog = screen.getByRole('dialog');
    await user.selectOptions(within(dialog).getByLabelText('Role'), 'admin');
    await user.click(within(dialog).getByRole('button', { name: 'Revoke a role' }));
    expect(await within(dialog).findByText('That would leave the clinic without an active administrator. Nothing was changed.')).toBeInTheDocument();
  });

  it('is not available to a role the backend refuses', async () => {
    await openConsole({ role: 'admin', hash: '/admin/accounts', routes: [['get', '/v1/staff/accounts', () => fail(403)]] });
    await screen.findByText('This is not available to your role.');
  });
});

describe('admin: autonomy', () => {
  const proposalRow = (over: Record<string, unknown> = {}) => ({ created_at: '2026-11-09T05:00:00Z', proposal_id: PROPOSAL, proposed_by: ID.user, proposer_role: 'doctor', ...over });
  const modeRoute = (over: Record<string, unknown> = {}): Routes[number] => ['get', '/v1/staff/console/autonomy', () => json({ clinic_id: CLINIC_A, configured: false, mode: 'shadow', ...over })];
  const proposalsRoute = (rows: unknown[]): Routes[number] => ['get', '/v1/staff/console/autonomy/proposals', () => json({ proposals: rows })];

  it('says the mode is shadow because it was never set, and sets assisted only after choosing it and confirming', async () => {
    const user = userEvent.setup();
    const set: Routes[number] = ['post', '/v1/staff/console/autonomy', () => json({ created: true, mode: 'assisted' })];
    const { calls } = await openConsole({ role: 'admin', hash: '/admin/autonomy', routes: [modeRoute(), proposalsRoute([]), set] });
    expect(await screen.findByText(/never set, so shadow/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Change the mode' }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByRole('button', { name: 'Set mode' })).toBeDisabled();
    await user.selectOptions(within(dialog).getByLabelText('Mode'), 'assisted');
    expect(writes(calls)).toHaveLength(0);
    await user.click(within(dialog).getByRole('button', { name: 'Set mode' }));
    await screen.findByText('The mode was set.');
    expect(writes(calls)[0]?.body).toEqual({ clinic_id: CLINIC_A, mode: 'assisted' });
    expect(within(screen.getByRole('main')).queryByText('Autonomous')).toBeNull(); // autonomous is not a choice here
  });

  it('proposes as the role the person is acting as, and shows the 503 state when the switch is off', async () => {
    const user = userEvent.setup();
    const propose: Routes[number] = ['post', '/v1/staff/console/autonomy/proposals', () => fail(503)];
    const { calls } = await openConsole({ role: 'doctor', hash: '/admin/autonomy', routes: [modeRoute(), proposalsRoute([]), propose] });
    await user.click(await screen.findByRole('button', { name: 'Propose switching to autonomous' }));
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveTextContent('You propose as Doctor.');
    await user.click(within(dialog).getByRole('button', { name: 'Propose' }));
    expect(await within(dialog).findByText('Switching to autonomous is not enabled yet. Nothing was changed.')).toBeInTheDocument();
    expect(writes(calls)[0]?.body).toEqual({ clinic_id: CLINIC_A, proposer_role: 'doctor' });
    expect(screen.queryByRole('button', { name: 'Change the mode' })).toBeNull(); // admin only
  });

  it('offers approval only to a different person holding the other role, and tells every refusal in fixed words', async () => {
    const user = userEvent.setup();
    const resolve: Routes[number] = ['post', `/v1/staff/console/autonomy/proposals/${PROPOSAL}/resolve`, () => json({ result: 'same_person' })];
    const { calls } = await openConsole({
      role: 'admin',
      hash: '/admin/autonomy',
      routes: [modeRoute(), proposalsRoute([proposalRow(), proposalRow({ proposal_id: ID.hold, proposed_by: ACCOUNT_ID, proposer_role: 'admin' }), proposalRow({ proposal_id: ID.slot, proposer_role: 'admin' })]), resolve],
    });
    await screen.findAllByRole('row');
    // Row 1: a doctor's proposal by someone else: approve offered. Row 2: mine, so no approve. Row 3: proposed in the admin role, so no approve.
    expect(screen.getAllByRole('button', { name: /^Approve/ })).toHaveLength(1);
    expect(screen.getAllByRole('button', { name: /^Reject/ })).toHaveLength(3);
    await user.click(screen.getByRole('button', { name: /^Approve/ }));
    expect(writes(calls)).toHaveLength(0);
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Approve' }));
    expect(await screen.findByText('The person who proposed it cannot approve it.')).toBeInTheDocument();
    expect(writes(calls)[0]).toMatchObject({ body: { decision: 'approve' } });
  });

  it('shows the 503 state when resolving is not enabled', async () => {
    const user = userEvent.setup();
    const resolve: Routes[number] = ['post', `/v1/staff/console/autonomy/proposals/${PROPOSAL}/resolve`, () => fail(503)];
    await openConsole({ role: 'doctor', hash: '/admin/autonomy', routes: [modeRoute(), proposalsRoute([proposalRow({ proposer_role: 'admin' })]), resolve] });
    await user.click(await screen.findByRole('button', { name: /^Reject/ }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Reject' }));
    expect(await screen.findByText('Switching to autonomous is not enabled yet. Nothing was changed.')).toBeInTheDocument();
  });

  it('shows the history of mode changes and proposals with their outcomes, a null as a dash, and an empty state', async () => {
    const history = (items: unknown[]): Routes[number] => ['get', '/v1/staff/console/autonomy/history', () => json({ items })];
    const a = await openConsole({
      role: 'admin',
      hash: '/admin/autonomy',
      routes: [modeRoute(), proposalsRoute([]), history([
        { at: '2026-11-08T05:00:00Z', by: ID.user, decided_at: '2026-11-09T05:00:00Z', decided_by: ID.user2, kind: 'proposal', mode: 'autonomous', outcome: 'approved', proposer_role: 'doctor' },
        { at: '2026-11-01T05:00:00Z', by: ID.user, decided_at: null, decided_by: null, kind: 'mode_change', mode: 'assisted', outcome: null, proposer_role: null },
      ])],
    });
    const table = (await screen.findAllByRole('table')).find((t) => t.querySelector('caption')?.textContent === 'History') as HTMLElement;
    const rows = within(table).getAllByRole('row').slice(1);
    expect(within(rows[0] as HTMLElement).getByText('Approved')).toBeInTheDocument();
    expect(within(rows[0] as HTMLElement).getByText(/Proposal \(Doctor\)/)).toBeInTheDocument();
    expect(within(rows[1] as HTMLElement).getByText('Mode change')).toBeInTheDocument();
    expect(within(rows[1] as HTMLElement).getAllByText('–').length).toBeGreaterThan(0);
    a.unmount();
    await openConsole({ role: 'doctor', hash: '/admin/autonomy', routes: [modeRoute(), proposalsRoute([]), history([])] });
    expect(await screen.findByText('There is no history yet.')).toBeInTheDocument();
  });

  it('shows a doctor the Autonomy and Red-flag rules tabs only', async () => {
    await openConsole({ role: 'doctor', hash: '/admin', routes: [modeRoute(), proposalsRoute([])] });
    const tabs = await screen.findByRole('navigation', { name: 'Admin sections' });
    expect(within(tabs).getAllByRole('link').map((a) => a.textContent)).toEqual(['Autonomy', 'Red-flag rules']);
  });
});

describe('admin: reviewer statistics and cost canvas', () => {
  it('shows a missing rate or time as "Not recorded", never as zero, and says when the policy is not set', async () => {
    const stats = { clinic_id: CLINIC_A, days: 30, policy_configured: false, reviewers: [{ approval_rate: null, approvals: 0, canaries_approved: 0, canaries_seen: 2, decisions: 0, edits: 0, median_seconds: null, reasons: [], rejections: 0, reviewer: ID.user, verdict: 'not_configured' }] };
    await openConsole({ role: 'admin', hash: '/admin/reviewer', routes: [['get', '/v1/staff/console/reviewer-stats', () => json(stats)]] });
    const row = (await screen.findAllByRole('row'))[1] as HTMLElement;
    expect(within(row).getAllByText('Not recorded')).toHaveLength(2);
    expect(screen.getByText(/policy that judges rubber-stamping is not set/)).toBeInTheDocument();
    expect(within(row).getByText('Policy not set')).toBeInTheDocument();
  });

  it('keeps a null column and a null term null, with the reasons the backend gives', async () => {
    const column = { calls_per_workflow: null, failure_tax_per_month: null, platform_floor_per_month: 100, total_per_month: null, usd_per_call: null, workflows_per_day: 12, workload_x_amplification_x_unit_per_month: null };
    const canvas = { actual: column, shadow: null, amplification_over_ceiling: false, clinic_id: CLINIC_A, reasons: ['fewer than seven days of measured traffic', 'a sentence the console does not know yet'], state: 'insufficient_data' };
    await openConsole({ role: 'admin', hash: '/admin/cost', routes: [['get', '/v1/staff/console/cost-canvas', () => json(canvas)]] });
    const table = await screen.findByRole('table');
    const rows = within(table).getAllByRole('row').slice(1);
    const total = rows.find((r) => within(r).queryByText('Total per month')) as HTMLElement;
    expect(within(total).getByText('Not measured')).toBeInTheDocument();
    expect(within(total).getByText('No column')).toBeInTheDocument();
    expect(within(total).queryByText('0')).toBeNull();
    expect(screen.getByText('Not enough data')).toBeInTheDocument(); // the state, as a label
    expect(screen.getByText('Fewer than seven days of measured traffic')).toBeInTheDocument(); // a known reason, labelled
    expect(screen.getByText('a sentence the console does not know yet')).toBeInTheDocument(); // an unknown one is shown as sent, never hidden
  });

  it('is not available to a role the backend refuses', async () => {
    await openConsole({ role: 'admin', hash: '/admin/cost', routes: [['get', '/v1/staff/console/cost-canvas', () => fail(403)]] });
    await screen.findByText('This is not available to your role.');
  });
});

describe('admin: voice transcripts (audited)', () => {
  const sessions = [{ chunk_count: 3, expired_chunk_count: 1, retention_days: 30, session_id: SESSION, started_at: '2026-11-09T05:00:00Z', status: 'ended', workflow: 'intake' }];
  const sessionsRoute: Routes[number] = ['get', '/v1/staff/voice/sessions', () => json({ sessions })];
  const transcript = (answer: () => Response): Routes[number] => ['get', `/v1/staff/voice/sessions/${SESSION}/transcript`, answer];

  it('reads a transcript only on a click, in order, and drops the text when the window closes', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({
      role: 'admin',
      hash: '/admin/voice',
      routes: [sessionsRoute, transcript(() => json({ chunks: [{ seq: 2, text: 'Second words' }, { seq: 1, text: 'First words' }], session_id: SESSION }))],
    });
    await screen.findAllByRole('row');
    expect(calls.some((c) => c.path.includes('/transcript'))).toBe(false); // nothing is read, so nothing is audited, until asked
    await user.click(screen.getByRole('button', { name: /^Read transcript/ }));
    const dialog = screen.getByRole('dialog', { name: 'Read this transcript?' });
    expect(calls.some((c) => c.path.includes('/transcript'))).toBe(false); // opening the window reads nothing either
    await user.click(within(dialog).getByRole('button', { name: 'Read transcript' }));
    const items = await within(dialog).findAllByRole('listitem');
    expect(items.map((i) => i.textContent)).toEqual(['First words', 'Second words']);
    expect(calls.find((c) => c.path.includes('/transcript'))?.query.get('clinic_id')).toBe(CLINIC_A);
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(document.body.textContent).not.toContain('First words');
  });

  it('says plainly when keys are not configured (503), and is not available to a role the backend refuses', async () => {
    const user = userEvent.setup();
    const a = await openConsole({ role: 'admin', hash: '/admin/voice', routes: [sessionsRoute, transcript(() => fail(503))] });
    await user.click(await screen.findByRole('button', { name: /^Read transcript/ }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Read transcript' }));
    expect(await screen.findByText('Encryption keys are not configured, so the transcript cannot be read.')).toBeInTheDocument();
    a.unmount();
    await openConsole({ role: 'admin', hash: '/admin/voice', routes: [['get', '/v1/staff/voice/sessions', () => fail(403)]] });
    await screen.findByText('This is not available to your role.');
  });

  it('purges expired chunks only after a confirmation; leaving the limit empty sends none; a limit over the maximum is refused', async () => {
    const user = userEvent.setup();
    const purge: Routes[number] = ['post', '/v1/staff/voice/purge', () => json({ deleted: 4 })];
    const { calls } = await openConsole({ role: 'admin', hash: '/admin/voice', routes: [sessionsRoute, purge] });
    await user.click(await screen.findByRole('button', { name: 'Purge expired transcripts' }));
    const dialog = screen.getByRole('dialog');
    const confirm = within(dialog).getByRole('button', { name: 'Purge' });
    await user.type(within(dialog).getByLabelText('Most chunks to delete (optional)'), '5000');
    expect(confirm).toBeDisabled();
    await user.clear(within(dialog).getByLabelText('Most chunks to delete (optional)'));
    expect(writes(calls)).toHaveLength(0);
    await user.click(confirm);
    await screen.findByText('Deleted 4 expired chunks.');
    expect(writes(calls)[0]?.body).toEqual({ clinic_id: CLINIC_A });
    expect(writes(calls)[0]?.key).toBeTruthy();
  });

  it('has an empty state', async () => {
    await openConsole({ role: 'admin', hash: '/admin/voice', routes: [['get', '/v1/staff/voice/sessions', () => json({ sessions: [] })]] });
    expect(await screen.findByText('There are no voice sessions.')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
  });
});
