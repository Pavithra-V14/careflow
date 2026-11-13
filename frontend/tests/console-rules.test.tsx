import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ACCOUNT_ID, CLINIC_A, json } from './helpers/fakes';
import { ID, LEAK, fail, list, openConsole, writes, type Routes } from './helpers/console';

// MOCK-ONLY (see helpers/console.tsx): fakes written from openapi.snapshot.json. Red-flag rule approval (two different doctors), the front-desk
// supervisor, the new alert class and the admin_alert word on a revoke. Nothing here is clinical content: the phrases are placeholders.

const RULE = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd';
const RULE2 = 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee';
const rule = (over: Record<string, unknown> = {}) => ({
  approved_at: null,
  decisions: [],
  has_vitals_criteria: false,
  origin: 'test',
  patterns: ['PLACEHOLDER-PATTERN-A'],
  phrases: ['PLACEHOLDER-PHRASE-A', 'PLACEHOLDER-PHRASE-B'],
  rule_code: 'placeholder_rule',
  rule_id: RULE,
  status: 'draft_unreviewed',
  version_id: 1,
  ...over,
});
const listRoute = (rules: unknown[]): Routes[number] => ['get', '/v1/staff/console/red-flag-rules', () => json({ rules })];
const decisionPath = `/v1/staff/console/red-flag-rules/${RULE}/decision`;
const decideRoute = (answer: string | number): Routes[number] => ['post', decisionPath, () => (typeof answer === 'number' ? fail(answer) : json({ result: answer }))];

describe('red-flag rules: a doctor decides', () => {
  it('shows status, content as sent, and who decided as ids with "You" for the person; asks for the clinic', async () => {
    const decided = [{ decided_at: '2026-11-10T05:00:00Z', decided_by: ID.user, decision: 'approve' }, { decided_at: '2026-11-11T05:00:00Z', decided_by: ACCOUNT_ID, decision: 'approve' }];
    const { calls } = await openConsole({ role: 'doctor', hash: '/admin/rules', routes: [listRoute([rule({ decisions: decided, status: 'approved', approved_at: '2026-11-11T05:00:00Z' })])] });
    expect(await screen.findByText('PLACEHOLDER-PHRASE-A')).toBeInTheDocument();
    expect(screen.getByText('PLACEHOLDER-PHRASE-B')).toBeInTheDocument();
    expect(screen.getByText('PLACEHOLDER-PATTERN-A')).toBeInTheDocument();
    expect(screen.getByText('Approved', { selector: '.badge, span, strong' })).toBeInTheDocument();
    expect(screen.getByText('This version has no vital-sign criteria.')).toBeInTheDocument();
    const rows = within(screen.getByRole('table')).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(2);
    expect(within(rows[0] as HTMLElement).getByText(`${ID.user.slice(0, 8)}…`)).toBeInTheDocument();
    expect(within(rows[1] as HTMLElement).getByText('You')).toBeInTheDocument();
    expect(calls.find((c) => c.path === '/v1/staff/console/red-flag-rules')?.query.get('clinic_id')).toBe(CLINIC_A);
    // An approved version offers no decision.
    expect(screen.queryByRole('button', { name: /^Approve/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /^Reject/ })).toBeNull();
  });

  it('asks for a confirmation showing the content before approving, writes nothing before it, and sends a key', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ role: 'doctor', hash: '/admin/rules', routes: [listRoute([rule()]), decideRoute('recorded')] });
    await user.click(await screen.findByRole('button', { name: 'Approve placeholder_rule v1' }));
    const dialog = screen.getByRole('dialog', { name: 'Approve this red-flag rule version?' });
    expect(within(dialog).getByText('PLACEHOLDER-PHRASE-A')).toBeInTheDocument(); // the person approves what they can see
    expect(writes(calls)).toHaveLength(0);
    await user.click(within(dialog).getByRole('button', { name: 'Approve' }));
    expect(await screen.findByText('Your approval was recorded. The version is not approved yet: a second, different doctor must approve it.')).toBeInTheDocument();
    expect(writes(calls)).toHaveLength(1);
    expect(writes(calls)[0]).toMatchObject({ path: decisionPath, body: { decision: 'approve' } });
    expect(writes(calls)[0]?.key).toBeTruthy();
  });

  it('asks for a confirmation before rejecting, too, and says the version is closed', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ role: 'doctor', hash: '/admin/rules', routes: [listRoute([rule()]), decideRoute('rejected')] });
    await user.click(await screen.findByRole('button', { name: 'Reject placeholder_rule v1' }));
    expect(writes(calls)).toHaveLength(0);
    await user.click(within(screen.getByRole('dialog', { name: 'Reject this red-flag rule version?' })).getByRole('button', { name: 'Reject' }));
    expect(await screen.findByText('Your rejection was recorded. This version is closed.')).toBeInTheDocument();
    expect(writes(calls)[0]).toMatchObject({ body: { decision: 'reject' } });
  });

  it('cancelling the confirmation writes nothing', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ role: 'doctor', hash: '/admin/rules', routes: [listRoute([rule()]), decideRoute('recorded')] });
    await user.click(await screen.findByRole('button', { name: 'Approve placeholder_rule v1' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(writes(calls)).toHaveLength(0);
  });

  it('offers no decision on a version this person already decided, on a rejected one, or on a retired one', async () => {
    await openConsole({
      role: 'doctor',
      hash: '/admin/rules',
      routes: [
        listRoute([
          rule({ decisions: [{ decided_at: '2026-11-10T05:00:00Z', decided_by: ACCOUNT_ID, decision: 'approve' }] }),
          rule({ rule_id: RULE2, rule_code: 'second_rule', decisions: [{ decided_at: '2026-11-10T05:00:00Z', decided_by: ID.user, decision: 'reject' }] }),
          rule({ rule_id: ID.doc, rule_code: 'third_rule', status: 'retired' }),
        ]),
      ],
    });
    await screen.findByText('second_rule');
    expect(screen.queryByRole('button', { name: /^(Approve|Reject)/ })).toBeNull();
    expect(screen.getByText('You have already decided on this version.')).toBeInTheDocument();
  });

  it.each([
    ['recorded', 'a second, different doctor must approve it'],
    ['approved', 'this version is now approved'],
    ['already_decided', 'You already decided on this version the other way. Nothing changed.'],
    ['not_draft', 'This version is not a draft any more, so nothing was recorded.'],
    ['origin_mismatch', 'This version belongs to a different environment than this clinic, so nothing was recorded.'],
    ['author_cannot_decide', 'You wrote this version, so you cannot decide it. A different doctor must.'],
    ['empty_rule', 'This version has no phrases, patterns or vital-sign criteria, so it cannot be approved.'],
    ['rejected_earlier', 'A doctor already rejected this version, so it cannot be approved.'],
    ['platform_rule', 'This rule applies to every clinic. Who may approve it has not been decided, so nothing was approved here.'],
  ])('tells the answer %s in fixed words', async (word, text) => {
    const user = userEvent.setup();
    await openConsole({ role: 'doctor', hash: '/admin/rules', routes: [listRoute([rule()]), decideRoute(word)] });
    await user.click(await screen.findByRole('button', { name: 'Approve placeholder_rule v1' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Approve' }));
    await waitFor(() => expect(document.body.textContent).toContain(text));
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('never reads a result word it does not know as approved', async () => {
    const user = userEvent.setup();
    await openConsole({ role: 'doctor', hash: '/admin/rules', routes: [listRoute([rule()]), decideRoute('something_new')] });
    await user.click(await screen.findByRole('button', { name: 'Approve placeholder_rule v1' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Approve' }));
    expect(await screen.findByText(/a result this screen does not know/)).toBeInTheDocument();
    expect(screen.queryByText(/is now approved/)).toBeNull();
  });

  it('403 on the decision is "not available to your role", never the backend text', async () => {
    const user = userEvent.setup();
    await openConsole({ role: 'doctor', hash: '/admin/rules', routes: [listRoute([rule()]), decideRoute(403)] });
    await user.click(await screen.findByRole('button', { name: 'Approve placeholder_rule v1' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Approve' }));
    expect(await within(screen.getByRole('dialog')).findByText(/not available to your role/i)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('404 on the decision shows a fixed problem message', async () => {
    const user = userEvent.setup();
    await openConsole({ role: 'doctor', hash: '/admin/rules', routes: [listRoute([rule()]), decideRoute(404)] });
    await user.click(await screen.findByRole('button', { name: 'Approve placeholder_rule v1' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Approve' }));
    expect(await within(screen.getByRole('dialog')).findByRole('alert')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('shows the empty state, and the not-available state when the list is refused', async () => {
    const a = await openConsole({ role: 'doctor', hash: '/admin/rules', routes: [listRoute([])] });
    expect(await screen.findByText('There are no red-flag rule versions to show.')).toBeInTheDocument();
    a.unmount();
    await openConsole({ role: 'doctor', hash: '/admin/rules', routes: [['get', '/v1/staff/console/red-flag-rules', () => fail(403)]] });
    expect(await screen.findByText(/not available to your role/i)).toBeInTheDocument();
  });

  it('shows the screen in Tamil with the same buttons', async () => {
    await openConsole({ role: 'doctor', hash: '/admin/rules', lang: 'ta', routes: [listRoute([rule()])] });
    expect(await screen.findByRole('button', { name: /^ஒப்புக்கொள் / })).toBeInTheDocument();
    expect(screen.getByText('PLACEHOLDER-PHRASE-A')).toBeInTheDocument();
  });
});

describe('red-flag rules: an admin reads, never decides', () => {
  it('lists the versions and offers no decision', async () => {
    await openConsole({ role: 'admin', hash: '/admin/rules', routes: [listRoute([rule()])] });
    expect(await screen.findByText('PLACEHOLDER-PHRASE-A')).toBeInTheDocument();
    expect(screen.getByText('You can read these rules and who decided on them. Only doctors decide.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^(Approve|Reject)/ })).toBeNull();
  });

  it('is not in the front desk menu or the nurse menu', async () => {
    const a = await openConsole({ role: 'front_desk', hash: '/', routes: [] });
    expect(screen.queryByRole('link', { name: 'Admin' })).toBeNull();
    a.unmount();
    await openConsole({ role: 'nurse', hash: '/', routes: [] });
    expect(screen.queryByRole('link', { name: 'Admin' })).toBeNull();
  });
});

describe('front desk supervisor', () => {
  it('sees the task inbox and nothing else in the menu (besides Home), and the role has a name', async () => {
    const task = { acknowledged_at: null, acknowledged_by: null, assigned_role: 'front_desk_supervisor', clinic_id: CLINIC_A, created_at: '2026-11-13T05:00:00Z', kind: 'identity_confirmation_revoked', priority: 'standard', resolvable: true, resolved_at: null, status: 'open', task_id: ID.task };
    await openConsole({ role: 'front_desk_supervisor', hash: '/tasks', routes: [['get', '/v1/staff/review-tasks', () => list([task])]] });
    expect(await screen.findByText('Identity confirmation revoked')).toBeInTheDocument();
    expect(screen.getByText('Front desk supervisor', { selector: 'td' })).toBeInTheDocument();
    const nav = screen.getByRole('navigation', { name: /sections|main/i });
    expect(within(nav).getAllByRole('link').map((l) => l.textContent)).toEqual(['Home', 'Tasks']);
  });
});

describe('the new alert class', () => {
  it('has a plain label in English and Tamil', async () => {
    const row = { acknowledged: false, alert_class: 'identity_revoked_suspect', alert_id: ID.doc, audience_role: 'admin', clinic_id: CLINIC_A, count: 1, created_at: '2026-11-13T05:00:00Z', ref: ID.patient };
    const a = await openConsole({ role: 'admin', hash: '/alerts', routes: [['get', '/v1/staff/console/alerts', () => list([row])]] });
    expect(await screen.findByText('An identity confirmation was revoked as suspect')).toBeInTheDocument();
    a.unmount();
    await openConsole({ role: 'admin', hash: '/alerts', lang: 'ta', routes: [['get', '/v1/staff/console/alerts', () => list([row])]] });
    expect(await screen.findByText('அடையாள உறுதிப்படுத்தல் சந்தேகத்தின் பேரில் திரும்பப் பெறப்பட்டது')).toBeInTheDocument();
  });
});

describe('revoke result: the admin alert word', () => {
  const status = { clinic_id: CLINIC_A, latest_identity_event: 'confirm', latest_identity_event_at: '2026-10-30T05:00:00Z', merged_into: null, patient_id: ID.patient, registered_via: 'front_desk', verified: true };
  it.each([
    ['raised', 'An alert was sent to the administrators.'],
    ['not_for_this_reason', 'No administrator alert was raised for this reason.'],
    ['not_configured', 'No administrator alert was raised: the alert settings are not configured yet.'],
  ])('says %s next to the review task word', async (alertWord, text) => {
    const user = userEvent.setup();
    await openConsole({
      hash: `/patients/${ID.patient}`,
      routes: [['get', `/v1/staff/patients/${ID.patient}`, () => json(status)], ['post', `/v1/staff/patients/${ID.patient}/revoke-identity`, () => json({ admin_alert: alertWord, confirmed: false, patient_id: ID.patient, replayed: false, review_task: 'raised' })]],
    });
    await user.click(await screen.findByRole('button', { name: 'Revoke confirmation' }));
    const dialog = screen.getByRole('dialog');
    await user.click(within(dialog).getByRole('radio', { name: 'Planted or suspect' }));
    await user.click(within(dialog).getByRole('button', { name: 'Revoke confirmation' }));
    expect(await screen.findByText('A review task was raised.')).toBeInTheDocument();
    expect(screen.getByText(text)).toBeInTheDocument();
  });

  it('says nothing about an alert word it does not know, and nothing new on a replay', async () => {
    const user = userEvent.setup();
    await openConsole({
      hash: `/patients/${ID.patient}`,
      routes: [['get', `/v1/staff/patients/${ID.patient}`, () => json(status)], ['post', `/v1/staff/patients/${ID.patient}/revoke-identity`, () => json({ admin_alert: 'something_new', confirmed: false, patient_id: ID.patient, replayed: false, review_task: 'raised' })]],
    });
    await user.click(await screen.findByRole('button', { name: 'Revoke confirmation' }));
    const dialog = screen.getByRole('dialog');
    await user.click(within(dialog).getByRole('radio', { name: 'Planted or suspect' }));
    await user.click(within(dialog).getByRole('button', { name: 'Revoke confirmation' }));
    await screen.findByText('A review task was raised.');
    expect(screen.queryByText(/administrator/i)).toBeNull();
  });
});
