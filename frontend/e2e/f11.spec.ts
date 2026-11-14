import { expect, test, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { GOOD_PHONE, GOOD_PHONE_CODE, LEAK, installPortalMock, type PortalScenario } from './portalMock';

// The patient portal in a real browser against a fake phone-code sign-in and fake portal routes (e2e/portalMock.ts, MOCK ONLY).
// Mobile first: every screen is checked with axe (WCAG 2.2 AA is the goal we test toward) at 320, 768 and 1280, in English and Tamil, with no
// horizontal scroll, and the browser is checked to hold nothing about the patient except the sign-in session.

const LANGS = ['en', 'ta'] as const;
const VIEWPORTS = [320, 768, 1280] as const;

async function open(page: Page, lang: (typeof LANGS)[number], scenario: PortalScenario = {}, path = '/patient/sign-in/') {
  await page.addInitScript((l) => localStorage.setItem('careflow.language', l), lang);
  const mock = await installPortalMock(page, scenario);
  await page.goto(path);
  return mock;
}

async function signIn(page: Page) {
  await page.locator('input[type=tel]').fill(GOOD_PHONE);
  await page.locator('form button[type=submit]').click();
  await page.locator('input[autocomplete=one-time-code]').fill(GOOD_PHONE_CODE);
  await page.locator('form button[type=submit]').click();
}

async function sweep(page: Page, name: string, lang: string) {
  for (const width of VIEWPORTS) {
    await page.setViewportSize({ width, height: 800 });
    const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze();
    expect(results.violations, `${name} [${lang} ${width}]: ${JSON.stringify(results.violations.map((v) => v.id))}`).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth), `${name} [${lang} ${width}]: horizontal scroll`).toBe(false);
    expect(await page.locator('body').innerText(), `${name}: backend text on the page`).not.toContain(LEAK);
  }
}

for (const lang of LANGS) {
  test(`voice: the setting, then the session form with nothing chosen [${lang}]`, async ({ page }) => {
    await open(page, lang, { start: 'active', granted: true, voiceOn: true });
    await signIn(page);
    await expect(page.locator('#portal-voice')).toBeVisible();
    await expect(page.locator('#portal-voice').locator('..').getByRole('radio')).toHaveCount(3);
    await sweep(page, 'portal-voice', lang);
  });

  test(`voice: opening a session when voice is not set up (503) [${lang}]`, async ({ page }) => {
    await open(page, lang, { start: 'active', granted: true, voiceUnavailable: true, voiceOn: true });
    await signIn(page);
    const voice = page.locator('#portal-voice').locator('..');
    await voice.getByRole('radio').first().check();
    await voice.getByRole('checkbox').check();
    await voice.locator('button.button-primary').click();
    await expect(voice.locator('[role=status]').last()).toBeVisible();
    await sweep(page, 'portal-voice-503', lang);
  });

  test(`sign in: the number form [${lang}]`, async ({ page }) => {
    await open(page, lang);
    await expect(page.locator('input[type=tel]')).toBeVisible();
    await sweep(page, 'portal-phone', lang);
  });

  test(`sign in: the code form [${lang}]`, async ({ page }) => {
    await open(page, lang);
    await page.locator('input[type=tel]').fill(GOOD_PHONE);
    await page.locator('form button[type=submit]').click();
    await expect(page.locator('input[autocomplete=one-time-code]')).toBeVisible();
    await sweep(page, 'portal-code', lang);
  });

  test(`no patient account yet: create it [${lang}]`, async ({ page }) => {
    await open(page, lang, { start: 'none' });
    await signIn(page);
    await expect(page.locator('main button.button-primary')).toBeVisible();
    await sweep(page, 'portal-register', lang);
  });

  test(`a new account: the notice and activation [${lang}]`, async ({ page }) => {
    await open(page, lang, { start: 'registered' });
    await signIn(page);
    await expect(page.locator('.notice-text')).toBeVisible();
    await sweep(page, 'portal-consent', lang);
  });

  test(`an active account: claim, grievance and my data [${lang}]`, async ({ page }) => {
    await open(page, lang, { start: 'active', granted: true });
    await signIn(page);
    await expect(page.locator('#portal-mydata')).toBeVisible();
    await sweep(page, 'portal-active', lang);
  });
}

test('the notice is shown exactly as sent: line breaks kept, markup not interpreted', async ({ page }) => {
  await open(page, 'en', { start: 'registered' });
  await signIn(page);
  const text = page.locator('.notice-text');
  await expect(text).toHaveText('Notice line one.\nNotice line two <b>not markup</b>.');
  expect(await text.locator('b').count()).toBe(0);
});

test('keyboard only: sign in with Tab and Enter, grant the notice and activate', async ({ page }) => {
  const mock = await open(page, 'en', { start: 'registered' });
  await page.locator('input[type=tel]').focus();
  await page.keyboard.type(GOOD_PHONE);
  await page.keyboard.press('Enter');
  await page.locator('input[autocomplete=one-time-code]').focus();
  await page.keyboard.type(GOOD_PHONE_CODE);
  await page.keyboard.press('Enter');
  await page.getByRole('button', { name: 'Grant', exact: true }).focus();
  await page.keyboard.press('Enter');
  await page.getByRole('dialog').getByRole('button', { name: 'Grant', exact: true }).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByText('Your decision was recorded.')).toBeVisible();
  await page.getByRole('button', { name: 'Activate my account' }).focus();
  await page.keyboard.press('Enter');
  await page.getByRole('dialog').getByRole('button', { name: 'Activate my account' }).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByText('Your account is active.')).toBeVisible();
  expect(mock.stage()).toBe('active');
});

test('a wrong code shows one fixed message and reveals nothing', async ({ page }) => {
  await open(page, 'en');
  await page.locator('input[type=tel]').fill(GOOD_PHONE);
  await page.locator('form button[type=submit]').click();
  await page.locator('input[autocomplete=one-time-code]').fill('000000');
  await page.locator('form button[type=submit]').click();
  await expect(page.getByRole('alert')).toContainText('That code did not work');
  expect(await page.locator('body').innerText()).not.toContain(LEAK);
});

test('the browser keeps nothing about the patient except the sign-in session', async ({ page }) => {
  await open(page, 'en', { start: 'active', granted: true, bound: true });
  await signIn(page);
  await expect(page.locator('#portal-mydata')).toBeVisible();
  const kept = await page.evaluate(() => ({ local: Object.keys(localStorage), session: Object.keys(sessionStorage), idb: 'indexedDB' in window ? 1 : 0, cookies: document.cookie }));
  // Allowed: the Supabase session and the language choice (a convenience, not patient data). Nothing else.
  expect(kept.local.filter((k) => !k.startsWith('sb-') && k !== 'careflow.language')).toEqual([]);
  expect(kept.session).toEqual([]);
  expect(kept.cookies).toBe('');
  // The sign-in service's own session record (sb-...) may hold the signed-in user; everything else must hold nothing about the patient.
  const body = await page.evaluate(() => JSON.stringify(Object.entries(localStorage).filter(([k]) => !k.startsWith('sb-'))));
  expect(body).not.toContain(GOOD_PHONE);
  expect(body).not.toContain('Notice line');
});

test('download my data saves exactly the file the API sent and keeps nothing on the page', async ({ page }) => {
  await open(page, 'en', { start: 'active', granted: true, bound: true });
  await signIn(page);
  const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: 'Download my data' }).click()]);
  expect(download.suggestedFilename()).toBe('my-data.json');
  await expect(page.getByText('Your file was downloaded. It holds:')).toBeVisible();
  expect(await page.locator('body').innerText()).not.toContain('notice_version'); // the file's contents are not rendered
});

test('nothing outside the page origin and the two configured hosts is contacted', async ({ page }) => {
  const foreign: string[] = [];
  page.on('request', (r) => {
    const u = new URL(r.url());
    if (!['127.0.0.1', 'localhost', 'mock-project.supabase.invalid', 'mock-api.invalid'].includes(u.hostname) && u.protocol.startsWith('http')) foreign.push(u.hostname);
  });
  await open(page, 'en', { start: 'active', granted: true });
  await signIn(page);
  await expect(page.locator('#portal-mydata')).toBeVisible();
  expect(foreign).toEqual([]);
});

test('voice: opened with the keyboard only, and nothing asks for the microphone', async ({ page }) => {
  await page.addInitScript(() => {
    (window as unknown as { __mic: number }).__mic = 0;
    Object.defineProperty(navigator, 'mediaDevices', { value: { getUserMedia: () => { (window as unknown as { __mic: number }).__mic++; return Promise.reject(new Error('no')); } }, configurable: true });
  });
  const mock = await open(page, 'en', { start: 'active', granted: true, voiceOn: true });
  await signIn(page);
  const radio = page.getByRole('radio', { name: 'Replying to an appointment offer' });
  await radio.focus();
  await page.keyboard.press('Space');
  await page.getByRole('checkbox', { name: /I have read the notice above/ }).focus();
  await page.keyboard.press('Space');
  const open2 = page.getByRole('button', { name: 'Open voice session' });
  await open2.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByText('A voice session was opened.')).toBeVisible();
  expect(await page.evaluate(() => (window as unknown as { __mic: number }).__mic)).toBe(0);
  expect(mock.calls.filter((c) => c.path === '/v1/portal/voice/sessions')).toHaveLength(1);
});
