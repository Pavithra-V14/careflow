import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { beforeEach, describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import en from '../src/shared/i18n/en.json';
import ta from '../src/shared/i18n/ta.json';
import { initI18n, setLanguage } from '../src/shared/i18n';
import { FEATURES } from '../src/patient/features';
import { Landing } from '../src/patient/pages/Landing';
import { PATHS } from '../src/patient/paths';

const root = resolve(import.meta.dirname, '..');
// A route counts as built only if its HTML entry exists and is part of the production build inputs.
const entryFile = (route: string) => resolve(root, route.replace(/^\/|\/$/g, ''), 'index.html');

beforeEach(async () => {
  initI18n('en');
  await setLanguage('en');
});

describe('landing features: built vs coming soon', () => {
  it('covers exactly the features that have copy, no more and no fewer', () => {
    expect(FEATURES.map((f) => f.key).sort()).toEqual(Object.keys(en.landing.features).filter((k) => k !== 'heading').sort());
  });

  it('only marks a feature built if it has a route that exists', () => {
    for (const f of FEATURES) {
      if (f.status !== 'live') continue;
      expect(Object.values(PATHS), f.key).toContain(f.route);
      expect(existsSync(entryFile(f.route)), `${f.key}: no built page at ${f.route}`).toBe(true);
    }
  });

  it('never gives a feature that is not built a route that claims otherwise', () => {
    for (const f of FEATURES) if (f.status === 'soon') expect('route' in f).toBe(false);
  });

  it('shows the Coming soon label on every unbuilt feature and on no built one, in English and Tamil', async () => {
    for (const lng of ['en', 'ta'] as const) {
      await setLanguage(lng);
      const { container, unmount } = render(<Landing />);
      const label = (lng === 'en' ? en : ta).common.comingSoon;
      const items = [...container.querySelectorAll('.feature-list li')];
      expect(items).toHaveLength(FEATURES.length);
      FEATURES.forEach((f, i) => {
        const has = items[i]?.textContent?.includes(label) ?? false;
        expect(has, `${lng} ${f.key}`).toBe(f.status === 'soon');
      });
      unmount();
    }
  });

  it('has a Coming soon label in both languages that is not empty and differs between them', () => {
    expect(en.common.comingSoon).toBe('Coming soon');
    expect(ta.common.comingSoon.trim()).not.toBe('');
    expect(ta.common.comingSoon).not.toBe(en.common.comingSoon);
  });

  it('currently has no unbuilt feature described as available', () => {
    render(<Landing />);
    for (const f of FEATURES.filter((x) => x.status === 'soon')) {
      const li = screen.getByText(en.landing.features[f.key as keyof typeof en.landing.features]).closest('li');
      expect(li).toHaveTextContent('Coming soon');
    }
  });
});
