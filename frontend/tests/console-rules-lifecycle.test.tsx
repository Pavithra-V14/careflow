import { describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ACCOUNT_ID, json } from './helpers/fakes';
import { ID, LEAK, fail, openConsole, writes, type Routes } from './helpers/console';

// MOCK-ONLY (see helpers/console.tsx): fakes written from openapi.snapshot.json. The author flag (Approve and Reject are not offered to the
// author), withdrawing a draft, and the two-doctor retirement of an approved version. All words typed here are placeholders.

const RULE = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd';
const rule = (over: Record<string, unknown> = {}) => ({
  approved_at: null,
  authored_by_you: false,
  decisions: [],
  has_vitals_criteria: false,
  origin: 'test',
  patterns: [],
  phrases: ['PLACEHOLDER-PHRASE-A'],
  rule_code: 'placeholder_rule',
  rule_id: RULE,
  status: 'draft_unreviewed',
  token_sets: [],
  version_id: 1,
  ...over,
});
const listRoute = (rules: unknown[]): Routes[number] => ['get', '/v1/staff/console/red-flag-rules', () => json({ rules })];
const decisionPath = `/v1/staff/console/red-flag-rules/${RULE}/decision`;
const withdrawPath = `/v1/staff/console/red-flag-rules/${RULE}/withdraw`;
const answer = (path: string, result: string | number): Routes[number] => ['post', path, () => (typeof result === 'number' ? fail(result) : json({ result }))];
const doctor = (rules: unknown[], ...routes: Routes) => openConsole({ role: 'doctor', hash: '/admin/rules', routes: [listRoute(rules), ...routes] });

describe('the author flag', () => {
  it('offers no Approve and no Reject on a draft the person wrote, and says why', async () => {
    await doctor([rule({ authored_by_you: true })]);
    expect(await screen.findByText('You wrote this version. A different doctor has to approve or reject it.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Approve/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /^Reject/ })).toBeNull();
  });

  it('still offers them on a draft someone else wrote', async () => {
    await doctor([rule()]);
    expect(await screen.findByRole('button', { name: 'Approve placeholder_rule v1' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reject placeholder_rule v1' })).toBeInTheDocument();
  });

  it('keeps the database refusal as the fallback when an older answer has no flag', async () => {
    const user = userEvent.setup();
    const { authored_by_you: _flag, ...older } = rule();
    void _flag;
    await doctor([older], answer(decisionPath, 'author_cannot_decide'));
    await user.click(await screen.findByRole('button', { name: 'Approve placeholder_rule v1' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Approve' }));
    expect(await screen.findByText('You wrote this version, so you cannot decide it. A different doctor must.')).toBeInTheDocument();
  });
});

describe('withdrawing a draft', () => {
  it('shows the content, writes nothing before the confirmation, then sends a key and says it is closed', async () => {
    const user = userEvent.setup();
    const { calls } = await doctor([rule({ authored_by_you: true })], answer(withdrawPath, 'withdrawn'));
    await user.click(await screen.findByRole('button', { name: 'Withdraw this draft placeholder_rule v1' }));
    const dialog = screen.getByRole('dialog', { name: 'Withdraw this draft red-flag rule version?' });
    expect(within(dialog).getByText('PLACEHOLDER-PHRASE-A')).toBeInTheDocument();
    expect(within(dialog).getByText(/not the same as a retired one/)).toBeInTheDocument();
    expect(writes(calls)).toHaveLength(0);
    await user.click(within(dialog).getByRole('button', { name: 'Withdraw this draft' }));
    expect(await screen.findByText('This draft is withdrawn. It is closed for good and is not in force.')).toBeInTheDocument();
    expect(writes(calls)).toHaveLength(1);
    expect(writes(calls)[0]).toMatchObject({ method: 'post', path: withdrawPath });
    expect(writes(calls)[0]?.key).toBeTruthy();
  });

  it('cancelling writes nothing; an approved or withdrawn version has no Withdraw; an admin has none', async () => {
    const user = userEvent.setup();
    const { calls } = await doctor([rule(), rule({ rule_id: 'ffffffff-ffff-4fff-8fff-ffffffffffff', rule_code: 'other_rule', status: 'approved' }), rule({ rule_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', rule_code: 'old_rule', status: 'withdrawn' })]);
    await user.click(await screen.findByRole('button', { name: 'Withdraw this draft placeholder_rule v1' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }));
    expect(writes(calls)).toHaveLength(0);
    expect(screen.getAllByRole('button', { name: /^Withdraw this draft/ })).toHaveLength(1);
    expect(screen.getByText('Withdrawn', { selector: '.badge, span, strong' })).toBeInTheDocument();
  });

  it('an admin reads only', async () => {
    await openConsole({ role: 'admin', hash: '/admin/rules', routes: [listRoute([rule({ status: 'approved' }), rule({ rule_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', version_id: 2 })])] });
    await screen.findAllByText('PLACEHOLDER-PHRASE-A');
    expect(screen.queryByRole('button', { name: /Withdraw|Retire|Approve|Reject/ })).toBeNull();
  });

  it.each([
    [422, /Check what you entered/i],
    [403, /not available to your role/i],
  ])('tells %s in fixed words, never the backend text', async (status, text) => {
    const user = userEvent.setup();
    await doctor([rule()], answer(withdrawPath, status));
    await user.click(await screen.findByRole('button', { name: 'Withdraw this draft placeholder_rule v1' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Withdraw this draft' }));
    expect(await within(screen.getByRole('dialog')).findByText(text)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('says "not a draft" for a version that is not one any more', async () => {
    const user = userEvent.setup();
    await doctor([rule()], answer(withdrawPath, 'not_draft'));
    await user.click(await screen.findByRole('button', { name: 'Withdraw this draft placeholder_rule v1' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Withdraw this draft' }));
    expect(await screen.findByText('This version is not a draft any more, so nothing was recorded.')).toBeInTheDocument();
  });
});

describe('retiring an approved version (two different doctors)', () => {
  const approved = (decisions: unknown[] = []) => rule({ status: 'approved', approved_at: '2026-11-11T05:00:00Z', decisions });

  it('is offered on an approved version only, shows the content, writes nothing before the confirmation, and sends the retire decision with a key', async () => {
    const user = userEvent.setup();
    const { calls } = await doctor([approved(), rule({ rule_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', version_id: 2 })], answer(decisionPath, 'recorded'));
    expect(screen.queryByRole('button', { name: /^Retire/ })).toBeNull();
    const retire = await screen.findByRole('button', { name: 'Retire this version placeholder_rule v1' });
    expect(screen.getAllByRole('button', { name: /^Retire this version/ })).toHaveLength(1); // not on the draft
    await user.click(retire);
    const dialog = screen.getByRole('dialog', { name: 'Retire this approved red-flag rule version?' });
    expect(within(dialog).getByText('PLACEHOLDER-PHRASE-A')).toBeInTheDocument();
    expect(within(dialog).getByText(/nothing is retired until a second, different doctor/)).toBeInTheDocument();
    expect(writes(calls)).toHaveLength(0);
    await user.click(within(dialog).getByRole('button', { name: 'Retire this version' }));
    expect(await screen.findByText('Your approval was recorded. The version is not approved yet: a second, different doctor must approve it.')).toBeInTheDocument();
    expect(writes(calls)[0]).toMatchObject({ path: decisionPath, body: { decision: 'retire' } });
    expect(writes(calls)[0]?.key).toBeTruthy();
  });

  it('counts the retire decisions so far and hides the button from a doctor who already recorded one', async () => {
    await doctor([approved([{ decided_at: '2026-11-12T05:00:00Z', decided_by: ACCOUNT_ID, decision: 'retire' }])]);
    expect(await screen.findByText('Retire decisions recorded so far: 1. Two different doctors are needed.')).toBeInTheDocument();
    expect(screen.getByText('You have recorded your retire decision on this version.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Retire/ })).toBeNull();
  });

  it('still offers it to the second doctor, and an earlier approve decision does not count as a retire', async () => {
    await doctor([approved([{ decided_at: '2026-11-12T05:00:00Z', decided_by: ID.user, decision: 'retire' }, { decided_at: '2026-11-11T05:00:00Z', decided_by: ACCOUNT_ID, decision: 'approve' }])]);
    expect(await screen.findByRole('button', { name: 'Retire this version placeholder_rule v1' })).toBeInTheDocument();
    expect(screen.getByText('Retired it')).toBeInTheDocument();
  });

  it.each([
    ['retired', 'Your retire decision was recorded and this version is now retired.'],
    ['not_approved', 'This version is not approved, so it cannot be retired. Nothing was recorded.'],
    ['withdrawn', 'This draft is withdrawn. It is closed for good and is not in force.'],
  ])('tells the result word %s in fixed words', async (word, text) => {
    const user = userEvent.setup();
    await doctor([approved()], answer(decisionPath, word));
    await user.click(await screen.findByRole('button', { name: 'Retire this version placeholder_rule v1' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Retire this version' }));
    expect(await screen.findByText(text)).toBeInTheDocument();
  });

  it('is available in Tamil with the same buttons', async () => {
    await openConsole({ role: 'doctor', hash: '/admin/rules', lang: 'ta', routes: [listRoute([approved(), rule({ rule_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' })])] });
    expect(await screen.findByRole('button', { name: /இந்தப் பதிப்பை நிறுத்து/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /இந்த வரைவைத் திரும்பப் பெறு/ })).toBeInTheDocument();
  });
});
