import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { OPERATIONS, createApi, type Api, type ProposeMergeIn } from '../src/shared/api/client';

// Contract test (BUILD-SPEC 21): the client is driven for real; only the network transport is captured.
// It asserts every method targets an endpoint that exists in the backend's schema, with its required
// parameters and body fields.

type Json = Record<string, unknown>;
const spec = JSON.parse(readFileSync(resolve(import.meta.dirname, '../../openapi.snapshot.json'), 'utf8')) as Json;
const paths = spec.paths as Record<string, Record<string, Json>>;
const schemas = (spec.components as Json).schemas as Record<string, Json>;

function deref(node: Json): Json {
  const ref = node.$ref;
  if (typeof ref !== 'string') return node;
  return deref(schemas[ref.replace('#/components/schemas/', '')] as Json);
}

function sample(schema: Json): unknown {
  const s = deref(schema);
  if (s.type === 'string') return s.format === 'uuid' ? '3f2b8c1e-6a4d-4e1b-9d0a-1c2d3e4f5a6b' : 'x';
  if (s.type === 'object') {
    const props = (s.properties ?? {}) as Record<string, Json>;
    const out: Json = {};
    for (const name of (s.required ?? []) as string[]) out[name] = sample(props[name] as Json);
    return out;
  }
  throw new Error(`sample(): unsupported schema ${JSON.stringify(s)}`);
}

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
const proposeBody = () => sample({ $ref: '#/components/schemas/ProposeMergeIn' }) as ProposeMergeIn;

// One real invocation per client method. TypeScript forces the right argument shapes; the keys must
// equal OPERATIONS so a new method cannot ship without a contract case.
const INVOCATIONS: Record<keyof typeof OPERATIONS, (api: Api) => Promise<unknown>> = {
  getMe: (api) => api.getMe(),
  getReadyz: (api) => api.getReadyz(),
  proposeMerge: (api) => api.proposeMerge(proposeBody()),
  approveMerge: (api) => api.approveMerge(ID),
  unmerge: (api) => api.unmerge(ID),
};

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
      const expectedPath = op.path.replace('{merge_id}', ID);
      expect(call.url.pathname).toBe(expectedPath);

      const params = ((op_.parameters ?? []) as Json[]).filter((p) => p.required);
      for (const p of params) {
        if (p.in === 'header') expect(call.headers.get(p.name as string), `header ${String(p.name)}`).toBeTruthy();
        if (p.in === 'query') expect(call.url.searchParams.has(p.name as string), `query ${String(p.name)}`).toBe(true);
        if (p.in === 'path') expect(op.path).toContain(`{${String(p.name)}}`);
      }

      const bodySchema = (((op_.requestBody as Json | undefined)?.content as Json | undefined)?.['application/json'] as Json | undefined)?.schema;
      if (bodySchema) {
        const s = deref(bodySchema as Json);
        const body = call.body as Json;
        for (const field of (s.required ?? []) as string[]) expect(body, `body field ${field}`).toHaveProperty(field);
        if (s.additionalProperties === false) {
          const known = Object.keys((s.properties ?? {}) as Json);
          for (const field of Object.keys(body)) expect(known, `unknown body field ${field}`).toContain(field);
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

  it('gives every write a fresh idempotency key, and honours a caller-supplied one for retries', async () => {
    const { api, calls } = harness();
    await api.approveMerge(ID);
    await api.approveMerge(ID);
    await api.approveMerge(ID, { idempotencyKey: 'retry-key' });
    const keys = calls.map((c) => c.headers.get('x-idempotency-key'));
    expect(keys[0]).toBeTruthy();
    expect(keys[0]).not.toBe(keys[1]);
    expect(keys[2]).toBe('retry-key');
  });

  it('declares X-Idempotency-Key as required on every write operation in the schema the client uses', () => {
    for (const op of Object.values(OPERATIONS)) {
      if (op.method === 'get') continue;
      const names = ((paths[op.path]?.[op.method]?.parameters ?? []) as Json[]).filter((p) => p.required).map((p) => p.name);
      expect(names, op.path).toContain('X-Idempotency-Key');
    }
  });
});
