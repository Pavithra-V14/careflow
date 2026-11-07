// Finds exports nothing imports and i18n keys nothing uses. A report for a person to act on, run by hand:
//   node scripts/find-dead.mjs
// Strings counted as used: a literal key ('a.b.c'), or a key under a prefix built at run time (`a.b.${x}` or word('a.b', ...)).
// The i18n part is also a unit test (tests/unused-strings.test.ts), so a string cannot be left behind.
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';

const root = resolve(import.meta.dirname, '..');
const walk = (d) => readdirSync(d).flatMap((n) => (statSync(join(d, n)).isDirectory() ? walk(join(d, n)) : [join(d, n)]));
const src = walk(join(root, 'src')).filter((f) => /\.(ts|tsx)$/.test(f) && !f.endsWith('schema.d.ts'));
const others = [...walk(join(root, 'tests')).filter((f) => /\.(ts|tsx)$/.test(f)), ...walk(join(root, 'e2e')).filter((f) => f.endsWith('.ts'))];
const text = new Map([...src, ...others].map((f) => [f, readFileSync(f, 'utf8')]));
const slash = (p) => p.split('\\').join('/');

const deadExports = [];
for (const f of src) {
  const t = text.get(f);
  for (const m of t.matchAll(/^export (?:async )?(?:function|const|class|type|interface|enum) ([A-Za-z0-9_]+)/gm)) {
    const name = m[1];
    const word = new RegExp('\\b' + name + '\\b');
    const usedElsewhere = [...text].some(([g, u]) => g !== f && word.test(u));
    const usedInside = (t.match(new RegExp('\\b' + name + '\\b', 'g')) ?? []).length > 1;
    if (!usedElsewhere) deadExports.push(slash(relative(root, f)) + ': ' + name + (usedInside ? ' (used only inside its file: drop the export)' : ' (UNUSED)'));
  }
}

const flat = (o, p = '') => Object.entries(o).flatMap(([k, v]) => (typeof v === 'object' ? flat(v, p + k + '.') : [p + k]));
const i18nDir = join(root, 'src/shared/i18n');
const keys = readdirSync(i18nDir)
  .filter((f) => /^(?:[a-z0-9]+\.)?en\.json$/.test(f))
  .flatMap((f) => flat(JSON.parse(readFileSync(join(i18nDir, f), 'utf8'))));
const code = src.map((f) => text.get(f)).join('\n');
const prefixes = [
  ...[...code.matchAll(/`([a-zA-Z0-9_.]+)\.\$\{/g)].map((m) => m[1]),
  ...[...code.matchAll(/word\(\s*'([a-zA-Z0-9_.]+)'/g)].map((m) => m[1]),
  ...[...code.matchAll(/`([a-zA-Z0-9_.]+)\.\$\{/g)].map((m) => m[1]),
];
export const unusedKeys = keys.filter((k) => !code.includes("'" + k + "'") && !code.includes('"' + k + '"') && !code.includes('`' + k + '`') && !prefixes.some((p) => k.startsWith(p + '.')));

if (process.argv[1] && slash(process.argv[1]).endsWith('find-dead.mjs')) {
  console.log('UNUSED EXPORTS (' + deadExports.length + ')\n' + deadExports.join('\n'));
  console.log('\nUNUSED STRINGS (' + unusedKeys.length + ')\n' + unusedKeys.join('\n'));
}
