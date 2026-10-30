import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CLINIC_A, json } from './helpers/fakes';
import { ID, LEAK, fail, openConsole, writes, type Routes } from './helpers/console';

// MOCK-ONLY (see helpers/console.tsx). The `label`, `labels_withheld` and `truncated` search fields are NOT in the
// OpenAPI snapshot yet: those tests show how the screen reacts if the backend adds them (docs/design/patient-view-fields.md).

const hit = (over: Record<string, unknown> = {}) => ({ matched_kinds: ['name_dob'], patient_id: ID.patient, registered_via: 'front_desk', verified: true, ...over });
const search = (body: unknown, status = 200): Routes[number] => ['post', '/v1/staff/patients/search', () => (status === 200 ? json(body) : fail(status))];
const status = (over: Record<string, unknown> = {}) => ({
  clinic_id: CLINIC_A,
  latest_identity_event: null,
  latest_identity_event_at: null,
  merged_into: null,
  patient_id: ID.patient,
  registered_via: 'referral_worker',
  verified: false,
  ...over,
});
const statusRoute = (over: Record<string, unknown> = {}, id = ID.patient): Routes[number] => ['get', `/v1/staff/patients/${id}`, () => json(status({ patient_id: id, ...over }))];

async function fillSearch(user: ReturnType<typeof userEvent.setup>, phone = '') {
  await user.type(screen.getByLabelText('Full name'), 'Test Person');
  await user.type(screen.getByLabelText('Date of birth'), '1990-02-03');
  if (phone) await user.type(screen.getByLabelText('Phone number (optional)'), phone);
  await user.click(screen.getByRole('button', { name: 'Search' }));
}

describe('patient search', () => {
  it('searches the active clinic with exactly the typed details, sends no idempotency key (it is a read), and never stores the person', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ hash: '/patients', routes: [search({ items: [hit()] })] });
    await fillSearch(user, '9876543210');
    const row = await screen.findByRole('row', { name: /66666666/ });
    const sent = calls.find((c) => c.path === '/v1/staff/patients/search');
    expect(sent?.body).toEqual({ clinic_id: CLINIC_A, name: 'Test Person', dob: '1990-02-03', phone: '9876543210' });
    expect(sent?.key).toBeNull();
    expect(within(row).getByText('Verified')).toBeInTheDocument();
    expect(within(row).getByText('Front desk')).toBeInTheDocument();
    expect(within(row).getByText('Name and date of birth')).toBeInTheDocument();
    // The form never autocompletes or keeps the details in the page URL.
    expect(screen.getByLabelText('Full name')).toHaveAttribute('autocomplete', 'off');
    expect(window.location.href).not.toContain('Test');
  });

  it('omits the phone when none is typed', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ hash: '/patients', routes: [search({ items: [] })] });
    await fillSearch(user);
    await screen.findByText('No matching record was found.');
    expect(calls.find((c) => c.path === '/v1/staff/patients/search')?.body).toEqual({ clinic_id: CLINIC_A, name: 'Test Person', dob: '1990-02-03' });
  });

  it('shows a record that is not verified as not verified', async () => {
    const user = userEvent.setup();
    await openConsole({ hash: '/patients', routes: [search({ items: [hit({ verified: false, registered_via: 'referral_worker' })] })] });
    await fillSearch(user);
    expect(await screen.findByText('Not verified')).toBeInTheDocument();
    expect(screen.getByText('Referral worker')).toBeInTheDocument();
  });

  it.each([
    [429, 'Too many searches. Wait a moment and try again.'],
    [503, 'Search is not available right now. Try again later.'],
    [422, 'Check the date of birth and phone number.'],
    [500, 'Something went wrong. Try again.'],
  ])('answers %s with a calm fixed message, never the backend text, and keeps the form usable', async (code, message) => {
    const user = userEvent.setup();
    await openConsole({ hash: '/patients', routes: [search({}, code)] });
    await fillSearch(user);
    expect(await screen.findByRole('alert')).toHaveTextContent(message);
    expect(document.body.textContent).not.toContain(LEAK);
    expect(screen.getByRole('button', { name: 'Search' })).toBeEnabled();
  });

  it('shows "not available to your role" for a 403 on search', async () => {
    const user = userEvent.setup();
    await openConsole({ hash: '/patients', routes: [search({}, 403)] });
    await fillSearch(user);
    expect(await screen.findByText('This is not available to your role.')).toBeInTheDocument();
  });

  it('keeps the search button disabled until a name and a date of birth are given', async () => {
    const user = userEvent.setup();
    await openConsole({ hash: '/patients' });
    expect(screen.getByRole('button', { name: 'Search' })).toBeDisabled();
    await user.type(screen.getByLabelText('Full name'), 'A');
    expect(screen.getByRole('button', { name: 'Search' })).toBeDisabled();
    await user.type(screen.getByLabelText('Date of birth'), '1990-02-03');
    expect(screen.getByRole('button', { name: 'Search' })).toBeEnabled();
  });

  it('drops the results when you leave the page (nothing about the person is kept)', async () => {
    const user = userEvent.setup();
    await openConsole({ hash: '/patients', routes: [search({ items: [hit()] })] });
    await fillSearch(user);
    await screen.findByRole('row', { name: /66666666/ });
    await user.click(within(screen.getByRole('navigation', { name: 'Sections' })).getByRole('link', { name: 'Home' }));
    await user.click(within(screen.getByRole('navigation', { name: 'Sections' })).getByRole('link', { name: 'Patients' }));
    expect(screen.queryByRole('row', { name: /66666666/ })).toBeNull();
    expect(screen.getByLabelText('Full name')).toHaveValue('');
  });

  it('shows a clear "labels withheld" state when the backend withholds labels (contract not on main yet: mock-only)', async () => {
    const user = userEvent.setup();
    await openConsole({ hash: '/patients', routes: [search({ items: [hit()], labels_withheld: true })] });
    await fillSearch(user);
    expect(await screen.findByText('Labels are withheld for these results.')).toBeInTheDocument();
  });

  it('shows a label and a "more matches" note only when the backend sends them (mock-only)', async () => {
    const user = userEvent.setup();
    await openConsole({ hash: '/patients', routes: [search({ items: [hit({ label: 'Placeholder Label' })], truncated: true })] });
    await fillSearch(user);
    expect(await screen.findByText('Placeholder Label')).toBeInTheDocument();
    expect(screen.getByText('More matches exist than are shown.')).toBeInTheDocument();
  });

  it('says search is not available to a nurse or doctor, but still lets them open a record by id', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ role: 'nurse', hash: '/patients', routes: [statusRoute()] });
    expect(screen.queryByLabelText('Full name')).toBeNull();
    expect(document.querySelector('.state-forbidden')).toHaveTextContent('This is not available to your role.');
    await user.type(screen.getByLabelText('Record id'), ID.patient.toUpperCase());
    await user.click(screen.getByRole('button', { name: 'Open' }));
    expect(await screen.findByRole('heading', { level: 1, name: 'Patient record' })).toBeInTheDocument();
    expect(calls.some((c) => c.path === `/v1/staff/patients/${ID.patient}`)).toBe(true);
  });

  it('rejects something that is not an id without calling the API', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ hash: '/patients' });
    await user.type(screen.getByLabelText('Record id'), 'Test Person');
    await user.click(screen.getByRole('button', { name: 'Open' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('That is not a record id.');
    expect(calls.map((c) => c.path)).toEqual(['/v1/iam/me']);
  });
});

describe('patient record', () => {
  it('shows exactly what the open route returns', async () => {
    await openConsole({ hash: `/patients/${ID.patient}`, routes: [statusRoute({ latest_identity_event: 'revoke', latest_identity_event_at: '2026-10-30T05:00:00Z' })] });
    expect(await screen.findByText(ID.patient)).toBeInTheDocument();
    expect(screen.getByText('Not verified')).toBeInTheDocument();
    expect(screen.getByText('Referral worker')).toBeInTheDocument();
    expect(screen.getByText(/^Confirmation revoked/)).toBeInTheDocument();
  });

  it('confirms identity for front desk after a confirmation dialog, with one key, no body and NO step-up prompt', async () => {
    const user = userEvent.setup();
    let confirmed = false;
    const confirm: Routes[number] = ['post', `/v1/staff/patients/${ID.patient}/confirm-identity`, () => {
      confirmed = true;
      return json({ confirmed: true, patient_id: ID.patient, replayed: false });
    }];
    const { calls } = await openConsole({
      hash: `/patients/${ID.patient}`,
      routes: [['get', `/v1/staff/patients/${ID.patient}`, () => json(status({ verified: confirmed }))], confirm],
    });
    await user.click(await screen.findByRole('button', { name: 'Confirm identity' }));
    const dialog = screen.getByRole('dialog', { name: 'Confirm this identity?' });
    expect(within(dialog).getByText(/PLACEHOLDER: what must be checked/)).toHaveAttribute('data-placeholder');
    expect(writes(calls)).toHaveLength(0);
    await user.click(within(dialog).getByRole('button', { name: 'Confirm identity' }));
    await waitFor(() => expect(writes(calls)).toHaveLength(1));
    expect(writes(calls)[0]?.key).toBeTruthy();
    expect(writes(calls)[0]?.body).toBeUndefined();
    expect(screen.queryByRole('dialog', { name: 'Confirm it is you' })).toBeNull();
    // The record refreshes and no longer offers the action.
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Confirm identity' })).toBeNull());
    expect(await screen.findByText('Verified', { selector: '.pill' })).toBeInTheDocument();
  });

  it('can be cancelled without writing anything', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ hash: `/patients/${ID.patient}`, routes: [statusRoute()] });
    await user.click(await screen.findByRole('button', { name: 'Confirm identity' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(writes(calls)).toHaveLength(0);
  });

  it('retries a failed confirmation with the SAME key', async () => {
    const user = userEvent.setup();
    const answers = [503, 200];
    const confirm: Routes[number] = ['post', `/v1/staff/patients/${ID.patient}/confirm-identity`, () => {
      const s = answers.shift() ?? 200;
      return s === 200 ? json({ confirmed: true, patient_id: ID.patient, replayed: false }) : fail(s);
    }];
    const { calls } = await openConsole({ hash: `/patients/${ID.patient}`, routes: [statusRoute(), confirm] });
    await user.click(await screen.findByRole('button', { name: 'Confirm identity' }));
    const go = () => within(screen.getByRole('dialog')).getByRole('button', { name: 'Confirm identity' });
    await user.click(go());
    expect(await within(screen.getByRole('dialog')).findByRole('alert')).toHaveTextContent('This is not available right now.');
    await user.click(go());
    await waitFor(() => expect(writes(calls)).toHaveLength(2));
    expect(writes(calls)[1]?.key).toBe(writes(calls)[0]?.key);
  });

  it('says a conflict plainly, and "not available to your role" for a 403', async () => {
    const user = userEvent.setup();
    let code = 409;
    const confirm: Routes[number] = ['post', `/v1/staff/patients/${ID.patient}/confirm-identity`, () => fail(code)];
    await openConsole({ hash: `/patients/${ID.patient}`, routes: [statusRoute(), confirm] });
    await user.click(await screen.findByRole('button', { name: 'Confirm identity' }));
    const go = () => within(screen.getByRole('dialog')).getByRole('button', { name: 'Confirm identity' });
    await user.click(go());
    expect(await within(screen.getByRole('dialog')).findByRole('alert')).toHaveTextContent('That confirmation conflicts with an earlier request.');
    code = 403;
    await user.click(go());
    expect(await within(screen.getByRole('dialog')).findByText('This is not available to your role.')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it.each([
    ['a verified record', { verified: true }, 'front_desk'],
    ['a merged record', { merged_into: ID.patient2 }, 'front_desk'],
    ['a nurse', {}, 'nurse'],
    ['a doctor', {}, 'doctor'],
  ])('offers no confirm action on %s', async (_label, over, role) => {
    await openConsole({ role, hash: `/patients/${ID.patient}`, routes: [statusRoute(over)] });
    await screen.findByText(ID.patient);
    expect(screen.queryByRole('button', { name: 'Confirm identity' })).toBeNull();
  });

  it('points a merged record at the record to act on', async () => {
    await openConsole({ hash: `/patients/${ID.patient}`, routes: [statusRoute({ merged_into: ID.patient2 })] });
    expect(await screen.findByRole('link', { name: 'Open that record' })).toHaveAttribute('href', `#/patients/${ID.patient2}`);
  });

  it.each([
    [404, 'That could not be found.'],
    [429, 'Too many requests. Wait a moment and try again.'],
    [503, 'This is not available right now. Try again in a moment.'],
  ])('answers %s on open with a calm fixed message', async (code, message) => {
    await openConsole({ hash: `/patients/${ID.patient}`, routes: [['get', `/v1/staff/patients/${ID.patient}`, () => fail(code)]] });
    expect(await screen.findByRole('alert')).toHaveTextContent(message);
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('shows "not available to your role" for a 403 on open, and not-found for a malformed id', async () => {
    const a = await openConsole({ role: 'nurse', hash: `/patients/${ID.patient}`, routes: [['get', `/v1/staff/patients/${ID.patient}`, () => fail(403)]] });
    await screen.findByText('This is not available to your role.');
    a.unmount();
    const b = await openConsole({ hash: '/patients/abc' });
    expect(screen.getByRole('heading', { level: 1, name: 'Not found' })).toBeInTheDocument();
    expect(b.calls.map((c) => c.path)).toEqual(['/v1/iam/me']);
  });
});
