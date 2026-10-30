import { expect, test, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

// Patient pages in a real browser: automated accessibility (WCAG 2.2 AA tags, including colour
// contrast), keyboard-only operation, narrow-viewport overflow with Tamil text expansion, a static
// emergency banner without JavaScript, and no request leaving the origin. Screenshots are for review.

const PAGES = [
  { name: 'landing', path: '/patient/' },
  { name: 'sign-in', path: '/patient/sign-in/' },
  { name: 'sign-up', path: '/patient/sign-up/' },
  { name: 'privacy', path: '/patient/privacy/' },
] as const;
const LANGS = ['en', 'ta'] as const;
const VIEWPORTS = [
  { name: 'phone', width: 320, height: 700 },
  { name: 'tablet', width: 768, height: 900 },
  { name: 'desktop', width: 1280, height: 800 },
] as const;

async function open(page: Page, path: string, lng: (typeof LANGS)[number]) {
  await page.addInitScript((l) => localStorage.setItem('careflow.language', l), lng);
  await page.goto(path);
  await expect(page.locator('html')).toHaveAttribute('lang', lng);
  await expect(page.locator('main')).toBeVisible();
}

for (const p of PAGES) {
  for (const lng of LANGS) {
    for (const vp of VIEWPORTS) {
      test(`${p.name} [${lng}, ${vp.name}]: accessible, no overflow, screenshot`, async ({ page }) => {
        await page.setViewportSize({ width: vp.width, height: vp.height });
        await open(page, p.path, lng);

        // Open every FAQ item so its content is part of the check.
        for (const d of await page.locator('details').all()) await d.locator('summary').click();

        const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze();
        expect(results.violations, JSON.stringify(results.violations.map((v) => v.id))).toEqual([]);

        expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth), 'horizontal scroll').toBe(false);
        const skipBottom = await page.locator('.skip-link').evaluate((el) => el.getBoundingClientRect().bottom);
        expect(skipBottom, 'skip link off-screen until focused').toBeLessThanOrEqual(0);

        await page.screenshot({ path: `test-results/shots/patient-${p.name}-${lng}-${vp.name}.png`, fullPage: true });
      });
    }
  }
}

test('landing: keyboard only - skip link, sign in link, language switch, FAQ toggle', async ({ page }) => {
  await open(page, '/patient/', 'en');
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: 'Skip to main content' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('main')).toBeFocused();

  // Language select is reachable by Tab from the top and switchable with the keyboard alone.
  await page.reload();
  await expect(page.locator('main')).toBeVisible();
  await expect(page.getByRole('combobox', { name: 'Language' })).toBeVisible();
  const select = page.locator('select'); // its accessible name changes with the language
  for (let i = 0; i < 6 && !(await select.evaluate((el) => el === document.activeElement)); i++) await page.keyboard.press('Tab');
  await expect(select).toBeFocused();
  await select.selectOption('ta');
  await expect(page.locator('html')).toHaveAttribute('lang', 'ta');
  await select.selectOption('en');

  // The first FAQ item opens and closes with Enter.
  const first = page.locator('summary').first();
  await first.focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('details').first()).toHaveAttribute('open', '');
  await page.keyboard.press('Enter');
  await expect(page.locator('details').first()).not.toHaveAttribute('open', '');

  // Sign in is reachable and activates with Enter.
  const signIn = page.getByRole('banner').getByRole('link', { name: 'Sign in' });
  await signIn.focus();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/patient\/sign-in\/$/);
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Sign in');
  await expect(page.getByRole('status')).toContainText('not available yet');
});

test('landing: the emergency banner is present and dialable even with JavaScript disabled', async ({ browser }) => {
  const context = await browser.newContext({ javaScriptEnabled: false });
  const page = await context.newPage();
  await page.goto('/patient/');
  const link = page.getByRole('link', { name: /In an emergency call 108/ });
  await expect(link).toBeVisible();
  await expect(link).toHaveAttribute('href', 'tel:108');
  await context.close();
});

test('landing: the page makes no request outside its own origin', async ({ page }) => {
  const foreign: string[] = [];
  page.on('request', (r) => {
    const u = new URL(r.url());
    if (!['127.0.0.1', 'localhost'].includes(u.hostname) && u.protocol.startsWith('http')) foreign.push(u.hostname);
  });
  await open(page, '/patient/', 'ta');
  await page.waitForLoadState('networkidle');
  expect(foreign).toEqual([]);
});

test('landing: text zoom to 200% keeps everything readable without horizontal scroll', async ({ page }) => {
  await page.setViewportSize({ width: 640, height: 800 }); // 1280px at 200% zoom
  await open(page, '/patient/', 'ta');
  await page.evaluate(() => {
    document.documentElement.style.fontSize = '200%';
  });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false);
});
