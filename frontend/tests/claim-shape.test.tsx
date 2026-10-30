import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { decodeJwtPayload, describeClaimShape } from '../src/staff/auth/claimShape';
import { AuthProvider } from '../src/staff/auth/AuthProvider';
import DevClaimSpike from '../src/staff/components/DevClaimSpike';
import { initI18n } from '../src/shared/i18n';
import { fakeJwt, makeFakePort } from './helpers/fakes';

// Values that must never appear in the output. They are synthetic; the token itself is synthetic too.
const SECRET_TS = 1893456789;
const PLANTED = {
  aal: 'aal2-PLANTED',
  amr: [
    { method: 'totp-PLANTED', timestamp: SECRET_TS },
    { method: 'password', timestamp: SECRET_TS - 60 },
  ],
  iat: SECRET_TS + 5,
  sub: 'user-id-PLANTED',
  email: 'person-PLANTED@example.invalid',
  session_id: 'session-PLANTED',
};
const token = fakeJwt(PLANTED);

describe('claim structure (step-up spike)', () => {
  it('decodes the payload and rejects anything that is not a JWT', () => {
    expect(decodeJwtPayload(token)).toMatchObject({ aal: 'aal2-PLANTED' });
    expect(decodeJwtPayload('not-a-token')).toBeNull();
    expect(decodeJwtPayload('a.b.c')).toBeNull();
    expect(describeClaimShape('garbage')).toBeNull();
  });

  it('describes only types and shape, with every value blanked', () => {
    const out = describeClaimShape(token);
    expect(out?.shape).toMatchObject({
      aal: '<string>',
      amr: [
        { method: '<string>', timestamp: '<integer, 10 digits>' },
        { method: '<string>', timestamp: '<integer, 10 digits>' },
      ],
      iat: '<integer, 10 digits>',
      amrTimestampVsIat: ['before', 'before'],
    });
  });

  it('never contains the token, any claim value, or any other claim (sub, email, session id)', () => {
    const text = JSON.stringify(describeClaimShape(token));
    for (const leak of [token, token.split('.')[1] ?? '', 'PLANTED', String(SECRET_TS), 'user-id', 'person', 'session', 'example.invalid', 'sub', 'email']) {
      expect(text, leak).not.toContain(leak);
    }
  });

  it('reports a missing or malformed amr as such, not as a guess', () => {
    expect(describeClaimShape(fakeJwt({ aal: 'aal1', iat: 1 }))?.shape.amr).toBe('<missing>');
    expect(describeClaimShape(fakeJwt({ aal: 'aal1', amr: 'password', iat: 1 }))?.shape.amr).toBe('<not an array>');
    expect(describeClaimShape(fakeJwt({ aal: 'aal1', amr: ['pwd', 'otp'], iat: 1 }))?.shape.amr).toEqual(['<string>', '<string>']); // the legacy string form
    expect(describeClaimShape(fakeJwt({ amr: [{ method: 'totp', timestamp: 1.5 }], iat: 1 }))?.shape).toMatchObject({
      aal: '<missing>',
      amr: [{ method: '<string>', timestamp: '<non-integer number>' }],
    });
  });

  it('distinguishes a timestamp in milliseconds from seconds by digit count alone', () => {
    const ms = describeClaimShape(fakeJwt({ aal: 'aal2', amr: [{ method: 'totp', timestamp: 1893456789000 }], iat: 1893456789 }));
    expect(ms?.shape.amr).toEqual([{ method: '<string>', timestamp: '<integer, 13 digits>' }]);
    expect(ms?.shape.amrTimestampVsIat).toEqual(['after']);
  });

  it('reports whether amr timestamps moved between two captures, without revealing them', () => {
    const first = describeClaimShape(token);
    expect(first?.shape.amrTimestampsSinceLastCapture).toBe('first capture');
    const same = describeClaimShape(fakeJwt({ ...PLANTED, iat: SECRET_TS + 99 }), { previousFingerprint: first?.fingerprint });
    expect(same?.shape.amrTimestampsSinceLastCapture).toBe('unchanged');
    const moved = describeClaimShape(fakeJwt({ ...PLANTED, amr: [{ method: 'totp', timestamp: SECRET_TS + 500 }] }), { previousFingerprint: first?.fingerprint });
    expect(moved?.shape.amrTimestampsSinceLastCapture).toBe('changed');
  });

  it('reveals the enum-like values only when explicitly asked', () => {
    expect(describeClaimShape(token)?.shape.enumValues).toBeUndefined();
    const out = describeClaimShape(token, { revealEnumValues: true });
    expect(out?.shape.enumValues).toEqual({ aal: 'aal2-PLANTED', amrMethods: ['totp-PLANTED', 'password'] });
    expect(JSON.stringify(out)).not.toContain(token);
  });
});

describe('dev claim-spike panel', () => {
  function renderPanel(sessionToken: string) {
    const fake = makeFakePort({ signedInAt: 'aal2' });
    fake.port.getAccessToken.mockImplementation(async () => sessionToken);
    initI18n('en');
    render(
      <QueryClientProvider client={new QueryClient()}>
        <AuthProvider port={fake.port} apiBaseUrl="http://api.test">
          <DevClaimSpike />
        </AuthProvider>
      </QueryClientProvider>,
    );
    return fake;
  }

  it('renders the blanked structure and never the token', async () => {
    renderPanel(token);
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Capture claim structure' }));
    const pre = await screen.findByLabelText('Developer: claim structure', { selector: 'pre' });
    expect(pre.textContent).toContain('<integer, 10 digits>');
    expect(document.body.textContent).not.toContain(token);
    expect(document.body.textContent).not.toContain('PLANTED');
    expect(document.body.textContent).not.toContain(String(SECRET_TS));
  });

  it('refreshes the session then captures, and reports that the timestamps did not move', async () => {
    const fake = renderPanel(token);
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Capture claim structure' }));
    await user.click(screen.getByRole('button', { name: 'Refresh session, then capture' }));
    expect(fake.port.refreshSession).toHaveBeenCalled();
    const pre = await screen.findByLabelText('Developer: claim structure', { selector: 'pre' });
    await within(document.body).findByText(/amrTimestampsSinceLastCapture/);
    expect(pre.textContent).toMatch(/"amrTimestampsSinceLastCapture": "unchanged"/);
  });

  it('shows an error, not a guess, when there is no readable token', async () => {
    renderPanel('not-a-jwt');
    await userEvent.setup().click(screen.getByRole('button', { name: 'Capture claim structure' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not read the session.');
  });
});
