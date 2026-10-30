import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CLINIC_A, json } from './helpers/fakes';
import { ID, LEAK, fail, list, openConsole, writes, type Routes } from './helpers/console';

// MOCK-ONLY (see helpers/console.tsx).

const stateBox = () => document.querySelector('.state') as HTMLElement;
const provider = (over: Record<string, unknown> = {}) => ({ active: true, clinic_id: CLINIC_A, display_name: 'Placeholder Provider', provider_id: ID.doc, ...over });
const hold = (over: Record<string, unknown> = {}) => ({ clinic_id: CLINIC_A, created_at: '2026-10-30T05:00:00Z', expires_at: '2026-10-30T06:00:00Z', hold_id: ID.hold, patient_id: null, slot_id: ID.slot, status: 'active', ...over });

describe('reference lists (read-only)', () => {
  it('opens on providers, shows the list, and asks for inactive ones only when toggled', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ hash: '/scheduling', routes: [['get', '/v1/staff/scheduling/providers', (req) => list([provider(), ...(req.query.get('include_inactive') ? [provider({ display_name: 'Retired Provider', active: false, provider_id: ID.doc2 })] : [])])]] });
    expect(await screen.findByText('Placeholder Provider')).toBeInTheDocument();
    expect(screen.queryByText('Retired Provider')).toBeNull();
    await user.click(screen.getByRole('checkbox', { name: 'Show inactive' }));
    expect(await screen.findByText('Retired Provider')).toBeInTheDocument();
    expect(screen.getByText('Inactive')).toBeInTheDocument();
    expect(calls.filter((c) => c.path === '/v1/staff/scheduling/providers').map((c) => c.query.get('include_inactive'))).toEqual([null, 'true']);
  });

  it('shows locations, visit types with their length, and priority levels in rank order as given', async () => {
    const a = await openConsole({ hash: '/scheduling/locations', routes: [['get', '/v1/staff/scheduling/locations', () => list([{ active: true, clinic_id: CLINIC_A, location_id: ID.slot, name: 'Room 1' }])]] });
    expect(await screen.findByText('Room 1')).toBeInTheDocument();
    a.unmount();
    const b = await openConsole({ hash: '/scheduling/visit-types', routes: [['get', '/v1/staff/scheduling/visit-types', () => list([{ active: true, clinic_id: CLINIC_A, duration_minutes: 30, name: 'Placeholder Visit', visit_type_id: ID.visitType }])]] });
    expect(await screen.findByText('Placeholder Visit')).toBeInTheDocument();
    expect(screen.getByText('30 min')).toBeInTheDocument();
    b.unmount();
    await openConsole({ hash: '/scheduling/priority-levels', routes: [['get', '/v1/staff/scheduling/priority-levels', () => list([{ active: true, clinic_id: CLINIC_A, code: 'P1', label: 'Placeholder One', rank: 1 }, { active: true, clinic_id: CLINIC_A, code: 'P2', label: 'Placeholder Two', rank: 2 }])]] });
    const rows = (await screen.findAllByRole('row')).slice(1);
    expect(rows.map((r) => within(r).getAllByRole('cell')[0]?.textContent)).toEqual(['P1', 'P2']);
  });

  it('has loading-empty, error and not-available-to-your-role states, and no write controls', async () => {
    const a = await openConsole({ hash: '/scheduling/providers', routes: [['get', '/v1/staff/scheduling/providers', () => list([])]] });
    expect(await screen.findByText('Nothing to show.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /release|add|edit|delete/i })).toBeNull();
    a.unmount();
    const b = await openConsole({ hash: '/scheduling/providers', routes: [['get', '/v1/staff/scheduling/providers', () => fail(502)]] });
    expect(await screen.findByRole('alert')).toHaveTextContent('This is not available right now.');
    expect(document.body.textContent).not.toContain(LEAK);
    b.unmount();
    await openConsole({ role: 'nurse', hash: '/scheduling/providers', routes: [['get', '/v1/staff/scheduling/providers', () => fail(403)]] });
    await screen.findByText('This is not available to your role.');
  });

  it('switches lists with the keyboard and marks the current one', async () => {
    const user = userEvent.setup();
    await openConsole({ hash: '/scheduling', routes: [['get', /.*/, () => list([])]] });
    const sub = await screen.findByRole('navigation', { name: 'Scheduling lists' });
    expect(within(sub).getByRole('link', { name: 'Providers' })).toHaveAttribute('aria-current', 'page');
    within(sub).getByRole('link', { name: 'Holds' }).focus();
    await user.keyboard('{Enter}');
    expect(await screen.findByRole('heading', { level: 2, name: 'Holds' })).toBeInTheDocument();
  });

  it('treats an unknown list as not found', async () => {
    await openConsole({ hash: '/scheduling/rooms' });
    expect(screen.getByRole('heading', { level: 1, name: 'Not found' })).toBeInTheDocument();
  });
});

describe('holds', () => {
  const holdsRoute = (rows: () => unknown[]): Routes[number] => ['get', '/v1/staff/scheduling/holds', () => list(rows())];
  const release = (status: number): Routes[number] => ['post', `/v1/staff/scheduling/holds/${ID.hold}/release`, () => (status === 200 ? json({ hold_id: ID.hold, replayed: false, slot_id: ID.slot, status: 'released' }) : fail(status))];

  it('lists active holds by default and filters by status', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ hash: '/scheduling/holds', routes: [holdsRoute(() => [hold()])] });
    expect(await screen.findByRole('row', { name: /88888888/ })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Expired' }));
    await waitFor(() => expect(calls.filter((c) => c.path === '/v1/staff/scheduling/holds').map((c) => c.query.get('status'))).toEqual(['active', 'expired']));
  });

  it('shows the empty state', async () => {
    await openConsole({ hash: '/scheduling/holds', routes: [holdsRoute(() => [])] });
    expect(await screen.findByText('There are no holds to show.')).toBeInTheDocument();
  });

  it('releases a hold only after a confirmation, with one key, and refreshes the list', async () => {
    const user = userEvent.setup();
    let rows = [hold()];
    const rel: Routes[number] = ['post', `/v1/staff/scheduling/holds/${ID.hold}/release`, () => {
      rows = [];
      return json({ hold_id: ID.hold, replayed: false, slot_id: ID.slot, status: 'released' });
    }];
    const { calls } = await openConsole({ hash: '/scheduling/holds', routes: [holdsRoute(() => rows), rel] });
    await user.click(await screen.findByRole('button', { name: /^Release\s+[0-9a-f]{8}$/ }));
    const dialog = screen.getByRole('dialog', { name: 'Release this hold?' });
    expect(writes(calls)).toHaveLength(0);
    await user.click(within(dialog).getByRole('button', { name: 'Release' }));
    await waitFor(() => expect(writes(calls)).toHaveLength(1));
    expect(writes(calls)[0]?.key).toBeTruthy();
    expect(await screen.findByText('There are no holds to show.')).toBeInTheDocument();
  });

  it('shows a 409 (booked or expired meanwhile) as a normal message, not an alert, and refreshes the list on close', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ hash: '/scheduling/holds', routes: [holdsRoute(() => [hold()]), release(409)] });
    await user.click(await screen.findByRole('button', { name: /^Release\s+[0-9a-f]{8}$/ }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Release' }));
    const dialog = screen.getByRole('dialog');
    const message = await within(dialog).findByText('This hold was already booked or has expired. The list will refresh.');
    expect(message).toHaveAttribute('role', 'status');
    expect(within(dialog).queryByRole('alert')).toBeNull();
    expect(document.body.textContent).not.toContain(LEAK);
    const before = calls.filter((c) => c.path === '/v1/staff/scheduling/holds').length;
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(calls.filter((c) => c.path === '/v1/staff/scheduling/holds').length).toBeGreaterThan(before));
  });

  it('retries a failed release with the SAME key', async () => {
    const user = userEvent.setup();
    const answers = [503, 200];
    const rel: Routes[number] = ['post', `/v1/staff/scheduling/holds/${ID.hold}/release`, () => {
      const s = answers.shift() ?? 200;
      return s === 200 ? json({ hold_id: ID.hold, replayed: false, slot_id: ID.slot, status: 'released' }) : fail(s);
    }];
    const { calls } = await openConsole({ hash: '/scheduling/holds', routes: [holdsRoute(() => [hold()]), rel] });
    await user.click(await screen.findByRole('button', { name: /^Release\s+[0-9a-f]{8}$/ }));
    const go = () => within(screen.getByRole('dialog')).getByRole('button', { name: 'Release' });
    await user.click(go());
    expect(await within(screen.getByRole('dialog')).findByRole('alert')).toHaveTextContent('This is not available right now.');
    await user.click(go());
    await waitFor(() => expect(writes(calls)).toHaveLength(2));
    expect(writes(calls)[1]?.key).toBe(writes(calls)[0]?.key);
  });

  it('shows "not available to your role" for a 403 on release, and offers no release button to a role that cannot', async () => {
    const user = userEvent.setup();
    const a = await openConsole({ hash: '/scheduling/holds', routes: [holdsRoute(() => [hold()]), release(403)] });
    await user.click(await screen.findByRole('button', { name: /^Release\s+[0-9a-f]{8}$/ }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Release' }));
    expect(await within(screen.getByRole('dialog')).findByText('This is not available to your role.')).toBeInTheDocument();
    a.unmount();
    await openConsole({ role: 'nurse', hash: '/scheduling/holds', routes: [holdsRoute(() => [hold()])] });
    await screen.findByRole('row', { name: /88888888/ });
    expect(screen.queryByRole('button', { name: /^Release\s+[0-9a-f]{8}$/ })).toBeNull();
  });

  it('offers no release for a hold that is not active', async () => {
    await openConsole({ hash: '/scheduling/holds', routes: [holdsRoute(() => [hold({ status: 'expired' })])] });
    await screen.findByRole('row', { name: /88888888/ });
    expect(screen.queryByRole('button', { name: /^Release\s+[0-9a-f]{8}$/ })).toBeNull();
  });
});

describe('offers (read-only)', () => {
  const types: Routes[number] = ['get', '/v1/staff/scheduling/visit-types', () => list([{ active: true, clinic_id: CLINIC_A, duration_minutes: 30, name: 'Placeholder Visit', visit_type_id: ID.visitType }])];

  it('preselects nothing, and does not ask for a draft until a visit type is chosen', async () => {
    const { calls } = await openConsole({ hash: '/scheduling/offers', routes: [types] });
    const select = await screen.findByLabelText('Visit type');
    expect(select).toHaveValue('');
    expect(screen.getByRole('button', { name: 'Show draft' })).toBeDisabled();
    expect(calls.some((c) => c.path.includes('/offers/draft'))).toBe(false);
  });

  it.each([
    ['ok', 'A draft is ready'],
    ['empty', 'No candidates'],
    ['degraded', 'Partly available'],
    ['unavailable', 'Not set up yet'],
  ])('shows status %s as "%s" and never the reason or the data', async (status, label) => {
    const user = userEvent.setup();
    const draft: Routes[number] = ['get', '/v1/staff/scheduling/offers/draft', () => json({ status, reason: `not_configured: ${LEAK}`, data: [{ secret: LEAK }] })];
    const { calls } = await openConsole({ hash: '/scheduling/offers', routes: [types, draft] });
    await user.selectOptions(await screen.findByLabelText('Visit type'), ID.visitType);
    await user.click(screen.getByRole('button', { name: 'Show draft' }));
    expect(await screen.findByText(label)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
    const sent = calls.find((c) => c.path === '/v1/staff/scheduling/offers/draft');
    expect(sent?.query.get('clinic_id')).toBe(CLINIC_A);
    expect(sent?.query.get('visit_type_id')).toBe(ID.visitType);
    expect(writes(calls)).toHaveLength(0);
  });

  it('marks candidate details and sending as Coming soon, with the send button disabled', async () => {
    await openConsole({ hash: '/scheduling/offers', routes: [types] });
    await screen.findByLabelText('Visit type');
    expect(screen.getByRole('button', { name: 'Send offer' })).toBeDisabled();
    expect(screen.getAllByText('Coming soon').length).toBeGreaterThanOrEqual(1);
  });

  it('shows "not available to your role" for a 403 on the draft', async () => {
    const user = userEvent.setup();
    await openConsole({ hash: '/scheduling/offers', routes: [types, ['get', '/v1/staff/scheduling/offers/draft', () => fail(403)]] });
    await user.selectOptions(await screen.findByLabelText('Visit type'), ID.visitType);
    await user.click(screen.getByRole('button', { name: 'Show draft' }));
    await screen.findByText('This is not available to your role.');
    expect(stateBox()).toBeTruthy();
  });
});
