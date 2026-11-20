import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import en from '../src/shared/i18n/en.json';
import ta from '../src/shared/i18n/ta.json';
import { i18n, initI18n, setLanguage } from '../src/shared/i18n';
import { App } from '../src/patient/App';
import { Landing } from '../src/patient/pages/Landing';
import type { ClinicInfo } from '../src/patient/clinicInfo';
import type { PublicClinicProfile } from '../src/shared/api/client';
import { json, stubApi } from './helpers/fakes';
import { FEATURES } from '../src/patient/features';

// MOCK-ONLY. The signed-out home page from the hospital's public details: the name (GET /public/clinic) in the header and the page title, and the public
// profile (GET /public/clinic/profile) for the text, the features and the "Demo content" mark. A missing value keeps the placeholder; an unknown is never a value.
const profile = (over: Partial<PublicClinicProfile> = {}): PublicClinicProfile => ({
  name: 'Demo Clinic',
  about: null,
  departments: null,
  address: null,
  opening_hours: null,
  phone: null,
  privacy_promise: null,
  placeholder: false,
  features: { booking: false, messaging: false, offers: false, records: false, visit_preparation: false, family_links: false },
  languages: ['en', 'ta'],
  ...over,
});
const info = (p: PublicClinicProfile | null, name: string | null = 'Demo Clinic'): ClinicInfo => ({ name, profile: p, status: p ? 'ok' : 'failed' });
const COMING = { en: en.common.comingSoon, ta: ta.common.comingSoon };

beforeEach(async () => {
  initI18n('en');
  await setLanguage('en');
});
afterEach(() => {
  document.title = '';
});

describe('the home page from the public profile', () => {
  it.each(['en', 'ta'] as const)('shows the features the profile says are on, and "Coming soon" on every other one [%s]', async (lng) => {
    await setLanguage(lng);
    const { container } = render(<Landing info={info(profile({ features: { booking: true, messaging: false, offers: false, records: false, visit_preparation: true, family_links: true } }))} />);
    const items = [...container.querySelectorAll('.feature-list li')];
    const flagged: Record<string, boolean> = { book: true, offers: false, messages: false, records: false, family: true, language: true };
    FEATURES.forEach((f, i) => expect(items[i]?.textContent?.includes(COMING[lng]), `${lng} ${f.key}`).toBe(!flagged[f.key]));
  });

  it('never shows a feature as available when the profile could not be read or does not say so', () => {
    for (const unread of [undefined, info(null)]) {
      const { container, unmount } = render(<Landing {...(unread ? { info: unread } : {})} />);
      const items = [...container.querySelectorAll('.feature-list li')];
      FEATURES.forEach((f, i) => expect(items[i]?.textContent?.includes('Coming soon'), f.key).toBe(f.status === 'soon'));
      unmount();
    }
  });

  it('shows the hospital text when it is given, as plain text, and the placeholder box for each one that is null', () => {
    render(
      <Landing
        info={info(
          profile({ about: 'A plain line <b>about</b> the hospital.', opening_hours: 'Mon to Fri, 9 to 5', departments: [{ name: 'Cardiology', doctors: ['Dr A', 'Dr B'] }, { name: 'Eye care', doctors: [] }] }),
        )}
      />,
    );
    expect(screen.getByText('A plain line <b>about</b> the hospital.')).toBeInTheDocument(); // text, never markup
    expect(screen.getByText('Mon to Fri, 9 to 5')).toBeInTheDocument();
    const departments = screen.getByRole('heading', { name: 'Departments and doctors' }).closest('section') as HTMLElement;
    expect(within(departments).getByText('Cardiology')).toBeInTheDocument();
    expect(within(departments).getByText('Dr A')).toBeInTheDocument();
    expect(within(departments).getByText('Eye care')).toBeInTheDocument();
    // address, phone and the privacy promise were null: still the placeholder boxes
    expect(screen.getByText(en.placeholder.address)).toBeInTheDocument();
    expect(screen.getByText(en.placeholder.phone)).toBeInTheDocument();
    expect(screen.getByText(en.placeholder.privacyPromise)).toBeInTheDocument();
    expect(screen.queryByText(en.placeholder.about)).toBeNull();
  });

  it('shows counsel\'s privacy wording only when it is supplied, and never says it is approved', () => {
    const { unmount } = render(<Landing info={info(profile())} />);
    expect(screen.getByText(en.placeholder.privacyPromise)).toBeInTheDocument(); // still says it is TO BE written and approved: it is not claimed as done
    expect(document.body.textContent).not.toMatch(/(?:^|[^a-z])(?:is|has been|was) approved(?:[^a-z]|$)/i);
    unmount();
    render(<Landing info={info(profile({ privacy_promise: 'The hospital\'s own privacy sentence.' }))} />);
    expect(screen.getByText("The hospital's own privacy sentence.")).toBeInTheDocument();
    expect(screen.queryByText(en.placeholder.privacyPromise)).toBeNull();
    expect(document.body.textContent).not.toMatch(/approved/i); // nothing on the page says the supplied wording is approved
  });

  it.each(['en', 'ta'] as const)('marks the page "Demo content" only while the profile says it is placeholder values [%s]', async (lng) => {
    await setLanguage(lng);
    const label = i18n.t('landing.demoContent');
    expect(label).not.toBe('landing.demoContent'); // the string exists in both languages
    const { unmount } = render(<Landing info={info(profile({ placeholder: true }))} />);
    expect(screen.getByText(label)).toBeInTheDocument();
    unmount();
    render(<Landing info={info(profile({ placeholder: false }))} />);
    expect(screen.queryByText(label)).toBeNull();
  });
});

describe('the hospital name in the header and the page title', () => {
  it.each(['en', 'ta'] as const)('uses the name from the public clinic read when it is there [%s]', async (lng) => {
    await setLanguage(lng);
    stubApi((req) => (req.path === '/public/clinic' ? json({ clinic_id: '3f2b8c1e-6a4d-4e1b-9d0a-1c2d3e4f5a6b', name: 'Demo Clinic' }) : json({ detail: 'x' }, 404)));
    render(<App portal={{ auth: null, apiBaseUrl: 'http://api.test' }} />);
    const banner = screen.getByRole('banner');
    await waitFor(() => expect(within(banner).getByRole('heading', { level: 1 }).textContent).toBe('Demo Clinic'));
    expect(document.title).toBe('Demo Clinic');
  });

  it.each(['en', 'ta'] as const)('keeps the placeholder name in the header and the title when the name is missing [%s]', async (lng) => {
    await setLanguage(lng);
    const placeholder = (lng === 'en' ? en : ta).app.hospitalName;
    stubApi(() => json({ detail: 'LEAK' }, 404));
    render(<App portal={{ auth: null, apiBaseUrl: 'http://api.test' }} />);
    await waitFor(() => expect(document.title).toBe(placeholder));
    expect(within(screen.getByRole('banner')).getByRole('heading', { level: 1 }).textContent).toBe(placeholder);
    expect(document.body.textContent).not.toContain('LEAK');
  });

  it('treats an empty name as missing, and leaves every other page alone', async () => {
    stubApi((req) => (req.path === '/public/clinic' ? json({ clinic_id: '3f2b8c1e-6a4d-4e1b-9d0a-1c2d3e4f5a6b', name: '   ' }) : json({ detail: 'x' }, 404)));
    const { unmount } = render(<App portal={{ auth: null, apiBaseUrl: 'http://api.test' }} />);
    await waitFor(() => expect(document.title).toBe(en.app.hospitalName));
    unmount();
    const calls = stubApi(() => json({ clinic_id: '3f2b8c1e-6a4d-4e1b-9d0a-1c2d3e4f5a6b', name: 'Demo Clinic' }));
    render(<App page="privacy" portal={{ auth: null, apiBaseUrl: 'http://api.test' }} />);
    expect(within(screen.getByRole('banner')).queryByText('Demo Clinic')).toBeNull();
    expect(calls.filter((c) => c.path.startsWith('/public/clinic'))).toHaveLength(0); // only the home page reads the hospital's details
  });
});

// Found live (20 Nov 2026): in the development build the API base is an empty string (same origin, through the dev proxy). The home page treated that as "not
// configured" and never asked, so it stayed on placeholders although the backend had published the profile. An empty base means the page's own origin.
describe('the hospital details are read even when the API base is empty (same origin)', () => {
  it('asks the page\'s own origin for the name and the profile', async () => {
    const calls = stubApi((req) => {
      if (req.path === '/public/clinic') return json({ clinic_id: '3f2b8c1e-6a4d-4e1b-9d0a-1c2d3e4f5a6b', name: 'Demo Clinic' });
      if (req.path === '/public/clinic/profile') return json(profile({ about: 'Text from the profile.', features: { booking: true, messaging: false, offers: false, records: false, visit_preparation: false, family_links: false } }));
      return json({ detail: 'x' }, 404);
    });
    render(<App portal={{ auth: null, apiBaseUrl: '' }} />);
    await waitFor(() => expect(within(screen.getByRole('banner')).getByRole('heading', { level: 1 }).textContent).toBe('Demo Clinic'));
    expect(await screen.findByText('Text from the profile.')).toBeInTheDocument();
    expect(calls.map((c) => c.path).sort()).toEqual(['/public/clinic', '/public/clinic/profile']);
  });
});

describe('a failed read of the hospital details is said, in fixed words, never silently', () => {
  const failing = (status: number) => stubApi((req) => (req.path === '/public/clinic' || req.path === '/public/clinic/profile' ? json({ detail: 'LEAK-SENTINEL' }, status) : json({ detail: 'x' }, 404)));

  it.each([
    [404, "The hospital's details are not available yet."],
    [503, "The hospital's details could not be loaded."],
    [429, "The hospital's details could not be loaded."],
    [500, "The hospital's details could not be loaded."],
  ])('says the right fixed sentence when the profile answers %i, keeps the placeholders, and never shows backend text', async (status, sentence) => {
    failing(status);
    render(<App portal={{ auth: null, apiBaseUrl: 'http://api.test' }} />);
    expect(await screen.findByText(sentence)).toBeInTheDocument();
    expect(screen.getByText(en.placeholder.about)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('LEAK-SENTINEL');
  });

  it('says it also when the connection fails, and says nothing when the details loaded', async () => {
    stubApi(() => {
      throw new TypeError('network down');
    });
    const { unmount } = render(<App portal={{ auth: null, apiBaseUrl: 'http://api.test' }} />);
    expect(await screen.findByText("The hospital's details could not be loaded.")).toBeInTheDocument();
    unmount();
    stubApi((req) => (req.path === '/public/clinic/profile' ? json(profile()) : json({ clinic_id: '3f2b8c1e-6a4d-4e1b-9d0a-1c2d3e4f5a6b', name: 'Demo Clinic' })));
    render(<App portal={{ auth: null, apiBaseUrl: 'http://api.test' }} />);
    await waitFor(() => expect(within(screen.getByRole('banner')).getByRole('heading', { level: 1 }).textContent).toBe('Demo Clinic'));
    expect(screen.queryByText(/could not be loaded|not available yet/)).toBeNull();
  });

  it('says the sentences in Tamil too', async () => {
    await setLanguage('ta');
    failing(503);
    render(<App portal={{ auth: null, apiBaseUrl: 'http://api.test' }} />);
    expect(await screen.findByText(i18n.t('landing.profileFailed'))).toBeInTheDocument();
    expect(i18n.t('landing.profileFailed')).not.toBe('landing.profileFailed');
    expect(i18n.t('landing.profileUnpublished')).not.toBe('landing.profileUnpublished');
  });
});
