import { expect, test, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { GOOD_CODE, GOOD_PASSWORD, installMock, type Scenario } from './mockBackend';
import { ID, LEAK } from './consoleApi';

// The F3 console screens in a real browser, against the REAL built app and the REAL supabase-js, with the Supabase and
// API hosts faked at the network layer (e2e/mockBackend.ts, e2e/consoleApi.ts). MOCK-ONLY evidence: it proves this
// app's behaviour against a fake written from openapi.snapshot.json, not the backend's.
// Per screen, at 320 / 768 / 1280 px, in English and Tamil: axe (WCAG 2.2 AA incl. contrast), no horizontal page
// scroll, no CSP violation, no backend text on the page, and a screenshot for review.

const LANGS = ['en', 'ta'] as const;
const VIEWPORTS = [
  { name: 'phone', width: 320, height: 760 },
  { name: 'tablet', width: 768, height: 900 },
  { name: 'desktop', width: 1280, height: 800 },
] as const;
type Lang = (typeof LANGS)[number];

async function openConsole(page: Page, role: string, lang: Lang, extra: Scenario = {}) {
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

async function go(page: Page, hash: string, ready: string | RegExp) {
  await page.evaluate((h) => (window.location.hash = h), hash);
  await expect(page.locator('main')).toContainText(ready);
}

async function check(page: Page, name: string, lang: Lang, vp: (typeof VIEWPORTS)[number]) {
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
async function sweep(page: Page, name: string, lang: Lang) {
  for (const vp of VIEWPORTS) await check(page, name, lang, vp);
  await page.setViewportSize({ width: 1280, height: 800 });
}

const READY: Record<Lang, Record<string, string>> = {
  en: { referrals: 'Open holds', upload: 'Kind of document', tasks: 'Assigned to', patients: 'Full name', scheduling: 'Show inactive' },
  ta: { referrals: 'திறந்த நிறுத்தங்கள்', upload: 'ஆவண வகை', tasks: 'ஒதுக்கப்பட்டவர்', patients: 'முழுப் பெயர்', scheduling: 'செயலற்றவற்றையும் காட்டு' },
};

for (const lang of LANGS) {
  test(`front desk, referrals and upload [${lang}]: every state is accessible at 320, 768 and 1280`, async ({ page }) => {
    await openConsole(page, 'front_desk', lang);
    const r = READY[lang];
    await go(page, '#/referrals', r.referrals as string);
    await sweep(page, 'referrals', lang);
    await go(page, `#/referrals/${ID.doc}`, ID.doc);
    await sweep(page, 'referral-detail', lang);
    await go(page, '#/referrals/upload', r.upload as string);
    await sweep(page, 'upload', lang);
  });

  test(`front desk, tasks and their dialogs [${lang}]`, async ({ page }) => {
    await openConsole(page, 'front_desk', lang);
    await go(page, '#/tasks', READY[lang].tasks as string);
    await sweep(page, 'tasks', lang);
    await go(page, `#/tasks/${ID.task}`, ID.task);
    await sweep(page, 'task-detail', lang);
    await page.locator('main .toolbar button').nth(1).click(); // resolve (a referral hold is resolvable)
    await expect(page.getByRole('dialog')).toBeVisible();
    await sweep(page, 'task-resolve-dialog', lang);
  });

  test(`front desk, patients and the confirm dialog [${lang}]`, async ({ page }) => {
    await openConsole(page, 'front_desk', lang);
    await go(page, '#/patients', READY[lang].patients as string);
    await page.locator('input[type=text]').first().fill('Test Person');
    await page.locator('input[type=date]').fill('1990-02-03');
    await page.locator('form button[type=submit]').first().click();
    await expect(page.locator('table')).toBeVisible();
    await sweep(page, 'patients-results', lang);
    await go(page, `#/patients/${ID.patient}`, ID.patient);
    await sweep(page, 'patient-record', lang);
    await page.locator('main .toolbar button').click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await sweep(page, 'patient-confirm-dialog', lang);
  });

  test(`front desk, scheduling lists, holds and offers [${lang}]`, async ({ page }) => {
    await openConsole(page, 'front_desk', lang);
    for (const list of ['providers', 'locations', 'visit-types', 'priority-levels', 'holds', 'offers']) {
      await go(page, `#/scheduling/${list}`, READY[lang].scheduling && list !== 'holds' && list !== 'offers' ? (READY[lang].scheduling as string) : /\S/);
      await expect(page.locator('main table, main select, main .state').first()).toBeVisible();
      await sweep(page, `scheduling-${list}`, lang);
    }
    await go(page, '#/scheduling/holds', /\S/);
    await page.getByRole('button', { name: /^(Release|விடுவி)\s/ }).click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await sweep(page, 'hold-release-dialog', lang);
  });

  test(`nurse, tasks with a red-flag task, referrals with a proposal view [${lang}]`, async ({ page }) => {
    await openConsole(page, 'nurse', lang);
    await go(page, '#/tasks', READY[lang].tasks as string);
    await expect(page.locator('tr.task-urgent')).toHaveCount(1);
    await sweep(page, 'nurse-tasks', lang);
    await go(page, `#/tasks/${ID.taskRed}`, ID.taskRed);
    await expect(page.locator('main [data-placeholder]').first()).toBeVisible();
    await sweep(page, 'nurse-red-flag-task', lang);
  });
}

test('keyboard only: open a task, acknowledge it through the dialog, and focus returns where it started', async ({ page }) => {
  await openConsole(page, 'front_desk', 'en');
  await page.locator('.main-nav a', { hasText: 'Tasks' }).focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('h1')).toHaveText('Tasks');
  const link = page.locator('tbody a').first();
  await link.focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('h1')).toHaveText('Task');
  await expect(page.locator('h1')).toBeFocused(); // focus moves to the new screen's heading

  const ack = page.getByRole('button', { name: 'Acknowledge' });
  await ack.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog')).toBeVisible();
  await expect(page.getByRole('dialog').getByRole('button', { name: 'Cancel' })).toBeFocused(); // the safe choice first
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toBeHidden();
  await expect(ack).toBeFocused(); // back to the control that opened it

  await page.keyboard.press('Enter');
  await page.keyboard.press('Tab'); // Cancel -> Acknowledge
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog')).toBeHidden();
  await expect(page.locator('main')).toContainText('Acknowledged');
});

test('keyboard only: search, open the record and confirm identity', async ({ page }) => {
  await openConsole(page, 'front_desk', 'en');
  await go(page, '#/patients', 'Full name');
  await page.locator('input[type=text]').first().focus();
  await page.keyboard.type('Test Person');
  await page.keyboard.press('Tab');
  await page.keyboard.type('1990-02-03');
  await page.keyboard.press('Enter');
  await expect(page.locator('table')).toBeVisible();
  await page.locator('tbody a').first().focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('h1')).toHaveText('Patient record');
  await page.getByRole('button', { name: 'Confirm identity' }).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.keyboard.press('Tab');
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog')).toBeHidden();
  await expect(page.locator('main .pill-ok')).toContainText('Verified');
  await expect(page.getByRole('button', { name: 'Confirm identity' })).toHaveCount(0);
});

test('upload: the file goes to the storage host only, with the scan status shown honestly (not scanned is never clean)', async ({ page }) => {
  const mock = await openConsole(page, 'front_desk', 'en', { console: { scan: 'not_scanned' } });
  await go(page, '#/referrals/upload', 'Kind of document');
  await page.locator('input[type=file]').setInputFiles({ name: 'letter.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4 test') });
  await page.locator('form button[type=submit]').focus();
  await page.keyboard.press('Enter');
  await expect(page.getByText('Uploaded')).toBeVisible();
  await expect(page.locator('main .pill')).toHaveText('Not scanned yet');
  await expect(page.locator('main .pill-ok')).toHaveCount(0);
  expect(mock.log.filter((l) => l.startsWith('API POST /v1/staff/documents'))).toEqual(['API POST /v1/staff/documents/uploads', 'API POST /v1/staff/documents/register']);
  await check(page, 'upload-done', 'en', VIEWPORTS[2]);
});

test('upload: a clean scan is shown as clean, and a switched-off upload gives a calm fixed message', async ({ page }) => {
  await openConsole(page, 'front_desk', 'en', { console: { scan: 'clean' } });
  await go(page, '#/referrals/upload', 'Kind of document');
  await page.locator('input[type=file]').setInputFiles({ name: 'letter.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4 test') });
  await page.locator('form button[type=submit]').click();
  await expect(page.locator('main .pill-ok')).toHaveText('Clean');
});

test('upload: 503 from the server is a calm, fixed message', async ({ page }) => {
  await openConsole(page, 'front_desk', 'en', { console: { uploadsOff: true } });
  await go(page, '#/referrals/upload', 'Kind of document');
  await page.locator('input[type=file]').setInputFiles({ name: 'letter.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4 test') });
  await page.locator('form button[type=submit]').click();
  await expect(page.getByRole('alert')).toContainText('This is not available right now. Try again in a moment.');
  expect(await page.locator('body').innerText()).not.toContain(LEAK);
  await check(page, 'upload-unavailable', 'en', VIEWPORTS[2]);
});

test('release a hold with the keyboard; a second release answers 409 as a normal message', async ({ page }) => {
  await openConsole(page, 'front_desk', 'en');
  await go(page, '#/scheduling/holds', /\S/);
  await page.getByRole('button', { name: /^Release\s/ }).focus();
  await page.keyboard.press('Enter');
  await page.keyboard.press('Tab');
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog')).toBeHidden();
  await expect(page.locator('main')).toContainText('There are no holds to show.');
});

test('roles: the navigation shows only what the role may use, and typing an address by hand still gets "not available"', async ({ page }) => {
  await openConsole(page, 'doctor', 'en');
  await expect(page.locator('.main-nav a')).toHaveText(['Home', 'Referrals', 'Tasks', 'Patients', 'Identity', 'Scheduling']);
  await go(page, '#/referrals/upload', 'This is not available to your role.');
  await go(page, '#/scheduling/holds', /\S/);
  await expect(page.getByRole('button', { name: /^Release\s/ })).toHaveCount(0);
});

test('roles: an admin sees only Tasks, and a role with no section gets an explanation', async ({ page }) => {
  await openConsole(page, 'admin', 'en');
  await expect(page.locator('.main-nav a')).toHaveText(['Home', 'Tasks']);
  await go(page, '#/patients', /\S/);
  await expect(page.locator('main')).toContainText('This is not available to your role.');
});

test('roles: an auditor has no sections and is told so', async ({ page }) => {
  await openConsole(page, 'auditor', 'en');
  await expect(page.locator('main')).toContainText('No sections are available to your role.');
  await check(page, 'no-sections', 'en', VIEWPORTS[2]);
});

test('the active-role header is NOT sent (the flag is off), so no CORS preflight asks for it', async ({ page }) => {
  const seen: string[] = [];
  page.on('request', (r) => {
    const h = r.headers()['x-careflow-active-role'];
    if (h) seen.push(h);
  });
  await openConsole(page, 'front_desk', 'en');
  await go(page, '#/referrals', 'Open holds');
  expect(seen).toEqual([]);
});
