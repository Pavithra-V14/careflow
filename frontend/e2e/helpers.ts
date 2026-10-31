import { expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { GOOD_CODE, GOOD_PASSWORD, installMock, type Scenario } from './mockBackend';
import { ID, LEAK } from './consoleApi';

// Shared by the console browser specs. MOCK-ONLY (see mockBackend.ts and consoleApi.ts).

export const LANGS = ['en', 'ta'] as const;
export const VIEWPORTS = [
  { name: 'phone', width: 320, height: 760 },
  { name: 'tablet', width: 768, height: 900 },
  { name: 'desktop', width: 1280, height: 800 },
] as const;
export type Lang = (typeof LANGS)[number];

export async function openConsole(page: Page, role: string, lang: Lang, extra: Scenario = {}) {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.addInitScript((l) => {
    localStorage.setItem('careflow.language', l);
    (window as unknown as { __csp: string[] }).__csp = [];
    document.addEventListener('securitypolicyviolation', (e) => (window as unknown as { __csp: string[] }).__csp.push(`${e.violatedDirective} ${e.blockedURI}`));
  }, lang);
  const mock = await installMock(page, { enrolled: true, roles: [{ clinic_id: ID.clinic, role, clinic_name: 'Placeholder Clinic', clinic_origin: 'test' }], ...extra });
  await page.goto('/staff/');
  await page.locator('input[type=email]').fill('staff@example.invalid');
  await page.locator('input[type=password]').fill(GOOD_PASSWORD);
  await page.locator('button[type=submit]').click();
  await page.locator('input[inputmode=numeric]').fill(GOOD_CODE);
  await page.locator('form button[type=submit]').click();
  await expect(page.locator('.session-bar')).toBeVisible();
  return mock;
}

export async function go(page: Page, hash: string, ready: string | RegExp) {
  await page.evaluate((h) => (window.location.hash = h), hash);
  await expect(page.locator('main')).toContainText(ready);
}

export async function check(page: Page, name: string, lang: Lang, vp: (typeof VIEWPORTS)[number]) {
  await page.setViewportSize({ width: vp.width, height: vp.height });
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze();
  expect(results.violations, `${name} [${lang} ${vp.name}]: ${JSON.stringify(results.violations.map((v) => v.id))}`).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth), `${name} [${lang} ${vp.name}]: horizontal page scroll`).toBe(false);
  expect(await page.evaluate(() => (window as unknown as { __csp: string[] }).__csp), `${name}: CSP violations`).toEqual([]);
  expect(await page.locator('body').innerText(), `${name}: backend text on the page`).not.toContain(LEAK);
  await expect(page.locator('.env-badge')).toBeVisible(); // the LIVE / TEST badge stays on every screen
  if (vp.name !== 'tablet') await page.screenshot({ path: `test-results/shots/console-${name}-${lang}-${vp.name}.png`, fullPage: true });
}

/** Axe and friends at all three widths for the screen that is showing now. */
export async function sweep(page: Page, name: string, lang: Lang) {
  for (const vp of VIEWPORTS) await check(page, name, lang, vp);
  await page.setViewportSize({ width: 1280, height: 800 });
}

