import { useTranslation } from 'react-i18next';
import { PATHS } from '../paths';
import { Placeholder } from '../components/Placeholder';

const FEATURES = ['book', 'offers', 'messages', 'records', 'family', 'language'] as const;
const STEPS = ['step1', 'step2', 'step3'] as const;
const FAQ = [
  { q: 'q1', a: 'a1' },
  { q: 'q2', a: 'a2' },
  { q: 'q3', placeholder: 'faqGrievance' },
  { q: 'q4', placeholder: 'faqPrivacy' },
] as const;

// Section order follows BUILD-SPEC 19.2. The emergency banner and header are rendered by the page frame.
// Every section here is static: there is no data to load, so "empty" is a visible placeholder, and there
// is nothing that can fail.
export function Landing() {
  const { t } = useTranslation();
  return (
    <>
      <section aria-labelledby="about-heading">
        <h2 id="about-heading">{t('landing.about.heading')}</h2>
        <Placeholder>{t('placeholder.about')}</Placeholder>
      </section>

      <section aria-labelledby="departments-heading">
        <h2 id="departments-heading">{t('landing.departments.heading')}</h2>
        <Placeholder>{t('placeholder.departments')}</Placeholder>
      </section>

      <section aria-labelledby="features-heading">
        <h2 id="features-heading">{t('landing.features.heading')}</h2>
        <ul className="feature-list">
          {FEATURES.map((f) => (
            <li key={f}>{t(`landing.features.${f}`)}</li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="how-heading">
        <h2 id="how-heading">{t('landing.how.heading')}</h2>
        <ol className="steps">
          {STEPS.map((s) => (
            <li key={s}>
              <h3>{t(`landing.how.${s}Title`)}</h3>
              <p>{t(`landing.how.${s}Body`)}</p>
            </li>
          ))}
        </ol>
      </section>

      <section aria-labelledby="privacy-heading">
        <h2 id="privacy-heading">{t('landing.privacy.heading')}</h2>
        <Placeholder>{t('placeholder.privacyPromise')}</Placeholder>
        <p>
          <a href={PATHS.privacy}>{t('landing.privacy.readNotice')}</a>
        </p>
      </section>

      <section aria-labelledby="location-heading">
        <h2 id="location-heading">{t('landing.location.heading')}</h2>
        <dl className="facts">
          <dt>{t('landing.location.address')}</dt>
          <dd>
            <Placeholder>{t('placeholder.address')}</Placeholder>
          </dd>
          <dt>{t('landing.location.hours')}</dt>
          <dd>
            <Placeholder>{t('placeholder.hours')}</Placeholder>
          </dd>
          <dt>{t('landing.location.phone')}</dt>
          <dd>
            <Placeholder>{t('placeholder.phone')}</Placeholder>
          </dd>
        </dl>
      </section>

      <section aria-labelledby="faq-heading">
        <h2 id="faq-heading">{t('landing.faq.heading')}</h2>
        {FAQ.map((item) => (
          <details key={item.q}>
            <summary>{t(`landing.faq.${item.q}`)}</summary>
            {'a' in item ? <p>{t(`landing.faq.${item.a}`)}</p> : <Placeholder>{t(`placeholder.${item.placeholder}`)}</Placeholder>}
          </details>
        ))}
      </section>
    </>
  );
}
