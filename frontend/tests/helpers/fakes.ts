import { vi } from 'vitest';
import type { Aal, AuthPort, TotpEnrolment } from '../../src/staff/auth/port';

// In-memory stand-ins. Nothing here is a real token, key or account: tokens are synthetic and unsigned.
// Every flow that uses these is MOCK-ONLY evidence: it proves this app's logic, not Supabase's behaviour.

export const GOOD_CODE = '123456';
export const GOOD_PASSWORD = 'correct horse';

const b64url = (o: unknown) => btoa(JSON.stringify(o)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
export function fakeJwt(payload: Record<string, unknown>): string {
  return `${b64url({ alg: 'none', typ: 'JWT' })}.${b64url(payload)}.not-a-signature`;
}

export interface FakePortOptions {
  /** Start with an enrolled, verified authenticator factor. */
  enrolled?: boolean;
  /** Start already signed in at this level. */
  signedInAt?: Aal;
}

export function makeFakePort(options: FakePortOptions = {}) {
  let session: { aal: Aal; token: string } | null = null;
  let verifiedFactor: string | null = options.enrolled || options.signedInAt === 'aal2' ? 'factor-1' : null;
  let pendingFactor: string | null = null;
  let tokenSeq = 0;
  const listeners = new Set<() => void>();
  const mint = (aal: Aal) => ({
    aal,
    token: fakeJwt({ aal, amr: aal === 'aal2' ? [{ method: 'password', timestamp: 1 }, { method: 'totp', timestamp: 2 }] : [{ method: 'password', timestamp: 1 }], iat: 1, seq: ++tokenSeq }),
  });
  if (options.signedInAt) session = mint(options.signedInAt);

  const port = {
    getAccessToken: vi.fn(async () => session?.token ?? null),
    signInWithPassword: vi.fn(async (email: string, password: string) => {
      if (!email || password !== GOOD_PASSWORD) return false;
      session = mint('aal1');
      return true;
    }),
    getAssurance: vi.fn(async () => ({
      current: session ? session.aal : null,
      next: session ? (verifiedFactor ? ('aal2' as Aal) : ('aal1' as Aal)) : null,
    })),
    listVerifiedTotpFactorIds: vi.fn(async () => (verifiedFactor ? [verifiedFactor] : [])),
    enrollTotp: vi.fn(async (): Promise<TotpEnrolment | null> => {
      pendingFactor = 'factor-new';
      return { factorId: pendingFactor, qrCodeSrc: 'data:image/svg+xml;utf-8,<svg xmlns="http://www.w3.org/2000/svg"/>', secret: 'FAKESECRETKEY234' };
    }),
    abandonEnrolment: vi.fn(async () => {
      pendingFactor = null;
    }),
    verifyTotp: vi.fn(async (factorId: string, code: string) => {
      if (!session || code !== GOOD_CODE || (factorId !== verifiedFactor && factorId !== pendingFactor)) return false;
      verifiedFactor = factorId;
      pendingFactor = null;
      session = mint('aal2');
      return true;
    }),
    refreshSession: vi.fn(async () => {
      if (!session) return false;
      session = { ...session, token: fakeJwt({ aal: session.aal, amr: [{ method: 'totp', timestamp: 2 }], iat: 2 }) };
      return true;
    }),
    signOut: vi.fn(async () => {
      session = null;
    }),
    onSignedOut: vi.fn((listener: () => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    }),
  } satisfies AuthPort;

  return {
    port,
    /** Simulates the session ending elsewhere (another tab, a failed refresh). */
    endSessionElsewhere() {
      session = null;
      for (const l of listeners) l();
    },
    currentToken: () => session?.token ?? null,
  };
}

export type FakeApiRequest = {
  method: string;
  /** Host plus path, so a request to the storage host is told apart from one to the API. */
  host: string;
  path: string;
  query: URLSearchParams;
  auth: string | null;
  key: string | null;
  activeRole: string | null;
  contentType: string | null;
  body: unknown;
};

/**
 * Replaces global fetch with a fake. `respond` returns the Response for each request, in order of arrival. It accepts both
 * `fetch(Request)` (the API client) and `fetch(url, init)` (the storage upload), so one fake serves both.
 */
export function stubApi(respond: (req: FakeApiRequest, n: number) => Response | Promise<Response>) {
  const calls: FakeApiRequest[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: Request | string, init?: RequestInit) => {
      // A jsdom File is not a Node body type, so a binary upload body is recorded as a marker and not passed to Request.
      const binary = init?.body !== undefined && init.body !== null && typeof init.body !== 'string';
      const request = typeof input === 'string' ? new Request(input, binary ? { ...init, body: null } : init) : input;
      const url = new URL(request.url);
      const text = request.method === 'GET' || request.method === 'HEAD' ? '' : await request.clone().text();
      let body: unknown = binary ? '[binary body]' : text === '' ? undefined : text;
      try {
        if (text.startsWith('{')) body = JSON.parse(text);
      } catch {
        /* keep the text */
      }
      const call: FakeApiRequest = {
        method: request.method.toLowerCase(),
        host: url.host,
        path: url.pathname,
        query: url.searchParams,
        auth: request.headers.get('authorization'),
        key: request.headers.get('x-idempotency-key'),
        activeRole: request.headers.get('x-careflow-active-role'),
        contentType: request.headers.get('content-type'),
        body,
      };
      calls.push(call);
      return respond(call, calls.length);
    }),
  );
  return calls;
}

export const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json', ...headers } });

export const ACCOUNT_ID = '9d0a1c2d-3e4f-4a6b-8c1e-3f2b6a4d4e1b';
export const CLINIC_A = '3f2b8c1e-6a4d-4e1b-9d0a-1c2d3e4f5a6b';
export const CLINIC_B = '7a7a7a7a-6a4d-4e1b-9d0a-1c2d3e4f5a6b';

export const meBody = (roles: Record<string, unknown>[]) => ({ account_id: ACCOUNT_ID, account_type: 'staff', status: 'active', roles });
