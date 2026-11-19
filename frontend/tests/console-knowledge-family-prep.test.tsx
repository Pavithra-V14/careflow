import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ACCOUNT_ID, CLINIC_A, json } from './helpers/fakes';
import { ID, LEAK, fail, openConsole, writes, type Routes } from './helpers/console';

// MOCK-ONLY (see helpers/console.tsx): the cards-and-templates editor, the front-desk family link screen and the staff view of visit
// preparation. Every error body carries LEAK, which must never reach the page.
const NO_RULES = 'family links are not set up: the clinic has not entered its rules';
const ME = ACCOUNT_ID; // the account id in the fake /v1/iam/me answer
const OTHER = '12121212-1212-4212-8212-121212121212';
const D1 = 'a1a1a1a1-a1a1-4a1a-8a1a-a1a1a1a1a1a1';
const D2 = 'a2a2a2a2-a2a2-4a2a-8a2a-a2a2a2a2a2a2';

const draft = (over: Record<string, unknown> = {}) => ({ audience: ['nurse'], author: OTHER, body: 'Typed body\nline <b>two</b>', code: 'typed.code', decided_by: null, draft_id: D1, kind: 'card', patient_phrases: ['typed phrase'], reject_reason: null, status: 'submitted', synonyms: ['typed synonym'], title: 'Typed title', updated_at: '2026-11-09T05:00:00Z', ...over });
const drafts = (items: unknown[]): Routes[number] => ['get', '/v1/staff/knowledge/drafts', () => json({ drafts: items })];
const items = (rows: unknown[] = []): Routes[number] => ['get', '/v1/staff/knowledge/items', () => json({ items: rows })];

describe('cards and templates editor', () => {
  it('starts empty with an honest statement that nothing is supplied, offers a draft, and shows no content as clinical', async () => {
    await openConsole({ role: 'nurse', hash: '/knowledge', routes: [drafts([]), items()] });
    expect(await screen.findByText(/There are no drafts\. Nothing is supplied by the system/)).toBeInTheDocument();
    expect(screen.getByText('There are no approved cards or templates.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Write a new draft' })).toBeInTheDocument();
  });

  it('says "not turned on" for a 503 and offers nothing', async () => {
    await openConsole({ role: 'nurse', hash: '/knowledge', routes: [['get', '/v1/staff/knowledge/drafts', () => fail(503)], items()] });
    expect(await screen.findByText('The cards and templates editor is not turned on yet.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Write a new draft' })).toBeNull();
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('writes a draft only after a confirmation, with a key and exactly what was typed; nothing is pre-chosen', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ role: 'nurse', hash: '/knowledge', routes: [drafts([]), items(), ['post', '/v1/staff/knowledge/drafts', () => json({ created: true, draft_id: D1 }, 201)]] });
    await user.click(await screen.findByRole('button', { name: 'Write a new draft' }));
    expect(screen.getByLabelText('Kind')).toHaveValue('');
    expect(screen.getByRole('button', { name: 'Save the draft' })).toBeDisabled();
    await user.selectOptions(screen.getByLabelText('Kind'), 'card');
    await user.type(screen.getByLabelText('Code'), 'typed.code');
    await user.type(screen.getByLabelText('Title'), 'Typed title');
    await user.type(screen.getByLabelText('Text'), 'Typed body');
    await user.type(screen.getByLabelText(/^Synonyms/), 'one{enter}two');
    expect(screen.getByRole('button', { name: 'Save the draft' })).toBeDisabled(); // a card needs a role
    expect(screen.getByText('A card needs at least one role.')).toBeInTheDocument();
    await user.click(screen.getByRole('checkbox', { name: 'Nurse' }));
    await user.click(screen.getByRole('button', { name: 'Save the draft' }));
    expect(writes(calls)).toHaveLength(0);
    await user.click(within(screen.getByRole('dialog', { name: 'Save this draft?' })).getByRole('button', { name: 'Save the draft' }));
    expect(await screen.findByText('The draft was saved.')).toBeInTheDocument();
    expect(writes(calls)[0]?.body).toEqual({ audience: ['nurse'], body: 'Typed body', clinic_id: CLINIC_A, code: 'typed.code', kind: 'card', patient_phrases: [], synonyms: ['one', 'two'], title: 'Typed title' });
    expect(writes(calls)[0]?.key).toBeTruthy();
  });

  it('a template may have no audience; a bad code keeps Save off', async () => {
    const user = userEvent.setup();
    await openConsole({ role: 'nurse', hash: '/knowledge', routes: [drafts([]), items()] });
    await user.click(await screen.findByRole('button', { name: 'Write a new draft' }));
    await user.selectOptions(screen.getByLabelText('Kind'), 'template');
    await user.type(screen.getByLabelText('Code'), 'Bad Code');
    await user.type(screen.getByLabelText('Title'), 'T');
    await user.type(screen.getByLabelText('Text'), 'B');
    expect(screen.getByRole('button', { name: 'Save the draft' })).toBeDisabled();
    await user.clear(screen.getByLabelText('Code'));
    await user.type(screen.getByLabelText('Code'), 'good-code');
    expect(screen.getByRole('button', { name: 'Save the draft' })).toBeEnabled();
  });

  it.each([
    [409, 'A draft for this code is already open, or this request conflicts with an earlier one. Nothing was saved.'],
    [403, 'You may not do this, or it needs a recent second factor. Nothing was saved.'],
    [422, 'That was not accepted. Check what you typed. Nothing was recorded.'],
  ])('tells a %s on saving in fixed words and never the backend text', async (status, words) => {
    const user = userEvent.setup();
    await openConsole({ role: 'nurse', hash: '/knowledge', routes: [drafts([]), items(), ['post', '/v1/staff/knowledge/drafts', () => fail(status)]] });
    await user.click(await screen.findByRole('button', { name: 'Write a new draft' }));
    await user.selectOptions(screen.getByLabelText('Kind'), 'template');
    await user.type(screen.getByLabelText('Code'), 'good-code');
    await user.type(screen.getByLabelText('Title'), 'T');
    await user.type(screen.getByLabelText('Text'), 'B');
    await user.click(screen.getByRole('button', { name: 'Save the draft' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Save the draft' }));
    expect(await within(screen.getByRole('dialog')).findByText(words)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('the author edits and submits their own draft (a PUT and a POST, each with a key and after a confirmation)', async () => {
    const user = userEvent.setup();
    const mine = draft({ author: ME, status: 'draft' });
    const { calls } = await openConsole({
      role: 'nurse',
      hash: '/knowledge',
      routes: [drafts([mine]), items(), ['put', `/v1/staff/knowledge/drafts/${D1}`, () => json({ result: 'recorded' })], ['post', `/v1/staff/knowledge/drafts/${D1}/submit`, () => json({ result: 'recorded' })]],
    });
    await user.click(await screen.findByRole('button', { name: /^Edit typed.code/ }));
    await user.clear(screen.getByLabelText('Title'));
    await user.type(screen.getByLabelText('Title'), 'New title');
    await user.click(screen.getByRole('button', { name: 'Save the draft' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Save the draft' }));
    expect(await screen.findByText('The draft was changed.')).toBeInTheDocument();
    expect(calls.filter((c) => c.method === 'put')[0]?.key).toBeTruthy();
    await user.click(await screen.findByRole('button', { name: /^Submit typed.code/ }));
    await user.click(within(screen.getByRole('dialog', { name: 'Submit this draft for approval?' })).getByRole('button', { name: 'Submit' }));
    expect(await screen.findByText('It was recorded.')).toBeInTheDocument();
    expect(calls.some((c) => c.path.endsWith('/submit') && !!c.key)).toBe(true);
  });

  it('a doctor approves another person\'s submitted draft after a confirmation, seeing exactly the text; an approval names the version', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ role: 'doctor', hash: '/knowledge', routes: [drafts([draft()]), items(), ['post', `/v1/staff/knowledge/drafts/${D1}/decision`, () => json({ result: 'recorded', version: 3 })]] });
    await user.click(await screen.findByRole('button', { name: /^Approve typed.code/ }));
    const dialog = screen.getByRole('dialog', { name: 'Approve this draft?' });
    expect(within(dialog).getByText(/Typed body/).textContent).toBe('Typed body\nline <b>two</b>');
    expect(writes(calls)).toHaveLength(0);
    await user.click(within(dialog).getByRole('button', { name: 'Approve' }));
    expect(await screen.findByText('Approved as version 3.')).toBeInTheDocument();
    expect(writes(calls)[0]?.body).toEqual({ clinic_id: CLINIC_A, decision: 'approve' });
    expect(writes(calls)[0]?.key).toBeTruthy();
  });

  it('a reject needs a reason word and sends it', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ role: 'doctor', hash: '/knowledge', routes: [drafts([draft()]), items(), ['post', `/v1/staff/knowledge/drafts/${D1}/decision`, () => json({ result: 'recorded', version: null })]] });
    await user.click(await screen.findByRole('button', { name: /^Reject typed.code/ }));
    const dialog = screen.getByRole('dialog', { name: 'Reject this draft?' });
    expect(within(dialog).getByRole('button', { name: 'Reject' })).toBeDisabled();
    await user.type(within(dialog).getByLabelText('Reason word'), 'unclear_wording');
    await user.click(within(dialog).getByRole('button', { name: 'Reject' }));
    await waitFor(() => expect(writes(calls)).toHaveLength(1));
    expect(writes(calls)[0]?.body).toEqual({ clinic_id: CLINIC_A, decision: 'reject', reason: 'unclear_wording' });
  });

  it('offers no approve to the author or to a nurse; the author sees why', async () => {
    const mine = draft({ author: ME });
    const a = await openConsole({ role: 'doctor', hash: '/knowledge', routes: [drafts([mine]), items()] });
    expect(await screen.findByText('You wrote this draft. A different doctor must decide.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Approve/ })).toBeNull();
    a.unmount();
    await openConsole({ role: 'nurse', hash: '/knowledge', routes: [drafts([draft()]), items()] });
    await screen.findByRole('table');
    expect(screen.queryByRole('button', { name: /^Approve/ })).toBeNull();
  });

  it.each([
    [403, 'You may not decide this: only a doctor who did not write the draft may, and it needs a recent second factor. Nothing was recorded.'],
    [404, 'This draft was not found. Nothing was recorded.'],
  ])('tells a %s on a decision in fixed words', async (status, words) => {
    const user = userEvent.setup();
    await openConsole({ role: 'doctor', hash: '/knowledge', routes: [drafts([draft()]), items(), ['post', `/v1/staff/knowledge/drafts/${D1}/decision`, () => fail(status)]] });
    await user.click(await screen.findByRole('button', { name: /^Approve typed.code/ }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Approve' }));
    expect(await within(screen.getByRole('dialog')).findByText(words)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('shows approved versions with author and approver as ids', async () => {
    await openConsole({
      role: 'doctor',
      hash: '/knowledge',
      routes: [drafts([draft({ status: 'approved', draft_id: D2 })]), items([{ approved_at: '2026-11-09T06:00:00Z', approved_by: OTHER, audience: ['nurse', 'doctor'], author: ME, code: 'typed.code', kind: 'card', title: 'Typed title', version: 2 }])],
    });
    const table = (await screen.findAllByRole('table'))[1]!;
    expect(within(table).getByText('Nurse, Doctor')).toBeInTheDocument();
    expect(within(table).getByText('2')).toBeInTheDocument();
  });
});

describe('family links (front desk)', () => {
  const link = { created_at: '2026-11-09T05:00:00Z', dependant_patient_id: null, link_id: D1, link_type: 'guardian_of_minor', requester_patient_id: ID.patient, status: 'requested' };
  const family = (rows: unknown[] = [link]): Routes[number] => ['get', '/v1/staff/family-links', () => json({ links: rows })];
  const decision = (result = 'recorded'): Routes[number] => ['post', `/v1/staff/family-links/${D1}/decision`, () => json({ result })];
  const search: Routes[number] = ['post', '/v1/staff/patients/search', () => json({ items: [{ patient_id: ID.patient2, verified: true, registered_via: 'staff', matched_kinds: ['name'] }], labels_withheld: false, truncated: false })];

  it('lists links with closed words and ids only, and an empty state', async () => {
    const a = await openConsole({ hash: '/family', routes: [family()] });
    expect(await screen.findByText('Guardian of a child')).toBeInTheDocument();
    expect(screen.getByText('Waiting for a check')).toBeInTheDocument();
    a.unmount();
    await openConsole({ hash: '/family', routes: [family([])] });
    expect(await screen.findByText('There are no family link requests.')).toBeInTheDocument();
  });

  it('says "not set up: no rules" in fixed words (never the backend sentence) and "not turned on" for the switch, offering nothing', async () => {
    const a = await openConsole({ hash: '/family', routes: [['get', '/v1/staff/family-links', () => json({ detail: NO_RULES }, 503)]] });
    expect(await screen.findByText('Family links are not set up for this clinic: the clinic has not entered its rules. Nothing can be recorded.')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(NO_RULES);
    a.unmount();
    await openConsole({ hash: '/family', routes: [['get', '/v1/staff/family-links', () => json({ detail: LEAK }, 503)]] });
    expect(await screen.findByText('Family links are not turned on yet.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Verify/ })).toBeNull();
  });

  it('verifies only with a chosen record and a stated consent (nothing pre-chosen), after a confirmation, with a key and no age sent', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ hash: '/family', routes: [family(), search, decision()] });
    await user.click(await screen.findByRole('button', { name: /^Verify/ }));
    const dialog = screen.getByRole('dialog', { name: 'Verify this link?' });
    const confirm = within(dialog).getByRole('button', { name: 'Verify' });
    expect(confirm).toBeDisabled();
    expect(within(dialog).getByRole('radio', { name: 'Yes, it was obtained' })).not.toBeChecked();
    await user.type(within(dialog).getByLabelText('Full name'), 'Test Person');
    await user.type(within(dialog).getByLabelText('Date of birth'), '2015-02-03');
    await user.click(within(dialog).getByRole('button', { name: 'Find' }));
    await user.click((await within(dialog).findAllByRole('radio'))[0]!); // the found record (above the consent choice)
    expect(confirm).toBeDisabled(); // consent still unanswered
    await user.click(within(dialog).getByRole('radio', { name: 'No, it was not obtained' }));
    expect(writes(calls).filter((c) => c.path.endsWith('/decision'))).toHaveLength(0);
    await user.click(confirm);
    expect(await screen.findByText('The decision was recorded.')).toBeInTheDocument();
    const sent = writes(calls).find((c) => c.path.endsWith('/decision'));
    expect(sent?.body).toEqual({ clinic_id: CLINIC_A, decision: 'verify', dependant_consent: false, dependant_patient_id: ID.patient2 });
    expect(sent?.key).toBeTruthy();
  });

  it.each([
    ['age_not_allowed', "The clinic's rule does not allow this link for the dependant's age. Nothing was recorded."],
    ['consent_required', "The clinic's rule needs the linked person's consent. Nothing was recorded."],
    ['invalid_state', 'This request is not waiting for a decision. Nothing was recorded.'],
    ['replay', 'This was already recorded.'],
  ])('says %s in fixed words', async (result, words) => {
    const user = userEvent.setup();
    await openConsole({ hash: '/family', routes: [family(), decision(result)] });
    await user.click(await screen.findByRole('button', { name: /^Reject/ }));
    const dialog = screen.getByRole('dialog', { name: 'Reject this link?' });
    await user.type(within(dialog).getByLabelText('Reason word'), 'not_verified');
    await user.click(within(dialog).getByRole('button', { name: 'Reject' }));
    expect(await screen.findByText(words)).toBeInTheDocument();
  });

  it('a reject needs a valid reason word; errors are fixed words; no backend text', async () => {
    const user = userEvent.setup();
    await openConsole({ hash: '/family', routes: [family(), ['post', `/v1/staff/family-links/${D1}/decision`, () => fail(403)]] });
    await user.click(await screen.findByRole('button', { name: /^Reject/ }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByRole('button', { name: 'Reject' })).toBeDisabled();
    await user.type(within(dialog).getByLabelText('Reason word'), 'Bad Word');
    expect(within(dialog).getByRole('button', { name: 'Reject' })).toBeDisabled();
    await user.clear(within(dialog).getByLabelText('Reason word'));
    await user.type(within(dialog).getByLabelText('Reason word'), 'ok_word');
    await user.click(within(dialog).getByRole('button', { name: 'Reject' }));
    expect(await within(dialog).findByText(/You may not decide this, or it needs a recent second factor/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('is not available to another role', async () => {
    await openConsole({ role: 'admin', hash: '/family', routes: [['get', '/v1/staff/family-links', () => fail(403)]] });
    await screen.findByText('This is not available to your role.');
  });
});

describe('visit preparation (staff view)', () => {
  const VISIT = '99999999-9999-4999-8999-999999999999';
  const prep = (over: Record<string, unknown> = {}): Routes[number] => [
    'get',
    `/v1/staff/visits/${VISIT}/prep`,
    () => json({ booking_id: VISIT, items: [{ done: false, item_id: D1, marked_at: '2026-11-09T05:00:00Z', text: 'Clinic item one' }, { done: null, item_id: D2, marked_at: null, text: 'Clinic item two' }], note: null, nothing_to_prepare: false, starts_at: '2026-12-01T05:00:00Z', visit_type: 'Placeholder Visit', ...over }),
  ];
  async function open(routes: Routes, role = 'nurse') {
    const user = userEvent.setup();
    const r = await openConsole({ role, hash: '/readiness', routes });
    await user.type(await screen.findByLabelText('Visit id'), VISIT);
    await user.click(screen.getByRole('button', { name: 'Show the run' }));
    return { user, ...r };
  }

  it('shows what the patient marked, told apart: done, not done and not marked; the text as sent; read only', async () => {
    const { user, calls } = await open([['get', `/v1/staff/visits/${VISIT}/readiness`, () => fail(404)], prep()]);
    await user.click(await screen.findByRole('button', { name: 'Show what the patient marked' }));
    expect(await screen.findByText('Clinic item one')).toBeInTheDocument();
    expect(screen.getByText('Not done')).toBeInTheDocument();
    expect(screen.getByText('Not marked')).toBeInTheDocument(); // null is not "not done"
    expect(writes(calls)).toHaveLength(0);
    await user.click(screen.getByRole('button', { name: 'Hide' }));
    expect(screen.queryByText('Clinic item one')).toBeNull();
  });

  it.each([
    [403, 'You may not read this, or it needs a recent second factor. Nothing was shown.'],
    [404, 'This visit was not found. Nothing was shown.'],
    [503, 'Visit preparation is not turned on yet.'],
  ])('tells a %s in fixed words and never the backend text', async (status, words) => {
    const { user } = await open([['get', `/v1/staff/visits/${VISIT}/readiness`, () => fail(404)], ['get', `/v1/staff/visits/${VISIT}/prep`, () => fail(status)]]);
    await user.click(await screen.findByRole('button', { name: 'Show what the patient marked' }));
    expect(await screen.findByText(words)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('says "nothing to prepare" for an empty checklist', async () => {
    const { user } = await open([['get', `/v1/staff/visits/${VISIT}/readiness`, () => fail(404)], prep({ items: [], nothing_to_prepare: true })]);
    await user.click(await screen.findByRole('button', { name: 'Show what the patient marked' }));
    expect(await screen.findByText('Nothing to prepare for this visit.')).toBeInTheDocument();
  });
});

describe('family links (front desk): revoke and the two newer results', () => {
  const link = (over: Record<string, unknown> = {}) => ({ created_at: '2026-11-09T05:00:00Z', dependant_patient_id: ID.patient2, link_id: D1, link_type: 'carer_of_adult', requester_patient_id: ID.patient, status: 'verified', ...over });
  const family = (rows: unknown[]): Routes[number] => ['get', '/v1/staff/family-links', () => json({ links: rows })];

  it('revokes a verified link only after a confirmation, with a reason word and a key', async () => {
    const user = userEvent.setup();
    const { calls } = await openConsole({ hash: '/family', routes: [family([link()]), ['post', `/v1/staff/family-links/${D1}/revoke`, () => json({ result: 'recorded' })]] });
    await user.click(await screen.findByRole('button', { name: /^Revoke/ }));
    const dialog = screen.getByRole('dialog', { name: 'Revoke this link or request?' });
    expect(within(dialog).getByRole('button', { name: 'Revoke' })).toBeDisabled();
    await user.type(within(dialog).getByLabelText('Reason word'), 'consent_withdrawn');
    expect(writes(calls)).toHaveLength(0);
    await user.click(within(dialog).getByRole('button', { name: 'Revoke' }));
    expect(await screen.findByText('It was revoked. The patient sees it as withdrawn.')).toBeInTheDocument();
    expect(writes(calls)[0]?.body).toEqual({ clinic_id: CLINIC_A, reason: 'consent_withdrawn' });
    expect(writes(calls)[0]?.key).toBeTruthy();
  });

  it('offers Revoke on a waiting request too, and nothing on a rejected or withdrawn one', async () => {
    await openConsole({ hash: '/family', routes: [family([link({ status: 'requested', dependant_patient_id: null }), link({ link_id: D2, status: 'rejected' }), link({ link_id: ID.doc, status: 'revoked' })])] });
    const rows = within(await screen.findByRole('table')).getAllByRole('row');
    expect(within(rows[1]!).getAllByRole('button').map((b) => b.getAttribute('aria-label')?.split(' ')[0])).toEqual(['Verify', 'Reject', 'Revoke']);
    expect(within(rows[2]!).queryAllByRole('button')).toHaveLength(0);
    expect(within(rows[3]!).queryAllByRole('button')).toHaveLength(0);
  });

  it.each([
    ['requester_not_active', 'This request can no longer be verified: the person who asked no longer has an active account or record. Nothing was recorded.'],
    ['already_linked', 'This person is already linked to that dependant. Nothing was recorded.'],
  ])('says %s in fixed words', async (result, words) => {
    const user = userEvent.setup();
    await openConsole({ hash: '/family', routes: [family([link({ status: 'requested', dependant_patient_id: null })]), ['post', `/v1/staff/family-links/${D1}/decision`, () => json({ result })]] });
    await user.click(await screen.findByRole('button', { name: /^Reject/ }));
    const dialog = screen.getByRole('dialog');
    await user.type(within(dialog).getByLabelText('Reason word'), 'not_verified');
    await user.click(within(dialog).getByRole('button', { name: 'Reject' }));
    expect(await screen.findByText(words)).toBeInTheDocument();
  });
});
