import { describe, expect, it } from 'vitest';
import { fireEvent, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CLINIC_A, json } from './helpers/fakes';
import { LEAK, fail, openConsole, writes, type Routes } from './helpers/console';

// MOCK-ONLY (see helpers/console.tsx): fakes written from openapi.snapshot.json. A doctor writes a DRAFT red-flag rule version in the restricted
// language (phrases and token sets). Every word typed below is a placeholder: the console supplies no rule content of its own.

const NEW_RULE = 'a1a1a1a1-a1a1-41a1-81a1-a1a1a1a1a1a1';
const listRoute: Routes[number] = ['get', '/v1/staff/console/red-flag-rules', () => json({ rules: [] })];
const authorRoute = (answer: 'ok' | number): Routes[number] => ['post', '/v1/staff/console/red-flag-rules', () => (answer === 'ok' ? json({ rule_id: NEW_RULE, status: 'draft_unreviewed' }, 201) : fail(answer))];
const openDoctor = (routes: Routes) => openConsole({ role: 'doctor', hash: '/admin/rules', routes: [listRoute, ...routes] });

async function fill(user: ReturnType<typeof userEvent.setup>, dialog: HTMLElement, code: string, phrases: string, sets: string) {
  if (code) await user.type(within(dialog).getByLabelText('Rule code'), code);
  if (phrases) await user.type(within(dialog).getByLabelText('Phrases (one per line)'), phrases);
  if (sets) await user.type(within(dialog).getByLabelText('Token sets (one set per line, words separated by spaces)'), sets);
}

describe('rule authoring (doctor only)', () => {
  it('is offered to a doctor and not to an admin', async () => {
    const a = await openDoctor([]);
    expect(await screen.findByRole('button', { name: 'Write a new rule version' })).toBeInTheDocument();
    a.unmount();
    await openConsole({ role: 'admin', hash: '/admin/rules', routes: [listRoute] });
    await screen.findByText('There are no red-flag rule versions to show.');
    expect(screen.queryByRole('button', { name: 'Write a new rule version' })).toBeNull();
  });

  it('opens an empty form with no default text, no regular-expression field, and nothing to review yet', async () => {
    const user = userEvent.setup();
    await openDoctor([]);
    await user.click(await screen.findByRole('button', { name: 'Write a new rule version' }));
    const dialog = screen.getByRole('dialog', { name: 'Write a draft red-flag rule version' });
    expect((within(dialog).getByLabelText('Rule code') as HTMLInputElement).value).toBe('');
    expect((within(dialog).getByLabelText('Phrases (one per line)') as HTMLTextAreaElement).value).toBe('');
    expect((within(dialog).getByLabelText('Token sets (one set per line, words separated by spaces)') as HTMLTextAreaElement).value).toBe('');
    expect(within(dialog).queryByLabelText(/regular expression|pattern/i)).toBeNull();
    expect(within(dialog).getAllByRole('textbox')).toHaveLength(3); // code, phrases, token sets: nothing else
    expect(within(dialog).getByRole('button', { name: 'Review the draft' })).toBeDisabled();
  });

  it.each([
    ['a bad code', 'Bad-Code', 'placeholder phrase', '', 'The rule code must start with a lower-case letter'],
    ['a set of two identical words', 'ok_code', '', 'Same same', 'Token set line 1 needs at least two different words.'],
    ['a set of one word', 'ok_code', '', 'alone', 'Token set line 1 must have between 2 and 10 words.'],
    ['a set of eleven words', 'ok_code', '', 'a b c d e f g h i j k', 'Token set line 1 must have between 2 and 10 words.'],
    ['a word over 40 characters', 'ok_code', '', `${'x'.repeat(41)} other`, 'Token set line 1 has a word that is too long.'],
    ['a phrase over 200 characters', 'ok_code', 'y'.repeat(201), '', 'Phrase line 1 is too long.'],
    ['no phrase and no set', 'ok_code', '', '', 'Add at least one phrase or one token set.'],
  ])('refuses %s before anything is sent', async (_name, code, phrases, sets, message) => {
    const user = userEvent.setup();
    const { calls } = await openDoctor([authorRoute('ok')]);
    await user.click(await screen.findByRole('button', { name: 'Write a new rule version' }));
    const dialog = screen.getByRole('dialog');
    await fill(user, dialog, code, phrases, sets);
    if (code === 'ok_code' && !phrases && !sets) await user.type(within(dialog).getByLabelText('Phrases (one per line)'), ' ');
    expect(within(dialog).getByText(new RegExp(message.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Review the draft' })).toBeDisabled();
    expect(writes(calls)).toHaveLength(0);
  });

  it('refuses an invisible character in a phrase and in a word', async () => {
    const user = userEvent.setup();
    await openDoctor([authorRoute('ok')]);
    await user.click(await screen.findByRole('button', { name: 'Write a new rule version' }));
    const dialog = screen.getByRole('dialog');
    await user.type(within(dialog).getByLabelText('Rule code'), 'ok_code');
    fireEvent.change(within(dialog).getByLabelText('Phrases (one per line)'), { target: { value: 'visible​phrase' } });
    expect(within(dialog).getByText(/Phrase line 1 has a control character or an invisible character/)).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText('Phrases (one per line)'), { target: { value: '' } });
    fireEvent.change(within(dialog).getByLabelText('Token sets (one set per line, words separated by spaces)'), { target: { value: 'one tw o' } });
    expect(within(dialog).getByText(/Token set line 1 has a control character or an invisible character/)).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Review the draft' })).toBeDisabled();
  });

  it('reviews first, writes nothing until Save draft, then sends exactly what was shown with a key and says it is a draft', async () => {
    const user = userEvent.setup();
    const { calls } = await openDoctor([authorRoute('ok')]);
    await user.click(await screen.findByRole('button', { name: 'Write a new rule version' }));
    const dialog = screen.getByRole('dialog');
    await user.type(within(dialog).getByLabelText('Rule code'), 'placeholder_rule');
    fireEvent.change(within(dialog).getByLabelText('Phrases (one per line)'), { target: { value: 'placeholder phrase one\n\n placeholder phrase two ' } });
    fireEvent.change(within(dialog).getByLabelText('Token sets (one set per line, words separated by spaces)'), { target: { value: 'wordA wordB\nwordC  wordD wordE' } });
    await user.click(within(dialog).getByRole('button', { name: 'Review the draft' }));
    const review = screen.getByRole('dialog', { name: 'Save this draft red-flag rule version?' });
    expect(within(review).getByText('placeholder_rule')).toBeInTheDocument();
    expect(within(review).getByText('placeholder phrase two')).toBeInTheDocument();
    expect(within(review).getByText('wordC + wordD + wordE')).toBeInTheDocument();
    expect(within(review).getByText(/never active/)).toBeInTheDocument();
    expect(writes(calls)).toHaveLength(0);
    await user.click(within(review).getByRole('button', { name: 'Back to editing' }));
    expect((screen.getByLabelText('Rule code') as HTMLInputElement).value).toBe('placeholder_rule'); // nothing lost going back
    await user.click(screen.getByRole('button', { name: 'Review the draft' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Save draft' }));
    expect(await screen.findByText('The draft was saved. It is not approved and it is not active.')).toBeInTheDocument();
    expect(writes(calls)).toHaveLength(1);
    expect(writes(calls)[0]).toMatchObject({
      path: '/v1/staff/console/red-flag-rules',
      body: { clinic_id: CLINIC_A, rule_code: 'placeholder_rule', phrases: ['placeholder phrase one', 'placeholder phrase two'], token_sets: [['wordA', 'wordB'], ['wordC', 'wordD', 'wordE']] },
    });
    expect(Object.keys(writes(calls)[0]?.body as object).sort()).toEqual(['clinic_id', 'phrases', 'rule_code', 'token_sets']); // no pattern field is ever sent
    expect(writes(calls)[0]?.key).toBeTruthy();
  });

  it.each([
    [409, 'This request clashes with an earlier one that used the same key.'],
    [422, 'The draft is outside what a rule may contain. Nothing was saved.'],
  ])('tells %s in fixed words, never the backend text', async (status, text) => {
    const user = userEvent.setup();
    await openDoctor([authorRoute(status)]);
    await user.click(await screen.findByRole('button', { name: 'Write a new rule version' }));
    await fill(user, screen.getByRole('dialog'), 'placeholder_rule', 'placeholder phrase', '');
    await user.click(screen.getByRole('button', { name: 'Review the draft' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Save draft' }));
    expect(await within(screen.getByRole('dialog')).findByText(new RegExp(text))).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('403 is "not available to your role"', async () => {
    const user = userEvent.setup();
    await openDoctor([authorRoute(403)]);
    await user.click(await screen.findByRole('button', { name: 'Write a new rule version' }));
    await fill(user, screen.getByRole('dialog'), 'placeholder_rule', 'placeholder phrase', '');
    await user.click(screen.getByRole('button', { name: 'Review the draft' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Save draft' }));
    expect(await within(screen.getByRole('dialog')).findByText(/not available to your role/i)).toBeInTheDocument();
  });

  it('shows a draft in the list as a draft with its phrases and token sets, and an older answer without token sets does not break the screen', async () => {
    const draft = { approved_at: null, decisions: [], has_vitals_criteria: false, origin: 'test', patterns: [], phrases: ['placeholder phrase one'], rule_code: 'placeholder_rule', rule_id: NEW_RULE, status: 'draft_unreviewed', token_sets: [['wordA', 'wordB']], version_id: 1 };
    const { token_sets: _omit, ...older } = { ...draft, rule_id: 'b2b2b2b2-b2b2-42b2-82b2-b2b2b2b2b2b2', rule_code: 'older_rule' };
    void _omit;
    await openConsole({ role: 'doctor', hash: '/admin/rules', routes: [['get', '/v1/staff/console/red-flag-rules', () => json({ rules: [draft, older] })]] });
    expect(await screen.findByText('wordA + wordB')).toBeInTheDocument();
    expect(screen.getAllByText('Draft, not approved').length).toBeGreaterThan(0);
    expect(screen.getAllByText(/This is a draft. It is never active./).length).toBe(2);
    expect(screen.getByText('older_rule')).toBeInTheDocument();
  });

  it('is available in Tamil with the same controls', async () => {
    await openConsole({ role: 'doctor', hash: '/admin/rules', lang: 'ta', routes: [listRoute] });
    expect(await screen.findByRole('button', { name: 'புதிய விதிப் பதிப்பை எழுது' })).toBeInTheDocument();
  });
});
