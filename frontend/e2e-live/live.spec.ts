import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { checkAgainst, fieldPaths, matchTemplate, responseSchema, type Spec } from './schemaCheck';
import { refusalReason, renderReport, type Call } from './report';

// LIVE integration pass against a RUNNING backend. Run by hand with `npm run e2e:live`; never in CI (the isolation test
// tests/live-run-isolation.test.ts pins that). It does not type a credential: you sign in yourself in the window it opens
// (email, password, authenticator code). Then it walks every screen READ-ONLY and records each API call: method, path with
// ids removed, status, whether a key was sent, and where the response differs from the OpenAPI schema. No values are recorded.
// Writes are never automated: use `npm run e2e:live -- --record` to click through them yourself while the recorder watches.

const LIVE = process.env.CAREFLOW_LIVE === '1';
const RECORD = process.env.CAREFLOW_LIVE_RECORD === '1';
// LIVE_PORTAL=1 walks the PATIENT portal instead (phone sign-in by hand), read-only.
const PORTAL = process.env.LIVE_PORTAL === '1';
// LIVE_LINK=portal makes the DEMO claim for the synthetic portal patient (writes: create account if asked, the care-delivery consent, activation, the claim);
// LIVE_LINK=approve then approves that one pending claim as front desk (one write). Both stop at the first failure and never retry. The user signs in by hand.
const LINK = process.env.LIVE_LINK ?? '';
const spec = JSON.parse(readFileSync(resolve(import.meta.dirname, '../../openapi.snapshot.json'), 'utf8')) as Spec;
const REPORT_DIR = resolve(import.meta.dirname, '../live-report');
// One report per role signed in as: start with LIVE_LABEL=front_desk (or nurse, doctor). Letters, digits and underscores only.
const LABEL = (process.env.LIVE_LABEL ?? '').replace(/[^a-z0-9_]/gi, '');
const SUFFIX = LABEL ? `-${LABEL}` : '';

test.skip(!LIVE, 'live run: use `npm run e2e:live` (never in CI)');

// Every write that was SENT is remembered until its answer arrives (or the request fails). A write whose answer never came while the walk was
// running is listed in the report as "sent, no answer received": a status code is never lost because the run ended first.
const unanswered = new Map<unknown, string>();
const failedWrites: string[] = [];
const isWrite = (method: string) => !['GET', 'HEAD', 'OPTIONS'].includes(method);
function unansweredLines(): string[] {
  return [...unanswered.values(), ...failedWrites].map((x) => `- ${x}`);
}

function watch(page: Page, calls: Call[], phase: { current: string }) {
  // The step a request belongs to is the one showing when it was SENT; a response can arrive after the walk has moved on.
  const sentDuring = new Map<unknown, string>();
  page.on('request', (req) => {
    sentDuring.set(req, phase.current);
    const url = new URL(req.url());
    if (isWrite(req.method()) && (url.pathname.startsWith('/v1/') || url.pathname.startsWith('/public/'))) {
      unanswered.set(req, `${req.method()} ${matchTemplate(spec, url.pathname) ?? '?'}: sent during "${phase.current}", no answer received yet`);
    }
  });
  page.on('requestfailed', (req) => {
    if (unanswered.delete(req)) {
      const url = new URL(req.url());
      failedWrites.push(`${req.method()} ${matchTemplate(spec, url.pathname) ?? '?'}: the request failed before an answer (${req.failure()?.errorText ?? 'no reason given'}); it may or may not have reached the backend`);
    }
  });
  page.on('response', (res) => {
    unanswered.delete(res.request());
    void (async () => {
      const url = new URL(res.url());
      if (!url.pathname.startsWith('/v1/') && !url.pathname.startsWith('/public/') && url.pathname !== '/readyz') return;
      const req = res.request();
      const method = req.method();
      if (method === 'OPTIONS') return;
      const template = matchTemplate(spec, url.pathname) ?? '?';
      let issues: Call['issues'] = [];
      let itemCount: number | undefined;
      let fields: string[] | undefined;
      let reason: string | undefined;
      if (res.status() === 403) reason = refusalReason(((await res.json().catch(() => undefined)) as { detail?: unknown } | undefined)?.detail);
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
        ...(reason ? { reason } : {}),
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
  const lost = unansweredLines();
  if (lost.length) writeFileSync(resolve(REPORT_DIR, `unanswered-writes${SUFFIX}.md`), ['# Writes sent with no answer received (status unknown)', '', ...lost, ''].join('\n'));
  console.log(`\n>>> Report written to frontend/live-report/live-report.md (${calls.length} calls). It contains no values.\n`);
}

async function settle(page: Page) {
  await page.waitForTimeout(700); // let the screen mount and send its first read (a page's code is compiled on first use by the dev server)
  await page.locator('main .loading').first().waitFor({ state: 'detached', timeout: 20_000 }).catch(() => undefined);
  await page.waitForLoadState('networkidle').catch(() => undefined);
  await page.locator('main .state, main table, main form, main dl').first().waitFor({ timeout: 15_000 }).catch(() => undefined);
  await page.waitForLoadState('networkidle').catch(() => undefined);
}

test('walk every screen, read-only, and record each call', async ({ page }) => {
  test.skip(RECORD || PORTAL || LINK !== '', 'record mode, the portal walk or a link step is on');
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
  // What the reviewer view SHOWS for a model-made proposal (dev data, synthetic): the suggestion note, the facts and the alternatives as text,
  // so "shown exactly as sent" and "Not recorded" can be read from a real answer. Written beside the report, never into it.
  const observed: string[] = [];
  const observeReview = async (n: number) => {
    const view = page.locator('section[aria-labelledby^="rv-"]');
    await view.first().waitFor({ timeout: 8000 }).catch(() => undefined); // the reviewer view loads after the page
    await page.locator('section[aria-labelledby^="rv-"] .loading').waitFor({ state: 'detached', timeout: 8000 }).catch(() => undefined);
    if ((await view.count()) === 0) {
      observed.push(`- referral detail ${n}: no reviewer view on the screen`);
      return;
    }
    const note = ((await view.locator('p.hint').first().innerText({ timeout: 3000 }).catch(() => '')) || '').replace(/\s+/g, ' ').trim();
    const cells = (await view.locator('dl.facts').first().locator('dt, dd').allInnerTexts().catch(() => [])).map((t) => t.replace(/\s+/g, ' ').trim());
    const facts: string[] = [];
    for (let i = 0; i + 1 < cells.length; i += 2) facts.push(`${cells[i]}: ${cells[i + 1]}`);
    const alts = await view.locator('table').last().locator('tbody tr').allInnerTexts().catch(() => []);
    // Whether this is the referral of the proposal the user named (its id shown anywhere on the screen), by the first eight characters only.
    const named = ((await page.locator('main').innerText({ timeout: 3000 }).catch(() => '')) || '').includes(process.env.LIVE_PROPOSAL_PREFIX ?? 'no-such-text');
    observed.push(`- referral detail ${n}${named ? ' (SHOWS THE NAMED PROPOSAL ID)' : ''}: suggestion note "${note}"; facts [${facts.join(' | ')}]; alternatives [${alts.map((a) => a.replace(/\s+/g, ' ').trim()).join(' | ')}]`);
  };
  let seenPatientLink: string | null = null;
  const patientLinks = new Set<string>();
  const noteLink = async () => {
    const hrefs = await page.locator('main a[href^="#/patients/"]').evaluateAll((els) => els.map((e) => e.getAttribute('href') ?? '')).catch(() => [] as string[]);
    for (const h of hrefs) if (h) patientLinks.add(h);
    if (seenPatientLink === null) seenPatientLink = hrefs[0] ?? null;
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
      // The visit types arrive after the first loading box, so wait for the select and its choices (up to 15 s) before deciding there are none.
      await select.first().waitFor({ timeout: 15_000 }).catch(() => undefined);
      await expect.poll(async () => select.locator('option').count(), { timeout: 15_000 }).toBeGreaterThan(1).catch(() => undefined);
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
    // Referral details: up to fifteen, so a referral that has a proposal (and its reviewer view) is more likely to be among them.
    await step('referral details (up to six)', async () => {
      await visit('referrals list (for details)', '#/referrals');
      const hrefs: string[] = [];
      const links = page.locator('main tbody a');
      await links.first().waitFor({ timeout: 15_000 }).catch(() => undefined); // the rows arrive after the first loading box
      const total = Math.min(await links.count(), 15);
      for (let i = 0; i < total; i += 1) {
        const href = await links.nth(i).getAttribute('href', { timeout: 5000 });
        if (href) hrefs.push(href);
      }
      for (const [i, href] of hrefs.entries()) {
        await visit(`referral detail ${i + 1}`, href);
        await noteLink();
        await observeReview(i + 1);
      }
    });
    // Messages, labels, family links, the knowledge lists and the staff view of visit preparation (read-only: GETs, plus the one read-only
    // POST that shows names after its confirmation). A body is read once after its confirmation (an audited read of synthetic data); nothing
    // is screened, decided, revoked or saved.
    await step('staff messages and patient labels', async () => {
      await visit('staff messages (list)', '#/messages');
      if (await clickIf('staff messages (open a conversation)', page.locator('main tbody tr').first().getByRole('button'))) {
        const read = page.locator('main table').nth(1).locator('tbody tr').first().getByRole('button').first();
        if ((await read.count()) > 0 && /read|படி/i.test((await read.textContent()) ?? '')) {
          phase.current = 'staff messages (read one message after its confirmation)';
          await read.click();
          await page.getByRole('dialog').locator('.dialog-actions button').nth(1).click();
          await settle(page);
        }
      }
      const names = page.getByRole('button', { name: /show names|பெயர்களைக் காட்டு/i });
      if ((await names.count()) > 0) {
        phase.current = 'patient labels (show names after the confirmation)';
        await names.first().click();
        await page.getByRole('dialog').locator('.dialog-actions button').nth(1).click();
        await settle(page);
      }
    });
    await step('family link requests (front desk)', () => visit('family link requests', '#/family'));
    await step('cards and templates', () => visit('cards and templates (lists)', '#/knowledge'));
    await step('visit preparation (staff view)', async () => {
      await visit('visit readiness (upcoming visits)', '#/readiness');
      if (await clickIf('visit readiness (open the first visit)', page.locator('main tbody tr').first().getByRole('button'))) {
        await clickIf('visit preparation (show what the patient marked)', page.getByRole('button', { name: /show what the patient marked|நோயாளி குறித்தவற்றைக் காட்டு/i }));
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
    // Prefer a patient seen on a referral or another screen: the one opened from a merge may be the merged-away record, whose medications panel is hidden.
    // Open up to six distinct patient records until one shows the medications panel (a merged-away record hides it), and say what each showed.
    const candidates = [...new Set([...patientLinks, ...(patientLink ? [patientLink as string] : [])])].slice(0, 6);
    for (const [i, href] of candidates.entries()) {
      let shown = false;
      await step(`patient record ${i + 1}`, async () => {
        await visit(`patient record ${i + 1} (read-only)`, href);
        await page.locator('#meds-title').waitFor({ timeout: 4000 }).then(() => (shown = true)).catch(() => undefined);
        await settle(page);
      });
      const merged = (await page.locator('main .state-forbidden').count()) > 0;
      observed.push(`- patient record ${i + 1}: medications panel ${shown ? 'shown' : 'not shown'}; merged-away notice ${merged ? 'shown' : 'not shown'}`);
      if (shown) break;
    }
  } finally {
    await page.waitForTimeout(2500).catch(() => undefined); // let the last responses be recorded before the report is written
    // What the shell itself shows (closed words: the role label and the LIVE / TEST / unknown badge), so a clinic_origin the console
    // does not recognise (the database also allows eval and canary) is visible in the report as "Environment unknown".
    let shell = '';
    try {
      const text = async (sel: string) => ((await page.locator(sel).first().textContent({ timeout: 3000 })) ?? '').replace(/\s+/g, ' ').trim();
      shell = `role "${await text('.role-badge')}", environment badge "${await text('.env-badge')}", ${await text('.session-bar .profile')}`;
    } catch {
      /* the page may already be closed */
    }
    mkdirSync(REPORT_DIR, { recursive: true });
    if (observed.length > 0) writeFileSync(resolve(REPORT_DIR, `review-observations${SUFFIX}.md`), `# Reviewer view as shown (dev data, synthetic)\n\n${observed.join('\n')}\n`);
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
  // The one allowed non-GET is the read-only names read (a POST so ids stay out of the URL), made only after its confirmation.
  const writes = calls.filter((c) => !['GET', 'HEAD'].includes(c.method) && !(c.method === 'POST' && c.template === '/v1/staff/patients/labels'));
  expect(writes.map((c) => `${c.method} ${c.template}`), 'the read-only walk made a write').toEqual([]);
  if (process.env.LIVE_STRICT === '1') expect(calls.flatMap((c) => c.issues).filter((i) => i.severity === 'error')).toEqual([]);
});

test('walk the patient portal, read-only (phone sign-in by hand), and record each call', async ({ page }) => {
  test.skip(!PORTAL || LINK !== '', 'start with LIVE_PORTAL=1');
  test.setTimeout(20 * 60_000);
  const calls: Call[] = [];
  const phase = { current: 'sign-in' };
  watch(page, calls, phase);
  await page.goto('/patient/sign-in/');
  console.log('\n>>> Sign in yourself in the browser window: your mobile number, then the code sent to it.');
  console.log('>>> Waiting up to 10 minutes. Nothing is typed for you and no credential reaches this script.\n');
  await expect(page.locator('#portal-consent, #portal-claim').first()).toBeVisible({ timeout: 10 * 60_000 });
  phase.current = 'portal account (every section loads)';
  await settle(page);
  await page.waitForTimeout(4000); // every section starts its own read; let them finish
  const incomplete: string[] = [];
  const step = async (name: string, fn: () => Promise<unknown>) => {
    try {
      await fn();
    } catch {
      incomplete.push(name);
    }
  };
  try {
    await step('visit preparation (the first upcoming booking)', async () => {
      phase.current = 'portal visit preparation';
      const prep = page.getByRole('button', { name: /what to prepare|தயாராக வேண்டியவை/i });
      if ((await prep.count()) > 0) {
        await prep.first().click();
        await settle(page);
      }
    });
    await step('messages (open the first conversation)', async () => {
      phase.current = 'portal messages (read a conversation)';
      const open = page.locator('section[aria-labelledby="portal-messages"] ul button').first();
      if ((await open.count()) > 0) {
        await open.click();
        await settle(page);
      }
    });
  } finally {
    await page.waitForTimeout(2500).catch(() => undefined);
    // What the portal itself shows, as closed words: which sections are on the page.
    const sections = await page.locator('main section[aria-labelledby]').evaluateAll((els) => els.map((e) => e.getAttribute('aria-labelledby') ?? '')).catch(() => [] as string[]);
    mkdirSync(REPORT_DIR, { recursive: true });
    writeFileSync(resolve(REPORT_DIR, `portal-sections${SUFFIX}.md`), `# Sections on the portal page\n\n${sections.map((x) => `- ${x}`).join('\n')}\n`);
    const base = incomplete.length === 0 ? 'read-only portal walk' : `read-only portal walk; steps that did not complete: ${incomplete.join(', ')}`;
    save(calls, `${base}. Sections on the page: ${sections.join(', ')}`);
  }
  const writes = calls.filter((c) => !['GET', 'HEAD'].includes(c.method));
  expect(writes.map((c) => `${c.method} ${c.template}`), 'the read-only portal walk made a write').toEqual([]);
});

// What a screen says right now, as the fixed wording on it: status and alert lines, dialogs and the account facts. Nothing typed is read back.
async function screenWords(page: Page): Promise<string[]> {
  const lines = await page.locator('main [role=status], main [role=alert], dialog [role=status], dialog [role=alert], main dl.facts, main .notice h3, main .badge').allInnerTexts().catch(() => [] as string[]);
  return lines.map((l) => l.replace(/\s+/g, ' ').trim()).filter(Boolean);
}

// Shared by the link steps: log what a screen says, stop on the first failure, and write what was said and every write made.
function linkTools(page: Page, calls: Call[], name: string) {
  const log: string[] = [];
  const snap = async (title: string) => {
    const words = await screenWords(page);
    log.push(`## ${title}`, '', ...(words.length ? words.map((w) => `- ${w}`) : ['- (nothing said on screen)']), '');
    return words;
  };
  const stop = async (why: string): Promise<never> => {
    log.push(`## STOPPED: ${why}`, '');
    throw new Error(why);
  };
  // Each write waits for its own answer: its dialog must close within 30 s, or the step stops with what the screen says.
  const confirmAndWait = async (what: string) => {
    await page.getByRole('dialog').locator('.dialog-actions button').nth(1).click();
    const closed = await page.getByRole('dialog').waitFor({ state: 'detached', timeout: 30_000 }).then(() => true).catch(() => false);
    await settle(page);
    const words = await snap(`after ${what}`);
    if (!closed) await stop(`the dialog for "${what}" did not close within 30 seconds: ${words.join(' / ')}`);
    return words;
  };
  const finish = async (title: string) => {
    await page.waitForTimeout(1500).catch(() => undefined);
    mkdirSync(REPORT_DIR, { recursive: true });
    const writes = calls.filter((c) => !['GET', 'HEAD'].includes(c.method) && c.template !== '/v1/staff/patients/search').map((c) => `- ${c.method} ${c.template} -> ${c.status}${c.reason ? ` (reason given: ${c.reason})` : ''}`);
    const refused = calls.filter((c) => c.status === 403).map((c) => `- ${c.method} ${c.template} -> 403 (${c.reason ?? 'no reason sentence in the response'})`);
    writeFileSync(resolve(REPORT_DIR, `${name}${SUFFIX}.md`), [`# ${title}: what each screen said`, '', ...log, '## Writes made (a patient search is a read)', '', ...(writes.length ? writes : ['- none']), '', '## Refusals (403) and their reasons', '', ...(refused.length ? refused : ['- none']), '', '## Writes sent with no answer received (status unknown)', '', ...(unansweredLines().length ? unansweredLines() : ['- none']), ''].join('\n'));
    save(calls, `${title} (writes: see ${name} file)`);
  };
  return { log, snap, stop, confirmAndWait, finish };
}

test('DEMO link, front desk: find the demo patient and confirm identity (one write, synthetic patient only)', async ({ page }) => {
  test.skip(LINK !== 'confirm', 'start with LIVE_LINK=confirm');
  test.setTimeout(20 * 60_000);
  const calls: Call[] = [];
  const phase = { current: 'sign-in' };
  watch(page, calls, phase);
  const { log, snap, stop, confirmAndWait, finish } = linkTools(page, calls, 'link-confirm');
  try {
    await signInByHand(page);
    await settle(page);
    phase.current = 'patients (search)';
    await page.evaluate(() => (window.location.hash = '#/patients'));
    await settle(page);
    await page.locator('main input[type=text]').first().fill('DEMO Portal Patient');
    await page.locator('main input[type=date]').first().fill('1990-01-01');
    await page.locator('main form button[type=submit]').first().click();
    await settle(page);
    await snap('after the search');
    const hits = page.locator('main tbody tr');
    const n = await hits.count();
    log.push(`## records found for the demo patient: ${n}`, '');
    if (n !== 1) await stop(`the search found ${n} records for the demo patient, not exactly one; nothing was confirmed`);
    phase.current = 'patient record';
    await hits.first().locator('a').first().click();
    await settle(page);
    await snap('the record');
    const confirm = page.getByRole('button', { name: /^Confirm identity$|அடையாளத்தை உறுதிப்படுத்து/ });
    if ((await confirm.count()) === 0) await stop('there is no "Confirm identity" button on the record (it may already be confirmed, or this role may not confirm); nothing was confirmed');
    phase.current = 'confirm identity (write)';
    await confirm.first().click();
    await confirmAndWait('"Confirm identity"');
  } finally {
    await finish('Demo link, front desk: identity confirmation');
  }
});

test('DEMO link, portal: check the stage on a fresh load, then claim (one write)', async ({ page }) => {
  test.skip(LINK !== 'portal', 'start with LIVE_LINK=portal');
  test.setTimeout(20 * 60_000);
  const calls: Call[] = [];
  const phase = { current: 'sign-in' };
  watch(page, calls, phase);
  const { log, snap, stop, confirmAndWait, finish } = linkTools(page, calls, 'link-portal');
  try {
    await page.goto('/patient/sign-in/');
    console.log('\n>>> Sign in yourself with the "Demo sign-in" form (email and password). Nothing is typed for you and no credential reaches this script.');
    console.log('>>> Waiting up to 10 minutes.\n');
    await expect(page.locator('main dl.facts')).toBeVisible({ timeout: 10 * 60_000 });
    await settle(page);
    await snap('after sign-in');
    // A fresh page load, so the stage is read again from the backend and not from anything kept on the page.
    phase.current = 'fresh page load';
    await page.reload();
    await expect(page.locator('main dl.facts')).toBeVisible({ timeout: 60_000 });
    await settle(page);
    const stage = ((await page.locator('main dl.facts dd').first().innerText().catch(() => '')) || '').trim();
    await snap('fresh page load');
    log.push(`## stage on a fresh load: "${stage}"`, '');
    // "New patient" means the account is active and has no claim yet: the right stage to claim from (the backend's explanation, 20 Nov 2026). "Created" (invited) and
    // "Waiting to be activated" mean it is not active yet: stop with no write.
    if (/^Created$|^Waiting to be activated$|^உருவாக்கப்பட்டது|^செயல்படுத்தக் காத்திருக்கிறது/i.test(stage) || stage === '') await stop(`the stage reads "${stage}" on a fresh load: the account is not active yet; no claim was made (a backend finding)`);
    phase.current = 'claim a record';
    await page.locator('#claim-name').waitFor({ state: 'visible', timeout: 30_000 }).catch(() => undefined);
    if ((await page.locator('#claim-name').count()) === 0) await stop('the claim form is not on the screen (the account may be linked already); no claim was made');
    if ((await page.locator('#claim-clinic').count()) > 0) await stop('the claim form asks for a clinic code, which this walk was not given');
    await page.locator('#claim-name').fill('DEMO Portal Patient');
    await page.locator('#claim-dob').fill('1990-01-01');
    await page.locator('section[aria-labelledby=portal-claim] button[type=submit]').first().click();
    const words = await confirmAndWait('"Link my record"');
    // never repeated: report what the screen said, and whether the button is now off
    const button = page.locator('section[aria-labelledby=portal-claim] button[type=submit]');
    log.push(`## the claim button after the answer: ${(await button.count()) === 0 ? 'not on the screen' : (await button.first().isDisabled()) ? 'off' : 'on'}`, '', `## the claim was answered with: ${words.join(' / ')}`, '');
    // The read-only portal walk, in the same session: a fresh load so every section reads again, then the two openers that only read
    // (a visit's checklist and the first conversation). No further write is made. Each screen's own words are logged.
    phase.current = 'portal walk: fresh load';
    await page.reload();
    await expect(page.locator('main dl.facts')).toBeVisible({ timeout: 60_000 });
    await settle(page);
    await page.waitForTimeout(4000);
    await snap('portal walk: every section after a fresh load');
    const sections = await page.locator('main section[aria-labelledby]').evaluateAll((els) => els.map((e) => e.getAttribute('aria-labelledby') ?? '')).catch(() => [] as string[]);
    log.push(`## sections on the page: ${sections.join(', ')}`, '');
    const prep = page.getByRole('button', { name: /What to prepare|தயாராக வேண்டியவை/ });
    if ((await prep.count()) > 0) {
      phase.current = 'portal walk: visit preparation';
      await prep.first().click();
      await settle(page);
      await snap('portal walk: visit preparation');
    } else {
      log.push('## visit preparation: no appointment with a "What to prepare" button on the page', '');
    }
    const thread = page.locator('#portal-messages ul button');
    if ((await thread.count()) > 0) {
      phase.current = 'portal walk: messages (read a conversation)';
      await thread.first().click();
      await settle(page);
      await snap('portal walk: first conversation');
    } else {
      log.push('## messages: no conversation to open', '');
    }
  } finally {
    await finish('Demo link, portal: the claim');
  }
});

test('DEMO booking, portal: read the booking list and messages, make ONE booking, open the visit preparation (read-only)', async ({ page }) => {
  test.skip(LINK !== 'booking', 'start with LIVE_LINK=booking');
  test.setTimeout(20 * 60_000);
  const calls: Call[] = [];
  const phase = { current: 'sign-in' };
  watch(page, calls, phase);
  const { log, snap, confirmAndWait, finish } = linkTools(page, calls, 'link-booking');
  try {
    await page.goto('/patient/sign-in/');
    console.log('\n>>> Sign in yourself with the "Demo sign-in" form (email and password). Nothing is typed for you and no credential reaches this script.');
    console.log('>>> Waiting up to 10 minutes.\n');
    await expect(page.locator('main dl.facts')).toBeVisible({ timeout: 10 * 60_000 });
    await settle(page);
    await page.reload();
    await expect(page.locator('main dl.facts')).toBeVisible({ timeout: 60_000 });
    phase.current = 'portal: appointments, booking list and messages (read)';
    await settle(page);
    await page.waitForTimeout(4000);
    await snap('appointments, booking list and messages after a fresh load');
    const mine = page.getByRole('button', { name: /^What to prepare: / });
    const slots = page.getByRole('button', { name: /^Book: / });
    log.push(`## appointments already on the page: ${await mine.count()}; open times offered: ${await slots.count()}`, '');
    if ((await mine.count()) === 0) {
      if ((await slots.count()) === 0) {
        log.push('## NOT TESTED LIVE: no open time (slot) is offered to book, so no booking was made and there is no visit to open', '');
      } else {
        phase.current = 'ONE booking (write)';
        await slots.first().click();
        await confirmAndWait('booking the first open time');
        await page.waitForTimeout(2000);
        await snap('after the booking');
      }
    } else {
      log.push('## an appointment already exists: no booking was made', '');
    }
    if ((await mine.count()) > 0) {
      phase.current = 'portal: visit preparation (read-only; nothing is marked)';
      await mine.first().click();
      await settle(page);
      await snap('visit preparation (nothing marked)');
    }
    const thread = page.locator('#portal-messages ul button');
    if ((await thread.count()) > 0) {
      phase.current = 'portal: messages (read a conversation)';
      await thread.first().click();
      await settle(page);
      await snap('first conversation');
    } else {
      log.push('## messages: no conversation to open', '');
    }
  } finally {
    await finish('Demo booking, portal');
  }
});

test('DEMO booking, front desk: the same visit on the staff side, and the message screen (read-only, second factor)', async ({ page }) => {
  test.skip(LINK !== 'staffvisit', 'start with LIVE_LINK=staffvisit');
  test.setTimeout(20 * 60_000);
  const calls: Call[] = [];
  const phase = { current: 'sign-in' };
  watch(page, calls, phase);
  const { log, snap, finish } = linkTools(page, calls, 'link-staffvisit');
  try {
    await signInByHand(page);
    await settle(page);
    phase.current = 'scheduling (bookings)';
    await page.evaluate(() => (window.location.hash = '#/scheduling'));
    await settle(page);
    await page.waitForTimeout(2000);
    await snap('scheduling');
    phase.current = 'scheduling (bookings list: the booking as staff see it)';
    await page.evaluate(() => (window.location.hash = '#/scheduling/bookings'));
    await settle(page);
    await page.waitForTimeout(2000);
    await snap('scheduling: bookings');
    log.push(`## bookings listed: ${await page.locator('main tbody tr').count()} table rows`, '');
    phase.current = 'visit readiness (upcoming visits)';
    await page.evaluate(() => (window.location.hash = '#/readiness'));
    await settle(page);
    await page.waitForTimeout(2000);
    await snap('visit readiness');
    const open = page.locator('main tbody tr').first().getByRole('button');
    if ((await open.count()) > 0) {
      phase.current = 'visit readiness (open the first visit)';
      await open.first().click();
      await settle(page);
      const show = page.getByRole('button', { name: /show what the patient marked|நோயாளி குறித்தவற்றைக் காட்டு/i });
      if ((await show.count()) > 0) {
        phase.current = 'visit preparation (staff view, read-only)';
        await show.first().click();
        await settle(page);
        await snap('visit preparation, staff view');
      } else log.push('## no "Show what the patient marked" button on the page', '');
    } else {
      log.push('## no upcoming visit row to open', '');
    }
    phase.current = 'staff messages (list)';
    await page.evaluate(() => (window.location.hash = '#/messages'));
    await settle(page);
    await page.waitForTimeout(2000);
    await snap('messages');
  } finally {
    await finish('Demo booking, front desk: the staff side');
  }
});

// Nurse and doctor second-factor walks (PREPARED, not run until the two accounts have an authenticator): visit readiness with the staff view of a visit's
// preparation, and the cards and templates editor. Read-only. The one exception is a single demo draft by the NURSE, made only when LIVE_DRAFT=yes (the user
// says so): a template (no clinical card) with plainly marked placeholder text. The doctor only LOOKS at a draft and opens the Approve dialog and cancels it.
// Every screen's words, every 403 and its reason, and every write with its status code go to live-report/link-<role>-<label>.md.
for (const role of ['nurse', 'doctor'] as const) {
  test(`DEMO second-factor walk, ${role}: visit readiness, visit preparation (staff view) and cards and templates`, async ({ page }) => {
    test.skip(LINK !== role, `start with LIVE_LINK=${role}`);
    test.setTimeout(25 * 60_000);
    const calls: Call[] = [];
    const phase = { current: 'sign-in' };
    watch(page, calls, phase);
    const { log, snap, confirmAndWait, finish } = linkTools(page, calls, `link-${role}`);
    const visit = async (name: string, hash: string) => {
      phase.current = name;
      await page.evaluate((h) => (window.location.hash = h), hash);
      await settle(page);
      await page.waitForTimeout(2000);
      await snap(name);
    };
    try {
      await signInByHand(page);
      await settle(page);
      await visit('visit readiness (upcoming visits)', '#/readiness');
      const open = page.locator('main tbody tr').first().getByRole('button');
      if ((await open.count()) > 0) {
        phase.current = 'visit readiness (open the first visit)';
        await open.first().click();
        await settle(page);
        const show = page.getByRole('button', { name: /show what the patient marked|நோயாளி குறித்தவற்றைக் காட்டு/i });
        if ((await show.count()) > 0) {
          phase.current = 'visit preparation (staff view, read-only)';
          await show.first().click();
          await settle(page);
          await snap('visit preparation, staff view');
        } else log.push('## no "Show what the patient marked" button on the page', '');
      } else log.push('## no upcoming visit row to open', '');

      await visit('cards and templates (lists)', '#/knowledge');
      log.push(`## drafts and approved items on the page: ${await page.locator('main tbody tr').count()} table rows`, '');
      if (role === 'doctor') {
        // Read-only: look at the first draft, open the Approve dialog (it shows the exact text), and cancel it.
        const row = page.locator('main tbody tr').first();
        if ((await row.count()) > 0) {
          phase.current = 'cards and templates (look at the first draft)';
          await row.getByRole('button').first().click();
          await settle(page);
          await snap('a draft, looked at');
          const approve = page.locator('main tbody tr').first().getByRole('button', { name: /^Approve/ });
          if ((await approve.count()) > 0) {
            phase.current = 'cards and templates (the Approve dialog, then Cancel)';
            await approve.first().click();
            await snap('the Approve dialog (nothing is approved)');
            await page.getByRole('dialog').locator('.dialog-actions button').first().click();
            await settle(page);
          } else log.push('## no Approve button on the first row (it may be your own draft, or already decided)', '');
        } else log.push('## no draft to look at', '');
      }
      if (role === 'nurse' && process.env.LIVE_DRAFT === 'yes') {
        phase.current = 'cards and templates (ONE demo draft, write)';
        await page.locator('main .toolbar button').first().click();
        await page.locator('#kn-kind').selectOption('template');
        await page.locator('#kn-code').fill('demo.walk.draft');
        // ONE message template draft (never a card). Its title and its text are exactly this one plainly marked sentence and nothing else.
        await page.locator('#kn-title-in').fill('DEMO PLACEHOLDER, not a clinical template, not approved');
        await page.locator('#kn-body').fill('DEMO PLACEHOLDER, not a clinical template, not approved');
        await page.getByRole('button', { name: /^Save the draft$/ }).click();
        await confirmAndWait('"Save the draft"');
        // then submit it for approval (the second and last write); the author sees no Approve on it
        phase.current = 'cards and templates (submit the demo draft for approval, write)';
        await page.getByRole('button', { name: 'Submit demo.walk.draft' }).click();
        await confirmAndWait('"Submit this draft for approval"');
        await page.waitForTimeout(1500);
        const approveButtons = await page.getByRole('button', { name: /^Approve/ }).count();
        log.push(`## Approve buttons on the page after submitting (the author, a nurse): ${approveButtons}`, '');
        await snap('the drafts list after the submit');
      } else if (role === 'nurse') {
        log.push('## no draft was written (LIVE_DRAFT is not "yes")', '');
      }
    } finally {
      await finish(`Demo second-factor walk, ${role}`);
    }
  });
}

test('DEMO complaint without signing in: ONE demo complaint (one write, no sign-in needed)', async ({ page }) => {
  test.skip(LINK !== 'complaint', 'start with LIVE_LINK=complaint');
  test.setTimeout(10 * 60_000);
  const calls: Call[] = [];
  const phase = { current: 'the signed-out page' };
  watch(page, calls, phase);
  const { log, snap, stop, confirmAndWait, finish } = linkTools(page, calls, 'link-complaint');
  try {
    await page.goto('/patient/sign-in/');
    await settle(page);
    await page.waitForTimeout(3000);
    await snap('the signed-out page');
    const category = page.locator('#pg-category');
    if ((await category.evaluate((el) => el.tagName).catch(() => '')) !== 'SELECT') await stop('the category list is not on the page (it is not set up or did not load); nothing was sent');
    const values = await category.locator('option').evaluateAll((els) => els.map((e) => (e as HTMLOptionElement).value).filter(Boolean));
    log.push(`## categories offered: ${values.join(', ')}`, '');
    if (!values.includes('other')) await stop('the category "other" is not in the list; nothing was sent');
    phase.current = 'ONE demo complaint (write)';
    await category.selectOption('other');
    await page.locator('#pg-text').fill('DEMO complaint, not a real complaint');
    await page.locator('section[aria-labelledby=portal-public-grievance] button[type=submit]').click();
    await confirmAndWait('"Send" the demo complaint');
  } finally {
    await finish('Demo complaint without signing in');
  }
});

test('DEMO admin walk, read-only (second factor): every admin tab', async ({ page }) => {
  test.skip(LINK !== 'admin', 'start with LIVE_LINK=admin');
  test.setTimeout(25 * 60_000);
  const calls: Call[] = [];
  const phase = { current: 'sign-in' };
  watch(page, calls, phase);
  const { log, snap, finish } = linkTools(page, calls, 'link-admin');
  try {
    await signInByHand(page);
    await settle(page);
    log.push(`## the shell showed: ${(await page.locator('.session-bar').innerText().catch(() => '')).replace(/s+/g, ' ').slice(0, 160)}`, '');
    for (const [name, hash] of [
      ['dashboard', '#/dashboard'],
      ['admin: accounts', '#/admin/accounts'],
      ['admin: autonomy (and its history)', '#/admin/autonomy'],
      ['admin: red-flag rules', '#/admin/rules'],
      ['admin: reviewer stats', '#/admin/reviewer'],
      ['admin: cost', '#/admin/cost'],
      ['admin: voice', '#/admin/voice'],
      ['admin: recovery', '#/admin/recovery'],
      ['admin: grievances', '#/admin/grievances'],
    ] as const) {
      phase.current = name;
      await page.evaluate((h) => (window.location.hash = h), hash);
      await settle(page);
      await page.waitForTimeout(2000);
      await snap(name);
    }
    log.push('## the clinic public-profile screen: there is no such staff screen (the backend asked for none; the write is PUT /v1/staff/clinic/public-profile); not tested live', '');
  } finally {
    await finish('Demo admin walk, read-only');
  }
});

// ---- the authorised WRITE walk (21 Nov 2026), synthetic demo data only, ONE action per write, no retries, stop at the first unexpected problem ----
// Every write is on the demo clinic's made-up data. Nothing switched off in the build is touched (erasure, merge approve and unmerge), and DEMO Portal Patient's
// booking and consent are never touched. The user signs in by hand each time. Each step logs the screen's words and every write's status code.
// A small valid PDF (objects, a cross-reference table, a trailer) whose one line says it is synthetic. No real data.
function dummyPdf(): Buffer {
  const content = 'BT /F1 12 Tf 10 60 Td (DEMO synthetic referral letter. Not real data.) Tj ET';
  const objects = [
    '<</Type/Catalog/Pages 2 0 R>>',
    '<</Type/Pages/Kids[3 0 R]/Count 1>>',
    '<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 120]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>',
    `<</Length ${content.length}>>
stream
${content}
endstream`,
    '<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>',
  ];
  let out = `%PDF-1.4
`;
  const offsets: number[] = [];
  objects.forEach((o, i) => {
    offsets.push(out.length);
    out += `${i + 1} 0 obj
${o}
endobj
`;
  });
  const xref = out.length;
  out += `xref
0 ${objects.length + 1}
0000000000 65535 f 
${offsets.map((o) => `${String(o).padStart(10, '0')} 00000 n 
`).join('')}trailer
<</Size ${objects.length + 1}/Root 1 0 R>>
startxref
${xref}
%%EOF
`;
  return Buffer.from(out, 'latin1');
}

test('DEMO write walk, patient: ONE "ask the hospital" request (a closed word, no free text)', async ({ page }) => {
  test.skip(LINK !== 'w-portal', 'start with LIVE_LINK=w-portal');
  test.setTimeout(20 * 60_000);
  const calls: Call[] = [];
  const phase = { current: 'sign-in' };
  watch(page, calls, phase);
  const { log, snap, stop, confirmAndWait, finish } = linkTools(page, calls, 'write-portal');
  try {
    await page.goto('/patient/sign-in/');
    console.log('\n>>> Sign in yourself with the "Demo sign-in" form (email and password). Nothing is typed for you and no credential reaches this script.');
    await expect(page.locator('main dl.facts')).toBeVisible({ timeout: 10 * 60_000 });
    await settle(page);
    await page.waitForTimeout(3000);
    await snap('after sign-in');
    if ((await page.locator('#rq-kind').count()) === 0) await stop('the request form is not on the page; nothing was sent');
    phase.current = 'ONE ask-the-hospital request (write)';
    await page.locator('#rq-kind').selectOption('nominee');
    await page.getByRole('button', { name: 'Send request' }).click();
    await confirmAndWait('"Send request" (a nominee request, a closed word)');
    log.push(`## the portal's own list of requests now holds: ${(await page.locator('#portal-requests').locator('xpath=ancestor::section[1]//ul/li').count())} item(s)`, '');
  } finally {
    await finish('Demo write walk, patient');
  }
});

test('DEMO write walk, front desk: register, upload and run a referral, task, scheduling, alert, request status', async ({ page }) => {
  // 'w-fd-rest' resumes AFTER the register and the hold (already done) on the slot that is still held (LIVE_SLOT_WHEN = its start text): nothing is repeated.
  const REST = LINK === 'w-fd-rest';
  test.skip(LINK !== 'w-frontdesk' && !REST, 'start with LIVE_LINK=w-frontdesk (or w-fd-rest to resume)');
  test.setTimeout(40 * 60_000);
  const calls: Call[] = [];
  const phase = { current: 'sign-in' };
  watch(page, calls, phase);
  const { log, snap, stop, confirmAndWait, finish } = linkTools(page, calls, 'write-frontdesk');
  let bookingId = '';
  page.on('response', (res) => {
    if (res.request().method() === 'POST' && /\/v1\/staff\/scheduling\/slots\/[^/]+\/book$/.test(new URL(res.url()).pathname)) {
      void res.json().then((b: { booking_id?: string }) => { if (typeof b.booking_id === 'string') bookingId = b.booking_id; }).catch(() => undefined);
    }
  });
  const go = async (name: string, hash: string) => {
    phase.current = name;
    await page.evaluate((h) => (window.location.hash = h), hash);
    await settle(page);
    await page.waitForTimeout(1500);
  };
  try {
    await signInByHand(page);
    await settle(page);

    if (!REST) {
      // 1. register ONE synthetic patient (no phone)
      await go('patients (register one synthetic patient)', '#/patients');
      await page.getByLabel('Name to register').fill('Demo Zulu Sample');
      await page.getByLabel('Date of birth to register').fill('1985-04-04');
      phase.current = 'register ONE synthetic patient (write)';
      await page.getByRole('button', { name: /^Register$/ }).click();
      await confirmAndWait('"Register" Demo Zulu Sample');
    }

    // 2. scheduling: hold one open slot, book it for the new patient, cancel THAT booking
    await go('scheduling (slots)', '#/scheduling/slots');
    let slotText = REST ? (process.env.LIVE_SLOT_WHEN ?? '').trim() : '';
    if (!REST) {
      const openRow = page.locator('main tbody tr').filter({ has: page.getByRole('button', { name: /^Hold/ }).and(page.locator(':not([disabled])')) }).first();
      if ((await openRow.count()) === 0) await stop('no open slot to hold; nothing was held');
      slotText = ((await openRow.locator('th').first().innerText()) || '').replace(/\s+/g, ' ').trim();
      log.push(`## the slot used starts: ${slotText}`, '');
      phase.current = 'hold ONE slot (write)';
      await openRow.getByRole('button', { name: /^Hold/ }).click();
      await page.locator('#hold-minutes').fill('10');
      await confirmAndWait('"Hold" one slot for 10 minutes');
    }
    if (slotText === '') await stop('the slot to book is not known (set LIVE_SLOT_WHEN when resuming); nothing was booked');
    const heldRow = page.locator('main tbody tr').filter({ hasText: slotText }).filter({ hasText: /Held/ }).first();
    if ((await heldRow.count()) === 0) await stop('the held slot is not on the page as Held (its hold may have expired); nothing was booked');
    phase.current = 'book that slot (write)';
    await heldRow.getByRole('button', { name: /^Book/ }).click();
    const dialog = page.getByRole('dialog');
    await dialog.locator('input[type=text]').first().fill('Demo Zulu Sample');
    await dialog.locator('input[type=date]').first().fill('1985-04-04');
    await dialog.locator('input[type=date] ~ button[type=button]').first().click();
    await settle(page);
    const hits = dialog.getByRole('radio');
    await hits.first().waitFor({ timeout: 25_000 }).catch(() => undefined); // the search answers after a moment: wait for it, never count early
    await page.waitForTimeout(800);
    if ((await hits.count()) !== 1) await stop(`the patient search found ${await hits.count()} records for Demo Zulu Sample, not exactly one; nothing was booked`);
    await hits.first().check();
    await confirmAndWait('"Book" the held slot for Demo Zulu Sample');
    if (!bookingId) await stop('the booking answered but its id was not seen, so the booking to cancel cannot be told apart from DEMO Portal Patient\'s; nothing was cancelled');
    await go('scheduling (bookings)', '#/scheduling/bookings');
    phase.current = 'cancel THAT booking (write)';
    await page.getByRole('button', { name: `Cancel booking ${bookingId.slice(0, 8)}` }).click();
    await confirmAndWait('"Cancel booking" (the one just made)');

    // 3. referral: upload ONE dummy synthetic letter, then run it
    await go('referrals (upload)', '#/referrals/upload');
    await page.locator('main select').first().selectOption('referral');
    await page.locator('main input[type=file]').setInputFiles({ name: 'demo-referral.pdf', mimeType: 'application/pdf', buffer: dummyPdf() });
    phase.current = 'upload ONE dummy referral letter (write)';
    await page.getByRole('button', { name: /^Upload$/ }).click();
    await page.getByRole('link', { name: 'Open the referral' }).waitFor({ timeout: 90_000 }).catch(() => undefined);
    await snap('after the upload');
    if ((await page.getByRole('link', { name: 'Open the referral' }).count()) === 0) await stop('the upload did not finish with an "Open the referral" link; see the screen words above');
    await page.getByRole('link', { name: 'Open the referral' }).click();
    await settle(page);
    phase.current = 'run the uploaded referral (write)';
    await page.getByRole('button', { name: 'Run the referral' }).click();
    await confirmAndWait('"Run the referral"');

    // 4. tasks: acknowledge then resolve ONE task (never a red-flag or unassessable one)
    await go('tasks', '#/tasks');
    const taskRow = page.locator('main tbody tr').filter({ hasText: /Open/ }).filter({ hasNotText: /Red flag|Unassessable|Urgent/ }).first();
    if ((await taskRow.count()) === 0) {
      log.push('## tasks: no open, non-urgent task to acknowledge; not tested live', '');
    } else {
      await taskRow.getByRole('link').first().click();
      await settle(page);
      phase.current = 'acknowledge ONE task (write)';
      await page.getByRole('button', { name: /^Acknowledge$/ }).click();
      await confirmAndWait('"Acknowledge" the task');
      const resolve = page.getByRole('button', { name: /^Resolve$/ });
      if ((await resolve.count()) === 0) {
        log.push('## the task cannot be closed on this screen (its kind is closed through its own screen); only the acknowledge was made', '');
      } else {
        phase.current = 'resolve THAT task (write)';
        await resolve.first().click();
        await confirmAndWait('"Resolve" the task');
      }
    }

    // 5. alerts: acknowledge ONE if any exists
    await go('alerts', '#/alerts');
    const ack = page.locator('main').getByRole('button', { name: /^Acknowledge/ });
    if ((await ack.count()) === 0) {
      log.push('## alerts: none to acknowledge; not tested live', '');
    } else {
      phase.current = 'acknowledge ONE alert (write)';
      await ack.first().click();
      await confirmAndWait('"Acknowledge" the alert');
    }

    // 6. patient requests: ONE status change, only with a button allowed_next gave
    await go('patient requests', '#/requests');
    const reqBtn = page.locator('main tbody tr').first().getByRole('button');
    if ((await reqBtn.count()) === 0) {
      log.push('## patient requests: no request with an allowed next status; not tested live', '');
    } else {
      phase.current = 'change ONE patient request status (write)';
      await reqBtn.first().click();
      await confirmAndWait('the first status button the row offered');
    }
  } finally {
    await finish('Demo write walk, front desk');
  }
});

test('DEMO write walk, front desk: resolve ONE task, acknowledge ONE alert, change ONE patient request status', async ({ page }) => {
  test.skip(LINK !== 'w-fd-tasks', 'start with LIVE_LINK=w-fd-tasks');
  test.setTimeout(30 * 60_000);
  const calls: Call[] = [];
  const phase = { current: 'sign-in' };
  watch(page, calls, phase);
  const { log, snap, stop, confirmAndWait, finish } = linkTools(page, calls, 'write-fd-tasks');
  const go = async (name: string, hash: string) => {
    phase.current = name;
    await page.evaluate((h) => (window.location.hash = h), hash);
    await settle(page);
    await page.waitForTimeout(2000);
  };
  try {
    await signInByHand(page);
    await settle(page);

    // tasks: acknowledge (if the screen requires it) then resolve ONE task, never a red-flag or unassessable one
    await go('tasks', '#/tasks');
    await snap('tasks');
    const taskRow = page.locator('main tbody tr').filter({ hasText: /Open/ }).filter({ hasNotText: /Red flag|Unassessable|Urgent/ }).first();
    if ((await taskRow.count()) === 0) {
      log.push('## tasks: no open, non-urgent task; not tested live', '');
    } else {
      await taskRow.getByRole('link').first().click();
      await settle(page);
      await snap('the task, opened');
      const ack = page.getByRole('button', { name: /^Acknowledge$/ });
      if ((await ack.count()) > 0) {
        phase.current = 'acknowledge ONE task (write)';
        await ack.first().click();
        await confirmAndWait('"Acknowledge" the task');
      }
      const resolve = page.getByRole('button', { name: /^Resolve$/ });
      if ((await resolve.count()) === 0) {
        log.push('## this task cannot be closed on this screen (its kind is closed through its own screen); not resolved', '');
      } else {
        phase.current = 'resolve ONE task (write)';
        await resolve.first().click();
        await confirmAndWait('"Resolve" the task');
      }
    }

    // alerts: acknowledge ONE if any exists
    await go('alerts', '#/alerts');
    await snap('alerts');
    const ackAlert = page.locator('main').getByRole('button', { name: /^Acknowledge/ });
    if ((await ackAlert.count()) === 0) {
      log.push('## alerts: none to acknowledge; not tested live', '');
    } else {
      phase.current = 'acknowledge ONE alert (write)';
      await ackAlert.first().click();
      await confirmAndWait('"Acknowledge" the alert');
    }

    // patient requests: ONE status change, only with a button the row's allowed_next gave
    await go('patient requests', '#/requests');
    await snap('patient requests');
    const reqBtn = page.locator('main tbody tr').first().getByRole('button');
    if ((await reqBtn.count()) === 0) {
      log.push('## patient requests: no request with an allowed next status; not tested live', '');
    } else {
      log.push(`## the first request offers ${await reqBtn.count()} status button(s) (its allowed_next)`, '');
      phase.current = 'change ONE patient request status (write)';
      await reqBtn.first().click();
      await confirmAndWait('the first status button the row offered');
    }
    if (!log.length) await stop('nothing happened');
  } finally {
    await finish('Demo write walk, front desk (tasks, alert, request)');
  }
});

test('DEMO write walk, nurse: ONE screen-for-red-flags check on a message', async ({ page }) => {
  test.skip(LINK !== 'w-nurse', 'start with LIVE_LINK=w-nurse');
  test.setTimeout(25 * 60_000);
  const calls: Call[] = [];
  const phase = { current: 'sign-in' };
  watch(page, calls, phase);
  const { log, snap, stop, confirmAndWait, finish } = linkTools(page, calls, 'write-nurse');
  try {
    await signInByHand(page);
    await settle(page);
    // visit readiness for DEMO Portal Patient's visit (the switch is now on): record 200 or 503; read-only unless it offers a start button, which is pressed once
    phase.current = 'visit readiness (upcoming visits)';
    await page.evaluate(() => (window.location.hash = '#/readiness'));
    await settle(page);
    await page.waitForTimeout(2000);
    await snap('visit readiness');
    const visitBtn = page.locator('main tbody tr').first().getByRole('button');
    if ((await visitBtn.count()) > 0) {
      phase.current = 'visit readiness (open the visit)';
      await visitBtn.first().click();
      await settle(page);
      await page.waitForTimeout(2000);
      await snap('visit readiness, the visit opened');
      const start = page.getByRole('button', { name: /^Start readiness$/ });
      if ((await start.count()) > 0) {
        phase.current = 'start readiness ONCE (write)';
        await start.first().click();
        await confirmAndWait('"Start readiness"');
      } else {
        log.push('## no "Start readiness" button is offered; nothing was started', '');
      }
    } else {
      log.push('## no upcoming visit row to open', '');
    }
    phase.current = 'messages (list)';
    await page.evaluate(() => (window.location.hash = '#/messages'));
    await settle(page);
    await page.waitForTimeout(2000);
    await snap('messages');
    const open = page.locator('main tbody tr').first().getByRole('button');
    if ((await open.count()) === 0) await stop('there is no conversation to open; not tested live');
    await open.first().click();
    await settle(page);
    // reaching the screen action needs one audited read of a message first (a confirmation, recorded under the nurse's name)
    const read = page.locator('main table').nth(1).locator('tbody tr').first().getByRole('button').first();
    if ((await read.count()) === 0) await stop('the conversation has no message to read; not tested live');
    phase.current = 'read ONE message (an audited read, needed to reach the screening button)';
    await read.click();
    await confirmAndWait('the read of one message');
    const screenBtn = page.getByRole('button', { name: /^Screen for red flags$/ });
    if ((await screenBtn.count()) === 0) await stop('the messages screen does not offer "Screen for red flags" here; not tested live');
    phase.current = 'screen ONE message for red flags (write)';
    await screenBtn.first().click();
    await confirmAndWait('"Screen for red flags"');
  } finally {
    await finish('Demo write walk, nurse');
  }
});

test('DEMO write walk, doctor: ONE referral decision (reject) and REJECT the leftover demo draft', async ({ page }) => {
  test.skip(LINK !== 'w-doctor', 'start with LIVE_LINK=w-doctor');
  test.setTimeout(30 * 60_000);
  const calls: Call[] = [];
  const phase = { current: 'sign-in' };
  watch(page, calls, phase);
  const { log, snap, stop, confirmAndWait, finish } = linkTools(page, calls, 'write-doctor');
  try {
    await signInByHand(page);
    await settle(page);
    phase.current = 'referrals (find the proposal with the real engine fields)';
    await page.evaluate(() => (window.location.hash = '#/referrals'));
    await settle(page);
    await page.waitForTimeout(2500);
    const links = await page.locator('main tbody a').evaluateAll((els) => els.map((e) => (e as HTMLAnchorElement).getAttribute('href') ?? ''));
    log.push(`## referrals on the list: ${links.length}`, '');
    let found = false;
    for (const href of links.slice(0, 20)) {
      await page.evaluate((h) => (window.location.hash = h), href.replace(/^#?\/?/, '#/'));
      await settle(page);
      await page.waitForTimeout(1500);
      const text = await page.locator('main').innerText().catch(() => '');
      if (/jev/i.test(text) && /\b0\.5\b/.test(text) && /Record a decision/.test(text)) {
        found = true;
        break;
      }
    }
    if (!found) await stop('no open referral proposal with the engine "jev" and confidence 0.5 and a decision form was found; nothing was decided');
    await snap('the referral with the real engine fields');
    phase.current = 'record ONE referral decision (write): Reject';
    await page.getByRole('radio', { name: 'Reject' }).check();
    await page.getByRole('button', { name: /^Record decision$/ }).click();
    await confirmAndWait('"Record decision": Reject');

    phase.current = 'cards and templates (the leftover demo draft)';
    await page.evaluate(() => (window.location.hash = '#/knowledge'));
    await settle(page);
    await page.waitForTimeout(2000);
    const row = page.locator('main tbody tr').filter({ hasText: 'demo.walk.draft' }).first();
    if ((await row.count()) === 0) await stop('the demo draft (demo.walk.draft) is not on the page; nothing was rejected');
    phase.current = 'REJECT the demo draft (write)';
    await row.getByRole('button', { name: /^Reject/ }).click();
    await page.getByLabel('Reason word').fill('demo_placeholder');
    await confirmAndWait('"Reject" the demo draft, reason word demo_placeholder');
  } finally {
    await finish('Demo write walk, doctor');
  }
});

test('DEMO link, front desk: the family links page, read-only (second factor)', async ({ page }) => {
  test.skip(LINK !== 'family', 'start with LIVE_LINK=family');
  test.setTimeout(20 * 60_000);
  const calls: Call[] = [];
  const phase = { current: 'sign-in' };
  watch(page, calls, phase);
  const { snap, finish } = linkTools(page, calls, 'link-family');
  try {
    await signInByHand(page);
    await settle(page);
    phase.current = 'family link requests';
    await page.evaluate(() => (window.location.hash = '#/family'));
    await settle(page);
    await page.waitForTimeout(2000);
    await snap('the family links page');
  } finally {
    await finish('Demo walk, front desk: family links');
  }
});

test('DEMO link, step 2: front desk approves the one pending claim (one write)', async ({ page }) => {
  test.skip(LINK !== 'approve', 'start with LIVE_LINK=approve');
  test.setTimeout(20 * 60_000);
  const calls: Call[] = [];
  const phase = { current: 'sign-in' };
  const log: string[] = [];
  const snap = async (name: string) => {
    const words = await screenWords(page);
    log.push(`## ${name}`, '', ...(words.length ? words.map((w) => `- ${w}`) : ['- (nothing said on screen)']), '');
    return words;
  };
  const stop = async (why: string) => {
    log.push(`## STOPPED: ${why}`, '');
    throw new Error(why);
  };
  watch(page, calls, phase);
  try {
    await signInByHand(page);
    await settle(page);
    phase.current = 'portal claims (list)';
    await page.evaluate(() => (window.location.hash = '#/claims'));
    await settle(page);
    await snap('portal claims list');
    const rows = page.locator('main tbody tr');
    const n = await rows.count();
    log.push(`## pending claims on the list: ${n}`, '');
    if (n !== 1) await stop(`there are ${n} pending claims, not exactly one; the list shows only an id and a time, so nothing was approved`);
    phase.current = 'approve the claim (choose the record)';
    await rows.first().getByRole('button').first().click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    await dialog.locator('input[type=text]').first().fill('DEMO Portal Patient');
    await dialog.locator('input[type=date]').first().fill('1990-01-01');
    await dialog.locator('input[type=date] ~ button[type=button]').first().click();
    await settle(page);
    const hits = dialog.getByRole('radio');
    const hitCount = await hits.count();
    log.push(`## records found for the demo patient: ${hitCount}`, '', ...(await screenWords(page)).map((w) => `- ${w}`), '');
    if (hitCount !== 1) await stop(`the search found ${hitCount} records for the demo patient, not exactly one; nothing was approved`);
    await hits.first().check();
    await dialog.locator('.dialog-actions button').nth(1).click();
    await settle(page);
    await snap('after approving');
  } finally {
    await page.waitForTimeout(1500).catch(() => undefined);
    mkdirSync(REPORT_DIR, { recursive: true });
    const writes = calls.filter((c) => !['GET', 'HEAD'].includes(c.method) && c.template !== '/v1/staff/patients/search').map((c) => `- ${c.method} ${c.template} -> ${c.status}`);
    writeFileSync(resolve(REPORT_DIR, `link-approve${SUFFIX}.md`), ['# Demo link, step 2: what each screen said', '', ...log, '## Writes made (the patient search is a read)', '', ...(writes.length ? writes : ['- none']), ''].join('\n'));
    save(calls, 'demo link step 2 (one write: see link-approve file)');
  }
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
