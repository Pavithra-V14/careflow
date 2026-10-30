// Builds the app with MOCK origins (e2e/mock-env.json) into dist-e2e and serves it with `vite preview`, so
// Playwright can fake the Supabase and API hosts at the network layer. Nothing here is a real key or host.
import { readFileSync } from 'node:fs';
import { spawn, spawnSync } from 'node:child_process';
import { resolve } from 'node:path';

const root = resolve(import.meta.dirname, '..');
const vite = resolve(root, 'node_modules/vite/bin/vite.js');
const mock = JSON.parse(readFileSync(resolve(root, 'e2e/mock-env.json'), 'utf8'));
const env = { ...process.env, ...mock };

const build = spawnSync(process.execPath, [vite, 'build', '--outDir', 'dist-e2e', '--emptyOutDir'], { cwd: root, env, stdio: 'inherit' });
if (build.status !== 0) process.exit(build.status ?? 1);

const preview = spawn(process.execPath, [vite, 'preview', '--outDir', 'dist-e2e', '--host', '127.0.0.1', '--port', '4173', '--strictPort'], {
  cwd: root,
  env,
  stdio: 'inherit',
});
const stop = () => preview.kill();
process.on('SIGINT', stop);
process.on('SIGTERM', stop);
preview.on('exit', (code) => process.exit(code ?? 0));
