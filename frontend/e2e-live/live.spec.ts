import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { checkAgainst, matchTemplate, responseSchema, type Spec } from './schemaCheck';
import { renderReport, type Call } from './report';

// LIVE integration pass against a RUNNING backend. Run by hand with `npm run e2e:live`; never in CI (the isolation test
// tests/live-run-isolation.test.ts pins that). It does not type a credential: you sign in yourself in the window it opens
// (email, password, authenticator code). Then it walks every screen READ-ONLY and records each API call: method, path with
// ids removed, status, whether a key was sent, and where the response differs from the OpenAPI schema. No values are recorded.
// Writes are never automated: use `npm run e2e:live -- --record` to click through them yourself while the recorder watches.

const LIVE = process.env.CAREFLOW_LIVE === '1';
const RECORD = process.env.CAREFLOW_LIVE_RECORD === '1';
const spec = JSON.parse(readFileSync(resolve(import.meta.dirname, '../../openapi.snapshot.json'), 'utf8')) as Spec;
const REPORT_DIR = resolve(import.meta.dirname, '../live-report');
// One report per role signed in as: start with LIVE_LABEL=front_desk (or nurse, doctor). Letters, digits and underscores only.
const LABEL = (process.env.LIVE_LABEL ?? '').replace(/[^a-z0-9_]/gi, '');
const SUFFIX = LABEL ? `-${LABEL}` : '';

test.skip(!LIVE, 'live run: use `npm run e2e:live` (never in CI)');

function watch(page: Page, calls: Call[], phase: { current: string }) {
  page.on('response', (res) => {
    void (async () => {
      const url = new URL(res.url());
      if (!url.pathname.startsWith('/v1/') && url.pathname !== '/readyz') return;
      const req = res.request();
      const method = req.method();
      if (method === 'OPTIONS') return;
      const template = matchTemplate(spec, url.pathname) ?? '?';
      let issues: Call['issues'] = [];
      if (res.ok() && template !== '?') {
        const schema = responseSchema(spec, method, template);
        const body: unknown = await res.json().catch(() => undefined);
        if (schema && body !== undefined) issues = checkAgainst(spec, schema, body);
      }
      const headers = req.headers();
      calls.push({
        phase: phase.current,
        method,
        template,
        status: res.status(),
        hasIdempotencyKey: Boolean(headers['x-idempotency-key']),
        queryNames: [...url.searchParams.keys()],
        activeRoleHeaderSent: Boolean(headers['x-careflow-active-role']),
        issues,
      });
    })();
  });
}

async function signInByHand(page: Page) {
  await page.goto('/staff/');
  console.log('\n>>> Sign in yourself in the browser window: email, password, then the authenticator code (and pick a role if asked).');
  console.log('>>> Waiting up to 10 minutes. Nothing is typed for you and no credential reaches this script.\n');
  await expect(page.locator('.session-bar')).toBeVisible({ timeout: 10 * 60_000 });
}

function save(calls: Call[], mode: string) {
  mkdirSync(REPORT_DIR, { recursive: true });
  writeFileSync(resolve(REPORT_DIR, `live-report${SUFFIX}.md`), renderReport(spec, calls, mode));
  writeFileSync(resolve(REPORT_DIR, `live-calls${SUFFIX}.json`), JSON.stringify(calls, null, 2));
  console.log(`\n>>> Report written to frontend/live-report/live-report.md (${calls.length} calls). It contains no values.\n`);
}

async function settle(page: Page) {
  await page.waitForLoadState('networkidle').catch(() => undefined);
  await page.locator('main .state, main table, main form, main dl').first().waitFor({ timeout: 15_000 }).catch(() => undefined);
  await page.waitForLoadState('networkidle').catch(() => undefined);
}

test('walk every screen, read-only, and record each call', async ({ page }) => {
  test.skip(RECORD, 'record mode is on');
  test.setTimeout(20 * 60_000);
  const calls: Call[] = [];
  const phase = { current: 'sign-in' };
  watch(page, calls, phase);
  await signInByHand(page);
  await settle(page);

  const visit = async (name: string, hash: string) => {
    phase.current = name;
    await page.evaluate((h) => (window.location.hash = h), hash);
    await settle(page);
  };
  const clickIf = async (name: string, locator: ReturnType<Page['locator']>) => {
    if ((await locator.count()) === 0) return false;
    phase.current = name;
    await locator.first().click();
    await settle(page);
    return true;
  };

  phase.current = 'home (account)';
  await visit('referrals list', '#/referrals');
  if (await clickIf('referral detail', page.locator('main tbody a').first())) await page.goBack();

  await visit('tasks (open)', '#/tasks');
  for (const status of ['Acknowledged', 'Resolved']) await clickIf(`tasks (${status.toLowerCase()})`, page.getByRole('button', { name: status, exact: true }));
  await visit('tasks (open) again', '#/tasks');
  await clickIf('task detail', page.locator('main tbody a').first());

  await visit('patients page (no search: it needs personal data, do it by hand)', '#/patients');

  for (const list of ['providers', 'locations', 'visit-types', 'priority-levels']) {
    await visit(`scheduling ${list}`, `#/scheduling/${list}`);
    await clickIf(`scheduling ${list} (inactive)`, page.getByRole('checkbox'));
  }
  await visit('scheduling holds (active)', '#/scheduling/holds');
  for (const status of ['Released', 'Expired', 'Booked']) await clickIf(`holds (${status.toLowerCase()})`, page.getByRole('button', { name: status, exact: true }));
  await visit('scheduling offers', '#/scheduling/offers');
  const select = page.locator('main select');
  if ((await select.count()) > 0 && (await select.locator('option').count()) > 1) {
    phase.current = 'offer draft (read-only)';
    await select.selectOption({ index: 1 });
    await page.getByRole('button', { name: /show draft|வரைவைக் காட்டு/i }).click();
    await settle(page);
  }

  await visit('identity merges', '#/identity/merges');
  const patientLink = await page.locator('main a[href^="#/patients/"]').first().getAttribute('href');
  if (await clickIf('merge detail', page.locator('main tbody th a').first())) await page.goBack();
  await visit('identity duplicates', '#/identity/duplicates');
  await clickIf('duplicates (resolved)', page.getByRole('button', { name: 'Resolved', exact: true }));
  if (patientLink) await visit('patient record (opened from a merge)', patientLink);

  save(calls, 'read-only walk');

  // The walk must never change anything. A write here means the recorder or a screen did something it should not.
  const writes = calls.filter((c) => !['GET', 'HEAD'].includes(c.method));
  expect(writes.map((c) => `${c.method} ${c.template}`), 'the read-only walk made a write').toEqual([]);
  if (process.env.LIVE_STRICT === '1') expect(calls.flatMap((c) => c.issues).filter((i) => i.severity === 'error')).toEqual([]);
});

test('record while you use the console yourself (writes included)', async ({ page }) => {
  test.skip(!RECORD, 'start with `npm run e2e:live -- --record`');
  test.setTimeout(60 * 60_000);
  const calls: Call[] = [];
  const phase = { current: 'by hand' };
  watch(page, calls, phase);
  await signInByHand(page);
  console.log('>>> Recording. Use the console as you like: search for a patient, acknowledge a task, release a hold, review a referral.');
  console.log('>>> When you are done, press Resume in the Playwright inspector to write the report.\n');
  await page.pause();
  save(calls, 'recorded by hand');
});
