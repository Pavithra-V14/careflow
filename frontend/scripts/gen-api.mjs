// Regenerates src/shared/api/schema.d.ts from the backend's checked-in openapi.snapshot.json.
// `--check` regenerates into memory and exits 1 if the committed file differs (CI staleness gate).
import { readFileSync, writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

const root = resolve(import.meta.dirname, '..');
const snapshot = resolve(root, '..', 'openapi.snapshot.json');
const target = resolve(root, 'src/shared/api/schema.d.ts');
const bin = resolve(root, 'node_modules/openapi-typescript/bin/cli.js');

const tmp = mkdtempSync(join(tmpdir(), 'careflow-api-'));
try {
  const out = join(tmp, 'schema.d.ts');
  execFileSync(process.execPath, [bin, snapshot, '-o', out], { stdio: 'pipe' });
  const fresh = readFileSync(out, 'utf8');
  if (process.argv.includes('--check')) {
    const committed = readFileSync(target, 'utf8');
    if (committed !== fresh) {
      console.error('schema.d.ts is stale relative to openapi.snapshot.json. Run: npm run gen:api');
      process.exit(1);
    }
    console.log('schema.d.ts matches openapi.snapshot.json');
  } else {
    writeFileSync(target, fresh);
    console.log('wrote src/shared/api/schema.d.ts');
  }
} finally {
  rmSync(tmp, { recursive: true, force: true });
}
