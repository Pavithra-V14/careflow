import { describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CLINIC_A, json } from './helpers/fakes';
import { ID, LEAK, fail, list, openConsole, writes, type Routes } from './helpers/console';

// MOCK-ONLY (see helpers/console.tsx): the front-desk screen for patients' requests about their own record.
const REQ = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd';
const REQ2 = 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee';
const ERA = 'ffffffff-ffff-4fff-8fff-ffffffffffff';
const row = (over: Record<string, unknown> = {}) => ({ created_at: '2026-11-09T05:00:00Z', erasure_request_id: null, field: null, kind: 'erasure', patient_id: ID.patient, request_id: REQ, status: 'open', ...over });
const requests = (rows: unknown[] = [row(), row({ request_id: REQ2, kind: 'correction', field: 'phone', status: 'acknowledged' }), row({ request_id: ID.doc, status: 'resolved', kind: 'nominee' })]): Routes[number] => [
  'get',
  '/v1/staff/patient-requests',
  () => json({ requests: rows }),
];
const erasures: Routes[number] = ['get', '/v1/staff/erasure-requests', () => list([{ approved_by: null, created_at: '2026-11-09T05:00:00Z', decided_at: null, patient_id: ID.patient, reason: 'x', request_id: ERA, requested_by: ID.user, status: 'requested' }, { approved_by: null, created_at: '2026-11-09T05:00:00Z', decided_at: null, patient_id: ID.patient2, reason: 'x', request_id: ID.task, requested_by: ID.user, status: 'requested' }])];
const status = (path: string, result: string, code = 200): Routes[number] => ['post', path, () => json({ result }, code)];

describe('patient requests (front desk)', () => {
  it('lists the requests with closed words, offers only the next statuses, and asks the clinic of the active role', async () => {
    const { calls } = await openConsole({ hash: '/requests', routes: [requests()] });
    expect(await screen.findByRole('heading', { name: 'Patient requests' })).toBeInTheDocument();
    const rows = within(await screen.findByRole('table')).getAllByRole('row');
    expect(rows).toHaveLength(4);
    expect(within(rows[1]!).getAllByRole('button').map((b) => b.textContent)).toEqual(['Mark as seen', 'Resolve', 'Decline']);
    expect(within(rows[2]!).getAllByRole('button').map((b) => b.textContent)).toEqual(['Resolve', 'Decline']);
    expect(within(rows[3]!).queryAllByRole('button')).toHaveLength(0); // a final status offers nothing
    expect(within(rows[2]!).getByText('Phone number')).toBeInTheDocument(); // the field NAME, never a value
    expect(calls.find((c) => c.path === '/v1/staff/patient-requests')?.query.get('clinic_id')).toBe(CLINIC_A);
  });

  it('changes a status only after a confirmation, with a key and no free text, and says the patient was told', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ hash: '/requests', routes: [requests([row({ kind: 'nominee' })]), status(`/v1/staff/patient-requests/${REQ}/status`, 'recorded')] });
    await user.click(await screen.findByRole('button', { name: /^Mark as seen/ }));
    expect(writes(calls)).toHaveLength(0);
    const dialog = screen.getByRole('dialog', { name: 'Mark this request as seen?' });
    expect(within(dialog).queryByLabelText(/Link an erasure request/)).toBeNull(); // only an erasure may be linked
    await user.click(within(dialog).getByRole('button', { name: 'Mark as seen' }));
    expect(await screen.findByText('Recorded. The patient has been told.')).toBeInTheDocument();
    expect(writes(calls)).toHaveLength(1);
    expect(writes(calls)[0]?.body).toEqual({ erasure_request_id: null, status: 'acknowledged' });
    expect(writes(calls)[0]?.key).toBeTruthy();
  });

  it('for an erasure, links nothing unless chosen, offers only this patient\'s waiting erasure requests, and sends the one chosen', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ hash: '/requests', routes: [requests([row()]), erasures, status(`/v1/staff/patient-requests/${REQ}/status`, 'recorded')] });
    await user.click(await screen.findByRole('button', { name: /^Resolve/ }));
    const dialog = screen.getByRole('dialog', { name: 'Resolve this request?' });
    const select = await within(dialog).findByLabelText('Link an erasure request you started for this patient');
    expect(select).toHaveValue('');
    expect(within(select).getAllByRole('option').map((o) => o.textContent)).toEqual(['Do not link one', ERA.slice(0, 8)]);
    await user.selectOptions(select, ERA);
    await user.click(within(dialog).getByRole('button', { name: 'Resolve' }));
    await screen.findByText('Recorded. The patient has been told.');
    expect(writes(calls)[0]?.body).toEqual({ erasure_request_id: ERA, status: 'resolved' });
  });

  it('cancelling writes nothing', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ hash: '/requests', routes: [requests([row({ kind: 'nominee' })])] });
    await user.click(await screen.findByRole('button', { name: /^Decline/ }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }));
    expect(writes(calls)).toHaveLength(0);
  });

  it.each([
    ['invalid_transition', 'That change is not allowed from the current status. Nothing was recorded.'],
    ['replay', 'This was already recorded.'],
  ])('tells a %s answer in fixed words', async (result, words) => {
    const user = userEvent.setup();
    await openConsole({ hash: '/requests', routes: [requests([row({ kind: 'nominee' })]), status(`/v1/staff/patient-requests/${REQ}/status`, result)] });
    await user.click(await screen.findByRole('button', { name: /^Decline/ }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Decline' }));
    expect(await screen.findByText(words)).toBeInTheDocument();
  });

  it.each([
    [403, 'This is not available to your role.'],
    [404, 'This request was not found. Nothing was recorded.'],
    [422, 'That change is not allowed from the current status. Nothing was recorded.'],
  ])('tells an error %s in fixed words and never the backend text', async (code, words) => {
    const user = userEvent.setup();
    await openConsole({ hash: '/requests', routes: [requests([row({ kind: 'nominee' })]), ['post', `/v1/staff/patient-requests/${REQ}/status`, () => fail(code)]] });
    await user.click(await screen.findByRole('button', { name: /^Decline/ }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Decline' }));
    expect(await within(screen.getByRole('dialog')).findByText(words)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('has an empty state, and is not available to another role (403 on the read)', async () => {
    const a = await openConsole({ hash: '/requests', routes: [requests([])] });
    expect(await screen.findByText('There are no patient requests.')).toBeInTheDocument();
    a.unmount();
    await openConsole({ role: 'nurse', hash: '/requests', routes: [['get', '/v1/staff/patient-requests', () => fail(403)]] });
    await screen.findByText('This is not available to your role.');
  });

  it('is in Tamil', async () => {
    await openConsole({ hash: '/requests', lang: 'ta', routes: [requests()] });
    expect(await screen.findByRole('heading', { name: 'நோயாளி கோரிக்கைகள்' })).toBeInTheDocument();
  });
});
