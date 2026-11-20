import { expect, test, type Page } from '@playwright/test';
import { LANGS, VIEWPORTS, go, openConsole, type Lang } from '../e2e/helpers';
import { GOOD_PASSWORD, installMock } from '../e2e/mockBackend';
import { ID } from '../e2e/consoleApi';
import { GOOD_PHONE, GOOD_PHONE_CODE, installPortalMock } from '../e2e/portalMock';

// Screenshots of the main screens at 320 / 768 / 1280 in English and Tamil, ALL against fake data (e2e/consoleApi.ts and e2e/portalMock.ts, MOCK
// ONLY): no real person, backend or sign-in service is involved. They are compared with the stored baselines in e2e-visual/baselines.
// Run `npm run visual:check` to compare, `npm run visual:update` to replace them on purpose (then look at the pictures before committing).
// The clock is fixed so no time-of-day text moves; animation, caret and motion are off (playwright.visual.config.ts; the app's own security
// policy forbids injecting styles, so Playwright's built-in animation switch is used).
// Tamil text in the pictures is the machine-checked, NOT human-reviewed wording.

const NOW = new Date('2026-11-18T10:00:00Z');

async function shoot(page: Page, name: string, lang: Lang, target?: string) {
  for (const vp of VIEWPORTS) {
    await page.setViewportSize({ width: vp.width, height: vp.height });
    await page.waitForTimeout(250); // let the layout settle after the resize
    const shot = `${name}-${lang}-${vp.width}.png`;
    if (target) await expect(page.locator(target).first()).toHaveScreenshot(shot);
    else await expect(page).toHaveScreenshot(shot, { fullPage: true });
  }
}

async function staff(page: Page, role: string, lang: Lang) {
  await page.clock.setFixedTime(NOW);
  return openConsole(page, role, lang);
}

async function portal(page: Page, lang: Lang) {
  await page.clock.setFixedTime(NOW);
  await page.addInitScript((l) => localStorage.setItem('careflow.language', l), lang);
  await installPortalMock(page, { start: 'active', granted: true });
  await page.goto('/patient/sign-in/');
  await page.locator('input[type=tel]').fill(GOOD_PHONE);
  await page.locator('form:has(input[type=tel], input[autocomplete=one-time-code]) button[type=submit]').click();
  await page.locator('input[autocomplete=one-time-code]').fill(GOOD_PHONE_CODE);
  await page.locator('form:has(input[type=tel], input[autocomplete=one-time-code]) button[type=submit]').click();
  await expect(page.locator('#portal-booking')).toBeVisible();
  await page.waitForLoadState('networkidle');
}

for (const lang of LANGS) {
  test(`staff sign-in [${lang}]`, async ({ page }) => {
    await page.clock.setFixedTime(NOW);
    await page.addInitScript((l) => localStorage.setItem('careflow.language', l), lang);
    await installMock(page, { enrolled: true, roles: [{ clinic_id: ID.clinic, role: 'nurse', clinic_name: 'Placeholder Clinic', clinic_origin: 'test' }] });
    await page.goto('/staff/');
    await expect(page.locator('input[type=password]')).toBeVisible();
    expect(GOOD_PASSWORD).toBeTruthy(); // the mock password is never typed here: this is the sign-in page itself
    await shoot(page, 'staff-sign-in', lang);
  });

  test(`referral review [${lang}]`, async ({ page }) => {
    await staff(page, 'doctor', lang);
    await go(page, `#/referrals/${ID.doc}`, ID.doc);
    await page.waitForLoadState('networkidle');
    await shoot(page, 'referral-review', lang);
  });

  test(`scheduling [${lang}]`, async ({ page }) => {
    await staff(page, 'front_desk', lang);
    await go(page, '#/scheduling', /\S/);
    await page.waitForLoadState('networkidle');
    await shoot(page, 'scheduling', lang);
  });

  test(`patient requests [${lang}]`, async ({ page }) => {
    await staff(page, 'front_desk', lang);
    await go(page, '#/requests', /\S/);
    await expect(page.locator('main table')).toBeVisible();
    await shoot(page, 'patient-requests', lang);
  });

  test(`family links [${lang}]`, async ({ page }) => {
    await staff(page, 'front_desk', lang);
    await go(page, '#/family', /\S/);
    await expect(page.locator('main table')).toBeVisible();
    await shoot(page, 'family-links', lang);
  });

  test(`knowledge [${lang}]`, async ({ page }) => {
    await staff(page, 'doctor', lang);
    await go(page, '#/knowledge', /\S/);
    await expect(page.locator('main table').first()).toBeVisible();
    await shoot(page, 'knowledge', lang);
  });

  test(`messages [${lang}]`, async ({ page }) => {
    await staff(page, 'nurse', lang);
    await go(page, '#/messages', /\S/);
    await expect(page.locator('main table')).toBeVisible();
    await shoot(page, 'messages', lang);
  });

  test(`portal home [${lang}]`, async ({ page }) => {
    await portal(page, lang);
    await shoot(page, 'portal-home', lang);
  });

  test(`portal booking [${lang}]`, async ({ page }) => {
    await portal(page, lang);
    await shoot(page, 'portal-booking', lang, '#portal-booking');
  });

  test(`portal prep [${lang}]`, async ({ page }) => {
    await portal(page, lang);
    await page.getByRole('button', { name: /What to prepare|தயாராக வேண்டியவை/ }).first().click();
    await expect(page.getByText('PLACEHOLDER checklist item one')).toBeVisible();
    await shoot(page, 'portal-prep', lang, '#portal-booking');
  });
}
