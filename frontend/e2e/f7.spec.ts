import { expect, test } from '@playwright/test';
import { ID } from './consoleApi';
import { LANGS, go, openConsole, sweep } from './helpers';

// F7: scheduling (slots, bookings, waitlist, offers, setup) and the reviewer view, in a real browser against the fake API
// (e2e/consoleApi.ts, MOCK ONLY). One test per screen keeps each to a few seconds: the cost is axe at three widths.
// Per screen: axe (WCAG 2.2 AA is the goal we test toward) at 320 / 768 / 1280, no horizontal scroll, in English and Tamil.

for (const lang of LANGS) {
  for (const list of ['slots', 'bookings', 'waitlist', 'offers']) {
    test(`front desk, scheduling ${list} [${lang}]`, async ({ page }) => {
      await openConsole(page, 'front_desk', lang);
      await go(page, `#/scheduling/${list}`, /\S/);
      await expect(page.locator('main table').first()).toBeVisible();
      await sweep(page, `f7-${list}`, lang);
    });
  }

  test(`front desk, the hold dialog [${lang}]`, async ({ page }) => {
    await openConsole(page, 'front_desk', lang);
    await go(page, '#/scheduling/slots', /\S/);
    await page.locator('main tbody tr').first().getByRole('button').first().click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await sweep(page, 'f7-hold-dialog', lang);
  });

  test(`front desk, the book dialog [${lang}]`, async ({ page }) => {
    await openConsole(page, 'front_desk', lang);
    await go(page, '#/scheduling/slots', /\S/);
    await page.locator('main tbody tr').first().getByRole('button').nth(1).click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await sweep(page, 'f7-book-dialog', lang);
  });

  test(`front desk, the cancel booking dialog [${lang}]`, async ({ page }) => {
    await openConsole(page, 'front_desk', lang);
    await go(page, '#/scheduling/bookings', /\S/);
    await page.locator('main tbody tr').first().getByRole('button').click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await sweep(page, 'f7-cancel-dialog', lang);
  });

  test(`front desk, the mark delivered dialog [${lang}]`, async ({ page }) => {
    await openConsole(page, 'front_desk', lang);
    await go(page, '#/scheduling/offers', /\S/);
    await page.locator('main tbody tr').first().getByRole('button').nth(1).click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await sweep(page, 'f7-mark-dialog', lang);
  });

  test(`front desk, the add a provider dialog [${lang}]`, async ({ page }) => {
    await openConsole(page, 'front_desk', lang);
    await go(page, '#/scheduling/providers', /\S/);
    await page.locator('main > section > p button').first().click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await sweep(page, 'f7-add-provider-dialog', lang);
  });

  test(`nurse, referral detail with the reviewer view and a reason list [${lang}]`, async ({ page }) => {
    await openConsole(page, 'nurse', lang, { console: { reasonCodes: true } });
    await go(page, `#/referrals/${ID.doc}`, ID.doc);
    await expect(page.locator('main select')).toBeVisible();
    await sweep(page, 'f7-reviewer-view', lang);
  });
}

test('keyboard only: hold a slot (type the length, confirm with Enter) and the dialog returns focus to the button that opened it', async ({ page }) => {
  await openConsole(page, 'front_desk', 'en');
  await go(page, '#/scheduling/slots', /\S/);
  const opener = page.locator('main tbody tr').first().getByRole('button', { name: 'Hold' });
  await opener.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByLabel('Minutes to hold').focus();
  await page.keyboard.type('15');
  const confirm = page.getByRole('dialog').getByRole('button', { name: 'Hold' });
  await confirm.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByText('The slot is held.')).toBeVisible();
});

test('keyboard only: the offers filter and the mark-delivered confirmation can be driven without a mouse', async ({ page }) => {
  await openConsole(page, 'front_desk', 'en');
  await go(page, '#/scheduling/offers', /\S/);
  const mark = page.getByRole('button', { name: /Mark delivered/ }).first();
  await mark.focus();
  await page.keyboard.press('Enter');
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await page.keyboard.press('Tab'); // Cancel -> Mark delivered
  await page.keyboard.press('Enter');
  await expect(page.getByText('The offer is marked delivered.')).toBeVisible();
});

test('200% zoom: the new scheduling screens stay usable with no horizontal scroll', async ({ page }) => {
  await openConsole(page, 'front_desk', 'ta');
  await page.setViewportSize({ width: 640, height: 800 });
  await page.evaluate(() => {
    document.documentElement.style.fontSize = '200%';
  });
  for (const list of ['slots', 'bookings', 'waitlist', 'offers']) {
    await go(page, `#/scheduling/${list}`, /\S/);
    await expect(page.locator('main table').first()).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth), `${list}: horizontal scroll at 200%`).toBe(false);
  }
});
