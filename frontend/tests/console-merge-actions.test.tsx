import { describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ACCOUNT_ID, CLINIC_A, GOOD_CODE, json } from './helpers/fakes';
import { ID, LEAK, fail, openConsole, writes, type Routes } from './helpers/console';

// MOCK-ONLY. Merge approve and unmerge are OFF in every real build (tests/flags.test.ts). This file switches the flag ON for
// the unit environment only, to prove how the buttons behave once the step-up claim spike has been recorded and the flag is
// turned on: confirmation first, one idempotency key per intent, and the step-up prompt and single retry.
vi.mock('../src/staff/featureFlags', async (original) => ({ ...(await original<typeof import('../src/staff/featureFlags')>()), mergeActionsEnabled: () => true }));

const merge = (over: Record<string, unknown> = {}) => ({
  activated_at: null,
  candidate_id: ID.task2,
  clinic_id: CLINIC_A,
  created_at: '2026-10-30T05:00:00Z',
  master_patient_id: ID.patient,
  merge_id: ID.hold,
  proposed_by: ID.user,
  second_approved_by: null,
  status: 'awaiting_second_approval',
  subordinate_patient_id: ID.patient2,
  unmerged_at: null,
  unmerged_by: null,
  ...over,
});
const detail = (over: Record<string, unknown> = {}): Routes[number] => ['get', `/v1/staff/identity/merges/${ID.hold}`, () => json(merge(over))];
const approvePath = `/v1/staff/merges/${ID.hold}/approve`;
const unmergePath = `/v1/staff/merges/${ID.hold}/unmerge`;
const stepUp = () => json({ detail: 'step-up required' }, 401, { 'WWW-Authenticate': 'step-up' });
const merged = (status: string) => json({ master_patient_id: ID.patient, merge_id: ID.hold, replayed: false, status, subordinate_patient_id: ID.patient2 });
const hash = `/identity/merges/${ID.hold}`;

describe('merge approve and unmerge (flag on in this file only)', () => {
  it('approves a merge proposed by someone else, only after a confirmation, with one key', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ hash, routes: [detail(), ['post', approvePath, () => merged('active')]] });
    const approve = await screen.findByRole('button', { name: 'Approve' });
    expect(writes(calls)).toHaveLength(0);
    await user.click(approve);
    const dialog = screen.getByRole('dialog', { name: 'Approve this merge?' });
    expect(dialog).toHaveTextContent('fresh authenticator code');
    expect(writes(calls)).toHaveLength(0);
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(writes(calls)).toHaveLength(0);

    await user.click(screen.getByRole('button', { name: 'Approve' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Approve' }));
    await waitFor(() => expect(writes(calls)).toHaveLength(1));
    expect(writes(calls)[0]).toMatchObject({ path: approvePath, body: undefined });
    expect(writes(calls)[0]?.key).toBeTruthy();
    expect(await screen.findByText('The merge was approved.')).toBeInTheDocument();
  });

  it('prompts for a fresh code on a step-up 401, then retries ONCE with the same key and the new token', async () => {
    const user = userEvent.setup();
    let n = 0;
    const { calls, fake } = await openConsole({ hash, routes: [detail(), ['post', approvePath, () => (++n === 1 ? stepUp() : merged('active'))]] });
    await user.click(await screen.findByRole('button', { name: 'Approve' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Approve' }));

    const prompt = await screen.findByRole('dialog', { name: 'Confirm it is you' });
    const before = fake.currentToken();
    await user.type(within(prompt).getByLabelText('6-digit code'), GOOD_CODE);
    await user.click(within(prompt).getByRole('button', { name: 'Confirm' }));

    await waitFor(() => expect(writes(calls)).toHaveLength(2));
    const [first, second] = writes(calls);
    expect(second?.key).toBe(first?.key);
    expect(second?.auth).toBe(`Bearer ${fake.currentToken()}`);
    expect(second?.auth).not.toBe(`Bearer ${before}`);
    expect(await screen.findByText('The merge was approved.')).toBeInTheDocument();
  });

  it('says a fresh code is needed, and changes nothing, when the step-up prompt is cancelled; it never says "sign in again"', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ hash, routes: [detail(), ['post', approvePath, () => stepUp()]] });
    await user.click(await screen.findByRole('button', { name: 'Approve' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Approve' }));
    const prompt = await screen.findByRole('dialog', { name: 'Confirm it is you' });
    await user.click(within(prompt).getByRole('button', { name: 'Cancel' }));
    expect(await screen.findByText('A fresh authenticator code is needed to do this. Nothing was changed.')).toBeInTheDocument();
    expect(writes(calls)).toHaveLength(1); // no retry
    expect(screen.queryByText('Please sign in again.')).toBeNull();
    expect(screen.getByRole('group', { name: 'Session' })).toBeInTheDocument(); // still signed in
  });

  it('offers no approve to the person who proposed the merge, but does offer unmerge', async () => {
    await openConsole({ hash, routes: [detail({ proposed_by: ACCOUNT_ID })] });
    await screen.findByRole('button', { name: 'Unmerge' });
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
    expect(screen.getByText('A different person from the one who proposed this merge must approve it.')).toBeInTheDocument();
  });

  it('unmerges an active merge and reports the flagged decisions only when there are some', async () => {
    const user = userEvent.setup();
    let flagged = 2;
    const route: Routes[number] = ['post', unmergePath, () => json({ decisions_flagged: flagged, merge_id: ID.hold, replayed: false, review_task_id: null, status: 'unmerged' })];
    const a = await openConsole({ hash, routes: [detail({ status: 'active', second_approved_by: ID.user2 }), route] });
    await user.click(await screen.findByRole('button', { name: 'Unmerge' }));
    await user.click(within(screen.getByRole('dialog', { name: 'Unmerge these records?' })).getByRole('button', { name: 'Unmerge' }));
    expect(await screen.findByText('The link was broken. 2 earlier decisions were flagged for review.')).toBeInTheDocument();
    a.unmount();
    flagged = 0;
    await openConsole({ hash, routes: [detail({ status: 'active' }), route] });
    await user.click(await screen.findByRole('button', { name: 'Unmerge' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Unmerge' }));
    expect(await screen.findByText('The link was broken.')).toBeInTheDocument();
  });

  it('offers nothing on a merge that is already unmerged, and nothing at all to a role that cannot act', async () => {
    const a = await openConsole({ hash, routes: [detail({ status: 'unmerged' })] });
    await screen.findByText('None');
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Unmerge' })).toBeNull();
    a.unmount();
    await openConsole({ role: 'nurse', hash, routes: [detail()] });
    await screen.findByText('This is not available to your role.');
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
  });

  it('retries a failed approve with the same key, and shows 409 and 403 in fixed words', async () => {
    const user = userEvent.setup();
    const answers = [503, 409, 403];
    const { calls } = await openConsole({ hash, routes: [detail(), ['post', approvePath, () => fail(answers.shift() ?? 403)]] });
    await user.click(await screen.findByRole('button', { name: 'Approve' }));
    const go = () => within(screen.getByRole('dialog')).getByRole('button', { name: 'Approve' });
    await user.click(go());
    expect(await within(screen.getByRole('dialog')).findByRole('alert')).toHaveTextContent('This is not available right now.');
    await user.click(go());
    expect(await within(screen.getByRole('dialog')).findByRole('alert')).toHaveTextContent('That approval was refused.');
    await user.click(go());
    expect(await within(screen.getByRole('dialog')).findByText('This is not available to your role.')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
    const keys = writes(calls).map((c) => c.key);
    expect(keys[1]).toBe(keys[0]); // retry after 503
    expect(keys[2]).not.toBe(keys[1]); // after a 409 it is a new request
  });
});
