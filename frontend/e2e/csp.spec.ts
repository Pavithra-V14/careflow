import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test, type Page } from '@playwright/test';

// The production policy (csp.ts) against the real production build, ENFORCED through a <meta> tag in each
// page (stage 2; it was validated Report-Only first, see the commit history). Any violation fails here.

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

test('every built page carries the policy as its first head element, and it matches csp-policy.txt', async () => {
  const dist = resolve(import.meta.dirname, '../dist-e2e');
  const pages = ['patient/index.html', 'patient/sign-in/index.html', 'patient/sign-up/index.html', 'patient/privacy/index.html', 'staff/index.html'];
  for (const p of pages) {
    const html = readFileSync(resolve(dist, p), 'utf8');
    const m = /<head>\s*<meta http-equiv="Content-Security-Policy" content="([^"]+)"/.exec(html);
    expect(m?.[1], p).toBe(policy());
    expect(html, `${p} has an inline script`).not.toMatch(/<script(?![^>]*\ssrc=)[^>]*>/);
    expect(html, `${p} has an inline style`).not.toMatch(/<style|\sstyle=/);
  }
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

test('enforcement: a deliberately injected inline script is reported AND does not run', async ({ page }) => {
  await collectViolations(page);
  await page.goto('/patient/');
  await page.evaluate(() => {
    const s = document.createElement('script');
    s.textContent = 'window.__ran = true';
    document.head.append(s);
  });
  await expect.poll(() => violations(page)).toEqual([expect.stringContaining('script-src')]);
  expect(await page.evaluate(() => (window as unknown as { __ran?: boolean }).__ran)).toBeUndefined();
});

test('enforcement: a request to an origin outside connect-src is blocked', async ({ page }) => {
  await collectViolations(page);
  await page.goto('/patient/');
  const outcome = await page.evaluate(() => fetch('https://not-allowed.invalid/x').then(() => 'sent', () => 'blocked'));
  expect(outcome).toBe('blocked');
  expect(await violations(page)).toEqual([expect.stringContaining('connect-src')]);
});
