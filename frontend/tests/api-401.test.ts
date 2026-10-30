import { describe, expect, it, vi } from 'vitest';
import { createApi, isStepUpChallenge } from '../src/shared/api/client';

const ID = '3f2b8c1e-6a4d-4e1b-9d0a-1c2d3e4f5a6b';
const stepUp = () => new Response('{"detail":"step-up required"}', { status: 401, headers: { 'WWW-Authenticate': 'step-up', 'content-type': 'application/json' } });
const plain401 = () => new Response('{"detail":"nope"}', { status: 401, headers: { 'content-type': 'application/json' } });
const ok = () => new Response('{"merge_id":"x"}', { status: 200, headers: { 'content-type': 'application/json' } });

function setup(responses: (() => Response)[], hooks: { onStepUp?: () => Promise<boolean>; onUnauthorized?: () => void }) {
  const sent: { auth: string | null; key: string | null; body: string }[] = [];
  let token = 'token-1';
  const queue = [...responses];
  const api = createApi({
    baseUrl: 'http://api.test',
    getAccessToken: () => token,
    fetch: async (req) => {
      const r = req as Request;
      sent.push({ auth: r.headers.get('authorization'), key: r.headers.get('x-idempotency-key'), body: await r.clone().text() });
      return (queue.shift() ?? ok)();
    },
    ...hooks,
  });
  return { api, sent, rotateToken: () => (token = 'token-2') };
}

describe('step-up 401 handling', () => {
  it('recognises only the exact step-up challenge', () => {
    expect(isStepUpChallenge(stepUp())).toBe(true);
    expect(isStepUpChallenge(new Response('', { status: 401, headers: { 'WWW-Authenticate': 'Step-Up' } }))).toBe(true);
    expect(isStepUpChallenge(plain401())).toBe(false);
    expect(isStepUpChallenge(new Response('', { status: 401, headers: { 'WWW-Authenticate': 'Bearer' } }))).toBe(false);
    expect(isStepUpChallenge(new Response('', { status: 403, headers: { 'WWW-Authenticate': 'step-up' } }))).toBe(false);
  });

  it('prompts, then retries once with the new token, the same idempotency key and the same body', async () => {
    let rotate: () => void = () => undefined;
    const onStepUp = vi.fn(async () => {
      rotate();
      return true;
    });
    const withHooks = setup([stepUp, ok], { onStepUp });
    rotate = withHooks.rotateToken;
    const body = { clinic_id: ID, master_patient_id: ID, subordinate_patient_id: ID };
    const res = await withHooks.api.proposeMerge(body);
    expect(res.response.status).toBe(200);
    expect(onStepUp).toHaveBeenCalledTimes(1);
    expect(withHooks.sent).toHaveLength(2);
    expect(withHooks.sent[0]?.auth).toBe('Bearer token-1');
    expect(withHooks.sent[1]?.auth).toBe('Bearer token-2');
    expect(withHooks.sent[1]?.key).toBeTruthy();
    expect(withHooks.sent[1]?.key).toBe(withHooks.sent[0]?.key);
    expect(JSON.parse(withHooks.sent[1]?.body ?? '')).toEqual(body);
  });

  it('retries only once: a second step-up challenge is returned, not looped on, and not treated as sign-out', async () => {
    const onUnauthorized = vi.fn();
    const onStepUp = vi.fn(async () => true);
    const { api, sent } = setup([stepUp, stepUp], { onStepUp, onUnauthorized });
    const res = await api.approveMerge(ID);
    expect(res.response.status).toBe(401);
    expect(sent).toHaveLength(2);
    expect(onStepUp).toHaveBeenCalledTimes(1);
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it('returns the original 401 without retrying when the user cancels or fails the verification', async () => {
    const onUnauthorized = vi.fn();
    const { api, sent } = setup([stepUp], { onStepUp: async () => false, onUnauthorized });
    const res = await api.approveMerge(ID);
    expect(res.response.status).toBe(401);
    expect(sent).toHaveLength(1);
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it('sends every other 401 to sign-in without prompting and without passing a reason', async () => {
    const onUnauthorized = vi.fn();
    const onStepUp = vi.fn(async () => true);
    const { api, sent } = setup([plain401], { onStepUp, onUnauthorized });
    const res = await api.getMe();
    expect(res.response.status).toBe(401);
    expect(onStepUp).not.toHaveBeenCalled();
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
    expect(onUnauthorized.mock.calls[0]).toEqual([]);
    expect(sent).toHaveLength(1);
  });

  it('sends a 401 that stays 401 after a successful step-up to sign-in', async () => {
    const onUnauthorized = vi.fn();
    const { api } = setup([stepUp, plain401], { onStepUp: async () => true, onUnauthorized });
    await api.approveMerge(ID);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it('leaves 403 and 5xx alone', async () => {
    const onUnauthorized = vi.fn();
    const { api } = setup([() => new Response('{}', { status: 403, headers: { 'content-type': 'application/json' } })], { onUnauthorized });
    expect((await api.getMe()).response.status).toBe(403);
    expect(onUnauthorized).not.toHaveBeenCalled();
  });
});
