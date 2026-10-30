import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test, type Page } from '@playwright/test';

// The production policy (csp.ts) against the real production build. Stage 1 serves it Report-Only; any
// violation the pages cause fails here, before the policy is enforced.

const policy = () => readFileSync(resolve(import.meta.dirname, '../dist-e2e/csp-policy.txt'), 'utf8').trim();

const PAGES = ['/patient/', '/patient/sign-in/', '/patient/sign-up/', '/patient/privacy/', '/staff/'];

async function collectViolations(page: Page) {
  await page.addInitScript(() => {
    (window as unknown as { __csp: string[] }).__csp = [];
    document.addEventListener('securitypolicyviolation', (e) => {
      (window as unknown as { __csp: string[] }).__csp.push(`${e.violatedDirective} ${e.blockedURI}`);
    });
  });
}
const violations = (page: Page) => page.evaluate(() => (window as unknown as { __csp: string[] }).__csp);

test('the production policy ships as csp-policy.txt and is what the server sends (stage 1: report-only)', async ({ page }) => {
  const response = await page.goto('/patient/');
  expect(response?.headers()['content-security-policy-report-only']).toBe(policy());
  expect(policy()).toContain('https://mock-project.supabase.invalid');
});

for (const path of PAGES) {
  test(`${path}: loads, switches language and opens details with no CSP violation`, async ({ page }) => {
    await collectViolations(page);
    await page.goto(path);
    await expect(page.locator('main')).toBeVisible();
    await page.locator('select').selectOption('ta');
    await page.locator('select').selectOption('en');
    for (const s of await page.locator('summary').all()) await s.click();
    await page.waitForLoadState('networkidle');
    expect(await violations(page)).toEqual([]);
  });
}

test('negative control: a deliberately injected inline script IS reported, so the collector works', async ({ page }) => {
  await collectViolations(page);
  await page.goto('/patient/');
  await page.evaluate(() => {
    const s = document.createElement('script');
    s.textContent = 'window.__ran = true';
    document.head.append(s);
  });
  await expect.poll(() => violations(page)).toEqual([expect.stringContaining('script-src')]);
});
