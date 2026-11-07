import { expect, test, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { GOOD_CODE, GOOD_PASSWORD, installMock } from './mockBackend';
import { ID } from './consoleApi';
import { LANGS, VIEWPORTS, go, openConsole, type Lang } from './helpers';

// F6: the parts of the accessibility sweep the per-screen specs do not cover - 200% zoom on the console, the focus order of the
// signed-in page, the role picker (with its notice) and the offline banner at every width, and keyboard-only role choice.
// MOCK-ONLY (fake Supabase and API hosts). The active-role header flag is OFF in this build, so the 403 fallback is unit-tested.

const TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
const noHorizontalScroll = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);

async function axeClean(page: Page, label: string) {
  const results = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  expect(results.violations, `${label}: ${JSON.stringify(results.violations.map((v) => v.id))}`).toEqual([]);
}

const SCREENS: [string, string, RegExp | string][] = [
  ['home', '#/', /\S/],
  ['referrals', '#/referrals', /\S/],
  ['referral-detail', `#/referrals/${ID.doc}`, ID.doc],
  ['tasks', '#/tasks', /\S/],
  ['patients', '#/patients', /\S/],
  ['scheduling', '#/scheduling/providers', /\S/],
  ['identity', '#/identity/merges', /\S/],
];

for (const lang of LANGS) {
  test(`200% zoom [${lang}]: every console screen stays usable, with no horizontal scroll and no axe violation`, async ({ page }) => {
    await openConsole(page, 'front_desk', lang);
    // 1280px at 200% browser zoom is a 640px viewport; text is also enlarged to 200% as the harshest case.
    await page.setViewportSize({ width: 640, height: 800 });
    await page.evaluate(() => {
      document.documentElement.style.fontSize = '200%';
    });
    for (const [name, hash, ready] of SCREENS) {
      await go(page, hash, ready);
      expect(await noHorizontalScroll(page), `${name} [${lang}]: horizontal scroll at 200%`).toBe(false);
      await axeClean(page, `${name} [${lang}] at 200%`);
      await expect(page.locator('.env-badge')).toBeVisible();
    }
  });
}

test('focus order of the signed-in page: skip link, language, session controls, navigation, then the screen', async ({ page }) => {
  await openConsole(page, 'front_desk', 'en');
  await go(page, '#/referrals', /\S/);
  // A reload puts focus back at the top of the page (a session survives a reload), so Tab starts from the first stop.
  await page.reload();
  await expect(page.locator('.session-bar')).toBeVisible();
  const order: string[] = [];
  for (let i = 0; i < 9; i++) {
    await page.keyboard.press('Tab');
    order.push(await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null;
      return (el?.getAttribute('aria-label') ?? el?.textContent ?? el?.tagName ?? '').replace(/\s+/g, ' ').trim();
    }));
  }
  // The language picker reads as its option list (a select has no text of its own), then the session bar, then the sections in order.
  expect(order[0]).toBe('Skip to main content');
  expect(order[1]).toContain('English');
  expect(order.slice(2)).toEqual(['Sign out', 'Home', 'Referrals', 'Tasks', 'Patients', 'Identity', 'Scheduling']);
});

for (const lang of LANGS) {
  test(`role picker [${lang}]: accessible at 320, 768 and 1280, and chosen with the keyboard only`, async ({ page }) => {
    await page.addInitScript((l) => localStorage.setItem('careflow.language', l), lang);
    await installMock(page, { enrolled: true, roles: [{ clinic_id: ID.clinic, role: 'nurse' }, { clinic_id: '7a7a7a7a-6a4d-4e1b-9d0a-1c2d3e4f5a6b', role: 'doctor' }] });
    await page.goto('/staff/');
    await page.locator('input[type=email]').fill('staff@example.invalid');
    await page.locator('input[type=password]').fill(GOOD_PASSWORD);
    await page.locator('button[type=submit]').click();
    await page.locator('input[inputmode=numeric]').fill(GOOD_CODE);
    await page.locator('form button[type=submit]').click();
    await expect(page.getByRole('radio')).toHaveCount(2);
    for (const vp of VIEWPORTS) {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      await axeClean(page, `role-picker [${lang} ${vp.name}]`);
      expect(await noHorizontalScroll(page), `role-picker [${lang} ${vp.name}]: horizontal scroll`).toBe(false);
    }
    await page.setViewportSize({ width: 1280, height: 800 });
    // Keyboard only: the first radio, Space to choose, Tab to Continue (it was disabled until a choice), Enter.
    await page.getByRole('radio').first().focus();
    await page.keyboard.press('Space');
    await expect(page.getByRole('radio').first()).toBeChecked();
    await page.keyboard.press('Tab'); // the radios are one tab stop, so the next stop is Continue
    await expect(page.locator('button[type=submit]')).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page.locator('.role-badge')).toBeVisible();
  });
}

for (const lang of LANGS) {
  test(`offline banner [${lang}]: a fixed message, accessible at every width, gone when the connection returns`, async ({ page, context }) => {
    await openConsole(page, 'front_desk', lang as Lang);
    await go(page, '#/referrals', /\S/);
    await expect(page.locator('.offline-banner')).toHaveCount(0);
    await context.setOffline(true);
    await expect(page.locator('.offline-banner')).toBeVisible();
    expect(await page.locator('.offline-banner').getAttribute('role')).toBe('status');
    for (const vp of VIEWPORTS) {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      await axeClean(page, `offline banner [${lang} ${vp.name}]`);
      expect(await noHorizontalScroll(page), `offline banner [${lang} ${vp.name}]: horizontal scroll`).toBe(false);
    }
    await context.setOffline(false);
    await expect(page.locator('.offline-banner')).toHaveCount(0);
  });
}
