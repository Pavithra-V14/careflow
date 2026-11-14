import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { checkAgainst, fieldPaths, matchTemplate, responseSchema, type Spec } from './schemaCheck';
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
  // The step a request belongs to is the one showing when it was SENT; a response can arrive after the walk has moved on.
  const sentDuring = new Map<unknown, string>();
  page.on('request', (req) => {
    sentDuring.set(req, phase.current);
  });
  page.on('response', (res) => {
    void (async () => {
      const url = new URL(res.url());
      if (!url.pathname.startsWith('/v1/') && url.pathname !== '/readyz') return;
      const req = res.request();
      const method = req.method();
      if (method === 'OPTIONS') return;
      const template = matchTemplate(spec, url.pathname) ?? '?';
      let issues: Call['issues'] = [];
      let itemCount: number | undefined;
      let fields: string[] | undefined;
      if (res.ok() && template !== '?') {
        const schema = responseSchema(spec, method, template);
        const body: unknown = await res.json().catch(() => undefined);
        if (schema && body !== undefined) issues = checkAgainst(spec, schema, body);
        if (body !== undefined) {
          fields = fieldPaths(body);
          const items = (body as { items?: unknown }).items;
          if (Array.isArray(items)) itemCount = items.length;
        }
      }
      const headers = req.headers();
      calls.push({
        phase: sentDuring.get(req) ?? phase.current,
        method,
        template,
        status: res.status(),
        hasIdempotencyKey: Boolean(headers['x-idempotency-key']),
        queryNames: [...url.searchParams.keys()],
        activeRoleHeaderSent: Boolean(headers['x-careflow-active-role']),
        issues,
        ...(itemCount !== undefined ? { itemCount } : {}),
        ...(fields ? { fields } : {}),
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

  // One failing step must not lose the rest of the run: each step is tried on its own, a failure is noted by name only (never
  // by page content), and the report is written however the walk ends.
  const incomplete: string[] = [];
  const step = async (name: string, fn: () => Promise<unknown>) => {
    try {
      await fn();
    } catch {
      incomplete.push(name);
    }
  };
  let seenPatientLink: string | null = null;
  const noteLink = async () => {
    if (seenPatientLink === null) seenPatientLink = await optionalHref(page.locator('main a[href^="#/patients/"]'));
  };
  const optionalHref = async (locator: ReturnType<Page['locator']>) => ((await locator.count()) > 0 ? await locator.first().getAttribute('href', { timeout: 5000 }) : null);

  try {
    phase.current = 'home (account)';
    await step('referrals', async () => {
      await visit('referrals list', '#/referrals');
      if (await clickIf('referral detail', page.locator('main tbody a').first())) await page.goBack();
    });
    await step('tasks', async () => {
      await visit('tasks (open)', '#/tasks');
      for (const status of ['Acknowledged', 'Resolved']) await clickIf(`tasks (${status.toLowerCase()})`, page.getByRole('button', { name: status, exact: true }));
      // The hash is already #/tasks, so visiting it again would leave the Resolved filter showing: switch back to Open instead.
      await clickIf('tasks (open) again', page.getByRole('button', { name: 'Open', exact: true }));
      await clickIf('task detail', page.locator('main tbody a').first());
    });
    await step('patients page', () => visit('patients page (no search: it needs personal data, do it by hand)', '#/patients'));
    for (const list of ['providers', 'locations', 'visit-types', 'priority-levels']) {
      await step(`scheduling ${list}`, async () => {
        await visit(`scheduling ${list}`, `#/scheduling/${list}`);
        await clickIf(`scheduling ${list} (inactive)`, page.getByRole('checkbox'));
      });
    }
    await step('holds', async () => {
      await visit('scheduling holds (active)', '#/scheduling/holds');
      for (const status of ['Released', 'Expired', 'Booked']) await clickIf(`holds (${status.toLowerCase()})`, page.getByRole('button', { name: status, exact: true }));
    });
    await step('offers', async () => {
      await visit('scheduling offers', '#/scheduling/offers');
      const select = page.locator('main select');
      if ((await select.count()) > 0 && (await select.locator('option').count()) > 1) {
        phase.current = 'offer draft (read-only)';
        await select.selectOption({ index: 1 });
        await page.getByRole('button', { name: /show draft|வரைவைக் காட்டு/i }).click();
        await settle(page);
      }
    });
    // The screens added in F7: read-only visits. A role that may not use one gets a 403, which the report shows as an expected refusal.
    for (const [name, hash] of [
      ['slots', '#/scheduling/slots'],
      ['bookings', '#/scheduling/bookings'],
      ['waitlist', '#/scheduling/waitlist'],
      ['alerts', '#/alerts'],
      ['patient requests (front desk)', '#/requests'],
      ['admin red-flag rules', '#/admin/rules'],
      ['dashboard', '#/dashboard'],
      ['care gaps', '#/care/gaps'],
      ['care plans', '#/care/plans'],
      ['claims', '#/claims'],
      ['break-glass review', '#/access'],
      ['admin accounts', '#/admin/accounts'],
      ['admin autonomy', '#/admin/autonomy'],
      ['admin reviewer statistics', '#/admin/reviewer'],
      ['admin cost canvas', '#/admin/cost'],
      ['admin voice sessions', '#/admin/voice'],
      ['admin recovery', '#/admin/recovery'],
      ['admin grievances', '#/admin/grievances'],
    ] as const) {
      await step(name, async () => {
        await visit(name, hash);
        await noteLink();
      });
    }
    // The rule authoring form is OPENED to see it render, and never saved: a rule is clinical content and the walk writes nothing.
    await step('rule authoring form (opened, not saved)', async () => {
      await visit('admin red-flag rules', '#/admin/rules');
      await clickIf('rule authoring form (opened, not saved)', page.locator('main button', { hasText: /write a new rule version|புதிய விதிப் பதிப்பை எழுது/i }));
    });
    // Referral details: up to six, so a referral that has a proposal (and its reviewer view) is more likely to be among them.
    await step('referral details (up to six)', async () => {
      await visit('referrals list (for details)', '#/referrals');
      const hrefs: string[] = [];
      const links = page.locator('main tbody a');
      const total = Math.min(await links.count(), 6);
      for (let i = 0; i < total; i += 1) {
        const href = await links.nth(i).getAttribute('href', { timeout: 5000 });
        if (href) hrefs.push(href);
      }
      for (const [i, href] of hrefs.entries()) {
        await visit(`referral detail ${i + 1}`, href);
        await noteLink();
      }
    });
    let patientLink: string | null = null;
    await step('identity merges', async () => {
      await visit('identity merges', '#/identity/merges');
      patientLink = await optionalHref(page.locator('main a[href^="#/patients/"]'));
      if (await clickIf('merge detail', page.locator('main tbody th a').first())) await page.goBack();
    });
    await step('identity duplicates', async () => {
      await visit('identity duplicates', '#/identity/duplicates');
      await clickIf('duplicates (resolved)', page.getByRole('button', { name: 'Resolved', exact: true }));
    });
    const toOpen = (patientLink as string | null) ?? (seenPatientLink as string | null);
    if (toOpen) await step('patient record', () => visit('patient record (with its medications panel, read-only)', toOpen));
  } finally {
    // What the shell itself shows (closed words: the role label and the LIVE / TEST / unknown badge), so a clinic_origin the console
    // does not recognise (the database also allows eval and canary) is visible in the report as "Environment unknown".
    let shell = '';
    try {
      const text = async (sel: string) => ((await page.locator(sel).first().textContent({ timeout: 3000 })) ?? '').replace(/\s+/g, ' ').trim();
      shell = `role "${await text('.role-badge')}", environment badge "${await text('.env-badge')}", ${await text('.session-bar .profile')}`;
    } catch {
      /* the page may already be closed */
    }
    const base = incomplete.length === 0 ? 'read-only walk' : `read-only walk; steps that did not complete: ${incomplete.join(', ')}`;
    save(calls, shell ? `${base}. The shell showed: ${shell}` : base);
  }
  if (incomplete.length > 0) console.log(`>>> Steps that did not complete (nothing from the page is recorded): ${incomplete.join(', ')}`);

  // A walk where the backend refuses every staff route proves nothing: say so loudly instead of reporting a pass. A password-only session
  // gets 401 (WWW-Authenticate: mfa) on every staff route except /v1/iam/me when the backend requires a second factor (CAREFLOW_REQUIRE_AAL2).
  const refused = calls.filter((c) => c.status === 401 && c.template !== '/v1/iam/me');
  if (refused.length > 0) console.log(`>>> ${refused.length} call(s) answered 401: the backend is refusing this sign-in on every staff screen (a password-only session needs CAREFLOW_REQUIRE_AAL2 off, which is allowed only in demo with synthetic data). Nothing was walked.`);
  expect(refused.length, 'the backend refused this sign-in (401) on staff routes: nothing was walked').toBe(0);

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
