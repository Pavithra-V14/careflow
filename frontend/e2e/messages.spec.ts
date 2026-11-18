import { expect, test } from '@playwright/test';
import { LANGS, go, openConsole, sweep } from './helpers';

// The staff message screen and the patient labels, in a real browser against the fake API (e2e/consoleApi.ts, MOCK ONLY).
// Per screen: axe (WCAG 2.2 AA is the goal we test toward) at 320 / 768 / 1280, no horizontal scroll, English and Tamil.
for (const lang of LANGS) {
  test(`staff messages [${lang}]: the list, a conversation, the read and screen dialogs, and the names dialog`, async ({ page }) => {
    await openConsole(page, 'nurse', lang);
    await go(page, '#/messages', /\S/);
    await expect(page.locator('main table')).toBeVisible();
    await sweep(page, 'messages-list', lang);
    await page.locator('main tbody tr').first().getByRole('button').click();
    await expect(page.locator('main table').nth(1)).toBeVisible();
    await sweep(page, 'messages-thread', lang);
    await page.locator('main table').nth(1).locator('tbody tr').first().getByRole('button').first().click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await sweep(page, 'messages-read-dialog', lang);
    await page.getByRole('dialog').locator('.dialog-actions button').nth(1).click();
    await expect(page.locator('main blockquote')).toContainText('PLACEHOLDER message line one.');
    await sweep(page, 'messages-body', lang);
  });

  test(`patient labels [${lang}]: the confirmation, then the names beside the ids`, async ({ page }) => {
    await openConsole(page, 'front_desk', lang);
    await go(page, '#/messages', /\S/);
    await page.locator('main .toolbar button').nth(1).click(); // Show names (after Refresh)
    await expect(page.getByRole('dialog')).toBeVisible();
    await sweep(page, 'labels-dialog', lang);
    await page.getByRole('dialog').locator('.dialog-actions button').nth(1).click();
    await expect(page.locator('main tbody')).toContainText('Placeholder Person');
    await sweep(page, 'labels-shown', lang);
  });
}

test('keyboard only: read a message after its confirmation', async ({ page }) => {
  await openConsole(page, 'nurse', 'en');
  await go(page, '#/messages', /\S/);
  await page.locator('main tbody tr').first().getByRole('button').focus();
  await page.keyboard.press('Enter');
  const read = page.locator('main table').nth(1).locator('tbody tr').first().getByRole('button').first();
  await read.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('dialog').locator('.dialog-actions button').nth(1).focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('main blockquote')).toBeVisible();
});
