import { expect, test, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { LANGS, VIEWPORTS, openConsole, sweep } from './helpers';
import { GOOD_PHONE, GOOD_PHONE_CODE, LEAK, installPortalMock } from './portalMock';

// Idle sign-out with its "still there?" warning, in a real browser with a fake clock. The browser-test build carries the LABELLED
// PLACEHOLDER minutes (15 for staff, 10 for patients; see vite.config.ts). MOCK ONLY.

async function patientSignIn(page: Page, lang: (typeof LANGS)[number]) {
  await page.addInitScript((l) => localStorage.setItem('careflow.language', l), lang);
  const mock = await installPortalMock(page, { start: 'active', granted: true });
  await page.goto('/patient/sign-in/');
  await page.locator('input[type=tel]').fill(GOOD_PHONE);
  await page.locator('form:has(input[type=tel], input[autocomplete=one-time-code]) button[type=submit]').click();
  await page.locator('input[autocomplete=one-time-code]').fill(GOOD_PHONE_CODE);
  await page.locator('form:has(input[type=tel], input[autocomplete=one-time-code]) button[type=submit]').click();
  await expect(page.locator('#portal-mydata')).toBeVisible();
  return mock;
}

for (const lang of LANGS) {
  test(`staff [${lang}]: the warning appears for the last minute, is accessible at three widths, and the button keeps the session`, async ({ page }) => {
    await page.clock.install();
    await openConsole(page, 'front_desk', lang);
    await page.clock.fastForward('14:30');
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    await sweep(page, 'idle-warning-staff', lang);
    await dialog.getByRole('button').click();
    await expect(dialog).toBeHidden();
    await page.clock.fastForward('14:00'); // the whole period started again, so nothing happens yet
    await expect(page.locator('.session-bar')).toBeVisible();
  });

  test(`patient [${lang}]: the warning appears for the last minute, is accessible at three widths, and the button keeps the session`, async ({ page }) => {
    await page.clock.install();
    await patientSignIn(page, lang);
    await page.clock.fastForward('09:30');
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    for (const vp of VIEWPORTS) {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze();
      expect(results.violations, `idle warning patient [${lang} ${vp.width}]: ${JSON.stringify(results.violations.map((v) => v.id))}`).toEqual([]);
      expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false);
    }
    await dialog.getByRole('button').click();
    await expect(dialog).toBeHidden();
    await page.clock.fastForward('09:00');
    await expect(page.locator('#portal-mydata')).toBeVisible();
  });
}

test('staff: with no answer the session ends, the console is gone and the sign-in screen says so', async ({ page }) => {
  await page.clock.install();
  await openConsole(page, 'front_desk', 'en');
  await page.clock.fastForward('15:05');
  await expect(page.locator('input[type=email]')).toBeVisible();
  await expect(page.getByText('Please sign in again.')).toBeVisible();
  await expect(page.locator('.session-bar')).toHaveCount(0);
  expect(await page.locator('body').innerText()).not.toContain(LEAK);
});

test('patient: with no answer the account is gone and a plain note is shown; stray activity during the warning does not keep the session', async ({ page }) => {
  await page.clock.install();
  await patientSignIn(page, 'en');
  await page.clock.fastForward('09:30');
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.keyboard.press('Tab'); // stray activity is not an answer
  await page.clock.fastForward('00:40');
  await expect(page.getByText(/You were signed out because the page was not used for a while/)).toBeVisible();
  await expect(page.locator('#portal-mydata')).toHaveCount(0);
  expect(await page.locator('body').innerText()).not.toContain(LEAK);
});

test('keyboard only: the warning takes focus and the button answers it', async ({ page }) => {
  await page.clock.install();
  await openConsole(page, 'front_desk', 'en');
  await page.clock.fastForward('14:30');
  const button = page.getByRole('dialog').getByRole('button');
  await expect(button).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog')).toBeHidden();
});
