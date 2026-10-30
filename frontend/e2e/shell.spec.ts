import { expect, test, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

// Real-browser checks for every screen: automated accessibility (WCAG 2.2 AA tags, including colour
// contrast), keyboard-only operation, narrow-viewport overflow, and a screenshot per screen.
// Screenshots land in test-results/shots/ (git-ignored); they are for human review, not baselines.

const SCREENS = [
  { name: 'patient-shell', path: '/patient/' },
  { name: 'staff-shell', path: '/staff/' },
] as const;
const LANGS = ['en', 'ta'] as const;
const VIEWPORTS = [
  { name: 'phone', width: 320, height: 700 },
  { name: 'desktop', width: 1280, height: 800 },
] as const;

async function open(page: Page, path: string, lng: (typeof LANGS)[number]) {
  await page.addInitScript((l) => localStorage.setItem('careflow.language', l), lng);
  await page.goto(path);
  await expect(page.locator('html')).toHaveAttribute('lang', lng);
}

for (const screen of SCREENS) {
  for (const lng of LANGS) {
    for (const vp of VIEWPORTS) {
      test(`${screen.name} [${lng}, ${vp.name}]: accessible, no overflow, screenshot`, async ({ page }) => {
        await page.setViewportSize({ width: vp.width, height: vp.height });
        await open(page, screen.path, lng);

        const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze();
        expect(results.violations, JSON.stringify(results.violations.map((v) => v.id))).toEqual([]);

        const skipBottom = await page.locator('.skip-link').evaluate((el) => el.getBoundingClientRect().bottom);
        expect(skipBottom, 'skip link must be off-screen until focused').toBeLessThanOrEqual(0);

        const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
        expect(overflow, 'horizontal scroll').toBe(false);

        await page.screenshot({ path: `test-results/shots/${screen.name}-${lng}-${vp.name}.png`, fullPage: true });
      });
    }
  }

  test(`${screen.name}: keyboard only - skip link first, reaches the language switcher, moves focus to main`, async ({ page }) => {
    await open(page, screen.path, 'en');
    await page.keyboard.press('Tab');
    const skip = page.getByRole('link', { name: 'Skip to main content' });
    await expect(skip).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page.locator('main')).toBeFocused();

    // From the top of the page, two Tab presses reach the language select; switch with the keyboard alone.
    await page.reload();
    await expect(page.locator('main')).toBeVisible();
    await page.keyboard.press('Tab');
    await page.keyboard.press('Tab');
    const select = page.getByRole('combobox', { name: 'Language' });
    await expect(select).toBeFocused();
    await select.selectOption('ta');
    await expect(page.locator('html')).toHaveAttribute('lang', 'ta');
  });

  test(`${screen.name}: focus is visible on the focused control`, async ({ page }) => {
    await open(page, screen.path, 'en');
    await page.keyboard.press('Tab');
    const outline = await page.evaluate(() => getComputedStyle(document.activeElement as Element).outlineStyle);
    expect(outline).not.toBe('none');
  });
}
