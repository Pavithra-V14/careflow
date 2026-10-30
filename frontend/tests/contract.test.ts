import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { OPERATIONS, createApi, type Api, type WriteOptions, type ProposeMergeIn, type BeginUploadIn, type RegisterIn, type PatientSearchIn } from '../src/shared/api/client';

// Contract test (BUILD-SPEC 21): the client is driven for real; only the network transport is captured.
// It asserts every method targets an endpoint that exists in the backend's schema, with its required
// parameters and body fields, and that every write sends an idempotency key and reuses a supplied one.

type Json = Record<string, unknown>;
const spec = JSON.parse(readFileSync(resolve(import.meta.dirname, '../../openapi.snapshot.json'), 'utf8')) as Json;
const paths = spec.paths as Record<string, Record<string, Json>>;
const schemas = (spec.components as Json).schemas as Record<string, Json>;

function deref(node: Json): Json {
  const ref = node.$ref;
  if (typeof ref !== 'string') return node;
  return deref(schemas[ref.replace('#/components/schemas/', '')] as Json);
}

/** The smallest valid value for a schema: required fields only, the first enum word, a fixed uuid. */
function sample(schema: Json): unknown {
  const s = deref(schema);
  if (Array.isArray(s.anyOf)) return sample((s.anyOf as Json[]).find((x) => x.type !== 'null') as Json);
  if (Array.isArray(s.enum)) return s.enum[0];
  if (s.type === 'string') return s.format === 'uuid' ? '3f2b8c1e-6a4d-4e1b-9d0a-1c2d3e4f5a6b' : s.format === 'date' ? '1990-01-01' : 'x';
  if (s.type === 'integer') return 1;
  if (s.type === 'boolean') return true;
  if (s.type === 'array') return [];
  if (s.type === 'object') {
    const props = (s.properties ?? {}) as Record<string, Json>;
    const out: Json = {};
    for (const name of (s.required ?? []) as string[]) out[name] = sample(props[name] as Json);
    return out;
  }
  throw new Error(`sample(): unsupported schema ${JSON.stringify(s)}`);
}
const body = <T>(name: string) => sample({ $ref: `#/components/schemas/${name}` }) as T;

interface Captured {
  method: string;
  url: URL;
  headers: Headers;
  body: unknown;
}

function harness() {
  const calls: Captured[] = [];
  const api = createApi({
    baseUrl: 'http://api.test',
    getAccessToken: () => 'test-token-not-real',
    fetch: async (req) => {
      const request = req as Request;
      const text = await request.clone().text();
      calls.push({
        method: request.method.toLowerCase(),
        url: new URL(request.url),
        headers: request.headers,
        body: text ? (JSON.parse(text) as unknown) : undefined,
      });
      return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } });
    },
  });
  return { api, calls };
}

const ID = '3f2b8c1e-6a4d-4e1b-9d0a-1c2d3e4f5a6b';

// One real invocation per client method. TypeScript forces the right argument shapes; the keys must
// equal OPERATIONS so a new method cannot ship without a contract case.
const INVOCATIONS: Record<keyof typeof OPERATIONS, (api: Api, o?: WriteOptions) => Promise<unknown>> = {
  getMe: (api) => api.getMe(),
  getReadyz: (api) => api.getReadyz(),
  proposeMerge: (api, o) => api.proposeMerge(body<ProposeMergeIn>('ProposeMergeIn'), o),
  approveMerge: (api, o) => api.approveMerge(ID, o),
  unmerge: (api, o) => api.unmerge(ID, o),
  listReferrals: (api) => api.listReferrals(),
  openReferral: (api) => api.openReferral(ID),
  beginUpload: (api, o) => api.beginUpload(body<BeginUploadIn>('BeginUploadIn'), o),
  registerDocument: (api, o) => api.registerDocument(body<RegisterIn>('RegisterIn'), o),
  listReviewTasks: (api) => api.listReviewTasks(),
  openReviewTask: (api) => api.openReviewTask(ID),
  acknowledgeTask: (api, o) => api.acknowledgeTask(ID, o),
  resolveTask: (api, o) => api.resolveTask(ID, o),
  searchPatients: (api) => api.searchPatients(body<PatientSearchIn>('PatientSearchIn')),
  openPatient: (api) => api.openPatient(ID),
  confirmIdentity: (api, o) => api.confirmIdentity(ID, o),
  listProviders: (api) => api.listProviders(),
  listLocations: (api) => api.listLocations(),
  listVisitTypes: (api) => api.listVisitTypes(),
  listPriorityLevels: (api) => api.listPriorityLevels(),
  listHolds: (api) => api.listHolds(),
  releaseHold: (api, o) => api.releaseHold(ID, o),
  draftOffer: (api) => api.draftOffer(ID, ID),
};

const requiredParams = (op: Json) => ((op.parameters ?? []) as Json[]).filter((p) => p.required);
const isWrite = (name: keyof typeof OPERATIONS) => requiredParams(paths[OPERATIONS[name].path]?.[OPERATIONS[name].method] as Json).some((p) => p.name === 'X-Idempotency-Key');

describe('API client contract', () => {
  it('has a contract invocation for every declared operation, and no extras', () => {
    expect(Object.keys(INVOCATIONS).sort()).toEqual(Object.keys(OPERATIONS).sort());
  });

  describe.each(Object.entries(OPERATIONS))('%s', (name, op) => {
    const operation = paths[op.path]?.[op.method];

    it('targets an endpoint that exists in the schema', () => {
      expect(operation, `${op.method.toUpperCase()} ${op.path} is not in openapi.snapshot.json`).toBeDefined();
    });

    it('sends the right method and path, with every required parameter and body field', async () => {
      const { api, calls } = harness();
      await INVOCATIONS[name as keyof typeof OPERATIONS](api);
      expect(calls).toHaveLength(1);
      const call = calls[0] as Captured;
      const op_ = operation as Json;

      expect(call.method).toBe(op.method);
      expect(call.url.pathname).toBe(op.path.replace(/\{[a-z_]+\}/g, ID));

      for (const p of requiredParams(op_)) {
        if (p.in === 'header') expect(call.headers.get(p.name as string), `header ${String(p.name)}`).toBeTruthy();
        if (p.in === 'query') expect(call.url.searchParams.has(p.name as string), `query ${String(p.name)}`).toBe(true);
        if (p.in === 'path') expect(op.path).toContain(`{${String(p.name)}}`);
      }

      const bodySchema = (((op_.requestBody as Json | undefined)?.content as Json | undefined)?.['application/json'] as Json | undefined)?.schema;
      if (bodySchema) {
        const s = deref(bodySchema as Json);
        const sent = call.body as Json;
        for (const field of (s.required ?? []) as string[]) expect(sent, `body field ${field}`).toHaveProperty(field);
        if (s.additionalProperties === false) {
          const known = Object.keys((s.properties ?? {}) as Json);
          for (const field of Object.keys(sent)) expect(known, `unknown body field ${field}`).toContain(field);
        }
      } else {
        expect(call.body).toBeUndefined();
      }
    });
  });

  it('sends the bearer token on every call', async () => {
    const { api, calls } = harness();
    await api.getMe();
    expect(calls[0]?.headers.get('authorization')).toBe('Bearer test-token-not-real');
  });

  describe.each((Object.keys(OPERATIONS) as (keyof typeof OPERATIONS)[]).filter(isWrite))('write %s', (name) => {
    it('sends a fresh idempotency key each time, and reuses a supplied one (a retry replays, never repeats)', async () => {
      const { api, calls } = harness();
      await INVOCATIONS[name](api);
      await INVOCATIONS[name](api);
      await INVOCATIONS[name](api, { idempotencyKey: 'retry-key' });
      await INVOCATIONS[name](api, { idempotencyKey: 'retry-key' });
      const keys = calls.map((c) => c.headers.get('x-idempotency-key'));
      expect(keys[0]).toBeTruthy();
      expect(keys[0]).not.toBe(keys[1]);
      expect(keys[2]).toBe('retry-key');
      expect(keys[3]).toBe('retry-key');
    });
  });

  it('knows every write the schema says needs an idempotency key: none of the client writes lacks one', () => {
    for (const [name, op] of Object.entries(OPERATIONS)) {
      if (op.method === 'get') continue;
      const needs = isWrite(name as keyof typeof OPERATIONS);
      // The one POST that is a read (patient search) deliberately carries no key.
      expect(needs, `${name} (${op.path})`).toBe(name !== 'searchPatients');
    }
  });

  it('sends no idempotency key on a read', async () => {
    const { api, calls } = harness();
    await api.listReferrals();
    await api.searchPatients(body<PatientSearchIn>('PatientSearchIn'));
    expect(calls.map((c) => c.headers.get('x-idempotency-key'))).toEqual([null, null]);
  });
});
