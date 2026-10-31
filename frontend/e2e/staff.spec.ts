import { expect, test, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { GOOD_CODE, GOOD_PASSWORD, installMock, type Scenario } from './mockBackend';

// Staff sign-in and shell in a real browser, against the REAL built app and the REAL supabase-js, with the
// Supabase and API hosts faked at the network layer (e2e/mockBackend.ts). MOCK-ONLY evidence: this proves the
// app's behaviour against my reading of the Supabase Auth responses, not against a live project.
// Per screen: axe (WCAG 2.2 AA, including contrast), no overflow, no CSP violation, a screenshot.

const CLINIC = '3f2b8c1e-6a4d-4e1b-9d0a-1c2d3e4f5a6b';
const CLINIC_B = '7a7a7a7a-6a4d-4e1b-9d0a-1c2d3e4f5a6b';
const LANGS = ['en', 'ta'] as const;
const VIEWPORTS = [
  { name: 'phone', width: 320, height: 760 },
  { name: 'desktop', width: 1280, height: 800 },
] as const;

async function start(page: Page, scenario: Scenario, lng: (typeof LANGS)[number] = 'en', vp: (typeof VIEWPORTS)[number] = VIEWPORTS[1]) {
  await page.setViewportSize({ width: vp.width, height: vp.height });
  await page.addInitScript((l) => {
    localStorage.setItem('careflow.language', l);
    (window as unknown as { __csp: string[] }).__csp = [];
    document.addEventListener('securitypolicyviolation', (e) => (window as unknown as { __csp: string[] }).__csp.push(`${e.violatedDirective} ${e.blockedURI}`));
  }, lng);
  const mock = await installMock(page, scenario);
  await page.goto('/staff/');
  await expect(page.locator('html')).toHaveAttribute('lang', lng);
  return mock;
}

async function check(page: Page, name: string, lng: string, vp: string) {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze();
  expect(results.violations, `${name}: ${JSON.stringify(results.violations.map((v) => v.id))}`).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth), `${name}: horizontal scroll`).toBe(false);
  expect(await page.evaluate(() => (window as unknown as { __csp: string[] }).__csp), `${name}: CSP violations`).toEqual([]);
  await page.screenshot({ path: `test-results/shots/staff-${name}-${lng}-${vp}.png`, fullPage: true });
}

async function submitSignIn(page: Page, password = GOOD_PASSWORD) {
  await page.locator('input[type=email]').fill('staff@example.invalid');
  await page.locator('input[type=password]').fill(password);
  await page.locator('button[type=submit]').click();
}
const codeInput = (page: Page) => page.locator('input[inputmode=numeric]');
async function submitCode(page: Page, code = GOOD_CODE) {
  await codeInput(page).fill(code);
  await page.locator('form button[type=submit]').click();
}

for (const lng of LANGS) {
  for (const vp of VIEWPORTS) {
    test(`first sign-in [${lng}, ${vp.name}]: sign-in, authenticator enrolment (QR from a data: URI), then the console`, async ({ page }) => {
      await start(page, {}, lng, vp);
      await expect(page.locator('h1')).not.toBeFocused(); // first load starts at the top of the page
      await check(page, 'signin', lng, vp.name);

      await submitSignIn(page);
      await expect(page.locator('img.qr')).toBeVisible();
      expect(await page.locator('img.qr').evaluate((img: HTMLImageElement) => img.naturalWidth > 0)).toBe(true); // the data: QR actually decoded
      await check(page, 'enrol', lng, vp.name);

      await submitCode(page);
      await expect(page.locator('.session-bar')).toBeVisible();
      await expect(page.locator('.env-badge')).toHaveClass(/env-unknown/);
      await check(page, 'console', lng, vp.name);
    });
  }

  test(`returning sign-in [${lng}]: the authenticator challenge`, async ({ page }) => {
    await start(page, { enrolled: true }, lng);
    await submitSignIn(page);
    await expect(codeInput(page)).toBeVisible();
    await expect(page.locator('img.qr')).toHaveCount(0); // no re-enrolment
    await check(page, 'challenge', lng, 'desktop');
    await submitCode(page);
    await expect(page.locator('.session-bar')).toBeVisible();
  });
}

test('a user with several roles must pick one; nothing is preselected', async ({ page }) => {
  await start(page, { enrolled: true, roles: [{ clinic_id: CLINIC, role: 'nurse' }, { clinic_id: CLINIC_B, role: 'admin' }] });
  await submitSignIn(page);
  await submitCode(page);
  const radios = page.getByRole('radio');
  await expect(radios).toHaveCount(2);
  for (const r of await radios.all()) await expect(r).not.toBeChecked();
  await expect(page.locator('button[type=submit]')).toBeDisabled();
  await check(page, 'role-picker', 'en', 'desktop');
  await radios.nth(1).check();
  await page.locator('button[type=submit]').click();
  await expect(page.locator('.role-badge')).toContainText('Admin');
  await check(page, 'console-multirole', 'en', 'desktop');
});

for (const [origin, label] of [['live', 'LIVE'], ['test', 'TEST']] as const) {
  test(`the ${label} badge shows only when the backend says so, with the clinic name`, async ({ page }) => {
    await start(page, { enrolled: true, roles: [{ clinic_id: CLINIC, role: 'doctor', clinic_name: 'Placeholder Clinic', clinic_origin: origin }] });
    await submitSignIn(page);
    await submitCode(page);
    await expect(page.locator('.env-badge')).toHaveText(label);
    await expect(page.locator('.clinic')).toContainText('Placeholder Clinic');
    await check(page, `badge-${origin}`, 'en', 'desktop');
  });
}

test('failures show one generic message each and reveal nothing', async ({ page }) => {
  await start(page, { enrolled: true });
  await submitSignIn(page, 'wrong');
  const alert = page.getByRole('alert');
  await expect(alert).toBeVisible();
  expect((await alert.textContent()) ?? '').not.toMatch(/invalid|credentials|not found|locked/i);
  await check(page, 'signin-failed', 'en', 'desktop');

  await submitSignIn(page);
  await submitCode(page, '000000');
  await expect(page.getByRole('alert')).toContainText('not accepted');
  await check(page, 'code-failed', 'en', 'desktop');
});

test('an account the API refuses (403) gets one neutral message and a way out', async ({ page }) => {
  await start(page, { enrolled: true, meStatus: 403 });
  await submitSignIn(page);
  await submitCode(page);
  await expect(page.getByRole('alert')).toContainText('cannot use the staff console');
  await check(page, 'unavailable', 'en', 'desktop');
  await page.getByRole('alert').getByRole('button').click();
  await expect(page.locator('input[type=email]')).toBeVisible();
});

test('sign-out returns to sign-in and the session does not come back on reload', async ({ page }) => {
  await start(page, { enrolled: true });
  await submitSignIn(page);
  await submitCode(page);
  await page.locator('.session-bar button').last().click();
  await expect(page.locator('input[type=email]')).toBeVisible();
  await page.reload();
  await expect(page.locator('input[type=email]')).toBeVisible();
});

test('a signed-in session survives a reload (supabase-js default storage), and is aal2 so no second prompt', async ({ page }) => {
  await start(page, { enrolled: true });
  await submitSignIn(page);
  await submitCode(page);
  await expect(page.locator('.session-bar')).toBeVisible();
  await page.reload();
  await expect(page.locator('.session-bar')).toBeVisible();
});

test('keyboard only: sign in and verify with Tab and Enter, never the mouse', async ({ page }) => {
  await start(page, { enrolled: true });
  await page.keyboard.press('Tab'); // skip link
  await page.keyboard.press('Tab'); // language
  await page.keyboard.press('Tab'); // email
  await expect(page.locator('input[type=email]')).toBeFocused();
  await page.keyboard.type('staff@example.invalid');
  await page.keyboard.press('Tab');
  await expect(page.locator('input[type=password]')).toBeFocused();
  await page.keyboard.type(GOOD_PASSWORD);
  await page.keyboard.press('Enter');

  await expect(page.locator('h1')).toBeFocused(); // focus moved to the new screen's heading
  await page.keyboard.press('Tab');
  await expect(codeInput(page)).toBeFocused();
  await page.keyboard.type(GOOD_CODE);
  await page.keyboard.press('Enter');
  await expect(page.locator('.session-bar')).toBeVisible();
  // Every control in the session bar is reachable by keyboard.
  // Walking backwards from the heading passes the six navigation links, then reaches Sign out.
  await page.locator('h1').focus();
  await page.keyboard.press('Shift+Tab');
  await expect(page.locator('.main-nav a').last()).toBeFocused();
  for (let i = 0; i < 6; i++) await page.keyboard.press('Shift+Tab');
  await expect(page.locator('.session-bar button').last()).toBeFocused();
});

test('keyboard only: the focus ring is visible on every field of the sign-in form', async ({ page }) => {
  await start(page, {});
  for (const sel of ['input[type=email]', 'input[type=password]', 'button[type=submit]']) {
    await page.locator(sel).focus();
    expect(await page.locator(sel).evaluate((el) => getComputedStyle(el).outlineStyle), sel).not.toBe('none');
  }
});

test('the development claim panel is not in the production build', async ({ page }) => {
  await start(page, { enrolled: true });
  await submitSignIn(page);
  await submitCode(page);
  await expect(page.locator('.session-bar')).toBeVisible();
  await expect(page.locator('[data-dev-only]')).toHaveCount(0);
});

test('no sign-up route or link exists for staff', async ({ page }) => {
  await start(page, {});
  await expect(page.locator('a:not(.skip-link)')).toHaveCount(0);
  expect(await page.locator('body').innerText()).not.toMatch(/sign up|register/i);
});

test('nothing outside the page origin and the two configured hosts is contacted', async ({ page }) => {
  const hosts = new Set<string>();
  page.on('request', (r) => hosts.add(new URL(r.url()).hostname));
  await start(page, { enrolled: true });
  await submitSignIn(page);
  await submitCode(page);
  await expect(page.locator('.session-bar')).toBeVisible();
  expect([...hosts].sort()).toEqual(['127.0.0.1', 'mock-api.invalid', 'mock-project.supabase.invalid']);
});
