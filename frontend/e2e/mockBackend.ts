import type { Page, Route } from '@playwright/test';
import mock from './mock-env.json' with { type: 'json' };

// MOCK ONLY. Fakes the Supabase Auth (GoTrue) host and the CareFlow API at the network layer so the REAL built app,
// with the REAL supabase-js, runs end to end in Chromium without any account, key or network. The response shapes
// are this file's reading of supabase-js behaviour, NOT observed from a live Supabase project. Tokens are synthetic,
// unsigned and obviously fake. Nothing here is a credential.

export const GOOD_CODE = '123456';
export const GOOD_PASSWORD = 'correct horse';
const ORIGIN = 'http://127.0.0.1:4173';

export interface Scenario {
  /** The account already has a verified authenticator factor. */
  enrolled?: boolean;
  /** Roles returned by GET /v1/iam/me. Extra keys (clinic_name, clinic_origin) are passed through. */
  roles?: Record<string, unknown>[];
  /** Override /v1/iam/me with an error status. */
  meStatus?: number;
}

const b64url = (o: unknown) => Buffer.from(JSON.stringify(o)).toString('base64url');
function jwt(aal: 'aal1' | 'aal2'): string {
  const iat = Math.floor(Date.now() / 1000);
  const amr = aal === 'aal2' ? [{ method: 'password', timestamp: iat - 5 }, { method: 'totp', timestamp: iat }] : [{ method: 'password', timestamp: iat }];
  return `${b64url({ alg: 'none', typ: 'JWT' })}.${b64url({ aal, amr, iat, exp: iat + 3600, sub: 'mock-user', role: 'authenticated', session_id: 'mock-session' })}.mock-signature`;
}

export async function installMock(page: Page, scenario: Scenario = {}) {
  const factors: { id: string; factor_type: 'totp'; status: 'verified' | 'unverified'; friendly_name: string; created_at: string; updated_at: string }[] = scenario.enrolled
    ? [{ id: 'factor-1', factor_type: 'totp', status: 'verified', friendly_name: 'mock', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' }]
    : [];
  const user = () => ({ id: 'mock-user', aud: 'authenticated', role: 'authenticated', email: 'staff@example.invalid', app_metadata: {}, user_metadata: {}, created_at: '2026-01-01T00:00:00Z', factors: [...factors] });
  const session = (aal: 'aal1' | 'aal2') => ({ access_token: jwt(aal), token_type: 'bearer', expires_in: 3600, expires_at: Math.floor(Date.now() / 1000) + 3600, refresh_token: 'mock-refresh', user: user() });
  let aal: 'aal1' | 'aal2' = 'aal1';
  const log: string[] = [];

  const cors = (route: Route) => {
    const req = route.request();
    return {
      'access-control-allow-origin': ORIGIN,
      'access-control-allow-headers': req.headers()['access-control-request-headers'] ?? '*',
      'access-control-allow-methods': 'GET,POST,PUT,DELETE,OPTIONS',
      'access-control-expose-headers': 'WWW-Authenticate',
      'content-type': 'application/json',
    };
  };
  const reply = (route: Route, status: number, body: unknown) => route.fulfill({ status, headers: cors(route), body: body === undefined ? '' : JSON.stringify(body) });

  await page.route(`${mock.SUPABASE_URL}/**`, async (route) => {
    const req = route.request();
    if (req.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: cors(route) });
    const url = new URL(req.url());
    const path = url.pathname.replace('/auth/v1', '');
    log.push(`${req.method()} ${path}`);
    const body = req.postData() ? (JSON.parse(req.postData() as string) as Record<string, string>) : {};

    if (req.method() === 'POST' && path === '/token' && url.searchParams.get('grant_type') === 'password') {
      if (body.password !== GOOD_PASSWORD) return reply(route, 400, { code: 400, error_code: 'invalid_credentials', msg: 'Invalid login credentials' });
      aal = 'aal1';
      return reply(route, 200, session('aal1'));
    }
    if (req.method() === 'POST' && path === '/token' && url.searchParams.get('grant_type') === 'refresh_token') return reply(route, 200, session(aal));
    if (req.method() === 'GET' && path === '/user') return reply(route, 200, user());
    if (req.method() === 'POST' && path === '/factors') {
      factors.push({ id: 'factor-new', factor_type: 'totp', status: 'unverified', friendly_name: 'mock-new', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' });
      return reply(route, 200, {
        id: 'factor-new',
        type: 'totp',
        friendly_name: 'mock-new',
        totp: {
          qr_code: '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200" viewBox="0 0 8 8"><rect width="8" height="8" fill="white"/><rect x="1" y="1" width="2" height="2"/><rect x="5" y="1" width="2" height="2"/><rect x="1" y="5" width="2" height="2"/><rect x="4" y="4" width="1" height="1"/></svg>',
          secret: 'MOCKSECRETKEY234',
          uri: 'otpauth://totp/mock',
        },
      });
    }
    const challenge = /^\/factors\/([^/]+)\/challenge$/.exec(path);
    if (req.method() === 'POST' && challenge) return reply(route, 200, { id: 'challenge-1', type: 'totp', expires_at: Math.floor(Date.now() / 1000) + 300 });
    const verify = /^\/factors\/([^/]+)\/verify$/.exec(path);
    if (req.method() === 'POST' && verify) {
      if (body.code !== GOOD_CODE) return reply(route, 400, { code: 400, error_code: 'mfa_verification_failed', msg: 'Invalid TOTP code entered' });
      const f = factors.find((x) => x.id === verify[1]);
      if (f) f.status = 'verified';
      aal = 'aal2';
      return reply(route, 200, session('aal2'));
    }
    const unenroll = /^\/factors\/([^/]+)$/.exec(path);
    if (req.method() === 'DELETE' && unenroll) {
      const i = factors.findIndex((x) => x.id === unenroll[1]);
      if (i >= 0) factors.splice(i, 1);
      return reply(route, 200, { id: unenroll[1] });
    }
    if (req.method() === 'POST' && path === '/logout') return route.fulfill({ status: 204, headers: cors(route) });
    return reply(route, 404, { msg: 'mock: unhandled' });
  });

  await page.route(`${mock.CAREFLOW_API_BASE_URL}/**`, async (route) => {
    const req = route.request();
    if (req.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: cors(route) });
    const path = new URL(req.url()).pathname;
    log.push(`API ${req.method()} ${path}`);
    if (!req.headers()['authorization']?.startsWith('Bearer ')) return reply(route, 401, { detail: 'not authenticated' });
    if (path === '/v1/iam/me') {
      if (scenario.meStatus) return reply(route, scenario.meStatus, { detail: 'nope' });
      return reply(route, 200, { account_id: '9d0a1c2d-3e4f-4a6b-8c1e-3f2b6a4d4e1b', account_type: 'staff', status: 'active', roles: scenario.roles ?? [{ clinic_id: '3f2b8c1e-6a4d-4e1b-9d0a-1c2d3e4f5a6b', role: 'front_desk' }] });
    }
    return reply(route, 404, {});
  });

  return { log };
}
