import { expect, test } from '@playwright/test';
import { LANGS, go, openConsole, sweep } from './helpers';

// The offer run for a freed slot and the lists that replaced typing, in a real browser against the fake API (e2e/consoleApi.ts, MOCK ONLY).
// Per screen: axe (WCAG 2.2 AA is the goal we test toward) at 320 / 768 / 1280, no horizontal scroll, English and Tamil.

for (const lang of LANGS) {
  test(`the offer run after a cancel [${lang}]`, async ({ page }) => {
    await openConsole(page, 'front_desk', lang);
    await go(page, '#/scheduling/bookings', /\S/);
    await page.locator('main tbody tr').first().getByRole('button').click();
    await page.getByRole('dialog').locator('.dialog-actions button').nth(1).click();
    await expect(page.locator('main .state-empty button.primary')).toBeVisible();
    await page.locator('main .state-empty button.primary').click();
    await expect(page.locator('section[aria-labelledby^="bf-"]')).toBeVisible();
    await expect(page.locator('section[aria-labelledby^="bf-"] dl.facts')).toBeVisible();
    await sweep(page, 'f12-backfill', lang);
  });

  test(`the read-only offer draft [${lang}]: accessible at three widths, keyboard only`, async ({ page }) => {
    await openConsole(page, 'front_desk', lang);
    await go(page, '#/scheduling/offers', /S/);
    await page.locator('#offer-visit-type').selectOption({ index: 1 });
    await page.locator('#offer-visit-type').focus();
    await page.keyboard.press('Tab');
    await page.keyboard.press('Enter');
    await expect(page.locator('#offer-draft-h')).toBeVisible();
    await expect(page.locator('blockquote')).toContainText('PLACEHOLDER template line one.');
    await sweep(page, 'f23-offer-draft', lang);
  });

  test(`the offer run on an open slot, and its decision dialog [${lang}]`, async ({ page }) => {
    await openConsole(page, 'front_desk', lang);
    await go(page, '#/scheduling/slots', /\S/);
    await page.locator('main tbody tr').first().getByRole('button').nth(2).click();
    await expect(page.locator('section[aria-labelledby^="bf-"] dl.facts')).toBeVisible();
    await page.locator('section[aria-labelledby^="bf-"] .toolbar button').first().click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await sweep(page, 'f12-backfill-dialog', lang);
  });

  test(`entering the patient's reply [${lang}]`, async ({ page }) => {
    await openConsole(page, 'front_desk', lang);
    await go(page, '#/scheduling/slots', /\S/);
    await page.locator('main tbody tr').first().getByRole('button').nth(2).click();
    // Wait for the run to load first: before that the toolbar is not the one with Send, and a click that early opens no dialog (slow CI runners).
    await expect(page.locator('section[aria-labelledby^="bf-"] dl.facts')).toBeVisible();
    await page.locator('section[aria-labelledby^="bf-"] .toolbar button').first().click(); // Send
    await expect(page.getByRole('dialog')).toBeVisible();
    await page.getByRole('dialog').locator('.dialog-actions button').nth(1).click();
    await expect(page.locator('section[aria-labelledby^="bf-"] textarea')).toBeVisible();
    // Type the reply, confirm saving it: the text leaves the page and the saved message is picked.
    await page.locator('section[aria-labelledby^="bf-"] textarea').fill('Typed reply words');
    await page.getByRole('button', { name: lang === 'en' ? 'Save the reply' : /பதிலைச் சேமி/ }).first().click();
    await page.getByRole('dialog').locator('.dialog-actions button').nth(1).click();
    await expect(page.locator('section[aria-labelledby^="bf-"] textarea')).toHaveValue('');
    await page.locator('section[aria-labelledby^="bf-"] p button').nth(2).click(); // Suggest a reading
    await expect(page.locator('section[aria-labelledby^="bf-"] dl.facts').nth(1)).toBeVisible();
    await sweep(page, 'f12-backfill-reply', lang);
  });

  test(`break-glass with the clinic's lists [${lang}]`, async ({ page }) => {
    await openConsole(page, 'nurse', lang);
    await go(page, '#/access', /\S/);
    await expect(page.locator('main table').first()).toBeVisible();
    await sweep(page, 'f12-access-lists', lang);
  });

  test(`visit readiness picks from the upcoming visits [${lang}]`, async ({ page }) => {
    await openConsole(page, 'nurse', lang);
    await go(page, '#/readiness', /\S/);
    await expect(page.locator('main table').first()).toBeVisible();
    await sweep(page, 'f12-readiness-list', lang);
  });
}

test('keyboard only: prepare an offer for a freed slot and skip the first candidate, with confirmations', async ({ page }) => {
  await openConsole(page, 'front_desk', 'en');
  await go(page, '#/scheduling/slots', /\S/);
  await page.locator('main tbody tr').first().getByRole('button').nth(2).focus();
  await page.keyboard.press('Enter');
  const skip = page.getByRole('button', { name: 'Skip', exact: true });
  await skip.focus();
  await page.keyboard.press('Enter');
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await page.keyboard.press('Tab'); // Cancel -> Skip
  await page.keyboard.press('Enter');
  await expect(page.getByText(/The candidate was skipped/)).toBeVisible();
});
