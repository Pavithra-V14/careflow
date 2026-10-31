import { expect, test } from '@playwright/test';
import { GOOD_CODE } from './mockBackend';
import { ID, LEAK } from './consoleApi';
import { LANGS, VIEWPORTS, check, go, openConsole, sweep } from './helpers';

// F4 in a real browser: referral decisions (nurse), identity revoke and identity lists (front desk), and merge approve /
// unmerge with the step-up prompt. MOCK-ONLY evidence: the real built app and real supabase-js against fakes written from
// openapi.snapshot.json. This build is made with `--mode e2e`, the only mode in which the merge-actions flag is on.

for (const lang of LANGS) {
  test(`nurse, referral decision form and its confirmation [${lang}]: accessible at 320, 768 and 1280`, async ({ page }) => {
    await openConsole(page, 'nurse', lang);
    await go(page, `#/referrals/${ID.doc}`, ID.doc);
    await expect(page.getByRole('radio')).toHaveCount(3);
    await sweep(page, 'referral-review', lang);
    await page.getByRole('radio').nth(0).check();
    await page.locator('main form button[type=submit]').click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await sweep(page, 'referral-review-dialog', lang);
  });

  test(`front desk, identity lists and merge detail [${lang}]`, async ({ page }) => {
    await openConsole(page, 'front_desk', lang);
    await go(page, '#/identity/merges', /\S/);
    await expect(page.locator('main table')).toBeVisible();
    await sweep(page, 'identity-merges', lang);
    await go(page, '#/identity/duplicates', /\S/);
    await expect(page.locator('main table')).toBeVisible();
    await sweep(page, 'identity-duplicates', lang);
    await go(page, `#/identity/merges/${ID.merge}`, ID.merge);
    await expect(page.locator('main .toolbar button').first()).toBeVisible();
    await sweep(page, 'merge-detail', lang);
    await page.locator('main .toolbar button').first().click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await sweep(page, 'merge-approve-dialog', lang);
  });

  test(`front desk, patient record with the revoke dialog [${lang}]`, async ({ page }) => {
    await openConsole(page, 'front_desk', lang, { console: { patientVerified: true } });
    await go(page, `#/patients/${ID.patient}`, ID.patient);
    await sweep(page, 'patient-record-verified', lang);
    await page.locator('main .toolbar button').last().click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await expect(page.getByRole('radio')).toHaveCount(3);
    await sweep(page, 'revoke-dialog', lang);
  });
}

test('keyboard only: record a referral decision (nothing chosen for you, nothing written before the confirmation)', async ({ page }) => {
  const mock = await openConsole(page, 'nurse', 'en');
  await go(page, `#/referrals/${ID.doc}`, 'Record a decision');
  for (const r of await page.getByRole('radio').all()) await expect(r).not.toBeChecked();
  await expect(page.locator('main [data-placeholder]').first()).toHaveText('PLACEHOLDER - clinician to define'); // reasons: marked placeholder only
  await expect(page.getByRole('button', { name: 'Record decision' })).toBeDisabled();
  expect(mock.keys.filter((k) => k.path.endsWith('/review'))).toHaveLength(0);

  await page.getByRole('radio').first().focus();
  await page.keyboard.press('Space');
  await page.keyboard.press('Tab'); // a radio group is ONE tab stop; reasons are a placeholder, not a field, so the next stop is the button
  await expect(page.getByRole('button', { name: 'Record decision' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog')).toBeVisible();
  await expect(page.getByRole('dialog').getByRole('button', { name: 'Cancel' })).toBeFocused();
  expect(mock.keys.filter((k) => k.path.endsWith('/review'))).toHaveLength(0);
  await page.keyboard.press('Tab');
  await page.keyboard.press('Enter');
  await expect(page.getByText('The decision was recorded.')).toBeVisible();
  expect(mock.keys.filter((k) => k.path.endsWith('/review'))).toHaveLength(1);
});

test('keyboard only: revoke an identity confirmation, choosing the reason yourself', async ({ page }) => {
  const mock = await openConsole(page, 'front_desk', 'en', { console: { patientVerified: true } });
  await go(page, `#/patients/${ID.patient}`, 'Revoke confirmation');
  await page.getByRole('button', { name: 'Revoke confirmation' }).focus();
  await page.keyboard.press('Enter');
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  for (const r of await dialog.getByRole('radio').all()) await expect(r).not.toBeChecked();
  await expect(dialog.getByRole('button', { name: 'Revoke confirmation' })).toBeDisabled();
  await dialog.getByRole('radio').first().focus();
  await page.keyboard.press('Space');
  await expect(dialog.getByRole('button', { name: 'Revoke confirmation' })).toBeEnabled();
  expect(mock.keys.filter((k) => k.path.endsWith('/revoke-identity'))).toHaveLength(0);
  await dialog.getByRole('button', { name: 'Revoke confirmation' }).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByText('The confirmation was revoked.')).toBeVisible();
  await expect(page.getByText('A review task was raised.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Revoke confirmation' })).toHaveCount(0); // the record is no longer verified
  expect(mock.keys.filter((k) => k.path.endsWith('/revoke-identity'))).toHaveLength(1);
});

test('merge approve in a real browser: the first attempt is a step-up challenge, a fresh code is asked for, and the SAME request is retried once', async ({ page }) => {
  const mock = await openConsole(page, 'front_desk', 'en');
  await go(page, `#/identity/merges/${ID.merge}`, 'Awaiting second approval');
  expect(mock.verifications()).toBe(1); // only the sign-in challenge so far

  await page.getByRole('button', { name: 'Approve' }).click();
  await page.getByRole('dialog', { name: 'Approve this merge?' }).getByRole('button', { name: 'Approve' }).click();

  const prompt = page.getByRole('dialog', { name: 'Confirm it is you' });
  await expect(prompt).toBeVisible();
  expect(mock.keys.filter((k) => k.path.endsWith('/approve'))).toHaveLength(1); // the refused first attempt
  await prompt.getByLabel('6-digit code').fill(GOOD_CODE);
  await prompt.getByRole('button', { name: 'Confirm' }).click();

  await expect(page.getByText('The merge was approved.')).toBeVisible();
  const attempts = mock.keys.filter((k) => k.path.endsWith('/approve'));
  expect(attempts).toHaveLength(2); // exactly one retry, never a loop
  expect(attempts[1]?.key).toBeTruthy();
  expect(attempts[1]?.key).toBe(attempts[0]?.key); // same idempotency key
  expect(mock.verifications()).toBe(2); // the fresh second-factor verification happened in between
  await expect(page.locator('main')).toContainText('Active');
  expect(await page.locator('body').innerText()).not.toContain(LEAK);
  await check(page, 'merge-approved', 'en', VIEWPORTS[2]);
});

test('merge unmerge in a real browser asks for a fresh code too, and says so plainly if you cancel; you stay signed in', async ({ page }) => {
  const mock = await openConsole(page, 'front_desk', 'en');
  await go(page, `#/identity/merges/${ID.merge}`, 'Awaiting second approval');
  await page.getByRole('button', { name: 'Unmerge' }).click();
  await page.getByRole('dialog', { name: 'Unmerge these records?' }).getByRole('button', { name: 'Unmerge' }).click();
  const prompt = page.getByRole('dialog', { name: 'Confirm it is you' });
  await expect(prompt).toBeVisible();
  await prompt.getByRole('button', { name: 'Cancel' }).click();
  await expect(page.getByText('A fresh authenticator code is needed to do this. Nothing was changed.')).toBeVisible();
  expect(mock.keys.filter((k) => k.path.endsWith('/unmerge'))).toHaveLength(1); // no retry
  await expect(page.locator('.session-bar')).toBeVisible(); // not signed out
  await check(page, 'merge-unmerge-cancelled', 'en', VIEWPORTS[2]);
});

test('merge approve: the person who proposed it is not offered Approve', async ({ page }) => {
  await openConsole(page, 'front_desk', 'en', { console: { mergeProposedByMe: true } });
  await go(page, `#/identity/merges/${ID.merge}`, 'Awaiting second approval');
  await expect(page.getByRole('button', { name: 'Approve' })).toHaveCount(0);
  await expect(page.getByText('A different person from the one who proposed this merge must approve it.')).toBeVisible();
});

test('roles: a doctor can decide but not revoke or approve; front desk cannot decide; nurse cannot see duplicates', async ({ page }) => {
  await openConsole(page, 'doctor', 'en');
  await go(page, `#/referrals/${ID.doc}`, 'Record a decision');
  await go(page, `#/patients/${ID.patient}`, ID.patient);
  await expect(page.getByRole('button', { name: 'Revoke confirmation' })).toHaveCount(0);
  await go(page, `#/identity/merges/${ID.merge}`, ID.merge);
  await expect(page.getByRole('button', { name: 'Approve' })).toHaveCount(0);
  await expect(page.locator('main')).toContainText('This is not available to your role.');
  await go(page, '#/identity/duplicates', 'This is not available to your role.');
  await expect(page.locator('.sub-nav a')).toHaveText(['Merges']);
});

test('roles: front desk sees no decision form on a referral', async ({ page }) => {
  await openConsole(page, 'front_desk', 'en');
  await go(page, `#/referrals/${ID.doc}`, 'Not shown to your role');
  await expect(page.getByRole('radio')).toHaveCount(0);
});
