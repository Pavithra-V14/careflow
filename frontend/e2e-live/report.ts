import { OPERATIONS } from '../src/shared/api/client';
import type { Issue, Spec } from './schemaCheck';

// What a live run records for each API call, and the report built from it. NO VALUES: a call is a method, a path with its ids
// removed, a status, whether a key was sent, and the schema issues found (paths and type words only). The report can be pasted
// into a ticket without leaking a patient id, a name or a token.

export interface Call {
  /** Which screen or step was showing. */
  phase: string;
  method: string;
  /** The schema path template, e.g. /v1/staff/review-tasks/{task_id}; `?` when the backend answered a path the schema lacks. */
  template: string;
  status: number;
  hasIdempotencyKey: boolean;
  /** Names of query parameters, never their values. */
  queryNames: string[];
  activeRoleHeaderSent: boolean;
  issues: Issue[];
}

/** Statuses the console shows a fixed message for. A refusal with one of these is the backend working, not a mismatch. */
const EXPECTED_REFUSALS = new Set([401, 403, 404, 409, 422, 429, 503]);

export function documentedStatuses(spec: Spec, method: string, template: string): string[] {
  const op = spec.paths?.[template]?.[method.toLowerCase()] as { responses?: Record<string, unknown> } | undefined;
  return Object.keys(op?.responses ?? {});
}

export interface Finding {
  kind: 'unknown route' | 'unexpected status' | 'server error' | 'write without key' | 'active-role header sent' | 'schema error' | 'schema note';
  where: string;
  detail: string;
}

export function findings(spec: Spec, calls: Call[]): Finding[] {
  const out: Finding[] = [];
  const seen = new Set<string>();
  const add = (f: Finding) => {
    const k = `${f.kind}|${f.where}|${f.detail}`;
    if (!seen.has(k)) {
      seen.add(k);
      out.push(f);
    }
  };
  for (const c of calls) {
    const where = `${c.method} ${c.template}`;
    if (c.template === '?') add({ kind: 'unknown route', where: `${c.method} (path not in the schema)`, detail: `status ${c.status}, during "${c.phase}"` });
    if (c.status >= 500 && c.status !== 503) add({ kind: 'server error', where, detail: `status ${c.status}, during "${c.phase}"` });
    else if (c.template !== '?' && !EXPECTED_REFUSALS.has(c.status) && c.status >= 400) add({ kind: 'unexpected status', where, detail: `status ${c.status}, during "${c.phase}"` });
    else if (c.template !== '?' && c.status < 400 && !documentedStatuses(spec, c.method, c.template).includes(String(c.status))) add({ kind: 'unexpected status', where, detail: `status ${c.status} is not in the schema's responses (${documentedStatuses(spec, c.method, c.template).join(', ')})` });
    if (c.method !== 'GET' && c.method !== 'HEAD' && c.method !== 'OPTIONS' && !c.hasIdempotencyKey && c.template !== '/v1/staff/patients/search') add({ kind: 'write without key', where, detail: `during "${c.phase}"` });
    if (c.activeRoleHeaderSent) add({ kind: 'active-role header sent', where, detail: 'the flag should be off' });
    for (const i of c.issues) add({ kind: i.severity === 'error' ? 'schema error' : 'schema note', where, detail: `${i.path}: ${i.problem}` });
  }
  return out;
}

export function notExercised(calls: Call[]): string[] {
  const hit = new Set(calls.map((c) => `${c.method} ${c.template}`));
  return Object.values(OPERATIONS)
    .map((o) => `${o.method.toUpperCase()} ${o.path}`)
    .filter((k) => !hit.has(k));
}

export function renderReport(spec: Spec, calls: Call[], mode: string): string {
  const f = findings(spec, calls);
  const errors = f.filter((x) => x.kind !== 'schema note');
  const rows = calls.map((c) => `| ${c.phase} | ${c.method} | \`${c.template}\` | ${c.status} | ${c.queryNames.join(', ') || '–'} | ${c.method === 'GET' ? 'n/a' : c.hasIdempotencyKey ? 'yes' : 'NO'} | ${c.issues.filter((i) => i.severity === 'error').length} |`);
  return [
    '# Live run report',
    '',
    `Mode: ${mode}. ${calls.length} API calls recorded. **${errors.length} finding(s)** that may be a mismatch between the console's mocks and the backend, ${f.length - errors.length} note(s).`,
    '',
    'No values are recorded: paths have their ids removed, query parameters appear by name only, and schema issues name a field path and a type word.',
    '',
    '## Findings',
    '',
    ...(f.length === 0 ? ['None.'] : ['| Kind | Where | Detail |', '|---|---|---|', ...f.map((x) => `| ${x.kind} | \`${x.where}\` | ${x.detail.replaceAll('|', '/')} |`)]),
    '',
    '## Calls this run did not make',
    '',
    ...notExercised(calls).map((x) => `- \`${x}\``),
    '',
    '## Every call recorded',
    '',
    '| Step | Method | Path | Status | Query names | Key sent | Schema errors |',
    '|---|---|---|---|---|---|---|',
    ...rows,
    '',
  ].join('\n');
}
