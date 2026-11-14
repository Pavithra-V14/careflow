import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from '../src/staff/App';
import { initI18n, setLanguage } from '../src/shared/i18n';
import { CLINIC_A, json, makeFakePort, meBody, stubApi } from './helpers/fakes';

// MOCK-ONLY: an invited person activates their OWN account. /v1/iam/me answers 403 until they have, then their grants.
const LEAK = 'LEAK-SENTINEL-backend-text';
async function open(activate: Response | 'ok', lang: 'en' | 'ta' = 'en') {
  initI18n('en');
  await setLanguage(lang);
  window.location.hash = '#/';
  const { port } = makeFakePort({ signedInAt: 'aal2' });
  let active = false;
  const calls = stubApi((req) => {
    if (req.path === '/v1/iam/me') return active ? json(meBody([{ clinic_id: CLINIC_A, role: 'nurse' }])) : json({ detail: LEAK }, 403);
    if (req.method === 'post' && req.path === '/v1/staff/accounts/activate-me') {
      if (activate !== 'ok') return activate;
      active = true;
      return json({ activated: true });
    }
    return json({ detail: LEAK }, 404);
  });
  render(<App port={port} apiBaseUrl="http://api.test" />);
  return calls;
}

describe('activate my own account', () => {
  it('is offered next to the unchanged "cannot use the console" message, writes nothing before the confirmation, then opens the console', async () => {
    const user = userEvent.setup();
    const calls = await open('ok');
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('This account cannot use the staff console. Contact your administrator.');
    expect(alert.textContent).not.toMatch(/invited|activate/i); // the 403 page itself still says no more than the API does
    await user.click(screen.getByRole('button', { name: 'Activate my account' }));
    expect(calls.filter((c) => c.method === 'post')).toHaveLength(0);
    const dialog = screen.getByRole('dialog', { name: 'Activate your account?' });
    await user.click(within(dialog).getByRole('button', { name: 'Activate my account' }));
    expect(await screen.findByRole('group', { name: 'Session' })).toBeInTheDocument(); // the grants were re-read
    const posts = calls.filter((c) => c.method === 'post');
    expect(posts).toHaveLength(1);
    expect(posts[0]?.path).toBe('/v1/staff/accounts/activate-me');
    expect(posts[0]?.body).toBeUndefined();
  });

  it('cancelling writes nothing', async () => {
    const user = userEvent.setup();
    const calls = await open('ok');
    await user.click(await screen.findByRole('button', { name: 'Activate my account' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }));
    expect(calls.filter((c) => c.method === 'post')).toHaveLength(0);
  });

  it('tells a 403 in fixed words and never the backend text', async () => {
    const user = userEvent.setup();
    await open(json({ detail: LEAK }, 403));
    await user.click(await screen.findByRole('button', { name: 'Activate my account' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Activate my account' }));
    expect(await within(screen.getByRole('dialog')).findByText('This account cannot be activated here. Contact your administrator.')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('is available in Tamil', async () => {
    await open('ok', 'ta');
    expect(await screen.findByRole('button', { name: 'என் கணக்கைச் செயல்படுத்து' })).toBeInTheDocument();
  });
});
