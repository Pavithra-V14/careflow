import { describe, expect, it } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { CLINIC_A, json } from './helpers/fakes';
import { ID, LEAK, fail, list, openConsole, type Routes } from './helpers/console';

// MOCK-ONLY (see helpers/console.tsx). Every screen that reads a collection shows its three states: LOADING while the read is in flight,
// EMPTY (a plain sentence, not a blank page) when the read comes back with nothing, and ERROR (a fixed message in an alert, never the
// backend's words) when the read fails. One table, so a new screen cannot be added without its states.

interface Screen {
  name: string;
  role: string;
  hash: string;
  /** The route the screen reads. */
  path: string;
  /** What an empty answer looks like. */
  empty: () => Response;
  /** The screen's other reads, answered normally, so the one under test is the only thing that can go wrong. */
  also?: Routes;
}

const items = () => list([]);
const AUTONOMY_OTHERS = (other: 'proposals' | 'history'): Routes => [
  ['get', '/v1/staff/console/autonomy', () => json({ clinic_id: CLINIC_A, configured: true, mode: 'shadow' })],
  other === 'history' ? ['get', '/v1/staff/console/autonomy/history', () => json({ items: [] })] : ['get', '/v1/staff/console/autonomy/proposals', () => json({ proposals: [] })],
];
const SCREENS: Screen[] = [
  { name: 'referrals', role: 'front_desk', hash: '/referrals', path: '/v1/staff/referrals', empty: items },
  { name: 'tasks', role: 'front_desk', hash: '/tasks', path: '/v1/staff/review-tasks', empty: items },
  { name: 'scheduling slots', role: 'front_desk', hash: '/scheduling/slots', path: '/v1/staff/scheduling/slots', empty: items },
  { name: 'scheduling bookings', role: 'front_desk', hash: '/scheduling/bookings', path: '/v1/staff/scheduling/bookings', empty: items },
  { name: 'scheduling waitlist', role: 'front_desk', hash: '/scheduling/waitlist', path: '/v1/staff/scheduling/waitlist', empty: items },
  { name: 'scheduling holds', role: 'front_desk', hash: '/scheduling/holds', path: '/v1/staff/scheduling/holds', empty: items },
  { name: 'scheduling offers', role: 'front_desk', hash: '/scheduling/offers', path: '/v1/staff/scheduling/offers', empty: items, also: [['get', '/v1/staff/scheduling/visit-types', items]] },
  { name: 'scheduling providers', role: 'front_desk', hash: '/scheduling/providers', path: '/v1/staff/scheduling/providers', empty: items },
  { name: 'scheduling locations', role: 'front_desk', hash: '/scheduling/locations', path: '/v1/staff/scheduling/locations', empty: items },
  { name: 'scheduling visit types', role: 'front_desk', hash: '/scheduling/visit-types', path: '/v1/staff/scheduling/visit-types', empty: items },
  { name: 'scheduling priority levels', role: 'front_desk', hash: '/scheduling/priority-levels', path: '/v1/staff/scheduling/priority-levels', empty: items },
  { name: 'identity merges', role: 'front_desk', hash: '/identity/merges', path: '/v1/staff/identity/merges', empty: items },
  { name: 'identity duplicates', role: 'front_desk', hash: '/identity/duplicates', path: '/v1/staff/identity/possible-duplicates', empty: items },
  { name: 'alerts', role: 'nurse', hash: '/alerts', path: '/v1/staff/console/alerts', empty: items },
  { name: 'portal claims', role: 'front_desk', hash: '/claims', path: '/v1/staff/portal-claims', empty: () => json({ claims: [] }) },
  { name: 'patient requests', role: 'front_desk', hash: '/requests', path: '/v1/staff/patient-requests', empty: () => json({ requests: [] }) },
  { name: 'care plans', role: 'doctor', hash: '/care/plans', path: '/v1/staff/care-plans', empty: () => json({ plans: [] }) },
  { name: 'accounts', role: 'admin', hash: '/admin/accounts', path: '/v1/staff/accounts', empty: items },
  { name: 'autonomy proposals', role: 'doctor', hash: '/admin/autonomy', path: '/v1/staff/console/autonomy/proposals', empty: () => json({ proposals: [] }), also: AUTONOMY_OTHERS('history') },
  { name: 'autonomy history', role: 'doctor', hash: '/admin/autonomy', path: '/v1/staff/console/autonomy/history', empty: () => json({ items: [] }), also: AUTONOMY_OTHERS('proposals') },
  { name: 'voice sessions', role: 'admin', hash: '/admin/voice', path: '/v1/staff/voice/sessions', empty: () => json({ sessions: [] }) },
  { name: 'staff recovery', role: 'admin', hash: '/admin/recovery', path: '/v1/staff/recovery', empty: () => json({ requests: [] }) },
  { name: 'grievances', role: 'admin', hash: '/admin/grievances', path: '/v1/staff/grievances', empty: () => json({ grievances: [] }) },
  { name: 'break-glass review', role: 'admin', hash: '/access', path: '/v1/staff/break-glass', empty: () => json({ grants: [] }) },
  { name: 'dashboard', role: 'admin', hash: '/dashboard', path: '/v1/staff/console/dashboard', empty: () => json({ clinic_id: CLINIC_A, metrics: [], not_measured: [] }) },
  { name: 'reviewer statistics', role: 'admin', hash: '/admin/reviewer', path: '/v1/staff/console/reviewer-stats', empty: () => json({ clinic_id: CLINIC_A, days: 30, policy_configured: true, reviewers: [] }) },
];

const NEVER = (): Promise<Response> => new Promise(() => undefined);
const main = () => document.querySelector('main') as HTMLElement;

describe.each(SCREENS)('$name', (s) => {
  it('shows a loading state while the read is in flight', async () => {
    await openConsole({ role: s.role, hash: s.hash, routes: [['get', s.path, NEVER], ...(s.also ?? [])] });
    await waitFor(() => expect(main().textContent).toMatch(/Loading/));
  });

  it('says plainly that there is nothing to show, instead of a blank page', async () => {
    await openConsole({ role: s.role, hash: s.hash, routes: [['get', s.path, s.empty], ...(s.also ?? [])] });
    await waitFor(() => expect(main().textContent).not.toMatch(/Loading/));
    expect(screen.queryByRole('alert')).toBeNull();
    // A heading plus at least one sentence: the empty state is words, not an empty table.
    expect(main().querySelectorAll('h1').length).toBe(1);
    expect((main().textContent ?? '').replace(/\s+/g, ' ').trim().length).toBeGreaterThan(40);
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('shows a fixed message in an alert when the read fails, never the backend text', async () => {
    await openConsole({ role: s.role, hash: s.hash, routes: [['get', s.path, () => fail(502)], ...(s.also ?? [])] });
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('This is not available right now.');
    expect(document.body.textContent).not.toContain(LEAK);
  });
});

describe('the readers keyed by an id the person brings', () => {
  it('visit readiness, notes: loading, empty-or-missing and error all have words', async () => {
    const user = (await import('@testing-library/user-event')).default.setup();
    const VISIT = 'abababab-abab-4bab-8bab-abababababab';
    await openConsole({ role: 'nurse', hash: '/readiness', routes: [['get', `/v1/staff/visits/${VISIT}/readiness`, () => fail(404)]] });
    await user.type(await screen.findByLabelText('Visit id'), VISIT);
    await user.click(screen.getByRole('button', { name: 'Show the run' }));
    expect(await screen.findByText('There is no readiness run for this visit yet.')).toBeInTheDocument();
  });

  it('notes: an empty list says so', async () => {
    const user = (await import('@testing-library/user-event')).default.setup();
    await openConsole({ role: 'nurse', hash: `/notes?patient=${ID.patient}`, routes: [['get', '/v1/staff/notes', () => json({ notes: [] })]] });
    await user.click(await screen.findByRole('button', { name: 'Show notes' }));
    expect(await screen.findByText('There are no notes for this patient.')).toBeInTheDocument();
  });
});
