import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CLINIC_A, json } from './helpers/fakes';
import { ID, LEAK, fail, list, openConsole, writes, type Routes } from './helpers/console';
import { urgentFirst } from '../src/staff/pages/TasksPage';

// MOCK-ONLY (see helpers/console.tsx).

const task = (over: Record<string, unknown> = {}) => ({
  acknowledged_at: null,
  acknowledged_by: null,
  assigned_role: 'front_desk',
  clinic_id: CLINIC_A,
  created_at: '2026-10-30T05:00:00Z',
  kind: 'referral_hold',
  priority: 'standard',
  resolved_at: null,
  status: 'open',
  task_id: ID.task,
  ...over,
});
const detail = (over: Record<string, unknown> = {}) => ({ ...task(), provenance: { document_id: ID.doc, reason: 'needs_front_desk' }, source_message_id: ID.doc2, ...over });
const detailRoute = (over: Record<string, unknown> = {}, id = ID.task): Routes[number] => ['get', `/v1/staff/review-tasks/${id}`, () => json(detail({ task_id: id, ...over }))];

describe('urgentFirst', () => {
  it('puts urgent kinds and immediate tasks first and keeps every other order', () => {
    const item = (kind: string, priority: 'immediate' | 'standard', n: number) => ({ kind, priority, n });
    const items = [item('referral_hold', 'standard', 1), item('screening_unassessable', 'standard', 2), item('referral_hold', 'immediate', 3), item('unmerge_review', 'standard', 4), item('red_flag', 'immediate', 5)];
    expect(urgentFirst(items).map((i) => i.n)).toEqual([2, 3, 5, 1, 4]);
  });
});

describe('task inbox', () => {
  it('shows urgent tasks first, marked with the word Urgent and a distinct row, and never sorts a routine task above one', async () => {
    await openConsole({
      role: 'nurse',
      hash: '/tasks',
      routes: [['get', '/v1/staff/review-tasks', () => list([task({ task_id: ID.task, kind: 'referral_hold' }), task({ task_id: ID.task2, kind: 'screening_unassessable', assigned_role: 'nurse' }), task({ task_id: ID.task3, kind: 'red_flag', priority: 'immediate', assigned_role: 'nurse' })])]],
    });
    const rows = (await screen.findAllByRole('row')).slice(1);
    expect(rows.map((r) => r.querySelector('code')?.textContent)).toEqual(['44444444…', '55555555…', '33333333…']);
    expect(rows[0]).toHaveClass('task-urgent');
    expect(rows[1]).toHaveClass('task-urgent');
    expect(rows[2]).not.toHaveClass('task-urgent');
    expect(within(rows[0] as HTMLElement).getByText('Urgent')).toBeInTheDocument();
    expect(within(rows[1] as HTMLElement).getByText('Red flag')).toBeInTheDocument();
    expect(within(rows[0] as HTMLElement).getByText('Unassessable')).toBeInTheDocument();
    expect(within(rows[2] as HTMLElement).queryByText('Urgent')).toBeNull();
  });

  it('asks for open tasks first and switches the filter by keyboard', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ hash: '/tasks', routes: [['get', '/v1/staff/review-tasks', () => list([])]] });
    await screen.findByText('There are no tasks to show.');
    expect(calls.find((c) => c.path === '/v1/staff/review-tasks')?.query.get('status')).toBe('open');
    const tab = screen.getByRole('button', { name: 'Acknowledged' });
    expect(tab).toHaveAttribute('aria-pressed', 'false');
    tab.focus();
    await user.keyboard('{Enter}');
    await waitFor(() => expect(calls.filter((c) => c.path === '/v1/staff/review-tasks').map((c) => c.query.get('status'))).toEqual(['open', 'acknowledged']));
    expect(screen.getByRole('button', { name: 'Acknowledged' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('shows empty, error and "not available to your role" states', async () => {
    const first = await openConsole({ hash: '/tasks', routes: [['get', '/v1/staff/review-tasks', () => fail(500)]] });
    expect(await screen.findByRole('alert')).toHaveTextContent('Something went wrong. Try again.');
    expect(document.body.textContent).not.toContain(LEAK);
    first.unmount();
    await openConsole({ role: 'admin', hash: '/tasks', routes: [['get', '/v1/staff/review-tasks', () => fail(403)]] });
    await screen.findByText('This is not available to your role.');
    expect(screen.queryByRole('alert')).toBeNull();
  });
});

describe('task detail', () => {
  it('opens a task and does NOT change anything by itself: only reads happen until a person confirms', async () => {
    const { calls } = await openConsole({ role: 'nurse', hash: `/tasks/${ID.task}`, routes: [detailRoute()] });
    expect(await screen.findByRole('button', { name: 'Acknowledge' })).toBeInTheDocument();
    expect(writes(calls)).toHaveLength(0);
    expect(screen.getByRole('link', { name: 'Open the referral' })).toHaveAttribute('href', `#/referrals/${ID.doc}`);
    expect(screen.getByText('needs_front_desk')).toBeInTheDocument(); // closed word from provenance, shown as given
  });

  it('acknowledges only after a confirmation, with one idempotency key and no body', async () => {
    const user = userEvent.setup();
    const ack: Routes[number] = ['post', `/v1/staff/review-tasks/${ID.task}/acknowledge`, () => json({ replayed: false, status: 'acknowledged', task_id: ID.task })];
    const { calls } = await openConsole({ role: 'nurse', hash: `/tasks/${ID.task}`, routes: [detailRoute(), ack] });
    await user.click(await screen.findByRole('button', { name: 'Acknowledge' }));
    const dialog = screen.getByRole('dialog', { name: 'Acknowledge this task?' });
    expect(writes(calls)).toHaveLength(0);

    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(writes(calls)).toHaveLength(0);

    await user.click(screen.getByRole('button', { name: 'Acknowledge' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Acknowledge' }));
    await waitFor(() => expect(writes(calls)).toHaveLength(1));
    expect(writes(calls)[0]?.key).toBeTruthy();
    expect(writes(calls)[0]?.body).toBeUndefined();
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('retries a failed acknowledge with the SAME key, and uses a new key for a new attempt after a 409', async () => {
    const user = userEvent.setup();
    const answers = [503, 409, 200];
    const ack: Routes[number] = ['post', `/v1/staff/review-tasks/${ID.task}/acknowledge`, () => {
      const status = answers.shift() ?? 200;
      return status === 200 ? json({ replayed: false, status: 'acknowledged', task_id: ID.task }) : fail(status);
    }];
    const { calls } = await openConsole({ role: 'nurse', hash: `/tasks/${ID.task}`, routes: [detailRoute(), ack] });
    await user.click(await screen.findByRole('button', { name: 'Acknowledge' }));
    const confirm = () => within(screen.getByRole('dialog')).getByRole('button', { name: 'Acknowledge' });

    await user.click(confirm());
    expect(await within(screen.getByRole('dialog')).findByRole('alert')).toHaveTextContent('This is not available right now.');
    await user.click(confirm()); // retry
    expect(await within(screen.getByRole('dialog')).findByRole('alert')).toHaveTextContent('Someone else holds this task, or it is already closed.');
    expect(document.body.textContent).not.toContain(LEAK);
    await user.click(confirm()); // after a definite refusal this is a new request
    await waitFor(() => expect(writes(calls)).toHaveLength(3));
    const [a, b, c] = writes(calls).map((w) => w.key);
    expect(a).toBe(b); // retry after 503: same key, so the server replays instead of repeating
    expect(c).not.toBe(b); // after the 409 answer: a fresh key
  });

  it.each(['red_flag', 'screening_unassessable'])('offers NO resolve for a %s task, only a marked placeholder, but still lets a nurse acknowledge it', async (kind) => {
    await openConsole({ role: 'nurse', hash: `/tasks/${ID.task}`, routes: [detailRoute({ kind, priority: 'immediate', assigned_role: 'nurse' })] });
    expect(await screen.findByRole('button', { name: 'Acknowledge' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Resolve' })).toBeNull();
    expect(screen.getByText(/PLACEHOLDER - clinician to define how this kind of task is closed/)).toHaveAttribute('data-placeholder');
    expect(screen.getByText('Urgent')).toBeInTheDocument();
  });

  it('offers no resolve for a triage proposal and says it is closed through its own screen', async () => {
    await openConsole({ role: 'nurse', hash: `/tasks/${ID.task}`, routes: [detailRoute({ kind: 'triage_proposal', assigned_role: 'nurse' })] });
    await screen.findByRole('button', { name: 'Acknowledge' });
    expect(screen.queryByRole('button', { name: 'Resolve' })).toBeNull();
    expect(screen.getByText('This kind of task is closed through its own screen, not here.')).toBeInTheDocument();
  });

  it('resolves a referral hold only after a confirmation that preselects nothing and collects no reason; reason codes are a marked placeholder', async () => {
    const user = userEvent.setup();
    const resolve: Routes[number] = ['post', `/v1/staff/review-tasks/${ID.task}/resolve`, () => json({ replayed: false, status: 'resolved', task_id: ID.task })];
    const { calls } = await openConsole({ hash: `/tasks/${ID.task}`, routes: [detailRoute({ status: 'acknowledged' }), resolve] });
    await user.click(await screen.findByRole('button', { name: 'Resolve' }));
    const dialog = screen.getByRole('dialog', { name: 'Resolve this task?' });
    expect(within(dialog).getByText('PLACEHOLDER - clinician to define')).toHaveAttribute('data-placeholder');
    expect(dialog.querySelectorAll('input, select, textarea')).toHaveLength(0); // nothing to pre-select, nothing collected
    expect(writes(calls)).toHaveLength(0);

    await user.click(within(dialog).getByRole('button', { name: 'Resolve' }));
    await waitFor(() => expect(writes(calls)).toHaveLength(1));
    expect(writes(calls)[0]).toMatchObject({ path: `/v1/staff/review-tasks/${ID.task}/resolve`, body: undefined });
    expect(writes(calls)[0]?.key).toBeTruthy();
  });

  it('shows a closed task with no actions', async () => {
    await openConsole({ hash: `/tasks/${ID.task}`, routes: [detailRoute({ status: 'resolved', resolved_at: '2026-10-30T06:00:00Z' })] });
    expect(await screen.findByText('This task is closed.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Acknowledge' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Resolve' })).toBeNull();
  });

  it('shows "you" for a task you acknowledged, and shows only plain provenance values', async () => {
    await openConsole({
      role: 'nurse',
      hash: `/tasks/${ID.task}`,
      routes: [detailRoute({ status: 'acknowledged', acknowledged_by: '9d0a1c2d-3e4f-4a6b-8c1e-3f2b6a4d4e1b', acknowledged_at: '2026-10-30T05:30:00Z', provenance: { rule_id: 'rule-1', nested: { secret: 'x' }, list: [1, 2] } })],
    });
    expect(await screen.findByText(/^You/)).toBeInTheDocument();
    expect(screen.getByText('rule-1')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('secret');
  });

  it('shows not-available on a 403 from the server, a fixed message on 404, and not-found for a malformed id', async () => {
    const user = userEvent.setup();
    const ack: Routes[number] = ['post', `/v1/staff/review-tasks/${ID.task}/acknowledge`, () => fail(403)];
    const a = await openConsole({ role: 'doctor', hash: `/tasks/${ID.task}`, routes: [detailRoute(), ack] });
    await user.click(await screen.findByRole('button', { name: 'Acknowledge' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Acknowledge' }));
    expect(await within(screen.getByRole('dialog')).findByText('This is not available to your role.')).toBeInTheDocument();
    a.unmount();
    const b = await openConsole({ hash: `/tasks/${ID.task}`, routes: [['get', `/v1/staff/review-tasks/${ID.task}`, () => fail(404)]] });
    expect(await screen.findByRole('alert')).toHaveTextContent('That could not be found.');
    b.unmount();
    const c = await openConsole({ hash: '/tasks/12345' });
    expect(screen.getByRole('heading', { level: 1, name: 'Not found' })).toBeInTheDocument();
    expect(c.calls.map((x) => x.path)).toEqual(['/v1/iam/me']);
  });
});
