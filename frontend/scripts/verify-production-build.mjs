// Production-build proof. Builds the PRODUCTION bundle (the default mode) with the demo switches set and canary "secrets" planted in the
// build environment, then checks what actually shipped:
//   1. both demo flags are compiled to false (VITE_DEV_SKIP_STAFF_MFA, VITE_DEV_PATIENT_PASSWORD_LOGIN), even though the build was started with them set;
//   2. no key or secret is in the bundle (the agreed forbidden markers, key-shaped values, private-key blocks, and the planted canaries);
//   3. the Content Security Policy is intact: the same policy is the first head element of every page, equals dist/csp-policy.txt, has no unsafe-inline,
//      unsafe-eval or wildcard, allows only the two configured origins to be contacted, and no page has an inline script or style;
//   4. (with --browser) the built app, served as static files, shows NO demo sign-in form or banner and NO "development only" staff banner.
// Usage: node scripts/verify-production-build.mjs [--browser]      (also: npm run verify:production)
import { spawnSync, spawn } from 'node:child_process';
import { mkdtempSync, readdirSync, readFileSync, rmSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import { URL, pathToFileURL } from 'node:url';
import { findLeaks } from './check-bundle-secrets.mjs';

const root = resolve(import.meta.dirname, '..');
const MOCK = { SUPABASE_URL: 'https://mock-project.supabase.invalid', SUPABASE_PUBLISHABLE_KEY: 'mock-publishable-key-not-real', CAREFLOW_API_BASE_URL: 'https://mock-api.invalid' };
// Planted in the build environment. None is an allowlisted name, so none may appear in the output. They are assembled so no secret-shaped literal is committed.
export const CANARIES = {
  DATABASE_URL: ['postgres', '://canary-user:canary-pass@canary-host/canary-db'].join(''),
  SUPABASE_SECRET_KEY: ['canary-secret-key-', 'A1b2C3d4E5f6G7h8'].join(''),
  CAREFLOW_ENC_SECRET: ['canary-enc-secret-', 'Z9y8X7w6V5u4T3s2'].join(''),
  SOME_PRIVATE_TOKEN: ['canary-token-', 'Q1w2E3r4T5y6U7i8'].join(''),
};
const PAGES = ['patient/index.html', 'patient/sign-in/index.html', 'patient/sign-up/index.html', 'patient/privacy/index.html', 'staff/index.html'];

function* walk(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) yield* walk(p);
    else yield p;
  }
}

/** Static checks over a built output folder. Returns a list of problems in words (never the text around a match); empty means it passed. */
export function verifyDist(dist, { canaries = Object.values(CANARIES), origins = [MOCK.SUPABASE_URL, MOCK.CAREFLOW_API_BASE_URL] } = {}) {
  const problems = [];
  const files = [...walk(dist)];
  const textOf = (f) => readFileSync(f, 'latin1');

  // 1. the two demo flags
  const js = files.filter((f) => f.endsWith('.js'));
  const joined = js.map(textOf).join('\n');
  for (const flag of ['devSkipStaffMfa', 'devPatientPasswordLogin']) {
    const mentions = joined.match(new RegExp(`${flag}\\s*:\\s*[^,}\\s]+`, 'g')) ?? [];
    if (mentions.length === 0) problems.push(`the flag ${flag} is not in the compiled flags object, so it cannot be proven off`);
    for (const m of mentions) if (!/:\s*(!1|false)$/.test(m)) problems.push(`the flag ${flag} is not compiled to false (${m.replace(/\s+/g, '')})`);
  }

  // 2. keys and secrets
  for (const h of findLeaks(dist)) problems.push(`forbidden marker ${h.marker} in ${h.file}`);
  for (const f of files) {
    const text = textOf(f);
    if (/-----BEGIN [A-Z ]*PRIVATE KEY-----/.test(text)) problems.push(`a private key block in ${f}`);
    if (/postgres(ql)?:\/\/[^\s"']+:[^\s"']+@/.test(text)) problems.push(`a database connection string in ${f}`);
    for (const c of canaries) if (text.includes(c)) problems.push(`a planted canary secret reached ${f}`);
    if (/eyJ[A-Za-z0-9_-]{15,}\.eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}/.test(text)) problems.push(`a signed-token-shaped value in ${f}`);
  }

  // 3. the Content Security Policy
  let policy = '';
  try {
    policy = readFileSync(join(dist, 'csp-policy.txt'), 'utf8').trim();
  } catch {
    problems.push('dist/csp-policy.txt is missing');
  }
  if (policy) {
    const dirs = Object.fromEntries(policy.split(';').map((d) => d.trim().split(/\s+/)).filter((p) => p[0]).map(([n, ...v]) => [n, v]));
    if (!(dirs['default-src']?.length === 1 && dirs['default-src'][0] === "'none'")) problems.push("the policy's default-src is not 'none'");
    if (!(dirs['script-src']?.length === 1 && dirs['script-src'][0] === "'self'")) problems.push("the policy's script-src is not exactly 'self'");
    if (!(dirs['object-src']?.[0] === "'none'")) problems.push("the policy's object-src is not 'none'");
    if (/unsafe-inline|unsafe-eval|\*/.test(policy)) problems.push('the policy contains unsafe-inline, unsafe-eval or a wildcard');
    const connect = (dirs['connect-src'] ?? []).filter((v) => v !== "'self'").sort();
    const expected = [...new Set(origins.map((o) => new URL(o).origin))].sort();
    if (JSON.stringify(connect) !== JSON.stringify(expected)) problems.push(`connect-src allows ${connect.join(' ') || 'nothing'} besides 'self'; expected only the two configured origins`);
    for (const p of PAGES) {
      let html = '';
      try {
        html = readFileSync(join(dist, p), 'utf8');
      } catch {
        problems.push(`the page ${p} is missing`);
        continue;
      }
      const m = /<head>\s*<meta http-equiv="Content-Security-Policy" content="([^"]+)"/.exec(html);
      if (m?.[1] !== policy) problems.push(`${p} does not carry the policy as its first head element, or it differs from csp-policy.txt`);
      if (/<script(?![^>]*\ssrc=)[^>]*>/.test(html)) problems.push(`${p} has an inline script`);
      if (/<style|\sstyle=/.test(html)) problems.push(`${p} has an inline style`);
    }
  }
  return problems;
}

/** Builds the production bundle into a fresh temp folder, with the demo switches ON and canary secrets planted. Returns { dist, cleanup }. */
export function buildProduction() {
  const dist = mkdtempSync(join(tmpdir(), 'careflow-prod-proof-'));
  const env = {
    ...process.env,
    ...MOCK,
    ...CANARIES,
    NODE_ENV: 'production', // explicit: a parent process (a test runner) may have set another value, and that changes what is bundled
    VITE_DEV_SKIP_STAFF_MFA: '1',
    VITE_DEV_PATIENT_PASSWORD_LOGIN: '1',
  };
  const r = spawnSync(process.execPath, [resolve(root, 'node_modules/vite/bin/vite.js'), 'build', '--outDir', dist, '--emptyOutDir'], { cwd: root, env, encoding: 'utf8' });
  if (r.status !== 0) {
    rmSync(dist, { recursive: true, force: true });
    throw new Error(`the production build failed (exit ${r.status}): ${(r.stderr || r.stdout || '').slice(-400)}`);
  }
  return { dist, cleanup: () => rmSync(dist, { recursive: true, force: true }) };
}

/** Serves the built folder and loads the sign-in pages in a real browser: nothing demo may be on screen. Returns problems. */
async function browserProof(dist) {
  const problems = [];
  const port = 4900 + Math.floor(Math.random() * 90);
  const server = spawn(process.execPath, [resolve(root, 'node_modules/vite/bin/vite.js'), 'preview', '--outDir', dist, '--host', '127.0.0.1', '--port', String(port), '--strictPort'], { cwd: root, env: { ...process.env, ...MOCK }, stdio: 'ignore' });
  try {
    for (let i = 0; i < 40; i++) {
      const up = await fetch(`http://127.0.0.1:${port}/patient/`).then((r) => r.ok).catch(() => false);
      if (up) break;
      await sleep(500);
    }
    const { chromium } = await import('playwright');
    const browser = await chromium.launch();
    try {
      const look = async (path, check) => {
        const page = await browser.newPage();
        try {
          await page.route(/^https:\/\/mock-.*\.invalid\//, (route) => route.abort()); // nothing real is contacted
          await page.goto(`http://127.0.0.1:${port}${path}`);
          await page.waitForTimeout(2500);
          await check(page);
        } finally {
          await page.close();
        }
      };
      await look('/patient/sign-in/', async (page) => {
        if ((await page.locator('[data-demo-sign-in], #portal-demo-email, #portal-demo-password').count()) > 0) problems.push('the demo email and password form is on the production patient sign-in page');
        const text = await page.locator('body').innerText();
        if (/demo sign-in/i.test(text)) problems.push('the words "Demo sign-in" are on the production patient sign-in page');
        if ((await page.locator('input[type=tel]').count()) === 0) problems.push('the normal phone-code form is not on the patient sign-in page');
      });
      await look('/staff/', async (page) => {
        if ((await page.locator('[data-dev-mfa-skipped]').count()) > 0) problems.push('the "development only: second factor skipped" banner is on the production staff page');
        const text = await page.locator('body').innerText();
        if (/second factor.*skipped|authenticator\) is skipped/i.test(text)) problems.push('the words about skipping the second factor are on the production staff page');
        if ((await page.locator('input[type=password]').count()) === 0) problems.push('the staff sign-in form is not on the staff page');
      });
    } finally {
      await browser.close();
    }
  } finally {
    server.kill();
  }
  return problems;
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) {
  const { dist, cleanup } = buildProduction();
  try {
    const problems = verifyDist(dist);
    if (process.argv.includes('--browser')) problems.push(...(await browserProof(dist)));
    if (problems.length > 0) {
      for (const p of problems) console.error(`PROBLEM: ${p}`);
      process.exitCode = 1;
    } else {
      console.log(`production build proof passed (${process.argv.includes('--browser') ? 'static checks and the browser check' : 'static checks'}): both demo flags compiled to false although set at build time, no key or planted secret in the output, the CSP intact on all five pages.`);
    }
  } finally {
    cleanup();
  }
}
