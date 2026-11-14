import type { Page, Route } from '@playwright/test';
import mock from './mock-env.json' with { type: 'json' };

// MOCK ONLY. A fake Supabase phone-code sign-in and a small stateful fake of the patient portal routes, written from openapi.snapshot.json.
// It proves this app's behaviour, not the backend's. Every error body carries LEAK so a test can prove backend text never reaches the page.

export const LEAK = 'LEAK-SENTINEL-backend-text';
export const GOOD_PHONE = '+910000000000';
export const GOOD_PHONE_CODE = '123456';
const ORIGIN = 'http://127.0.0.1:4173';

export interface PortalScenario {
  /** Where the account starts: no patient account yet (me answers 403), or one of the stages. */
  start?: 'none' | 'registered' | 'pending_activation' | 'active';
  /** The care-delivery notice is already granted. */
  granted?: boolean;
  /** The patient is already linked to a record. */
  bound?: boolean;
  /** The voice setting starts on. */
  voiceOn?: boolean;
  /** Opening a voice session answers 503 (voice not set up) instead of opening one. */
  voiceUnavailable?: boolean;
}

const b64url = (o: unknown) => Buffer.from(JSON.stringify(o)).toString('base64url');
function jwt(): string {
  const iat = Math.floor(Date.now() / 1000);
  return `${b64url({ alg: 'none', typ: 'JWT' })}.${b64url({ aal: 'aal1', iat, exp: iat + 3600, sub: 'mock-patient', role: 'authenticated', session_id: 'mock-session' })}.mock-signature`;
}

export async function installPortalMock(page: Page, scenario: PortalScenario = {}) {
  let stage: 'none' | 'registered' | 'pending_activation' | 'active' = scenario.start ?? 'none';
  let granted = scenario.granted ?? false;
  let bound = scenario.bound ?? false;
  let voiceOn = scenario.voiceOn ?? false;
  const calls: { method: string; path: string; key: string | null; body: unknown }[] = [];
  const session = () => ({ access_token: jwt(), token_type: 'bearer', expires_in: 3600, expires_at: Math.floor(Date.now() / 1000) + 3600, refresh_token: 'mock-refresh', user: { id: 'mock-patient', aud: 'authenticated', role: 'authenticated', phone: GOOD_PHONE, app_metadata: {}, user_metadata: {}, created_at: '2026-01-01T00:00:00Z' } });

  const cors = (route: Route) => ({
    'access-control-allow-origin': ORIGIN,
    'access-control-allow-headers': route.request().headers()['access-control-request-headers'] ?? '*',
    'access-control-allow-methods': 'GET,POST,PUT,DELETE,OPTIONS',
    'access-control-expose-headers': 'WWW-Authenticate',
    'content-type': 'application/json',
  });
  const reply = (route: Route, status: number, body: unknown) => route.fulfill({ status, headers: cors(route), body: body === undefined ? '' : JSON.stringify(body) });
  const refuse = (route: Route, status: number) => reply(route, status, { detail: LEAK });

  await page.route(`${mock.SUPABASE_URL}/**`, async (route) => {
    const req = route.request();
    if (req.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: cors(route) });
    const path = new URL(req.url()).pathname.replace('/auth/v1', '');
    const body = req.postData() ? (JSON.parse(req.postData() as string) as Record<string, string>) : {};
    if (req.method() === 'POST' && path === '/otp') return body.phone === GOOD_PHONE ? reply(route, 200, {}) : reply(route, 400, { code: 400, msg: LEAK });
    if (req.method() === 'POST' && path === '/verify') return body.token === GOOD_PHONE_CODE ? reply(route, 200, session()) : reply(route, 400, { code: 400, error_code: 'otp_expired', msg: LEAK });
    if (req.method() === 'POST' && path === '/token') return reply(route, 200, session());
    if (req.method() === 'GET' && path === '/user') return reply(route, 200, session().user);
    if (req.method() === 'POST' && path === '/logout') return route.fulfill({ status: 204, headers: cors(route) });
    return reply(route, 404, { msg: 'mock: unhandled' });
  });

  await page.route(`${mock.CAREFLOW_API_BASE_URL}/**`, async (route) => {
    const req = route.request();
    if (req.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: cors(route) });
    const path = new URL(req.url()).pathname;
    const method = req.method();
    const body = req.postData() ? (JSON.parse(req.postData() as string) as unknown) : undefined;
    calls.push({ method, path, key: req.headers()['x-idempotency-key'] ?? null, body });
    // The public routes need no sign-in (a grievance without signing in, MOCK ONLY).
    if (method === 'POST' && path === '/public/grievances') return reply(route, 201, { created: true, grievance_id: '33333333-3333-4333-8333-333333333333' });
    if (!req.headers()['authorization']?.startsWith('Bearer ')) return reply(route, 401, { detail: 'not authenticated' });

    if (method === 'GET' && path === '/public/clinic') return reply(route, 200, { clinic_id: '3f2b8c1e-6a4d-4e1b-9d0a-1c2d3e4f5a6b', name: 'Placeholder Hospital' });
    if (method === 'GET' && path === '/v1/portal/grievance-categories') return reply(route, 200, { categories: ['access', 'service_quality'] });
    if (method === 'GET' && path === '/v1/portal/me') {
      if (stage === 'none') return refuse(route, 403);
      return reply(route, 200, { bound_to_a_patient_record: bound, consents: { care_delivery: granted ? true : null }, stage, status: stage === 'active' ? 'active' : 'invited' });
    }
    if (method === 'POST' && path === '/v1/portal/register') {
      const exists = stage !== 'none';
      if (!exists) stage = 'registered';
      return reply(route, 201, { result: exists ? 'exists' : 'registered' });
    }
    if (method === 'GET' && path === '/v1/portal/consents') {
      return reply(route, 200, { notices: [{ body: 'Notice line one.\nNotice line two <b>not markup</b>.', decision: granted ? true : null, notice_id: '11111111-1111-4111-8111-111111111111', label: 'Placeholder label', purpose: 'care_delivery', version: 3 }, { body: 'Voice notice text (mock).', decision: null, notice_id: '55555555-5555-4555-8555-555555555555', label: 'Voice notice (mock)', purpose: 'voice_session', version: 1 }] });
    }
    if (method === 'POST' && path === '/v1/portal/consents') {
      const b = body as { action?: string };
      granted = b.action === 'granted';
      return reply(route, 201, { recorded: true });
    }
    if (method === 'POST' && path === '/v1/portal/activate') {
      if (!granted) return reply(route, 200, { result: 'consent_required' });
      stage = 'active';
      return reply(route, 200, { result: 'activated' });
    }
    if (method === 'POST' && path === '/v1/portal/claims') {
      bound = true;
      return reply(route, 200, { outcome: 'claimed' });
    }
    if (method === 'GET' && path === '/v1/portal/grievances') return reply(route, 200, { grievances: [{ category: 'service', clinic_id: '3f2b8c1e-6a4d-4e1b-9d0a-1c2d3e4f5a6b', created_at: '2026-11-09T05:00:00Z', erased: false, grievance_id: '22222222-2222-4222-8222-222222222222', status: 'open', text: 'Earlier words' }] });
    if (method === 'POST' && path === '/v1/portal/grievances') return reply(route, 201, { created: true, grievance_id: '33333333-3333-4333-8333-333333333333' });
    if (method === 'GET' && path === '/v1/portal/my-data') {
      return reply(route, 200, { account_status: 'active', bookings: [], consents: [{ action: 'granted', notice_version: 3, occurred_at: '2026-11-09T05:00:00Z', purpose: 'care_delivery' }], grievances: [], messages: [], records: [], stage: 'active', truncated: false });
    }
    if (method === 'GET' && path === '/v1/portal/profile') return reply(route, 200, { display_name: 'Placeholder Person', languages: ['en', 'ta'], preferred_language: null, sex: null, sex_words: ['female', 'male'] });
    if (method === 'PUT' && path === '/v1/portal/profile') return reply(route, 200, { display_name: 'Placeholder Person', languages: ['en', 'ta'], ...(body as object), sex_words: ['female', 'male'] });
    if (method === 'GET' && path === '/v1/portal/notifications') return reply(route, 200, { notifications: [{ created_at: '2026-11-09T05:00:00Z', kind: 'request_update', notification_id: '66666666-6666-4666-8666-666666666666', read: false, ref: null }, { created_at: '2026-11-08T05:00:00Z', kind: 'offer_sent', notification_id: '77777777-7777-4777-8777-777777777777', read: true, ref: null }] });
    if (method === 'POST' && /^\/v1\/portal\/notifications\/[^/]+\/read$/.test(path)) return reply(route, 200, { changed: true, notification_id: path.split('/')[4] });
    if (method === 'GET' && path === '/v1/portal/requests') return reply(route, 200, { requests: [{ clinic_id: '3f2b8c1e-6a4d-4e1b-9d0a-1c2d3e4f5a6b', created_at: '2026-11-09T05:00:00Z', field: 'phone', kind: 'correction', request_id: '88888888-8888-4888-8888-888888888888', status: 'open', updated_at: '2026-11-09T05:00:00Z' }] });
    if (method === 'POST' && path === '/v1/portal/requests') return reply(route, 201, { created: true, request_id: '88888888-8888-4888-8888-888888888888' });
    if (method === 'GET' && path === '/v1/portal/voice/preference') return reply(route, 200, { voice_enabled: voiceOn });
    if (method === 'PUT' && path === '/v1/portal/voice/preference') {
      voiceOn = (body as { voice_enabled: boolean }).voice_enabled;
      return reply(route, 200, { voice_enabled: voiceOn });
    }
    if (method === 'POST' && path === '/v1/portal/voice/sessions') return scenario.voiceUnavailable ? refuse(route, 503) : reply(route, 201, { created: true, mic: 'ok', session_id: '44444444-4444-4444-8444-444444444444' });
    return refuse(route, 404);
  });

  return { calls, stage: () => stage };
}
