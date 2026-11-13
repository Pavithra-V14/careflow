import { describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CLINIC_A, json } from './helpers/fakes';
import { ID, LEAK, fail, openConsole, writes, type Routes } from './helpers/console';

// MOCK-ONLY (see helpers/console.tsx): fakes written from openapi.snapshot.json. Medication statements on a patient's record (nurse and doctor).
// The codes below are placeholders; the console holds no list of medicines.

const MED = 'f1f1f1f1-f1f1-41f1-81f1-f1f1f1f1f1f1';
const MED2 = 'f2f2f2f2-f2f2-42f2-82f2-f2f2f2f2f2f2';
const status = { clinic_id: CLINIC_A, latest_identity_event: 'confirm', latest_identity_event_at: '2026-10-30T05:00:00Z', merged_into: null, patient_id: ID.patient, registered_via: 'front_desk', verified: true };
const patientRoute: Routes[number] = ['get', `/v1/staff/patients/${ID.patient}`, () => json(status)];
const row = (over: Record<string, unknown> = {}) => ({
  code: 'PLACEHOLDER-CODE-1',
  medication_id: MED,
  recorded_at: '2026-11-10T05:00:00Z',
  superseded: false,
  supersedes_id: null,
  valid_from: '2026-11-01T05:00:00Z',
  valid_to: null,
  verified: true,
  ...over,
});
const listRoute = (medications: unknown[]): Routes[number] => ['get', '/v1/staff/medications', () => json({ medications })];
const recordRoute = (answer: number | 'ok'): Routes[number] => ['post', '/v1/staff/medications', () => (answer === 'ok' ? json({ created: true, medication_id: MED2 }, 201) : fail(answer))];
const open = (role = 'nurse', routes: Routes = [], extra: Record<string, unknown> = {}) => openConsole({ role, hash: `/patients/${ID.patient}`, routes: [patientRoute, ...routes], me: { clinic_time_zone: 'UTC' }, ...extra });

describe('medications on the patient record', () => {
  it('lists the rows as the API sends them: code, verified, times, a missing end as "No end recorded", replaced rows marked', async () => {
    const { calls } = await open('nurse', [listRoute([row(), row({ medication_id: MED2, code: 'PLACEHOLDER-CODE-2', superseded: true, verified: false, valid_to: '2026-11-05T05:00:00Z', supersedes_id: ID.doc })])]);
    expect(await screen.findByText('PLACEHOLDER-CODE-1')).toBeInTheDocument();
    const rows = within(screen.getByRole('table', { name: 'Medications' })).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent('No end recorded');
    expect(rows[0]).toHaveTextContent('Current');
    expect(rows[1]).toHaveTextContent('Replaced by a later row');
    expect(rows[1]).toHaveTextContent('No'); // verified: false is shown as No
    expect(rows[1]).toHaveTextContent(`Replaces ${ID.doc.slice(0, 8)}…`);
    // Only a current row offers a correction.
    expect(within(rows[0] as HTMLElement).getByRole('button', { name: 'Correct or stop PLACEHOLDER-CODE-1' })).toBeInTheDocument();
    expect(within(rows[1] as HTMLElement).queryByRole('button')).toBeNull();
    const q = calls.find((c) => c.path === '/v1/staff/medications');
    expect(q?.query.get('clinic_id')).toBe(CLINIC_A);
    expect(q?.query.get('patient_id')).toBe(ID.patient);
  });

  it('records a statement: nothing chosen for the person, the button disabled until all is chosen, one write with a key', async () => {
    const user = userEvent.setup();
    const { calls } = await open('doctor', [listRoute([]), recordRoute('ok')]);
    expect(await screen.findByText('No medication statements are recorded for this patient.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Record a medication' }));
    const dialog = screen.getByRole('dialog');
    const save = within(dialog).getByRole('button', { name: 'Record' });
    expect(save).toBeDisabled();
    for (const r of within(dialog).getAllByRole('radio')) expect(r).not.toBeChecked();
    expect((within(dialog).getByLabelText('From') as HTMLInputElement).value).toBe('');
    await user.type(within(dialog).getByLabelText('Code'), 'PLACEHOLDER-CODE-9');
    await user.type(within(dialog).getByLabelText('From'), '2026-11-10T09:30');
    expect(save).toBeDisabled(); // the verified answer is still the person's to give
    await user.click(within(dialog).getByRole('radio', { name: 'No' }));
    expect(writes(calls)).toHaveLength(0);
    await user.click(save);
    await screen.findByText('The medication statement was recorded.');
    expect(writes(calls)).toHaveLength(1);
    expect(writes(calls)[0]).toMatchObject({ path: '/v1/staff/medications', body: { clinic_id: CLINIC_A, patient_id: ID.patient, code: 'PLACEHOLDER-CODE-9', verified: false, valid_from: '2026-11-10T09:30:00.000Z', valid_to: null, supersedes_id: null } });
    expect(writes(calls)[0]?.key).toBeTruthy();
  });

  it('refuses a code that does not fit the pattern before anything is sent', async () => {
    const user = userEvent.setup();
    const { calls } = await open('nurse', [listRoute([]), recordRoute('ok')]);
    await user.click(await screen.findByRole('button', { name: 'Record a medication' }));
    const dialog = screen.getByRole('dialog');
    await user.type(within(dialog).getByLabelText('Code'), 'bad code!');
    await user.type(within(dialog).getByLabelText('From'), '2026-11-10T09:30');
    await user.click(within(dialog).getByRole('radio', { name: 'Yes' }));
    expect(within(dialog).getByRole('button', { name: 'Record' })).toBeDisabled();
    expect(writes(calls)).toHaveLength(0);
  });

  it('a correction names the row it replaces and sends that id; the end time is sent only when typed', async () => {
    const user = userEvent.setup();
    const { calls } = await open('nurse', [listRoute([row()]), recordRoute('ok')]);
    await user.click(await screen.findByRole('button', { name: 'Correct or stop PLACEHOLDER-CODE-1' }));
    const dialog = screen.getByRole('dialog', { name: 'Correct or stop this medication statement?' });
    expect(dialog).toHaveTextContent('PLACEHOLDER-CODE-1');
    await user.type(within(dialog).getByLabelText('Code'), 'PLACEHOLDER-CODE-1');
    await user.type(within(dialog).getByLabelText('From'), '2026-11-01T05:00');
    await user.type(within(dialog).getByLabelText('To (leave empty if no end is recorded)'), '2026-11-12T05:00');
    await user.click(within(dialog).getByRole('radio', { name: 'Yes' }));
    await user.click(within(dialog).getByRole('button', { name: 'Record' }));
    await screen.findByText('The medication statement was recorded.');
    expect(writes(calls)[0]?.body).toMatchObject({ supersedes_id: MED, valid_to: '2026-11-12T05:00:00.000Z' });
  });

  it.each([
    [503, "This clinic's list of medication codes is not set up yet. Nothing was recorded."],
    [422, "The code is not in this clinic's list, or the times are not valid. Nothing was recorded."],
    [409, 'That row was already replaced, or this request clashes with an earlier one. Reload and try again.'],
  ])('tells %s in fixed words, never the backend text', async (code, text) => {
    const user = userEvent.setup();
    await open('nurse', [listRoute([]), recordRoute(code)]);
    await user.click(await screen.findByRole('button', { name: 'Record a medication' }));
    const dialog = screen.getByRole('dialog');
    await user.type(within(dialog).getByLabelText('Code'), 'PLACEHOLDER-CODE-9');
    await user.type(within(dialog).getByLabelText('From'), '2026-11-10T09:30');
    await user.click(within(dialog).getByRole('radio', { name: 'Yes' }));
    await user.click(within(dialog).getByRole('button', { name: 'Record' }));
    expect(await within(dialog).findByText(text)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('403 on the list is "not available to your role"', async () => {
    await open('nurse', [['get', '/v1/staff/medications', () => fail(403)]]);
    expect(await screen.findByText(/not available to your role/i)).toBeInTheDocument();
  });

  it('is not shown to the front desk, and is shown in Tamil to a nurse', async () => {
    const a = await open('front_desk', [listRoute([row()])]);
    await screen.findByRole('heading', { name: /./, level: 1 });
    expect(screen.queryByRole('heading', { name: 'Medications' })).toBeNull();
    a.unmount();
    await openConsole({ role: 'nurse', hash: `/patients/${ID.patient}`, lang: 'ta', routes: [patientRoute, listRoute([row()])] });
    expect(await screen.findByRole('heading', { name: 'மருந்துகள்' })).toBeInTheDocument();
    expect(await screen.findByText('PLACEHOLDER-CODE-1')).toBeInTheDocument();
  });
});
