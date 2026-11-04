import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { ErrorBoundary } from '../src/staff/components/ErrorBoundary';
import { App } from '../src/staff/App';
import { initI18n, setLanguage } from '../src/shared/i18n';
import { CLINIC_A, CLINIC_B, json, makeFakePort, stubApi } from './helpers/fakes';
import { ID, LEAK, backend, fail, list, openConsole, type Routes } from './helpers/console';

// MOCK-ONLY (see helpers/console.tsx). The header flag is a reviewed constant that is OFF; this file switches it on, for this
// file only, to prove what the header does when it is on. tests/flags.test.ts and tests/active-role-header.test.ts pin it off.
vi.mock('../src/staff/featureFlags', async (original) => ({ ...(await original<typeof import('../src/staff/featureFlags')>()), SEND_ACTIVE_ROLE_HEADER: true }));

afterEach(() => {
  vi.unstubAllGlobals();
  Object.defineProperty(window.navigator, 'onLine', { value: true, configurable: true });
  try {
    sessionStorage.clear();
  } catch {
    /* ignore */
  }
});

const referral = { clinic_id: CLINIC_A, content_type: 'application/pdf', created_at: '2026-10-30T05:00:00Z', decided: null, document_id: ID.doc, open_holds: 0, proposal_id: null, proposal_visible: false, registered_by: ID.user, run_state: 'needs_front_desk', size_bytes: 20480 };
const referralsRoute: Routes = [['get', '/v1/staff/referrals', () => list([referral])]];
const TWO = [{ clinic_id: CLINIC_A, role: 'nurse' }, { clinic_id: CLINIC_B, role: 'doctor' }];

/** Several roles: the picker comes first, so the console cannot be opened with openConsole (it waits for the session bar). */
async function openPicker(routes: Routes) {
  initI18n('en');
  await setLanguage('en');
  window.location.hash = '#/referrals';
  const fake = makeFakePort({ signedInAt: 'aal2' });
  const calls = stubApi(backend(routes, TWO));
  render(<App port={fake.port} apiBaseUrl="http://api.test" />);
  await screen.findByRole('heading', { name: 'Choose your active role' });
  return { calls, fake };
}

describe('active role plumbing (flag on only in this file)', () => {
  it('a single-role user never sends the header', async () => {
    const { calls } = await openConsole({ role: 'nurse', hash: '/referrals', routes: referralsRoute });
    await waitFor(() => expect(calls.some((c) => c.path === '/v1/staff/referrals')).toBe(true));
    expect(calls.filter((c) => c.activeRole !== null)).toEqual([]);
  });

  it('a multi-role user sends <clinic_id>:<role> for the chosen grant, and nothing before the choice', async () => {
    const user = userEvent.setup();
    const { calls } = await openPicker(referralsRoute);
    expect(calls.filter((c) => c.activeRole !== null)).toEqual([]);
    await user.click(screen.getByRole('radio', { name: /Doctor/ }));
    await user.click(screen.getByRole('button', { name: 'Continue' }));
    await waitFor(() => expect(calls.some((c) => c.path === '/v1/staff/referrals')).toBe(true));
    const sent = calls.filter((c) => c.path === '/v1/staff/referrals').map((c) => c.activeRole);
    expect(sent).toEqual([`${CLINIC_B}:doctor`]);
  });

  it('a 403 "active role not available" shows a calm message, goes back to the picker and does not sign the user out', async () => {
    const user = userEvent.setup();
    let first = true; // only the first choice is refused (say the nurse grant was revoked); the second works
    const refused: Routes = [['get', '/v1/staff/referrals', () => (first ? ((first = false), json({ detail: 'active role not available' }, 403)) : list([referral]))]];
    const { fake } = await openPicker(refused);
    await user.click(screen.getByRole('radio', { name: /Nurse/ }));
    await user.click(screen.getByRole('button', { name: 'Continue' }));
    const notice = await screen.findByText('That role is not available right now. Choose a role to continue. You are still signed in.');
    expect(notice).toHaveAttribute('role', 'status');
    expect(screen.getByRole('heading', { name: 'Choose your active role' })).toBeInTheDocument();
    expect(fake.port.signOut).not.toHaveBeenCalled();
    expect(document.body.textContent).not.toContain(LEAK);
    expect(sessionStorage.getItem('careflow.activeRole')).toBeNull(); // the refused choice is not remembered
    // Choosing again carries on: the notice goes away.
    await user.click(screen.getByRole('radio', { name: /Doctor/ }));
    await user.click(screen.getByRole('button', { name: 'Continue' }));
    await waitFor(() => expect(screen.queryByText(/not available right now/)).toBeNull());
  });

  it('any other 403 is the ordinary "not available to your role" and keeps the chosen role', async () => {
    const user = userEvent.setup();
    const other: Routes = [['get', '/v1/staff/referrals', () => fail(403)]];
    await openPicker(other);
    await user.click(screen.getByRole('radio', { name: /Nurse/ }));
    await user.click(screen.getByRole('button', { name: 'Continue' }));
    await screen.findByText('This is not available to your role.');
    expect(screen.queryByRole('heading', { name: 'Choose your active role' })).toBeNull();
  });
});

describe('session expiry', () => {
  it('a 401 ends the session with the calm fixed message and no data stays on screen', async () => {
    const user = userEvent.setup();
    let expired = false;
    const routes: Routes = [
      ['get', '/v1/staff/referrals', () => list([referral])],
      ['get', '/v1/staff/review-tasks', () => (expired ? fail(401) : list([]))],
    ];
    const { fake } = await openConsole({ role: 'nurse', hash: '/referrals', routes });
    await waitFor(() => expect(document.querySelector('main tbody tr')).not.toBeNull());
    expired = true;
    await user.click(screen.getByRole('link', { name: 'Tasks' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Please sign in again.');
    expect(fake.port.signOut).toHaveBeenCalled();
    expect(screen.queryByRole('group', { name: 'Session' })).toBeNull();
    expect(document.querySelector('table')).toBeNull();
    expect(document.body.textContent).not.toContain(LEAK);
    expect(document.body.textContent).not.toContain(ID.doc);
  });

  it('a session that ends elsewhere also clears the screen', async () => {
    const { fake } = await openConsole({ role: 'nurse', hash: '/referrals', routes: referralsRoute });
    await waitFor(() => expect(document.querySelector('main tbody tr')).not.toBeNull());
    act(() => fake.endSessionElsewhere());
    expect(await screen.findByRole('status')).toHaveTextContent('Please sign in again.');
    expect(document.querySelector('table')).toBeNull();
    expect(document.body.textContent).not.toContain(ID.doc);
  });
});

describe('offline', () => {
  it('shows a fixed banner while the browser reports no connection and removes it when it returns', async () => {
    await openConsole({ role: 'nurse', hash: '/referrals', routes: referralsRoute });
    expect(screen.queryByText(/You are offline/)).toBeNull();
    Object.defineProperty(window.navigator, 'onLine', { value: false, configurable: true });
    act(() => void window.dispatchEvent(new Event('offline')));
    expect(await screen.findByText('You are offline. Changes cannot be saved until the connection is back.')).toHaveAttribute('role', 'status');
    Object.defineProperty(window.navigator, 'onLine', { value: true, configurable: true });
    act(() => void window.dispatchEvent(new Event('online')));
    await waitFor(() => expect(screen.queryByText(/You are offline/)).toBeNull());
  });

  it('sends no write while offline and shows the calm connection message', async () => {
    const user = userEvent.setup();
    const task = {
      acknowledged_at: null, acknowledged_by: null, assigned_role: 'nurse', clinic_id: CLINIC_A, created_at: '2026-10-30T05:00:00Z', kind: 'referral_hold', priority: 'standard',
      resolved_at: null, status: 'open', task_id: ID.task, provenance: { document_id: ID.doc, reason: 'needs_front_desk' }, source_message_id: ID.doc2,
    };
    const routes: Routes = [['get', `/v1/staff/review-tasks/${ID.task}`, () => json(task)]];
    const { calls } = await openConsole({ role: 'nurse', hash: `/tasks/${ID.task}`, routes });
    const ack = await screen.findByRole('button', { name: /Acknowledge/ });
    Object.defineProperty(window.navigator, 'onLine', { value: false, configurable: true });
    await user.click(ack);
    const confirm = await screen.findByRole('dialog');
    await user.click(within(confirm).getByRole('button', { name: /Acknowledge/ }));
    expect(await screen.findByText('You are offline. Nothing was saved. What you typed is still here; send it again when the connection is back.')).toBeInTheDocument();
    expect(calls.filter((c) => c.method === 'post')).toEqual([]);
  });
});

describe('offline write keeps what the person chose', () => {
  it('says nothing was saved, keeps the chosen decision, and the same click works once the connection is back', async () => {
    const user = userEvent.setup();
    const detail = {
      clinic_id: CLINIC_A, content_type: 'application/pdf', created_at: '2026-10-30T05:00:00Z', decided: false, document_id: ID.doc, holds: [], open_holds: 0,
      proposal: { abstained: false, candidates: [{ card_id: 'card-x', version: 3 }], created_at: '2026-10-30T05:00:00Z', mode: 'shadow', patient_id: ID.patient, proposal_id: ID.doc2, review_task_id: ID.task },
      proposal_id: ID.doc2, proposal_visible: true, registered_by: ID.user, run_state: 'awaiting_decision', size_bytes: 2048,
    };
    const routes: Routes = [
      ['get', `/v1/staff/referrals/${ID.doc}`, () => json(detail)],
      ['post', `/v1/staff/referrals/${ID.doc}/review`, (req) => json({ decision: (req.body as { decision: string }).decision, document_id: ID.doc, replayed: false })],
    ];
    const { calls } = await openConsole({ role: 'nurse', hash: `/referrals/${ID.doc}`, routes });
    const group = await screen.findByRole('group', { name: 'Decision' });
    await user.click(within(group).getByRole('radio', { name: 'Reject' }));
    Object.defineProperty(window.navigator, 'onLine', { value: false, configurable: true });
    await user.click(screen.getByRole('button', { name: 'Record decision' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Record decision' }));
    expect(await screen.findByText(/Nothing was saved/)).toBeInTheDocument();
    expect(calls.filter((c) => c.method === 'post')).toEqual([]);
    expect(screen.getByRole('radio', { name: 'Reject' })).toBeChecked(); // what they chose is still there
    expect(document.body.textContent).not.toContain('The decision was recorded.');
    // No hidden queue: nothing is sent by itself when the connection returns.
    Object.defineProperty(window.navigator, 'onLine', { value: true, configurable: true });
    act(() => void window.dispatchEvent(new Event('online')));
    expect(calls.filter((c) => c.method === 'post')).toEqual([]);
    // The confirmation stays open after a failed attempt, with the same choice in it: confirm again.
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Record decision' }));
    expect(await screen.findByText('The decision was recorded.')).toBeInTheDocument();
    expect(calls.filter((c) => c.method === 'post')).toHaveLength(1);
  });
});

describe('role memory is per sign-in', () => {
  it('forgets the chosen role at sign-out, so the next sign-in picks again', async () => {
    const user = userEvent.setup();
    const { fake } = await openPicker(referralsRoute);
    await user.click(screen.getByRole('radio', { name: /Nurse/ }));
    await user.click(screen.getByRole('button', { name: 'Continue' }));
    await screen.findByRole('group', { name: 'Session' });
    expect(sessionStorage.getItem('careflow.activeRole')).not.toBeNull();
    await user.click(screen.getByRole('button', { name: 'Sign out' }));
    await waitFor(() => expect(sessionStorage.getItem('careflow.activeRole')).toBeNull());
    void fake;
  });
});

function Boom({ explode }: { explode: boolean }) {
  if (explode) throw new Error(`secret-detail ${LEAK}`);
  return <p>fine</p>;
}

describe('error boundary', () => {
  it('shows a fixed message with no error text, and a fresh screen gets a fresh chance', async () => {
    initI18n('en');
    await setLanguage('en');
    const spy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    function Host() {
      const [key, setKey] = useState('a');
      return (
        <>
          <button type="button" onClick={() => setKey('b')}>
            go
          </button>
          <ErrorBoundary resetKey={key}>
            <Boom explode={key === 'a'} />
          </ErrorBoundary>
        </>
      );
    }
    const user = userEvent.setup();
    render(<Host />);
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('This screen could not be shown');
    expect(alert).toHaveTextContent('Reload the page, or go to the home screen.');
    expect(document.body.textContent).not.toMatch(/secret-detail|LEAK-SENTINEL|Error:/);
    expect(screen.getByRole('heading', { level: 1 })).toHaveFocus();
    await user.click(screen.getByRole('button', { name: 'go' }));
    expect(await screen.findByText('fine')).toBeInTheDocument();
    spy.mockRestore();
  });
});
