import { expect, test } from '@playwright/test';
import { ID } from './consoleApi';
import { LANGS, go, openConsole, sweep } from './helpers';

// The audit reader, register a patient, the Run button, the two-person erasure screen and the decrypted record view, in a real browser against
// the fake API (e2e/consoleApi.ts, MOCK ONLY). The erasure write buttons are on in this build only (--mode e2e). Per screen: axe (WCAG 2.2 AA is
// the goal we test toward) at 320 / 768 / 1280, no horizontal scroll, English and Tamil.

const text = (lang: string, en: string, ta: string) => (lang === 'en' ? en : ta);

for (const lang of LANGS) {
  test(`the audit log, before and after reading [${lang}]`, async ({ page }) => {
    await openConsole(page, 'auditor', lang);
    await go(page, '#/audit', /\S/);
    await expect(page.locator('main form')).toBeVisible();
    await sweep(page, 'f13-audit-form', lang);
    await page.locator('main form button[type=submit]').click();
    await expect(page.locator('main table tbody tr')).toHaveCount(3);
    await page.locator('main table summary').first().click();
    await expect(page.locator('main table dl').first()).toBeVisible();
    await sweep(page, 'f13-audit-rows', lang);
  });

  test(`register a patient, its confirmation and its outcome [${lang}]`, async ({ page }) => {
    await openConsole(page, 'front_desk', lang);
    await go(page, '#/patients', /\S/);
    const form = page.locator('main form').nth(1);
    await form.locator('input[type=text]').fill('Synthetic Person');
    await form.locator('input[type=date]').fill('1990-02-03');
    await form.locator('input[type=tel]').fill('9000000000');
    await form.locator('button[type=submit]').click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await sweep(page, 'f13-register-dialog', lang);
    await page.getByRole('dialog').locator('.dialog-actions button').nth(1).click();
    await expect(page.locator('main .state-ok')).toBeVisible();
    await expect(form.locator('input[type=text]')).toHaveValue(''); // the typed details left the page
    await sweep(page, 'f13-register-outcome', lang);
  });

  test(`the Run button on a referral, its confirmation and its result [${lang}]`, async ({ page }) => {
    await openConsole(page, 'nurse', lang);
    await go(page, `#/referrals/${ID.doc}`, /\S/);
    await page.getByRole('button', { name: text(lang, 'Run the referral', 'பரிந்துரையை இயக்கு') }).click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await sweep(page, 'f13-run-dialog', lang);
    await page.getByRole('dialog').locator('.dialog-actions button').nth(1).click();
    await expect(page.locator('main .state-ok').first()).toBeVisible();
    await sweep(page, 'f13-run-result', lang);
  });

  test(`erasure requests, the approve confirmation and the ask form with the clinic's reasons [${lang}]`, async ({ page }) => {
    await openConsole(page, 'front_desk', lang);
    await go(page, '#/erasure', /\S/);
    await expect(page.locator('main table tbody tr')).toHaveCount(2);
    await expect(page.locator('main fieldset input[type=radio]')).toHaveCount(2); // the clinic's reasons, none chosen
    await sweep(page, 'f13-erasure-list', lang);
    await page.locator('main tbody button').first().click(); // Approve on a request somebody else made
    await expect(page.getByRole('dialog')).toBeVisible();
    await expect(page.getByRole('dialog').locator('[data-placeholder]')).toBeVisible(); // the wording is marked as awaiting the privacy owner
    await sweep(page, 'f13-erasure-dialog', lang);
  });

  test(`the full patient details, opened only on a click [${lang}]`, async ({ page }) => {
    await openConsole(page, 'nurse', lang);
    let viewed = 0;
    page.on('request', (r) => {
      if (r.url().includes('/view')) viewed++;
    });
    await go(page, `#/patients/${ID.patient}`, /\S/);
    await expect(page.locator('main [data-placeholder]').first()).toBeVisible();
    await sweep(page, 'f13-full-closed', lang);
    expect(viewed).toBe(0); // nothing read until the click
    await page.getByRole('button', { name: text(lang, 'Open the full details', 'முழு விவரங்களைத் திற') }).click();
    await expect(page.locator('main')).toContainText('Synthetic Person');
    expect(viewed).toBe(1);
    await sweep(page, 'f13-full-open', lang);
    await page.getByRole('button', { name: text(lang, 'Hide the details', 'விவரங்களை மறை') }).click();
    await expect(page.locator('main')).not.toContainText('Synthetic Person');
  });
}
