import { render, screen } from '@testing-library/react';
import { App } from '../../src/staff/App';
import { initI18n, setLanguage } from '../../src/shared/i18n';
import { CLINIC_A, json, makeFakePort, meBody, stubApi, type FakeApiRequest } from './fakes';

// MOCK-ONLY: drives the real console against a fake backend written from openapi.snapshot.json. It proves this app's
// behaviour, not the backend's. Every unknown route and every error body carries LEAK, and tests assert it never shows.
export const LEAK = 'LEAK-SENTINEL-backend-text';

export const ID = {
  doc: '11111111-1111-4111-8111-111111111111',
  doc2: '22222222-2222-4222-8222-222222222222',
  task: '33333333-3333-4333-8333-333333333333',
  task2: '44444444-4444-4444-8444-444444444444',
  task3: '55555555-5555-4555-8555-555555555555',
  patient: '66666666-6666-4666-8666-666666666666',
  patient2: '77777777-7777-4777-8777-777777777777',
  hold: '88888888-8888-4888-8888-888888888888',
  slot: '99999999-9999-4999-8999-999999999999',
  visitType: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  user: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
  user2: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
};

export type Handler = (req: FakeApiRequest, match: RegExpMatchArray) => Response | Promise<Response>;
export type Routes = [method: string, path: RegExp | string, handler: Handler][];

export const list = <T,>(items: T[], more = false) => json({ items, limit: 100, has_more: more });
export const fail = (status: number, headers: Record<string, string> = {}) => json({ detail: LEAK }, status, headers);

/** A fake backend: first matching route wins; /v1/iam/me is answered for the given roles unless overridden. */
export function backend(routes: Routes, roles: Record<string, unknown>[] = [{ clinic_id: CLINIC_A, role: 'front_desk' }]) {
  const table: Routes = [['get', '/v1/iam/me', () => json(meBody(roles))], ...routes];
  return (req: FakeApiRequest): Response | Promise<Response> => {
    for (const [method, path, handler] of table) {
      if (method !== req.method) continue;
      const m = typeof path === 'string' ? (req.path === path ? [req.path] : null) : req.path.match(path);
      if (m) return handler(req, m as RegExpMatchArray);
    }
    return fail(404);
  };
}

export interface OpenOptions {
  /** The role of the single grant. */
  role?: string;
  roles?: Record<string, unknown>[];
  hash?: string;
  routes?: Routes;
  lang?: 'en' | 'ta';
}

/** Signs in at aal2 (no sign-in screens) and renders the console at a hash route. Returns the recorded API calls. */
export async function openConsole({ role = 'front_desk', roles, hash = '/', routes = [], lang = 'en' }: OpenOptions = {}) {
  initI18n('en');
  await setLanguage(lang);
  window.location.hash = `#${hash}`;
  const fake = makeFakePort({ signedInAt: 'aal2' });
  const grants = roles ?? [{ clinic_id: CLINIC_A, role }];
  const calls = stubApi(backend(routes, grants));
  const view = render(<App port={fake.port} apiBaseUrl="http://api.test" />);
  await screen.findByRole('group', { name: lang === 'en' ? 'Session' : 'அமர்வு' });
  return { calls, fake, ...view };
}

export const writes = (calls: FakeApiRequest[]) => calls.filter((c) => c.method === 'post');
