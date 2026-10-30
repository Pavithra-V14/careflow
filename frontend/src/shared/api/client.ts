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
  /** Transport. Defaults to the global fetch; tests wrap it. */
  fetch?: typeof fetch;
  /**
   * Called when the API answers 401 with `WWW-Authenticate: step-up`. Resolves true only if the user just
   * completed a fresh second-factor verification; the request is then retried ONCE with the new token and the
   * same idempotency key. Resolving false (cancelled, failed) returns the original 401 to the caller.
   */
  onStepUp?: () => Promise<boolean>;
  /** Called for every other 401. It receives no reason on purpose: the UI must not reveal one. */
  onUnauthorized?: () => void;
}

/** True only for the exact step-up challenge the backend sends. Anything else is an ordinary 401. */
export function isStepUpChallenge(res: Response): boolean {
  if (res.status !== 401) return false;
  return (res.headers.get('www-authenticate') ?? '').trim().split(/[\s,]/)[0]?.toLowerCase() === 'step-up';
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
      // Cloned before the first send: a request body can be read only once, and the retry needs it.
      const retry = options.onStepUp ? request.clone() : null;
      const authorize = async (r: Request) => {
        const token = await options.getAccessToken();
        if (token) r.headers.set('Authorization', `Bearer ${token}`);
      };
      await authorize(request);
      const first = await transport(request);
      if (first.status !== 401) return first;

      if (isStepUpChallenge(first) && options.onStepUp && retry) {
        if (!(await options.onStepUp())) return first;
        await authorize(retry); // the token minted by the fresh verification
        const second = await transport(retry);
        // One retry only. A second step-up challenge is returned to the caller, never looped on.
        if (second.status === 401 && !isStepUpChallenge(second)) options.onUnauthorized?.();
        return second;
      }
      options.onUnauthorized?.();
      return first;
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
