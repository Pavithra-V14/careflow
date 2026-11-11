import { describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ACCOUNT_ID, GOOD_CODE, json } from './helpers/fakes';
import { ID, LEAK, fail, list, openConsole, writes, type Routes } from './helpers/console';

// MOCK-ONLY. The erasure write buttons are OFF in every real build (tests/flags.test.ts). This file switches the flag ON for the unit
// environment only, to prove how the screen behaves once the step-up claim spike is recorded: confirmation first, what is erased and
// what is kept, one idempotency key per intent, a different person to approve, and the step-up prompt. The reasons list has no route
// yet, so it is faked here through the single seam (erasureReasons.ts).
const h = vi.hoisted(() => ({ reasons: null as readonly string[] | null }));
vi.mock('../src/staff/featureFlags', async (original) => ({ ...(await original<typeof import('../src/staff/featureFlags')>()), erasureActionsEnabled: () => true }));
vi.mock('../src/staff/pages/erasureReasons', () => ({ useErasureReasons: () => h.reasons }));

const req = (over: Record<string, unknown> = {}) => ({ approved_by: null, created_at: '2026-11-10T04:00:00Z', decided_at: null, patient_id: ID.patient, reason: 'reason_x', request_id: ID.task, requested_by: ID.user, status: 'requested', ...over });
const listRoute = (...rows: unknown[]): Routes[number] => ['get', '/v1/staff/erasure-requests', () => list(rows)];
const approvePath = `/v1/staff/erasure-requests/${ID.task}/approve`;
const cancelPath = `/v1/staff/erasure-requests/${ID.task}/cancel`;
const stepUp = () => json({ detail: 'step-up required' }, 401, { 'WWW-Authenticate': 'step-up' });

describe('erasure: approve and cancel', () => {
  it('offers no approve to the person who asked, but does offer cancel', async () => {
    await openConsole({ hash: '/erasure', routes: [listRoute(req({ requested_by: ACCOUNT_ID }))] });
    await screen.findByRole('button', { name: /Cancel the request/ });
    expect(screen.queryByRole('button', { name: /^Approve/ })).toBeNull();
    expect(screen.getByText('A different person must approve.')).toBeInTheDocument();
  });

  it('asks first, says what is erased and what is kept, then approves once with a key', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ hash: '/erasure', routes: [listRoute(req()), ['post', approvePath, () => json({ erased: true, request_id: ID.task })]] });
    await user.click(await screen.findByRole('button', { name: /^Approve/ }));
    const dialog = screen.getByRole('dialog');
    expect(writes(calls)).toHaveLength(0);
    expect(within(dialog).getByText(/removes the patient's encrypted name, date of birth and phone number/)).toBeInTheDocument();
    expect(within(dialog).getByText(/keeps the request and its audit rows/)).toBeInTheDocument();
    expect(within(dialog).getByText(/recent authenticator code/)).toBeInTheDocument();
    expect(dialog.querySelector('[data-placeholder]')).not.toBeNull(); // the wording is marked as awaiting the privacy owner
    await user.click(within(dialog).getByRole('button', { name: 'Approve' }));
    expect(await screen.findByText('Approved. The identity was erased.')).toBeInTheDocument();
    expect(writes(calls)).toHaveLength(1);
    expect(writes(calls)[0]?.key).toBeTruthy();
  });

  it('never says "erased" when the route says nothing was erased', async () => {
    const user = userEvent.setup();
    await openConsole({ hash: '/erasure', routes: [listRoute(req()), ['post', approvePath, () => json({ erased: false, request_id: ID.task })]] });
    await user.click(await screen.findByRole('button', { name: /^Approve/ }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Approve' }));
    expect(await screen.findByText(/Nothing new was done/)).toBeInTheDocument();
    expect(screen.queryByText('Approved. The identity was erased.')).toBeNull();
  });

  it('prompts for a fresh code on a step-up 401, then retries ONCE with the same key', async () => {
    const user = userEvent.setup();
    let n = 0;
    const { calls, fake } = await openConsole({ hash: '/erasure', routes: [listRoute(req()), ['post', approvePath, () => (++n === 1 ? stepUp() : json({ erased: true, request_id: ID.task }))]] });
    await user.click(await screen.findByRole('button', { name: /^Approve/ }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Approve' }));
    const prompt = await screen.findByRole('dialog', { name: 'Confirm it is you' });
    const before = fake.currentToken();
    await user.type(within(prompt).getByLabelText('6-digit code'), GOOD_CODE);
    await user.click(within(prompt).getByRole('button', { name: 'Confirm' }));
    await waitFor(() => expect(writes(calls)).toHaveLength(2));
    const [first, second] = writes(calls);
    expect(second?.key).toBe(first?.key);
    expect(second?.auth).not.toBe(`Bearer ${before}`);
    expect(await screen.findByText('Approved. The identity was erased.')).toBeInTheDocument();
  });

  it('changes nothing and does not retry when the step-up prompt is cancelled', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ hash: '/erasure', routes: [listRoute(req()), ['post', approvePath, () => stepUp()]] });
    await user.click(await screen.findByRole('button', { name: /^Approve/ }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Approve' }));
    const prompt = await screen.findByRole('dialog', { name: 'Confirm it is you' });
    await user.click(within(prompt).getByRole('button', { name: 'Cancel' }));
    expect(await screen.findByText('A fresh authenticator code is needed to do this. Nothing was changed.')).toBeInTheDocument();
    expect(writes(calls)).toHaveLength(1);
  });

  it.each([
    [403, /You cannot approve this request/],
    [409, /This cannot be approved now/],
  ])('answers %i in fixed words and never shows the backend text', async (code, text) => {
    const user = userEvent.setup();
    await openConsole({ hash: '/erasure', routes: [listRoute(req()), ['post', approvePath, () => fail(code)]] });
    await user.click(await screen.findByRole('button', { name: /^Approve/ }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Approve' }));
    expect(await screen.findByText(text)).toBeInTheDocument();
    expect(screen.queryByText(LEAK)).toBeNull();
  });

  it('cancels with a confirmation and a key, and says nothing is erased', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ hash: '/erasure', routes: [listRoute(req({ requested_by: ACCOUNT_ID })), ['post', cancelPath, () => json({ cancelled: true, request_id: ID.task })]] });
    await user.click(await screen.findByRole('button', { name: /Cancel the request/ }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('Cancelling closes the request. Nothing is erased.')).toBeInTheDocument();
    expect(writes(calls)).toHaveLength(0);
    await user.click(within(dialog).getByRole('button', { name: 'Cancel the request' }));
    expect(await screen.findByText('The request was cancelled.')).toBeInTheDocument();
    expect(writes(calls)[0]?.key).toBeTruthy();
  });

  it('offers no buttons on a request that is already decided', async () => {
    await openConsole({ hash: '/erasure', routes: [listRoute(req({ status: 'erased', decided_at: '2026-11-10T05:00:00Z', approved_by: ID.user2 }), req({ request_id: ID.task2, status: 'cancelled' }))] });
    await screen.findAllByRole('row');
    expect(screen.queryByRole('button', { name: /^Approve/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Cancel the request/ })).toBeNull();
  });
});

describe('erasure: ask for one', () => {
  const searchRoute: Routes[number] = ['post', '/v1/staff/patients/search', () => json({ items: [{ patient_id: ID.patient, verified: true, registered_via: 'front_desk', matched_kinds: ['name_dob'] }], labels_withheld: false, truncated: false })];
  const askPath = `/v1/staff/patients/${ID.patient}/erasure-requests`;

  it('shows the calm "not set up" state, with no way to submit, while the reasons list has no route', async () => {
    h.reasons = null;
    const { calls } = await openConsole({ hash: '/erasure', routes: [listRoute()] });
    expect(await screen.findByText(/list of erasure reasons is not set up yet/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Ask for erasure' })).toBeNull();
    expect(writes(calls)).toHaveLength(0);
  });

  it('with a reasons list: nothing is pre-selected, it asks first, and sends the chosen reason once with a key', async () => {
    h.reasons = ['reason_x', 'reason_y'];
    const user = userEvent.setup();
    const { calls } = await openConsole({ hash: '/erasure', routes: [listRoute(), searchRoute, ['post', askPath, () => json({ patient_id: ID.patient, request_id: ID.task, status: 'requested' })]] });
    await screen.findByText('No requests.');
    const ask = screen.getByRole('button', { name: 'Ask for erasure' });
    expect(ask).toBeDisabled();
    expect(screen.getAllByRole('radio').filter((r) => (r as HTMLInputElement).checked)).toHaveLength(0);
    await user.type(screen.getByLabelText('Full name'), 'Test Person');
    await user.type(screen.getByLabelText('Date of birth'), '1990-02-03');
    await user.click(screen.getByRole('button', { name: 'Find' }));
    await user.click(await screen.findByRole('radio', { name: /^(?!reason)/ }));
    expect(ask).toBeDisabled(); // a patient alone is not enough
    await user.click(screen.getByRole('radio', { name: 'reason y' }));
    await user.click(ask);
    const dialog = await screen.findByRole('dialog');
    expect(writes(calls).filter((c) => !c.path.endsWith('/search'))).toHaveLength(0); // the patient search is a POST but a read
    expect(within(dialog).getByText(/removes the patient's encrypted name/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Ask for erasure' }));
    expect(await screen.findByText('The request was made. A different front desk person must approve it.')).toBeInTheDocument();
    const w = writes(calls).find((c) => c.path === askPath);
    expect(w?.path).toBe(askPath);
    expect(w?.body).toEqual({ reason: 'reason_y' });
    expect(w?.key).toBeTruthy();
  });

  it.each([
    [409, /already has an open request/],
    [422, /not on the clinic's list/],
    [503, /list of erasure reasons is not set up yet/],
  ])('answers %i in fixed words', async (code, text) => {
    h.reasons = ['reason_x'];
    const user = userEvent.setup();
    await openConsole({ hash: '/erasure', routes: [listRoute(), searchRoute, ['post', askPath, () => fail(code)]] });
    await screen.findByText('No requests.');
    await user.type(screen.getByLabelText('Full name'), 'Test Person');
    await user.type(screen.getByLabelText('Date of birth'), '1990-02-03');
    await user.click(screen.getByRole('button', { name: 'Find' }));
    const radios = await screen.findAllByRole('radio');
    await user.click(radios[0] as HTMLElement);
    await user.click(screen.getByRole('radio', { name: 'reason x' }));
    await user.click(screen.getByRole('button', { name: 'Ask for erasure' }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Ask for erasure' }));
    expect(await within(dialog).findByText(text)).toBeInTheDocument();
    expect(screen.queryByText(LEAK)).toBeNull();
  });
});
