import { describe, expect, it, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import en from '../src/shared/i18n/en.json';
import ta from '../src/shared/i18n/ta.json';
import { initI18n, setLanguage } from '../src/shared/i18n';
import { App, type PatientPage } from '../src/patient/App';

beforeEach(async () => {
  initI18n('en');
  await setLanguage('en');
});

describe('landing page (BUILD-SPEC 19.2)', () => {
  it('shows the sections in the specified order', () => {
    render(<App />);
    const headings = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent);
    expect(headings).toEqual([
      'About the hospital',
      'Departments and doctors',
      'What you can do here',
      'How it works',
      'Our privacy promise',
      'Location and timings',
      'Frequently asked questions',
      'Grievance contact', // footer
    ]);
  });

  it('puts the hospital name in the only h1, with sign in and sign up links in the header', () => {
    render(<App />);
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
    const banner = screen.getByRole('banner');
    expect(within(banner).getByRole('heading', { level: 1 }).textContent).toBe(en.app.hospitalName);
    expect(within(banner).getByRole('link', { name: 'Sign in' })).toHaveAttribute('href', '/patient/sign-in/');
    expect(within(banner).getByRole('link', { name: 'Sign up' })).toHaveAttribute('href', '/patient/sign-up/');
  });

  it('shows the emergency banner as static content, linked to 108, in both languages', async () => {
    const { unmount } = render(<App />);
    const region = screen.getByRole('region', { name: 'Emergency information' });
    expect(within(region).getByRole('link')).toHaveAttribute('href', 'tel:108');
    expect(region).toHaveTextContent('In an emergency call 108');
    unmount();
    await setLanguage('ta');
    render(<App />);
    expect(screen.getByRole('region', { name: ta.landing.emergencyLabel })).toHaveTextContent('108');
  });

  it('has exactly three how-it-works steps', () => {
    render(<App />);
    expect(screen.getAllByRole('heading', { level: 3 })).toHaveLength(3);
  });

  it('has a small staff sign-in link and a privacy-notice link in the footer', () => {
    render(<App />);
    const footer = screen.getByRole('contentinfo');
    expect(within(footer).getByRole('link', { name: 'Staff sign in' })).toHaveAttribute('href', '/staff/');
    expect(within(footer).getByRole('link', { name: 'Privacy notice' })).toHaveAttribute('href', '/patient/privacy/');
  });

  it('keeps everything on the same origin: no external links, images or frames', () => {
    const { container } = render(<App />);
    for (const a of container.querySelectorAll('a[href]')) {
      expect(a.getAttribute('href'), a.outerHTML).toMatch(/^(\/|#|tel:)/);
    }
    expect(container.querySelectorAll('img, iframe, script, link')).toHaveLength(0);
  });
});

describe('placeholders: wording the hospital or counsel must supply', () => {
  it('renders about, departments, privacy promise, address, hours, phone, two FAQ answers and the grievance contact as placeholders', () => {
    const { container } = render(<App />);
    const marked = [...container.querySelectorAll('[data-placeholder]')].map((n) => n.textContent ?? '');
    expect(marked).toHaveLength(9);
    for (const text of marked) expect(text.startsWith('[PLACEHOLDER')).toBe(true);
  });

  it('keeps the privacy and grievance FAQ answers closed-form placeholders, never written wording', () => {
    render(<App />);
    expect(en.placeholder.faqGrievance).toMatch(/^\[PLACEHOLDER/);
    expect(en.placeholder.faqPrivacy).toMatch(/^\[PLACEHOLDER/);
  });

  it('uses identical, untranslated placeholder text in both languages', () => {
    expect(ta.placeholder).toEqual(en.placeholder);
  });

  it('marks every placeholder string as a placeholder', () => {
    for (const v of Object.values(en.placeholder)) expect(v).toMatch(/^\[PLACEHOLDER/);
  });

  it('shows the privacy notice page as a placeholder, not as written notice wording', () => {
    const { container } = render(<App page="privacy" />);
    expect(container.querySelectorAll('[data-placeholder]')).toHaveLength(2); // notice + footer grievance
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Privacy notice');
  });
});

describe('no promotional claims', () => {
  const BANNED = [/\bbest\b/i, /\bleading\b/i, /\btop[- ]rated\b/i, /number one|no\.? ?1\b/i, /world[- ]class/i, /\bfinest\b/i, /premier/i, /award/i, /guarantee/i, /100\s?%/, /\bcure[sd]?\b/i, /state[- ]of[- ]the[- ]art/i, /cutting[- ]edge/i, /trusted/i, /safest/i];
  it('has no superlative or promotional wording in the English landing copy', () => {
    const copy = JSON.stringify({ landing: en.landing, pages: en.pages, app: en.app });
    for (const re of BANNED) expect(copy, String(re)).not.toMatch(re);
  });
});

describe.each(['sign-in', 'sign-up', 'privacy'] as PatientPage[])('%s page', (kind) => {
  it('has one h1, a way back home, and says plainly when content is not available', () => {
    render(<App page={kind} />);
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
    expect(screen.getByRole('link', { name: 'Back to the home page' })).toHaveAttribute('href', '/patient/');
    if (kind !== 'privacy') expect(screen.getByRole('status')).toHaveTextContent('not available yet');
  });
});
