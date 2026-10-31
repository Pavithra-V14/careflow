// Starts the manual LIVE integration run (e2e-live/). Refuses unless the dev server and the backend answer. Never used in CI.
// Usage: npm run e2e:live            read-only walk of every screen
//        npm run e2e:live -- --record   open the console and record while you use it yourself (writes included)
import { spawn } from 'node:child_process';
import { resolve } from 'node:path';

const root = resolve(import.meta.dirname, '..');
const base = process.env.LIVE_BASE_URL ?? 'http://127.0.0.1:5173';
const record = process.argv.includes('--record');

async function probe(path) {
  try {
    const res = await fetch(base + path, { signal: AbortSignal.timeout(8000) });
    return { status: res.status, json: (res.headers.get('content-type') ?? '').includes('json') };
  } catch {
    return { status: 0, json: false };
  }
}
const status = async (path) => (await probe(path)).status;

const page = await status('/staff/');
if (page !== 200) {
  console.error(`The dev server is not answering at ${base}/staff/ (status ${page}). Start it: npm run dev`);
  process.exit(2);
}
// /readyz goes through the dev server's proxy to the backend. A proxy that cannot reach the backend answers 500, 502 or 504;
// 200 or 503 (not ready) both mean something is listening.
// It must be JSON: with no proxy target the dev server answers every unknown path with its HTML page and a 200.
const { status: ready, json: readyIsJson } = await probe('/readyz');
if (ready === 0 || ready === 500 || ready === 502 || ready === 504 || ready === 404 || !readyIsJson) {
  console.error(`The backend is not answering through the dev proxy (GET /readyz gave ${ready}${readyIsJson ? '' : ', and not JSON'}). Start the backend and check CAREFLOW_API_BASE_URL.`);
  process.exit(2);
}
console.log(`dev server: ${page}; backend /readyz: ${ready}${ready === 503 ? ' (reachable, reports not ready)' : ''}`);

const cli = resolve(root, 'node_modules/@playwright/test/cli.js');
const child = spawn(process.execPath, [cli, 'test', '-c', 'playwright.live.config.ts'], {
  cwd: root,
  stdio: 'inherit',
  env: { ...process.env, CAREFLOW_LIVE: '1', ...(record ? { CAREFLOW_LIVE_RECORD: '1' } : {}) },
});
child.on('exit', (code) => process.exit(code ?? 1));
