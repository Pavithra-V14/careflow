import { expect, test } from '@playwright/test';
import { LANGS, go, openConsole, sweep } from './helpers';

// Batch 2 screens in a real browser against the fake API (e2e/consoleApi.ts, MOCK ONLY): alerts, dashboard, care gaps, care plans
// and results. Per screen: axe (WCAG 2.2 AA is the goal we test toward) at 320 / 768 / 1280, no horizontal scroll, English and Tamil.
// One test per screen keeps each short: the cost is axe at three widths.

for (const lang of LANGS) {
  test(`alerts [${lang}]`, async ({ page }) => {
    await openConsole(page, 'nurse', lang);
    await go(page, '#/alerts', /\S/);
    await expect(page.locator('main table')).toBeVisible();
    await sweep(page, 'f8-alerts', lang);
  });

  test(`the acknowledge alert dialog [${lang}]`, async ({ page }) => {
    await openConsole(page, 'nurse', lang);
    await go(page, '#/alerts', /\S/);
    await page.locator('main tbody tr').first().getByRole('button').click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await sweep(page, 'f8-alert-dialog', lang);
  });

  test(`dashboard (admin) [${lang}]`, async ({ page }) => {
    await openConsole(page, 'admin', lang);
    await go(page, '#/dashboard', /\S/);
    await expect(page.locator('main table')).toBeVisible();
    await sweep(page, 'f8-dashboard', lang);
  });

  test(`care gaps [${lang}]`, async ({ page }) => {
    await openConsole(page, 'nurse', lang);
    await go(page, '#/care/gaps', /\S/);
    await expect(page.locator('main table').first()).toBeVisible();
    await sweep(page, 'f8-care-gaps', lang);
  });

  test(`care plans (doctor) [${lang}]`, async ({ page }) => {
    await openConsole(page, 'doctor', lang);
    await go(page, '#/care/plans', /\S/);
    await expect(page.locator('main table')).toBeVisible();
    await sweep(page, 'f8-care-plans', lang);
  });

  test(`the write a care plan dialog [${lang}]`, async ({ page }) => {
    await openConsole(page, 'doctor', lang);
    await go(page, '#/care/plans', /\S/);
    await page.locator('main .toolbar button').nth(1).click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await sweep(page, 'f8-plan-dialog', lang);
  });

  test(`the record a result dialog [${lang}]`, async ({ page }) => {
    await openConsole(page, 'nurse', lang);
    await go(page, '#/care/results', /\S/);
    await page.locator('main section p button').first().click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await sweep(page, 'f8-result-dialog', lang);
  });
}

test('keyboard only: acknowledge an alert with the keyboard and the confirmation', async ({ page }) => {
  await openConsole(page, 'nurse', 'en');
  await go(page, '#/alerts', /\S/);
  const button = page.locator('main tbody tr').first().getByRole('button');
  await button.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.keyboard.press('Tab'); // Cancel -> Acknowledge
  await page.keyboard.press('Enter');
  await expect(page.getByText('The alert was acknowledged.')).toBeVisible();
});

test('200% zoom: alerts, care gaps and care plans stay usable with no horizontal scroll (Tamil)', async ({ page }) => {
  await openConsole(page, 'doctor', 'ta');
  await page.setViewportSize({ width: 640, height: 800 });
  await page.evaluate(() => {
    document.documentElement.style.fontSize = '200%';
  });
  for (const hash of ['#/alerts', '#/care/gaps', '#/care/plans']) {
    await go(page, hash, /\S/);
    await expect(page.locator('main table').first()).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth), `${hash}: horizontal scroll at 200%`).toBe(false);
  }
});

test('the admin menu has Scheduling (read plus setup), Alerts, Dashboard and Care', async ({ page }) => {
  await openConsole(page, 'admin', 'en');
  const nav = page.locator('.main-nav');
  for (const name of ['Tasks', 'Scheduling', 'Alerts', 'Dashboard', 'Care', 'Admin']) await expect(nav.getByRole('link', { name, exact: true })).toBeVisible();
  await go(page, '#/scheduling/slots', /\S/);
  await expect(page.locator('main table').first()).toBeVisible();
});
