import { expect, test } from '@playwright/test';
import { LANGS, go, openConsole, sweep } from './helpers';

// Batch 4 in a real browser against the fake API (e2e/consoleApi.ts, MOCK ONLY): visit readiness, portal claims, break-glass, notes,
// staff recovery and grievances. Per screen: axe (WCAG 2.2 AA is the goal we test toward) at 320 / 768 / 1280, no horizontal scroll,
// English and Tamil. One test per screen keeps each short: the cost is axe at three widths.

const VISIT = 'abababab-abab-4bab-8bab-abababababab';
const PATIENT = '66666666-6666-4666-8666-666666666666';

for (const lang of LANGS) {
  test(`visit readiness [${lang}]`, async ({ page }) => {
    await openConsole(page, 'nurse', lang);
    await go(page, '#/readiness', /\S/);
    await page.locator('main input[type=text]').fill(VISIT);
    await page.locator('main form button[type=submit]').click();
    await expect(page.locator('main dl.facts')).toBeVisible();
    await sweep(page, 'f10-readiness', lang);
  });

  test(`the relay a reply dialog [${lang}]`, async ({ page }) => {
    await openConsole(page, 'nurse', lang);
    await go(page, '#/readiness', /\S/);
    await page.locator('main input[type=text]').fill(VISIT);
    await page.locator('main form button[type=submit]').click();
    await expect(page.locator('main dl.facts')).toBeVisible();
    await page.locator('main .toolbar button').nth(1).click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await sweep(page, 'f10-reply-dialog', lang);
  });

  test(`portal claims (front desk) [${lang}]`, async ({ page }) => {
    await openConsole(page, 'front_desk', lang);
    await go(page, '#/claims', /\S/);
    await expect(page.locator('main table')).toBeVisible();
    await sweep(page, 'f10-claims', lang);
  });

  test(`the approve a claim dialog [${lang}]`, async ({ page }) => {
    await openConsole(page, 'front_desk', lang);
    await go(page, '#/claims', /\S/);
    await page.locator('main tbody tr').first().getByRole('button').first().click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await sweep(page, 'f10-claim-dialog', lang);
  });

  test(`break-glass, open access (nurse) [${lang}]`, async ({ page }) => {
    await openConsole(page, 'nurse', lang);
    await go(page, '#/access', /\S/);
    await page.locator('main section > p button').first().click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await sweep(page, 'f10-access-dialog', lang);
  });

  test(`break-glass, admin review [${lang}]`, async ({ page }) => {
    await openConsole(page, 'admin', lang);
    await go(page, '#/access', /\S/);
    await expect(page.locator('main table')).toBeVisible();
    await sweep(page, 'f10-access-review', lang);
  });

  test(`notes (doctor) [${lang}]`, async ({ page }) => {
    await openConsole(page, 'doctor', lang);
    await go(page, `#/notes?patient=${PATIENT}`, /\S/);
    await page.locator('main form button[type=submit]').click();
    await expect(page.locator('main ol.notes')).toBeVisible();
    await sweep(page, 'f10-notes', lang);
  });

  test(`the write a note dialog [${lang}]`, async ({ page }) => {
    await openConsole(page, 'doctor', lang);
    await go(page, `#/notes?patient=${PATIENT}`, /\S/);
    await page.locator('main form button[type=submit]').click();
    await expect(page.locator('main ol.notes')).toBeVisible();
    await page.locator('main .toolbar button').first().click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await sweep(page, 'f10-note-dialog', lang);
  });

  test(`admin, staff recovery [${lang}]`, async ({ page }) => {
    await openConsole(page, 'admin', lang);
    await go(page, '#/admin/recovery', /\S/);
    await expect(page.locator('main table')).toBeVisible();
    await sweep(page, 'f10-recovery', lang);
  });

  test(`admin, grievances [${lang}]`, async ({ page }) => {
    await openConsole(page, 'admin', lang);
    await go(page, '#/admin/grievances', /\S/);
    await expect(page.locator('main table')).toBeVisible();
    await sweep(page, 'f10-grievances', lang);
  });
}

test('keyboard only: open a break-glass grant and read under it with the keyboard', async ({ page }) => {
  await openConsole(page, 'nurse', 'en');
  await go(page, '#/access', /\S/);
  await page.locator('main section > p button').first().focus();
  await page.keyboard.press('Enter');
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('Patient id').fill(PATIENT);
  await dialog.getByLabel('Reason code').fill('reason_a');
  await dialog.getByRole('button', { name: 'Open access' }).focus();
  await page.keyboard.press('Enter');
  await page.getByRole('button', { name: 'Read the messages' }).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByText('Message words')).toBeVisible();
});

test('200% zoom: access and the new admin tabs stay usable with no horizontal scroll (Tamil)', async ({ page }) => {
  await openConsole(page, 'admin', 'ta');
  await page.setViewportSize({ width: 640, height: 800 });
  await page.evaluate(() => {
    document.documentElement.style.fontSize = '200%';
  });
  for (const hash of ['#/access', '#/admin/recovery', '#/admin/grievances']) {
    await go(page, hash, /\S/);
    await expect(page.locator('main table').first()).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth), `${hash}: horizontal scroll at 200%`).toBe(false);
  }
});
