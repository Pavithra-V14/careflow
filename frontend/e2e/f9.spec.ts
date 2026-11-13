import { expect, test } from '@playwright/test';
import { LANGS, go, openConsole, sweep } from './helpers';

// Batch 3 in a real browser against the fake API (e2e/consoleApi.ts, MOCK ONLY): care-gap writes and the admin screens.
// Per screen: axe (WCAG 2.2 AA is the goal we test toward) at 320 / 768 / 1280, no horizontal scroll, English and Tamil. One test per
// screen keeps each short: the cost is axe at three widths.

for (const lang of LANGS) {
  test(`record a barrier dialog (nurse) [${lang}]`, async ({ page }) => {
    await openConsole(page, 'nurse', lang);
    await go(page, '#/care/gaps', /\S/);
    await page.locator('main .toolbar button').nth(1).click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await sweep(page, 'f9-barrier-dialog', lang);
  });

  test(`care gaps with row actions (nurse) [${lang}]`, async ({ page }) => {
    await openConsole(page, 'nurse', lang);
    await go(page, '#/care/gaps', /\S/);
    await expect(page.locator('main table').first()).toBeVisible();
    await sweep(page, 'f9-care-gaps-actions', lang);
  });

  test(`admin, staff accounts [${lang}]`, async ({ page }) => {
    await openConsole(page, 'admin', lang);
    await go(page, '#/admin/accounts', /\S/);
    await expect(page.locator('main table').first()).toBeVisible();
    await sweep(page, 'f9-accounts', lang);
  });

  test(`admin, the invite dialog and its 503 state [${lang}]`, async ({ page }) => {
    await openConsole(page, 'admin', lang);
    await go(page, '#/admin/accounts', /\S/);
    await page.locator('main .toolbar button').nth(1).click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await sweep(page, 'f9-invite-dialog', lang);
  });

  test(`admin, autonomy [${lang}]`, async ({ page }) => {
    await openConsole(page, 'admin', lang);
    await go(page, '#/admin/autonomy', /\S/);
    await expect(page.locator('main table').first()).toBeVisible();
    await sweep(page, 'f9-autonomy', lang);
  });

  test(`doctor, autonomy (the first tab) [${lang}]`, async ({ page }) => {
    await openConsole(page, 'doctor', lang);
    await go(page, '#/admin', /\S/);
    await expect(page.locator('main table').first()).toBeVisible();
    await sweep(page, 'f9-autonomy-doctor', lang);
  });

  test(`admin, reviewer statistics [${lang}]`, async ({ page }) => {
    await openConsole(page, 'admin', lang);
    await go(page, '#/admin/reviewer', /\S/);
    await expect(page.locator('main table').first()).toBeVisible();
    await sweep(page, 'f9-reviewer', lang);
  });

  test(`admin, cost canvas [${lang}]`, async ({ page }) => {
    await openConsole(page, 'admin', lang);
    await go(page, '#/admin/cost', /\S/);
    await expect(page.locator('main table').first()).toBeVisible();
    await sweep(page, 'f9-cost', lang);
  });

  test(`admin, voice sessions [${lang}]`, async ({ page }) => {
    await openConsole(page, 'admin', lang);
    await go(page, '#/admin/voice', /\S/);
    await expect(page.locator('main table').first()).toBeVisible();
    await sweep(page, 'f9-voice', lang);
  });

  test(`admin, the transcript window after reading [${lang}]`, async ({ page }) => {
    await openConsole(page, 'admin', lang);
    await go(page, '#/admin/voice', /\S/);
    await page.locator('main tbody tr').first().getByRole('button').click();
    await page.getByRole('dialog').locator('.dialog-actions button').nth(1).click();
    await expect(page.getByRole('dialog').locator('ol.transcript li')).toHaveCount(2);
    await sweep(page, 'f9-transcript', lang);
  });
}

test('keyboard only: read a transcript, and the text is gone when the window closes', async ({ page }) => {
  await openConsole(page, 'admin', 'en');
  await go(page, '#/admin/voice', /\S/);
  const open = page.locator('main tbody tr').first().getByRole('button');
  await open.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.keyboard.press('Tab'); // Cancel -> Read transcript
  await page.keyboard.press('Enter');
  await expect(page.getByText('First words')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByText('First words')).toHaveCount(0);
});

test('keyboard only: record a barrier with the keyboard', async ({ page }) => {
  await openConsole(page, 'nurse', 'en');
  await go(page, '#/care/gaps?patient=66666666-6666-4666-8666-666666666666', /\S/);
  await page.getByRole('button', { name: 'Record a barrier' }).focus();
  await page.keyboard.press('Enter');
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel('Patient id')).toHaveValue('66666666-6666-4666-8666-666666666666');
  await dialog.getByLabel('Barrier').focus();
  await page.keyboard.press('ArrowDown'); // the first real option after the empty one
  await dialog.getByRole('button', { name: 'Record' }).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByText('The barrier was recorded.')).toBeVisible();
});

test('200% zoom: the admin screens stay usable with no horizontal scroll (Tamil)', async ({ page }) => {
  await openConsole(page, 'admin', 'ta');
  await page.setViewportSize({ width: 640, height: 800 });
  await page.evaluate(() => {
    document.documentElement.style.fontSize = '200%';
  });
  for (const tab of ['accounts', 'autonomy', 'reviewer', 'cost', 'voice']) {
    await go(page, `#/admin/${tab}`, /\S/);
    await expect(page.locator('main table').first()).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth), `${tab}: horizontal scroll at 200%`).toBe(false);
  }
});
