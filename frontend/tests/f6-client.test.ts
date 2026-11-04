import { describe, expect, it, vi } from 'vitest';
import { ACTIVE_ROLE_HEADER, ACTIVE_ROLE_REFUSAL_DETAIL, createApi } from '../src/shared/api/client';

const CLINIC = '3f2b8c1e-6a4d-4e1b-9d0a-1c2d3e4f5a6b';
const ID = '66666666-6666-4666-8666-666666666666';
const okJson = () => new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } });
const refusal = (detail: string) => () => new Response(JSON.stringify({ detail }), { status: 403, headers: { 'content-type': 'application/json' } });

function setup(opts: { offline?: boolean; headerOn?: boolean; responses?: (() => Response)[]; onRefused?: () => void } = {}) {
  const sent: { method: string; role: string | null }[] = [];
  const queue = [...(opts.responses ?? [])];
  const api = createApi({
    baseUrl: 'http://api.test',
    getAccessToken: () => 't',
    isOffline: () => opts.offline ?? false,
    activeRole: { enabled: opts.headerOn ?? false, get: () => `${CLINIC}:nurse` },
    ...(opts.onRefused ? { onActiveRoleRefused: opts.onRefused } : {}),
    fetch: async (req) => {
      const r = req as Request;
      sent.push({ method: r.method, role: r.headers.get(ACTIVE_ROLE_HEADER) });
      return (queue.shift() ?? okJson)();
    },
  });
  return { api, sent };
}

describe('offline: a write is never sent', () => {
  it('fails a write as a lost connection without sending anything', async () => {
    const { api, sent } = setup({ offline: true });
    await expect(api.acknowledgeTask(ID)).rejects.toThrow();
    await expect(api.reviewReferral(ID, 'approve')).rejects.toThrow();
    expect(sent).toEqual([]);
  });

  it('keeps the same idempotency key available for the retry: the key is the caller\'s, nothing was consumed', async () => {
    const { api, sent } = setup({ offline: true });
    await expect(api.acknowledgeTask(ID, { idempotencyKey: 'k-1' })).rejects.toThrow();
    expect(sent).toEqual([]);
  });

  it('sends writes again once the connection is back', async () => {
    let offline = true;
    const sent: string[] = [];
    const api = createApi({ baseUrl: 'http://api.test', getAccessToken: () => 't', isOffline: () => offline, fetch: async (r) => (sent.push((r as Request).method), okJson()) });
    await expect(api.acknowledgeTask(ID)).rejects.toThrow();
    offline = false;
    await api.acknowledgeTask(ID);
    expect(sent).toEqual(['POST']);
  });

  it('does not hold back a read or a patient search (neither changes anything)', async () => {
    const { api, sent } = setup({ offline: true });
    await api.getMe();
    await api.searchPatients({ clinic_id: CLINIC, full_name: 'x', date_of_birth: '1990-01-01' } as never);
    expect(sent.map((s) => s.method)).toEqual(['GET', 'POST']);
  });
});

describe('active role: the one fixed 403', () => {
  it('calls onActiveRoleRefused for the exact refusal on a request that carried the header', async () => {
    const onRefused = vi.fn();
    const { api, sent } = setup({ headerOn: true, onRefused, responses: [refusal(ACTIVE_ROLE_REFUSAL_DETAIL)] });
    const res = await api.getMe();
    expect(sent[0]?.role).toBe(`${CLINIC}:nurse`);
    expect(res.response.status).toBe(403);
    expect(onRefused).toHaveBeenCalledTimes(1);
  });

  it('ignores every other 403, and a refusal for a request that did not carry the header', async () => {
    const onRefused = vi.fn();
    const other = setup({ headerOn: true, onRefused, responses: [refusal('not allowed'), refusal('Active role not available')] });
    await other.api.getMe();
    await other.api.getMe();
    const off = setup({ headerOn: false, onRefused, responses: [refusal(ACTIVE_ROLE_REFUSAL_DETAIL)] });
    await off.api.getMe();
    expect(onRefused).not.toHaveBeenCalled();
  });

  it('survives a refusal whose body is not JSON', async () => {
    const onRefused = vi.fn();
    const { api } = setup({ headerOn: true, onRefused, responses: [() => new Response('plain', { status: 403 })] });
    await api.getMe();
    expect(onRefused).not.toHaveBeenCalled();
  });
});
