import { readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { checkAgainst, matchTemplate, responseSchema, type Spec } from '../e2e-live/schemaCheck';
import { findings, notExercised, renderReport, type Call } from '../e2e-live/report';
import { OPERATIONS } from '../src/shared/api/client';

// Tests of the LIVE-run tooling itself (e2e-live/). The live run is manual and never in CI, so what it relies on must be
// proven here: the schema checker finds real mismatches, never repeats a value, and the run cannot start from CI.

const root = resolve(import.meta.dirname, '..');
const spec = JSON.parse(readFileSync(resolve(root, '../openapi.snapshot.json'), 'utf8')) as Spec;
const ID = '3f2b8c1e-6a4d-4e1b-9d0a-1c2d3e4f5a6b';

const referral = {
  clinic_id: ID, content_type: 'application/pdf', created_at: '2026-10-30T05:00:00Z', decided: null, document_id: ID,
  open_holds: 0, proposal_id: null, proposal_visible: false, registered_by: ID, run_state: null, size_bytes: 10,
};
const listSchema = () => responseSchema(spec, 'GET', '/v1/staff/referrals') as Record<string, unknown>;

describe('schema checker', () => {
  it('accepts a response that follows the schema', () => {
    expect(checkAgainst(spec, listSchema(), { items: [referral], limit: 100, has_more: false }).filter((i) => i.severity === 'error')).toEqual([]);
  });

  it('finds a missing required field, with its path', () => {
    const broken: Partial<typeof referral> = { ...referral };
    delete broken.size_bytes;
    const issues = checkAgainst(spec, listSchema(), { items: [broken], limit: 100, has_more: false });
    expect(issues).toContainEqual({ path: '$.items[0].size_bytes', problem: 'required field is missing', severity: 'error' });
  });

  it('finds a wrong type, a null where none is allowed, and a bad uuid and date-time', () => {
    const issues = checkAgainst(spec, listSchema(), { items: [{ ...referral, size_bytes: 'ten', open_holds: null, document_id: 'nope', created_at: 'yesterday' }], limit: 100, has_more: false });
    const text = issues.map((i) => `${i.path}: ${i.problem}`).join('\n');
    expect(text).toContain('$.items[0].size_bytes: expected integer, got string');
    expect(text).toContain('$.items[0].open_holds: expected integer, got null');
    expect(text).toContain('$.items[0].document_id: a string that is not a uuid');
    expect(text).toContain('$.items[0].created_at: a string that is not a date-time');
  });

  it('accepts null where the schema allows it, and finds a word outside a closed list WITHOUT repeating it', () => {
    const secret = 'PLANTED-WORD-NOT-IN-LIST';
    const issues = checkAgainst(spec, listSchema(), { items: [{ ...referral, content_type: secret }], limit: 100, has_more: false });
    expect(issues.some((i) => i.path === '$.items[0].content_type')).toBe(true);
    expect(JSON.stringify(issues)).not.toContain(secret);
    expect(issues.filter((i) => i.path === '$.items[0].run_state')).toEqual([]); // null is allowed there
  });

  it('reports a field the schema forbids as an error, and one it merely does not mention as a note', () => {
    const forbidden = checkAgainst(spec, listSchema(), { items: [{ ...referral, brand_new_field: 1 }], limit: 100, has_more: false });
    expect(forbidden).toContainEqual({ path: '$.items[0].brand_new_field', problem: 'field not allowed by the schema', severity: 'error' });
    const open = { type: 'object', properties: { a: { type: 'string' } } };
    expect(checkAgainst(spec, open, { a: 'x', b: 1 })).toEqual([{ path: '$.b', problem: 'field the schema does not mention', severity: 'info' }]);
  });

  it('never puts a value from the response in an issue (a planted name, id and time are absent)', () => {
    const planted = { ...referral, document_id: 'PLANTED-NAME-Person', created_at: 'PLANTED-TIME', size_bytes: 'PLANTED-SIZE', brand_new_field: 'PLANTED-EXTRA' };
    const issues = checkAgainst(spec, listSchema(), { items: [planted], limit: 100, has_more: false });
    expect(issues.length).toBeGreaterThan(0);
    expect(JSON.stringify(issues)).not.toContain('PLANTED');
  });

  it('matches a concrete path to its schema template, ids included', () => {
    expect(matchTemplate(spec, `/v1/staff/review-tasks/${ID}/acknowledge`)).toBe('/v1/staff/review-tasks/{task_id}/acknowledge');
    expect(matchTemplate(spec, '/v1/staff/referrals')).toBe('/v1/staff/referrals');
    expect(matchTemplate(spec, '/v1/staff/nothing-here')).toBeUndefined();
  });
});

const call = (over: Partial<Call>): Call => ({ phase: 'step', method: 'GET', template: '/v1/staff/referrals', status: 200, hasIdempotencyKey: false, queryNames: [], activeRoleHeaderSent: false, issues: [], ...over });

describe('live report', () => {
  it('treats the refusals the console shows fixed messages for as the backend working, not as a mismatch', () => {
    for (const status of [401, 403, 404, 409, 422, 429, 503]) expect(findings(spec, [call({ status })]), String(status)).toEqual([]);
  });

  it('flags a 500, a status the schema does not list, an unknown route, an unkeyed write and a sent active-role header', () => {
    const f = findings(spec, [
      call({ status: 500 }),
      call({ template: '/v1/staff/patients/{patient_id}', status: 202 }),
      call({ template: '?', status: 200 }),
      call({ method: 'POST', template: '/v1/staff/review-tasks/{task_id}/acknowledge', hasIdempotencyKey: false }),
      call({ activeRoleHeaderSent: true }),
    ]).map((x) => x.kind);
    expect(f).toEqual(expect.arrayContaining(['server error', 'unexpected status', 'unknown route', 'write without key', 'active-role header sent']));
  });

  it('does not ask a patient search for a key (it is a read)', () => {
    expect(findings(spec, [call({ method: 'POST', template: '/v1/staff/patients/search', hasIdempotencyKey: false })])).toEqual([]);
  });

  it('lists the client operations a run never made, and renders no values', () => {
    const calls = [call({ template: '/v1/staff/referrals' })];
    const missing = notExercised(calls);
    expect(missing).not.toContain('GET /v1/staff/referrals');
    expect(missing).toContain('GET /v1/staff/review-tasks');
    expect(missing.length).toBe(Object.keys(OPERATIONS).length - 1);
    const text = renderReport(spec, [call({ issues: [{ path: '$.x', problem: 'required field is missing', severity: 'error' }] })], 'test');
    expect(text).toContain('1 finding(s)');
    expect(text).toContain('No values are recorded');
  });
});

describe('the live run cannot run from CI or by accident', () => {
  const read = (p: string) => readFileSync(resolve(root, p), 'utf8');
  const workflows = readdirSync(resolve(root, '../.github/workflows')).map((f) => ({ f, text: readFileSync(resolve(root, '../.github/workflows', f), 'utf8') }));

  it('no workflow mentions the live run', () => {
    for (const { f, text } of workflows) expect(text, f).not.toMatch(/e2e:live|e2e-live|playwright\.live|CAREFLOW_LIVE/);
  });

  it('the default browser config and the default scripts never reach the live specs', () => {
    expect(read('playwright.config.ts')).toMatch(/testDir: 'e2e'/);
    const scripts = (JSON.parse(read('package.json')) as { scripts: Record<string, string> }).scripts;
    for (const [name, cmd] of Object.entries(scripts)) if (name !== 'e2e:live') expect(cmd, name).not.toMatch(/e2e-live|playwright\.live/);
    expect(scripts['e2e:live']).toBe('node scripts/e2e-live.mjs');
  });

  it('every live spec is skipped unless the run was started on purpose', () => {
    for (const f of readdirSync(resolve(root, 'e2e-live')).filter((n) => n.endsWith('.spec.ts'))) expect(read(`e2e-live/${f}`), f).toMatch(/test\.skip\(!LIVE/);
  });

  it('the live run never types a credential and the read-only walk makes no write', () => {
    const spec = read('e2e-live/live.spec.ts');
    expect(spec).not.toMatch(/\.fill\(|keyboard\.type|process\.env\.(LIVE_)?(PASSWORD|EMAIL|TOTP)/i);
    expect(spec).toContain('the read-only walk made a write');
  });

  it('the generated report and run output are git-ignored', () => {
    const ignore = read('.gitignore');
    expect(ignore).toContain('live-report/');
    expect(ignore).toContain('test-results-live/');
  });
});
