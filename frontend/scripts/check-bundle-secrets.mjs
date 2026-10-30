// Fails if any forbidden marker appears anywhere in a production build output.
// Usage: node scripts/check-bundle-secrets.mjs dist
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

export const FORBIDDEN = ['sb_secret_', 'SUPABASE_SECRET_KEY', 'DATABASE_URL', 'CAREFLOW_ENC_SECRET'];

function* walk(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) yield* walk(p);
    else yield p;
  }
}

/** Returns [{ file, marker }]; never returns the surrounding text, so a real secret is not echoed. */
export function findLeaks(dir) {
  const hits = [];
  for (const file of walk(dir)) {
    const text = readFileSync(file, 'latin1');
    for (const marker of FORBIDDEN) if (text.includes(marker)) hits.push({ file, marker });
  }
  return hits;
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) {
  const dir = resolve(process.argv[2] ?? 'dist');
  try {
    statSync(dir);
  } catch {
    console.error(`bundle secret check: ${dir} does not exist (build first)`);
    process.exit(2);
  }
  const hits = findLeaks(dir);
  if (hits.length > 0) {
    for (const h of hits) console.error(`FORBIDDEN marker ${h.marker} found in ${h.file}`);
    process.exit(1);
  }
  console.log(`bundle secret check passed: no forbidden markers in ${dir}`);
}
