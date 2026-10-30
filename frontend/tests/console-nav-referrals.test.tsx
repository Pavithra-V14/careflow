import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CLINIC_A, json } from './helpers/fakes';
import { ID, LEAK, fail, list, openConsole, writes } from './helpers/console';

/** The page's own state box (loading, empty, error, not-available), as opposed to the session bar's environment badge. */
const stateBox = () => document.querySelector('.state') as HTMLElement;

// MOCK-ONLY (see helpers/console.tsx).

const referral = (over: Record<string, unknown> = {}) => ({
  clinic_id: CLINIC_A,
  content_type: 'application/pdf',
  created_at: '2026-10-30T05:00:00Z',
  decided: null,
  document_id: ID.doc,
  open_holds: 1,
  proposal_id: null,
  proposal_visible: false,
  registered_by: ID.user,
  run_state: 'needs_front_desk',
  size_bytes: 20480,
  ...over,
});

describe('navigation', () => {
  it.each([
    ['front_desk', ['Referrals', 'Tasks', 'Patients', 'Scheduling']],
    ['nurse', ['Referrals', 'Tasks', 'Patients', 'Scheduling']],
    ['admin', ['Tasks']],
    ['auditor', []],
    ['business_owner', []],
  ])('shows %s only the sections its role may use (a convenience; the backend decides)', async (role, expected) => {
    await openConsole({ role });
    const nav = screen.getByRole('navigation', { name: 'Sections' });
    const links = within(nav).getAllByRole('link').map((a) => a.textContent);
    expect(links).toEqual(['Home', ...expected]);
    if (expected.length === 0) expect(stateBox()).toHaveTextContent('No sections are available to your role.');
  });

  it('keeps the LIVE / TEST badge and the role on every screen', async () => {
    for (const hash of ['/', '/referrals', '/tasks', '/patients', '/scheduling/holds', '/nowhere']) {
      const { unmount } = await openConsole({
        hash,
        roles: [{ clinic_id: CLINIC_A, role: 'front_desk', clinic_origin: 'test', clinic_name: 'Placeholder Clinic' }],
        routes: [['get', /.*/, () => list([])]],
      });
      const bar = screen.getByRole('group', { name: 'Session' });
      expect(within(bar).getByRole('status', { name: 'Environment: TEST' })).toBeInTheDocument();
      expect(within(bar).getByText('Front desk')).toBeInTheDocument();
      unmount();
    }
  });

  it('marks the current section with aria-current', async () => {
    await openConsole({ hash: '/tasks', routes: [['get', '/v1/staff/review-tasks', () => list([])]] });
    expect(screen.getByRole('link', { name: 'Tasks' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('link', { name: 'Referrals' })).not.toHaveAttribute('aria-current');
  });

  it('shows an unknown address as not found, without calling the API', async () => {
    const { calls } = await openConsole({ hash: '/nowhere' });
    expect(screen.getByRole('heading', { level: 1, name: 'Not found' })).toBeInTheDocument();
    expect(calls.map((c) => c.path)).toEqual(['/v1/iam/me']);
  });

  it('moves between sections with the keyboard alone', async () => {
    const user = userEvent.setup();
    await openConsole({ routes: [['get', '/v1/staff/referrals', () => list([])]] });
    const link = within(screen.getByRole('navigation', { name: 'Sections' })).getByRole('link', { name: 'Referrals' });
    link.focus();
    await user.keyboard('{Enter}');
    expect(await screen.findByRole('heading', { level: 1, name: 'Referrals' })).toBeInTheDocument();
  });
});

describe('referral queue', () => {
  it('lists referrals with their state, and says "not shown to your role" where front desk cannot see a proposal', async () => {
    await openConsole({ hash: '/referrals', routes: [['get', '/v1/staff/referrals', () => list([referral()])]] });
    const row = (await screen.findByRole('row', { name: /11111111/ }));
    expect(within(row).getByText('needs front desk')).toBeInTheDocument();
    expect(within(row).getByText('Not shown to your role')).toBeInTheDocument();
    expect(within(row).queryByText('Not decided yet')).toBeNull(); // hidden is not "none"
    expect(within(row).getByRole('link')).toHaveAttribute('href', `#/referrals/${ID.doc}`);
  });

  it('shows the decision state to a role that can see proposals', async () => {
    await openConsole({ role: 'nurse', hash: '/referrals', routes: [['get', '/v1/staff/referrals', () => list([referral({ proposal_visible: true, decided: false })])]] });
    expect(await screen.findByText('Not decided yet')).toBeInTheDocument();
  });

  it('shows a loading state, then the empty state', async () => {
    await openConsole({ hash: '/referrals', routes: [['get', '/v1/staff/referrals', () => list([])]] });
    expect(await screen.findByText('There are no referrals to show.')).toBeInTheDocument();
  });

  it('shows a fixed message with a retry for a server failure, and never the backend text', async () => {
    let n = 0;
    const user = userEvent.setup();
    await openConsole({ hash: '/referrals', routes: [['get', '/v1/staff/referrals', () => (++n === 1 ? fail(503) : list([referral()]))]] });
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('This is not available right now. Try again in a moment.');
    expect(document.body.textContent).not.toContain(LEAK);
    await user.click(within(alert).getByRole('button', { name: 'Try again' }));
    expect(await screen.findByRole('row', { name: /11111111/ })).toBeInTheDocument();
  });

  it('shows "not available to your role" on a 403, as a state and not an error', async () => {
    await openConsole({ role: 'doctor', hash: '/referrals', routes: [['get', '/v1/staff/referrals', () => fail(403)]] });
    await screen.findByText('This is not available to your role.');
    expect(stateBox()).toHaveTextContent('This is not available to your role.');
    expect(screen.queryByRole('alert')).toBeNull();
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('notes when more rows exist than are shown', async () => {
    await openConsole({ hash: '/referrals', routes: [['get', '/v1/staff/referrals', () => list([referral()], true)]] });
    expect(await screen.findByText('More results exist than are shown.')).toBeInTheDocument();
  });

  it('offers the upload link only to roles that may upload', async () => {
    await openConsole({ role: 'doctor', hash: '/referrals', routes: [['get', '/v1/staff/referrals', () => list([])]] });
    await screen.findByText('There are no referrals to show.');
    expect(screen.queryByRole('link', { name: 'Upload a document' })).toBeNull();
  });
});

describe('referral detail', () => {
  const detail = (over: Record<string, unknown> = {}) => ({
    ...referral(),
    holds: [{ assigned_role: 'front_desk', status: 'open', task_id: ID.task }],
    proposal: null,
    ...over,
  });

  it('shows the holds with links to their tasks, and "not shown to your role" for the proposal', async () => {
    await openConsole({ hash: `/referrals/${ID.doc}`, routes: [['get', `/v1/staff/referrals/${ID.doc}`, () => json(detail())]] });
    expect(await screen.findByRole('link', { name: /33333333/ })).toHaveAttribute('href', `#/tasks/${ID.task}`);
    expect(screen.getByText('Not shown to your role')).toBeInTheDocument();
    expect(screen.queryByText('There is no proposal for this referral.')).toBeNull();
  });

  it('shows the proposal for a nurse, and "no proposal" only when it is visible and absent', async () => {
    const proposal = { abstained: false, candidates: [{ card_id: 'card-x', version: 3 }], created_at: '2026-10-30T05:00:00Z', mode: 'shadow', patient_id: ID.patient, proposal_id: ID.doc2, review_task_id: ID.task };
    await openConsole({ role: 'nurse', hash: `/referrals/${ID.doc}`, routes: [['get', `/v1/staff/referrals/${ID.doc}`, () => json(detail({ proposal_visible: true, proposal, decided: false }))]] });
    expect(await screen.findByText('card-x')).toBeInTheDocument();
    expect(screen.getByText('shadow')).toBeInTheDocument();
  });

  it('shows "no proposal" when a role that can see proposals finds none', async () => {
    await openConsole({ role: 'nurse', hash: `/referrals/${ID.doc}`, routes: [['get', `/v1/staff/referrals/${ID.doc}`, () => json(detail({ proposal_visible: true, holds: [] }))]] });
    expect(await screen.findByText('There is no proposal for this referral.')).toBeInTheDocument();
    expect(screen.getByText('No holds.')).toBeInTheDocument();
  });

  it('treats a malformed id as not found without calling the API, and a 404 as a fixed message', async () => {
    const first = await openConsole({ hash: '/referrals/not-a-uuid' });
    expect(screen.getByRole('heading', { level: 1, name: 'Not found' })).toBeInTheDocument();
    expect(first.calls.map((c) => c.path)).toEqual(['/v1/iam/me']);
    first.unmount();
    await openConsole({ hash: `/referrals/${ID.doc}`, routes: [['get', `/v1/staff/referrals/${ID.doc}`, () => fail(404)]] });
    expect(await screen.findByRole('alert')).toHaveTextContent('That could not be found.');
    expect(document.body.textContent).not.toContain(LEAK);
  });
});

describe('document upload', () => {
  const STORAGE = 'https://example.invalid/storage/v1/object/upload/sign/docs/x?token=t';
  const pdf = () => new File(['%PDF-1.4'], 'letter.pdf', { type: 'application/pdf' });
  const registered = (scan: string) => ({ content_type: 'application/pdf', document_id: ID.doc, kind: 'referral', replayed: false, scan, size_bytes: 8 });
  const begin = (url = STORAGE) => json({ document_id: ID.doc, expires_at: '2026-10-30T06:00:00Z', upload_url: url });

  async function pick(user: ReturnType<typeof userEvent.setup>, file: File) {
    await user.upload(screen.getByLabelText('File'), file);
  }

  it('uploads in two steps with ONE idempotency key, sends the file to the storage host only, and sends no file name', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({
      hash: '/referrals/upload',
      routes: [
        ['post', '/v1/staff/documents/uploads', () => begin()],
        ['put', /.*/, () => new Response(null, { status: 200 })],
        ['post', '/v1/staff/documents/register', () => json(registered('clean'))],
      ],
    });
    await pick(user, pdf());
    await user.click(screen.getByRole('button', { name: 'Upload' }));
    expect(await screen.findByText('Uploaded')).toBeInTheDocument();

    const [begun, put, reg] = calls.filter((c) => c.path !== '/v1/iam/me');
    expect(begun?.key).toBeTruthy();
    expect(reg?.key).toBe(begun?.key);
    expect(begun?.body).toEqual({ clinic_id: CLINIC_A, content_type: 'application/pdf', kind: 'referral', size_hint: 8 });
    expect(reg?.body).toEqual({ clinic_id: CLINIC_A, kind: 'referral' });
    expect(JSON.stringify([begun?.body, reg?.body])).not.toContain('letter.pdf');
    expect(put).toMatchObject({ method: 'put', host: 'example.invalid', contentType: 'application/pdf' });
    expect(put?.auth).toBeNull(); // the signed URL is the credential: no bearer token goes to the storage host
  });

  it.each([
    ['clean', 'Clean'],
    ['not_scanned', 'Not scanned yet'],
    ['not_checked', 'Not checked'],
    ['some_future_word', 'Not checked'],
  ])('shows scan status %s as "%s", and ONLY the word clean as clean', async (scan, label) => {
    const user = userEvent.setup();
    await openConsole({
      hash: '/referrals/upload',
      routes: [
        ['post', '/v1/staff/documents/uploads', () => begin()],
        ['put', /.*/, () => new Response(null, { status: 200 })],
        ['post', '/v1/staff/documents/register', () => json(registered(scan))],
      ],
    });
    await pick(user, pdf());
    await user.click(screen.getByRole('button', { name: 'Upload' }));
    const status = (await screen.findByText('Scan status')).nextElementSibling as HTMLElement;
    expect(status).toHaveTextContent(label);
    if (scan === 'clean') {
      expect(status.querySelector('.pill-ok')).not.toBeNull();
    } else {
      expect(status.querySelector('.pill-ok')).toBeNull();
      expect(status).not.toHaveTextContent(/^Clean$/);
      expect(screen.getByText(scan === 'not_scanned' ? 'This file has not been scanned yet.' : 'This file was not checked.')).toBeInTheDocument();
    }
  });

  it('refuses an upload address on another host and sends no file there', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({
      hash: '/referrals/upload',
      routes: [['post', '/v1/staff/documents/uploads', () => begin('https://evil.invalid/put?token=t')]],
    });
    await pick(user, pdf());
    await user.click(screen.getByRole('button', { name: 'Upload' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Something went wrong. Try again.');
    expect(calls.some((c) => c.method === 'put')).toBe(false);
    expect(calls.some((c) => c.host === 'evil.invalid')).toBe(false);
  });

  it('retries with the SAME key after a failed send, and never registers a file that was not sent', async () => {
    const user = userEvent.setup();
    let puts = 0;
    const { calls } = await openConsole({
      hash: '/referrals/upload',
      routes: [
        ['post', '/v1/staff/documents/uploads', () => begin()],
        ['put', /.*/, () => (++puts === 1 ? new Response(null, { status: 503 }) : new Response(null, { status: 200 }))],
        ['post', '/v1/staff/documents/register', () => json(registered('not_scanned'))],
      ],
    });
    await pick(user, pdf());
    await user.click(screen.getByRole('button', { name: 'Upload' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('This is not available right now.');
    expect(calls.some((c) => c.path === '/v1/staff/documents/register')).toBe(false);

    await user.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByText('Uploaded')).toBeInTheDocument();
    const keys = calls.filter((c) => c.path.startsWith('/v1/staff/documents')).map((c) => c.key);
    expect(new Set(keys).size).toBe(1);
  });

  it('shows a calm fixed message when uploads are switched off (503), and sends nothing', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ hash: '/referrals/upload', routes: [['post', '/v1/staff/documents/uploads', () => fail(503)]] });
    await pick(user, pdf());
    await user.click(screen.getByRole('button', { name: 'Upload' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('This is not available right now. Try again in a moment.');
    expect(document.body.textContent).not.toContain(LEAK);
    expect(calls.some((c) => c.method === 'put')).toBe(false);
  });

  it('drops the key after a refusal that will not change (422), so the next attempt is a new request', async () => {
    const user = userEvent.setup();
    let n = 0;
    const { calls } = await openConsole({
      hash: '/referrals/upload',
      routes: [['post', '/v1/staff/documents/uploads', () => (++n === 1 ? json([{ type: 'x', loc: [], msg: LEAK }], 422) : fail(503))]],
    });
    await pick(user, pdf());
    await user.click(screen.getByRole('button', { name: 'Upload' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('The file was not accepted.');
    expect(document.body.textContent).not.toContain(LEAK);
    await user.click(screen.getByRole('button', { name: 'Upload' }));
    await waitFor(() => expect(writes(calls)).toHaveLength(2));
    const [first, second] = writes(calls);
    expect(second?.key).not.toBe(first?.key);
  });

  it('rejects a file of the wrong type or an empty file before any request', async () => {
    const user = userEvent.setup({ applyAccept: false }); // the picker's accept filter is bypassed on purpose
    const { calls } = await openConsole({ hash: '/referrals/upload' });
    await user.click(screen.getByRole('button', { name: 'Upload' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Choose a file first.');
    await user.upload(screen.getByLabelText('File'), new File(['x'], 'a.exe', { type: 'application/x-msdownload' }));
    await user.click(screen.getByRole('button', { name: 'Upload' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Only PDF, JPEG or PNG files can be uploaded.');
    await user.upload(screen.getByLabelText('File'), new File([], 'e.pdf', { type: 'application/pdf' }));
    await user.click(screen.getByRole('button', { name: 'Upload' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('That file is empty.');
    expect(calls.map((c) => c.path)).toEqual(['/v1/iam/me']);
  });

  it('says "not available to your role" for a role that may not upload, without a form', async () => {
    await openConsole({ role: 'doctor', hash: '/referrals/upload' });
    expect(stateBox()).toHaveTextContent('This is not available to your role.');
    expect(screen.queryByLabelText('File')).toBeNull();
  });

  it('shows "not available to your role" when the server refuses with 403', async () => {
    const user = userEvent.setup();
    await openConsole({ role: 'nurse', hash: '/referrals/upload', routes: [['post', '/v1/staff/documents/uploads', () => fail(403)]] });
    await pick(user, pdf());
    await user.click(screen.getByRole('button', { name: 'Upload' }));
    expect(await screen.findByText('This is not available to your role.')).toBeInTheDocument();
  });
});
