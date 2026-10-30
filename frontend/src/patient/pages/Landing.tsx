import { useTranslation } from 'react-i18next';
import { PATHS } from '../paths';
import { FAQ_ITEMS, FEATURES, HOW_STEPS } from '../features';
import { Placeholder } from '../components/Placeholder';


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
            <li key={f.key}>
              <span>{t(`landing.features.${f.key}`)}</span>
              {f.status === 'soon' && <span className="badge">{t('common.comingSoon')}</span>}
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="how-heading">
        <h2 id="how-heading">{t('landing.how.heading')}</h2>
        <ol className="steps">
          {HOW_STEPS.map((s) => (
            <li key={s.key} data-unbuilt={s.status === 'soon' ? '' : undefined}>
              <h3>{t(`landing.how.${s.key}Title`)}</h3>
              {s.status === 'soon' && (
                <p>
                  <span className="badge">{t('common.comingSoon')}</span>
                </p>
              )}
              <p>{t(`landing.how.${s.key}Body`)}</p>
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
        {FAQ_ITEMS.map((item) => (
          <details key={item.q} data-unbuilt={item.status === 'soon' ? '' : undefined}>
            <summary>
              {t(`landing.faq.${item.q}`)}
              {item.status === 'soon' && <span className="badge">{t('common.comingSoon')}</span>}
            </summary>
            {'a' in item ? <p>{t(`landing.faq.${item.a}`)}</p> : <Placeholder>{t(`placeholder.${item.placeholder}`)}</Placeholder>}
          </details>
        ))}
      </section>
    </>
  );
}
