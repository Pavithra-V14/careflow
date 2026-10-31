import { beforeEach, describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import en from '../src/shared/i18n/en.json';
import ta from '../src/shared/i18n/ta.json';
import { initI18n, setLanguage } from '../src/shared/i18n';
import { FAQ_ITEMS, FEATURES, HOW_STEPS } from '../src/patient/features';
import { Landing } from '../src/patient/pages/Landing';

// "Every present-tense sentence about an unbuilt feature carries the Coming soon label."
// Two guards: the data (every unbuilt item is marked, in both languages) and a scan of the rendered page (a sentence
// that talks about the portal's unbuilt abilities cannot appear outside a marked container).

beforeEach(async () => {
  initI18n('en');
  await setLanguage('en');
});

/** Words that describe what the portal lets a person do. None of this exists yet. */
const PORTAL_TOPICS = /\b(sign up|create (an |your )?account|book|appointment|message|reminder|records?|family|one-time code|mobile number)\b/i;

function labelFor(lng: 'en' | 'ta') {
  return (lng === 'en' ? en : ta).common.comingSoon;
}

describe.each(['en', 'ta'] as const)('landing page, unbuilt features [%s]', (lng) => {
  it('marks every unbuilt step and FAQ item with the label, and no built one', async () => {
    await setLanguage(lng);
    const { container } = render(<Landing />);
    const label = labelFor(lng);

    const steps = [...container.querySelectorAll('.steps > li')];
    expect(steps).toHaveLength(HOW_STEPS.length);
    HOW_STEPS.forEach((s, i) => expect(steps[i]?.textContent?.includes(label), `step ${s.key}`).toBe(s.status === 'soon'));

    const faq = [...container.querySelectorAll('details')];
    expect(faq).toHaveLength(FAQ_ITEMS.length);
    FAQ_ITEMS.forEach((item, i) => expect(faq[i]?.querySelector('summary')?.textContent?.includes(label), `faq ${item.q}`).toBe(item.status === 'soon'));
  });

  it('keeps the label inside the container it describes, so it cannot drift away from the sentence', async () => {
    await setLanguage(lng);
    const { container } = render(<Landing />);
    for (const el of container.querySelectorAll('[data-unbuilt]')) {
      expect(el.querySelector('.badge'), el.outerHTML.slice(0, 80)).not.toBeNull();
    }
  });
});

describe('landing page text scan (English)', () => {
  it('has no sentence about the portal\'s unbuilt abilities outside a labelled container', () => {
    const { container } = render(<Landing />);
    const offenders: string[] = [];
    for (const el of container.querySelectorAll('main li, main p, main summary, main h3, main dd')) {
      const own = el.textContent ?? '';
      if (!PORTAL_TOPICS.test(own)) continue;
      // Labelled when it, or something that contains it, is marked unbuilt or is itself a feature item with a badge.
      const marked = el.closest('[data-unbuilt]') !== null || el.querySelector('.badge') !== null || el.closest('li')?.querySelector('.badge') != null;
      if (!marked) offenders.push(own.slice(0, 80));
    }
    expect(offenders).toEqual([]);
  });

  it('the scan really does catch an unlabelled sentence (negative control)', () => {
    const { container } = render(
      <main>
        <p>Book an appointment online.</p>
      </main>,
    );
    const hit = [...container.querySelectorAll('main p')].filter((el) => PORTAL_TOPICS.test(el.textContent ?? '') && !el.closest('[data-unbuilt]') && !el.querySelector('.badge'));
    expect(hit).toHaveLength(1);
  });

  it('says plainly that sign-up is not available yet, in both languages, and in no present tense', () => {
    expect(en.landing.faq.a1).toMatch(/not available yet/i);
    expect(en.landing.faq.a1).not.toMatch(/^Choose Sign up/);
    expect(ta.landing.faq.a1).toContain('இன்னும் கிடைக்கவில்லை');
  });

  it('keeps the data in step with the feature list: features, steps and FAQ never call something built that has no route', () => {
    for (const f of [...FEATURES, ...HOW_STEPS, ...FAQ_ITEMS]) if (f.status === 'soon') expect('route' in f).toBe(false);
  });
});
