import createClient from 'openapi-fetch';
import type { components, paths } from './schema';

export type Me = components['schemas']['Me'];
export type ProposeMergeIn = components['schemas']['ProposeMergeIn'];

/**
 * Every operation this client may call, keyed by method name. The contract test checks that each entry
 * exists in openapi.snapshot.json and that the request the client really sends matches it. Add a client
 * method here AND to the contract test table, or the test fails.
 */
export const OPERATIONS = {
  getMe: { method: 'get', path: '/v1/iam/me' },
  getReadyz: { method: 'get', path: '/readyz' },
  proposeMerge: { method: 'post', path: '/v1/staff/merges' },
  approveMerge: { method: 'post', path: '/v1/staff/merges/{merge_id}/approve' },
  unmerge: { method: 'post', path: '/v1/staff/merges/{merge_id}/unmerge' },
} as const satisfies Record<string, { method: 'get' | 'post'; path: keyof paths }>;

export interface ApiOptions {
  baseUrl: string;
  /** Supplies the current access token, or null when signed out. Never logged. */
  getAccessToken: () => Promise<string | null> | string | null;
  /** Transport. Defaults to the global fetch; tests and the 401 layer wrap it. */
  fetch?: typeof fetch;
}

export interface WriteOptions {
  /** Reuse the same key when retrying a write so the server replays it instead of repeating it. */
  idempotencyKey?: string;
}

const newKey = (): string => globalThis.crypto.randomUUID();

export function createApi(options: ApiOptions) {
  const transport = options.fetch ?? ((input: Request) => globalThis.fetch(input));
  const client = createClient<paths>({
    baseUrl: options.baseUrl,
    fetch: async (request: Request) => {
      const token = await options.getAccessToken();
      if (token) request.headers.set('Authorization', `Bearer ${token}`);
      return transport(request);
    },
  });
  const key = (o?: WriteOptions): string => o?.idempotencyKey ?? newKey();

  return {
    getMe: () => client.GET('/v1/iam/me'),
    getReadyz: () => client.GET('/readyz'),
    proposeMerge: (body: ProposeMergeIn, o?: WriteOptions) =>
      client.POST('/v1/staff/merges', { params: { header: { 'X-Idempotency-Key': key(o) } }, body }),
    approveMerge: (mergeId: string, o?: WriteOptions) =>
      client.POST('/v1/staff/merges/{merge_id}/approve', {
        params: { path: { merge_id: mergeId }, header: { 'X-Idempotency-Key': key(o) } },
      }),
    unmerge: (mergeId: string, o?: WriteOptions) =>
      client.POST('/v1/staff/merges/{merge_id}/unmerge', {
        params: { path: { merge_id: mergeId }, header: { 'X-Idempotency-Key': key(o) } },
      }),
  };
}

export type Api = ReturnType<typeof createApi>;
