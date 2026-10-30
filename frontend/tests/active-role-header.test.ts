import { describe, expect, it } from 'vitest';
import { ACTIVE_ROLE_HEADER, createApi, formatActiveRole } from '../src/shared/api/client';
import { SEND_ACTIVE_ROLE_HEADER } from '../src/staff/featureFlags';

const CLINIC = '3f2b8c1e-6a4d-4e1b-9d0a-1c2d3e4f5a6b';
const stepUp = () => new Response('{}', { status: 401, headers: { 'WWW-Authenticate': 'step-up' } });

function setup(activeRole: Parameters<typeof createApi>[0]['activeRole'], responses: (() => Response)[] = []) {
  const sent: (string | null)[] = [];
  const queue = [...responses];
  const api = createApi({
    baseUrl: 'http://api.test',
    getAccessToken: () => 't',
    fetch: async (req) => {
      sent.push((req as Request).headers.get(ACTIVE_ROLE_HEADER));
      return (queue.shift() ?? (() => new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } })))();
    },
    ...(activeRole ? { activeRole } : {}),
    onStepUp: async () => true,
  });
  return { api, sent };
}

describe('active-role header (docs/design/active-role.md; behind a flag)', () => {
  it('is OFF by default: the application flag is false and nothing is sent', async () => {
    expect(SEND_ACTIVE_ROLE_HEADER).toBe(false);
    const { api, sent } = setup(undefined);
    await api.getMe();
    expect(sent).toEqual([null]);
  });

  it('is not sent when the flag is off, even if a role is known', async () => {
    const { api, sent } = setup({ enabled: false, get: () => formatActiveRole(CLINIC, 'nurse') });
    await api.getMe();
    expect(sent).toEqual([null]);
  });

  it('is sent as <clinic>:<role> when the flag is on', async () => {
    const { api, sent } = setup({ enabled: true, get: () => formatActiveRole(CLINIC, 'front_desk') });
    await api.getMe();
    expect(sent).toEqual([`${CLINIC}:front_desk`]);
  });

  it('is not sent when no role is chosen, or when the value is not exactly <uuid>:<role>', async () => {
    for (const value of [null, '', 'nurse', `${CLINIC}`, `${CLINIC}:Nurse`, `${CLINIC}: nurse`, `${CLINIC}:nurse\r\nX-Evil: 1`, 'not-a-uuid:nurse']) {
      const { api, sent } = setup({ enabled: true, get: () => value });
      await api.getMe();
      expect(sent, String(value)).toEqual([null]);
    }
  });

  it('is sent again on the step-up retry', async () => {
    const { api, sent } = setup({ enabled: true, get: () => formatActiveRole(CLINIC, 'nurse') }, [stepUp]);
    await api.approveMerge(CLINIC);
    expect(sent).toEqual([`${CLINIC}:nurse`, `${CLINIC}:nurse`]);
  });
});
