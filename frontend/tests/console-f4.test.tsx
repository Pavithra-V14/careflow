import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CLINIC_A, json } from './helpers/fakes';
import { ID, LEAK, fail, list, openConsole, writes, type Routes } from './helpers/console';

// MOCK-ONLY (see helpers/console.tsx). Merge approve / unmerge are OFF in this build; see console-merge-actions.test.tsx.

// ---- referral review --------------------------------------------------------------------------------------------------------
const proposal = { abstained: false, candidates: [{ card_id: 'card-x', version: 3 }], created_at: '2026-10-30T05:00:00Z', mode: 'shadow', patient_id: ID.patient, proposal_id: ID.doc2, review_task_id: ID.task };
const detail = (over: Record<string, unknown> = {}) => ({
  clinic_id: CLINIC_A,
  content_type: 'application/pdf',
  created_at: '2026-10-30T05:00:00Z',
  decided: false,
  document_id: ID.doc,
  holds: [],
  open_holds: 0,
  proposal,
  proposal_id: ID.doc2,
  proposal_visible: true,
  registered_by: ID.user,
  run_state: 'awaiting_decision',
  size_bytes: 2048,
  ...over,
});
const detailRoute = (over: Record<string, unknown> = {}): Routes[number] => ['get', `/v1/staff/referrals/${ID.doc}`, () => json(detail(over))];
const reviewPath = `/v1/staff/referrals/${ID.doc}/review`;
const review = (answers: number[]): Routes[number] => ['post', reviewPath, (req) => {
  const s = answers.shift() ?? 200;
  const body = req.body as { decision: string };
  return s === 200 ? json({ decision: body.decision, document_id: ID.doc, replayed: false }) : fail(s);
}];

describe('referral review decision (nurse, doctor)', () => {
  it('chooses nothing for the person and writes nothing until they confirm', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ role: 'nurse', hash: `/referrals/${ID.doc}`, routes: [detailRoute(), review([])] });
    const group = await screen.findByRole('group', { name: 'Decision' });
    const radios = within(group).getAllByRole('radio');
    expect(radios.map((r) => (r as HTMLInputElement).value)).toEqual(['approve', 'edit', 'reject']);
    for (const r of radios) expect(r).not.toBeChecked();
    const record = screen.getByRole('button', { name: 'Record decision' });
    expect(record).toBeDisabled();
    expect(writes(calls)).toHaveLength(0);

    await user.click(within(group).getByRole('radio', { name: 'Reject' }));
    expect(writes(calls)).toHaveLength(0); // choosing is not recording
    await user.click(record);
    const dialog = screen.getByRole('dialog', { name: 'Record this decision?' });
    expect(dialog).toHaveTextContent('"Reject"');
    expect(writes(calls)).toHaveLength(0); // the dialog is not recording either

    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(writes(calls)).toHaveLength(0);

    await user.click(screen.getByRole('button', { name: 'Record decision' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Record decision' }));
    await waitFor(() => expect(writes(calls)).toHaveLength(1));
    expect(writes(calls)[0]).toMatchObject({ path: reviewPath, body: { decision: 'reject' } });
    expect(writes(calls)[0]?.key).toBeTruthy();
    expect(await screen.findByText('The decision was recorded.')).toBeInTheDocument();
  });

  it('offers approve and reject only: "edit" is shown as Coming soon, cannot be chosen, and is never sent', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ role: 'doctor', hash: `/referrals/${ID.doc}`, routes: [detailRoute(), review([])] });
    const group = await screen.findByRole('group', { name: 'Decision' });
    const radios = within(group).getAllByRole('radio') as HTMLInputElement[];
    expect(radios.filter((r) => !r.disabled).map((r) => r.value)).toEqual(['approve', 'reject']);
    const edit = radios.find((r) => r.value === 'edit') as HTMLInputElement;
    expect(edit).toBeDisabled();
    expect(edit.closest('label')).toHaveTextContent('Coming soon');

    await user.click(edit); // clicking a disabled option does nothing
    expect(edit).not.toBeChecked();
    expect(screen.getByRole('button', { name: 'Record decision' })).toBeDisabled();

    await user.click(within(group).getByRole('radio', { name: 'Approve' }));
    await user.click(screen.getByRole('button', { name: 'Record decision' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Record decision' }));
    await waitFor(() => expect(writes(calls)).toHaveLength(1));
    expect(JSON.stringify(writes(calls).map((c) => c.body))).not.toContain('edit');
  });

  it('shows the reasons only as a marked clinician placeholder, and collects and sends none', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ role: 'doctor', hash: `/referrals/${ID.doc}`, routes: [detailRoute(), review([])] });
    const placeholder = await screen.findByText('PLACEHOLDER - clinician to define');
    expect(placeholder).toHaveAttribute('data-placeholder');
    expect(screen.queryByRole('textbox')).toBeNull();
    expect(document.querySelectorAll('main textarea, main select')).toHaveLength(0);
    await user.click(screen.getByRole('radio', { name: 'Approve' }));
    await user.click(screen.getByRole('button', { name: 'Record decision' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Record decision' }));
    await waitFor(() => expect(writes(calls)).toHaveLength(1));
    expect(Object.keys(writes(calls)[0]?.body as object)).toEqual(['decision']);
  });

  it('keeps the success message after the referral refreshes to decided and the form disappears (regression)', async () => {
    const user = userEvent.setup();
    let decided = false;
    const get: Routes[number] = ['get', `/v1/staff/referrals/${ID.doc}`, () => json(detail({ decided }))];
    const post: Routes[number] = ['post', reviewPath, () => {
      decided = true; // the next read of the referral says a decision exists
      return json({ decision: 'approve', document_id: ID.doc, replayed: false });
    }];
    await openConsole({ role: 'nurse', hash: `/referrals/${ID.doc}`, routes: [get, post] });
    await user.click(await screen.findByRole('radio', { name: 'Approve' }));
    await user.click(screen.getByRole('button', { name: 'Record decision' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Record decision' }));
    await waitFor(() => expect(screen.queryByRole('group', { name: 'Decision' })).toBeNull()); // refreshed, the form is gone
    expect(screen.getByText('The decision was recorded.')).toBeInTheDocument();
    expect(screen.queryByText('A decision has already been recorded.')).toBeNull(); // not replaced by the generic note
  });

  it('retries a failed record with the SAME key, but a changed decision is a new request with a new key', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ role: 'nurse', hash: `/referrals/${ID.doc}`, routes: [detailRoute(), review([503, 503, 200])] });
    await user.click(await screen.findByRole('radio', { name: 'Approve' }));
    await user.click(screen.getByRole('button', { name: 'Record decision' }));
    const go = () => within(screen.getByRole('dialog')).getByRole('button', { name: 'Record decision' });
    await user.click(go());
    expect(await within(screen.getByRole('dialog')).findByRole('alert')).toHaveTextContent('This is not available right now.');
    await user.click(go()); // retry, same decision
    await waitFor(() => expect(writes(calls)).toHaveLength(2));
    expect(writes(calls)[1]?.key).toBe(writes(calls)[0]?.key);

    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }));
    await user.click(screen.getByRole('radio', { name: 'Reject' })); // a different decision
    await user.click(screen.getByRole('button', { name: 'Record decision' }));
    await user.click(go());
    await waitFor(() => expect(writes(calls)).toHaveLength(3));
    expect(writes(calls)[2]?.key).not.toBe(writes(calls)[0]?.key);
    expect(writes(calls)[2]?.body).toEqual({ decision: 'reject' });
  });

  it('says a conflict plainly, a 403 as "not available", and never shows backend text', async () => {
    const user = userEvent.setup();
    const answers = [409, 403];
    await openConsole({ role: 'nurse', hash: `/referrals/${ID.doc}`, routes: [detailRoute(), review(answers)] });
    await user.click(await screen.findByRole('radio', { name: 'Approve' }));
    await user.click(screen.getByRole('button', { name: 'Record decision' }));
    const go = () => within(screen.getByRole('dialog')).getByRole('button', { name: 'Record decision' });
    await user.click(go());
    expect(await within(screen.getByRole('dialog')).findByRole('alert')).toHaveTextContent('The referral is not waiting for a decision');
    await user.click(go());
    expect(await within(screen.getByRole('dialog')).findByText('This is not available to your role.')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('offers no form once a decision exists, and none to a role that cannot see the proposal', async () => {
    const a = await openConsole({ role: 'nurse', hash: `/referrals/${ID.doc}`, routes: [detailRoute({ decided: true })] });
    expect(await screen.findByText('A decision has already been recorded.')).toBeInTheDocument();
    expect(screen.queryByRole('group', { name: 'Decision' })).toBeNull();
    a.unmount();
    await openConsole({ role: 'front_desk', hash: `/referrals/${ID.doc}`, routes: [detailRoute({ proposal_visible: false, proposal: null, decided: null })] });
    await screen.findByText('Not shown to your role');
    expect(screen.queryByRole('group', { name: 'Decision' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Record decision' })).toBeNull();
  });
});

// ---- identity revoke --------------------------------------------------------------------------------------------------------
const status = (over: Record<string, unknown> = {}) => ({ clinic_id: CLINIC_A, latest_identity_event: 'confirm', latest_identity_event_at: '2026-10-30T05:00:00Z', merged_into: null, patient_id: ID.patient, registered_via: 'front_desk', verified: true, ...over });
const patientRoute = (over: Record<string, unknown> = {}): Routes[number] => ['get', `/v1/staff/patients/${ID.patient}`, () => json(status(over))];
const revokePath = `/v1/staff/patients/${ID.patient}/revoke-identity`;
const revoke = (answers: (number | string)[]): Routes[number] => ['post', revokePath, () => {
  const a = answers.shift() ?? 'raised';
  return typeof a === 'number' ? fail(a) : json({ confirmed: false, patient_id: ID.patient, replayed: false, review_task: a });
}];

describe('identity revoke (front desk)', () => {
  it('needs a reason chosen by the person, preselects none, and writes nothing before the confirmation', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ hash: `/patients/${ID.patient}`, routes: [patientRoute(), revoke(['raised'])] });
    await user.click(await screen.findByRole('button', { name: 'Revoke confirmation' }));
    const dialog = screen.getByRole('dialog', { name: 'Revoke this identity confirmation?' });
    const radios = within(dialog).getAllByRole('radio');
    expect(radios.map((r) => (r as HTMLInputElement).value)).toEqual(['wrong_person', 'planted_or_suspect', 'mistaken_click']);
    for (const r of radios) expect(r).not.toBeChecked();
    const confirm = within(dialog).getByRole('button', { name: 'Revoke confirmation' });
    expect(confirm).toBeDisabled();
    expect(within(dialog).getByText(/PLACEHOLDER: the wording of these reasons/)).toHaveAttribute('data-placeholder');
    expect(writes(calls)).toHaveLength(0);

    await user.click(within(dialog).getByRole('radio', { name: 'Wrong person' }));
    expect(confirm).toBeEnabled();
    expect(writes(calls)).toHaveLength(0);
    await user.click(confirm);
    await waitFor(() => expect(writes(calls)).toHaveLength(1));
    expect(writes(calls)[0]).toMatchObject({ path: revokePath, body: { reason: 'wrong_person' } });
    expect(writes(calls)[0]?.key).toBeTruthy();
    expect(screen.queryByRole('dialog', { name: 'Confirm it is you' })).toBeNull(); // no step-up
    expect(await screen.findByText('The confirmation was revoked.')).toBeInTheDocument();
  });

  it.each([
    ['raised', 'A review task was raised.'],
    ['not_configured', 'No review task was raised: the task settings are not configured yet.'],
    ['on_record', 'A review task is already on record.'],
  ])('tells the review-task outcome "%s" in fixed words', async (word, message) => {
    const user = userEvent.setup();
    await openConsole({ hash: `/patients/${ID.patient}`, routes: [patientRoute(), revoke([word])] });
    await user.click(await screen.findByRole('button', { name: 'Revoke confirmation' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('radio', { name: 'Mistaken click' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Revoke confirmation' }));
    expect(await screen.findByText(message)).toBeInTheDocument();
  });

  it('keeps the success message after the record refreshes to unverified and the revoke control disappears (regression)', async () => {
    const user = userEvent.setup();
    let verified = true;
    const route: Routes[number] = ['get', `/v1/staff/patients/${ID.patient}`, () => json(status({ verified }))];
    const post: Routes[number] = ['post', revokePath, () => {
      verified = false; // the next read of the record says unverified
      return json({ confirmed: false, patient_id: ID.patient, replayed: false, review_task: 'raised' });
    }];
    await openConsole({ hash: `/patients/${ID.patient}`, routes: [route, post] });
    await user.click(await screen.findByRole('button', { name: 'Revoke confirmation' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('radio', { name: 'Wrong person' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Revoke confirmation' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Revoke confirmation' })).toBeNull()); // refreshed, now unverified
    expect(screen.getByText('The confirmation was revoked.')).toBeInTheDocument();
    expect(screen.getByText('A review task was raised.')).toBeInTheDocument();
  });

  it('never reads an unknown review-task word as "raised"', async () => {
    const user = userEvent.setup();
    await openConsole({ hash: `/patients/${ID.patient}`, routes: [patientRoute(), revoke(['some_future_word'])] });
    await user.click(await screen.findByRole('button', { name: 'Revoke confirmation' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('radio', { name: 'Mistaken click' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Revoke confirmation' }));
    expect(await screen.findByText('The confirmation was revoked.')).toBeInTheDocument();
    expect(screen.queryByText('A review task was raised.')).toBeNull();
    expect(document.body.textContent).not.toContain('some_future_word');
  });

  it('retries a failed revoke with the same key, and a different reason with a new one', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ hash: `/patients/${ID.patient}`, routes: [patientRoute(), revoke([503, 503, 'raised'])] });
    await user.click(await screen.findByRole('button', { name: 'Revoke confirmation' }));
    const go = () => within(screen.getByRole('dialog')).getByRole('button', { name: 'Revoke confirmation' });
    await user.click(within(screen.getByRole('dialog')).getByRole('radio', { name: 'Wrong person' }));
    await user.click(go());
    expect(await within(screen.getByRole('dialog')).findByRole('alert')).toHaveTextContent('This is not available right now.');
    await user.click(go());
    await waitFor(() => expect(writes(calls)).toHaveLength(2));
    expect(writes(calls)[1]?.key).toBe(writes(calls)[0]?.key);
    await user.click(within(screen.getByRole('dialog')).getByRole('radio', { name: 'Planted or suspect' }));
    await user.click(go());
    await waitFor(() => expect(writes(calls)).toHaveLength(3));
    expect(writes(calls)[2]?.key).not.toBe(writes(calls)[0]?.key);
    expect(writes(calls)[2]?.body).toEqual({ reason: 'planted_or_suspect' });
  });

  it('shows 409 and 403 in fixed words', async () => {
    const user = userEvent.setup();
    await openConsole({ hash: `/patients/${ID.patient}`, routes: [patientRoute(), revoke([409, 403])] });
    await user.click(await screen.findByRole('button', { name: 'Revoke confirmation' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('radio', { name: 'Wrong person' }));
    const go = () => within(screen.getByRole('dialog')).getByRole('button', { name: 'Revoke confirmation' });
    await user.click(go());
    expect(await within(screen.getByRole('dialog')).findByRole('alert')).toHaveTextContent('That revoke conflicts with an earlier request.');
    await user.click(go());
    expect(await within(screen.getByRole('dialog')).findByText('This is not available to your role.')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it.each([
    ['a record that is not verified', { verified: false }, 'front_desk'],
    ['a merged record', { merged_into: ID.patient2 }, 'front_desk'],
    ['a nurse', {}, 'nurse'],
    ['a doctor', {}, 'doctor'],
  ])('offers no revoke on %s', async (_label, over, role) => {
    await openConsole({ role, hash: `/patients/${ID.patient}`, routes: [patientRoute(over)] });
    await screen.findByText(ID.patient);
    expect(screen.queryByRole('button', { name: 'Revoke confirmation' })).toBeNull();
  });
});

// ---- merge and duplicate lists (read-only) ----------------------------------------------------------------------------------
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
const duplicate = (over: Record<string, unknown> = {}) => ({ candidate_id: ID.task3, candidate_patient_id: ID.patient2, clinic_id: CLINIC_A, created_at: '2026-10-30T05:00:00Z', matched_kinds: ['name_dob'], patient_id: ID.patient, reason: 'near_match', resolved_at: null, status: 'pending', ...over });

describe('identity lists (read-only)', () => {
  it('opens on merges, redirecting the bare section', async () => {
    await openConsole({ hash: '/identity', routes: [['get', '/v1/staff/identity/merges', () => list([merge()])]] });
    expect(await screen.findByRole('link', { name: /Open merge 88888888/ })).toHaveAttribute('href', `#/identity/merges/${ID.hold}`);
    expect(screen.getByText('Awaiting second approval', { selector: '.pill' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Merges' })).toHaveAttribute('aria-current', 'page');
  });

  it('filters merges by status (All sends none) and shows empty, error and not-available states', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ hash: '/identity/merges', routes: [['get', '/v1/staff/identity/merges', () => list([])]] });
    await screen.findByText('There are no merges to show.');
    await user.click(screen.getByRole('button', { name: 'Active' }));
    await waitFor(() => expect(calls.filter((c) => c.path === '/v1/staff/identity/merges').map((c) => c.query.get('status'))).toEqual([null, 'active']));
    expect(screen.getByRole('button', { name: 'All' })).toHaveAttribute('aria-pressed', 'false');
  });

  it('shows a fixed message for a server error and "not available" for a 403', async () => {
    const a = await openConsole({ hash: '/identity/merges', routes: [['get', '/v1/staff/identity/merges', () => fail(503)]] });
    expect(await screen.findByRole('alert')).toHaveTextContent('This is not available right now.');
    expect(document.body.textContent).not.toContain(LEAK);
    a.unmount();
    await openConsole({ role: 'doctor', hash: '/identity/merges', routes: [['get', '/v1/staff/identity/merges', () => fail(403)]] });
    await screen.findByText('This is not available to your role.');
  });

  it('lists possible duplicates for front desk, pending first, and links both records', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ hash: '/identity/duplicates', routes: [['get', '/v1/staff/identity/possible-duplicates', () => list([duplicate()])]] });
    const row = await screen.findByRole('row', { name: /33333333|55555555/ });
    expect(within(row).getByText('Near match')).toBeInTheDocument();
    expect(within(row).getByText('Name and date of birth')).toBeInTheDocument();
    expect(within(row).getAllByRole('link').map((a) => a.getAttribute('href'))).toEqual([`#/patients/${ID.patient}`, `#/patients/${ID.patient2}`]);
    await user.click(screen.getByRole('button', { name: 'Resolved' }));
    await waitFor(() => expect(calls.filter((c) => c.path.endsWith('possible-duplicates')).map((c) => c.query.get('status'))).toEqual(['pending', 'resolved']));
  });

  it('does not offer the duplicates list to nurse or doctor, and a typed address gets "not available"', async () => {
    await openConsole({ role: 'nurse', hash: '/identity/duplicates', routes: [['get', '/v1/staff/identity/possible-duplicates', () => fail(403)]] });
    await screen.findByText('This is not available to your role.');
    expect(screen.queryByRole('link', { name: 'Possible duplicates' })).toBeNull();
    expect(screen.getByRole('link', { name: 'Merges' })).toBeInTheDocument();
  });

  it('has no write control anywhere on the lists', async () => {
    await openConsole({ hash: '/identity/merges', routes: [['get', '/v1/staff/identity/merges', () => list([merge()])]] });
    await screen.findByRole('link', { name: /Open merge/ });
    expect(screen.queryByRole('button', { name: /^(approve|unmerge|resolve|confirm identity|revoke confirmation)$/i })).toBeNull(); // the status filters are not actions
  });
});

describe('merge detail (read-only while merge actions are off)', () => {
  const route = (over: Record<string, unknown> = {}): Routes[number] => ['get', `/v1/staff/identity/merges/${ID.hold}`, () => json(merge(over))];

  it('shows the merge, links both records and says Approve and unmerge are Coming soon, with no button', async () => {
    const { calls } = await openConsole({ hash: `/identity/merges/${ID.hold}`, routes: [route()] });
    expect(await screen.findByText(ID.hold)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: ID.patient })).toHaveAttribute('href', `#/patients/${ID.patient}`);
    expect(screen.getByText('Approve and unmerge')).toBeInTheDocument();
    expect(screen.getAllByText('Coming soon').length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Unmerge' })).toBeNull();
    expect(writes(calls)).toHaveLength(0);
  });

  it('shows "you" for the people who acted, 404 as a fixed message, and a malformed id as not found', async () => {
    const first = await openConsole({ hash: `/identity/merges/${ID.hold}`, routes: [route({ proposed_by: '9d0a1c2d-3e4f-4a6b-8c1e-3f2b6a4d4e1b' })] });
    expect(await screen.findByText('You')).toBeInTheDocument();
    first.unmount();
    const a = await openConsole({ hash: `/identity/merges/${ID.hold}`, routes: [['get', `/v1/staff/identity/merges/${ID.hold}`, () => fail(404)]] });
    expect(await screen.findByRole('alert')).toHaveTextContent('That could not be found.');
    a.unmount();
    const b = await openConsole({ hash: '/identity/merges/xyz' });
    expect(screen.getByRole('heading', { level: 1, name: 'Not found' })).toBeInTheDocument();
    expect(b.calls.map((c) => c.path)).toEqual(['/v1/iam/me']);
  });
});
