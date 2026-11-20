import { useTranslation } from 'react-i18next';
import { PATHS } from '../paths';
import { FAQ_ITEMS, FEATURES, HOW_STEPS } from '../features';
import { Placeholder } from '../components/Placeholder';
import type { ClinicInfo } from '../clinicInfo';

// Which switch of the hospital's public profile each feature on this page follows. The language switcher is on every page, so it has none.
const PROFILE_FLAG = { book: 'booking', offers: 'offers', messages: 'messaging', records: 'records', family: 'family_links' } as const;


// Section order follows BUILD-SPEC 19.2. The emergency banner and header are rendered by the page frame.
// Every section here is static: there is no data to load, so "empty" is a visible placeholder, and there
// is nothing that can fail.
export function Landing({ info }: { info?: ClinicInfo } = {}) {
  const { t } = useTranslation();
  // The hospital's public profile, when it could be read. Without it the page is exactly the static one: placeholders, and "Coming soon" on every unbuilt feature.
  const profile = info?.profile ?? null;
  const available = (f: (typeof FEATURES)[number]): boolean => (f.status === 'live' ? true : profile !== null && f.key in PROFILE_FLAG && profile.features[PROFILE_FLAG[f.key as keyof typeof PROFILE_FLAG]] === true);
  // A text the hospital supplied is shown as plain text, one line; a missing one (null) keeps the placeholder box. Nothing here ever says it is approved.
  const text = (value: string | null | undefined, placeholder: string) => (value ? <p className="profile-text">{value}</p> : <Placeholder>{t(`placeholder.${placeholder}`)}</Placeholder>);
  const departments = profile?.departments ?? null;
  return (
    <>
      {profile?.placeholder === true && (
        <p role="status">
          <span className="badge">{t('landing.demoContent')}</span>
        </p>
      )}
      <section aria-labelledby="about-heading">
        <h2 id="about-heading">{t('landing.about.heading')}</h2>
        {text(profile?.about, 'about')}
      </section>

      <section aria-labelledby="departments-heading">
        <h2 id="departments-heading">{t('landing.departments.heading')}</h2>
        {departments && departments.length > 0 ? (
          <ul className="plain-list">
            {departments.map((d) => (
              <li key={d.name}>
                {d.name}
                {d.doctors.length > 0 && (
                  <ul className="plain-list">
                    {d.doctors.map((doc) => (
                      <li key={doc}>{doc}</li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <Placeholder>{t('placeholder.departments')}</Placeholder>
        )}
      </section>

      <section aria-labelledby="features-heading">
        <h2 id="features-heading">{t('landing.features.heading')}</h2>
        <ul className="feature-list">
          {FEATURES.map((f) => (
            <li key={f.key}>
              <span>{t(`landing.features.${f.key}`)}</span>
              {!available(f) && <span className="badge">{t('common.comingSoon')}</span>}
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
        {text(profile?.privacy_promise, 'privacyPromise')}
        <p>
          <a href={PATHS.privacy}>{t('landing.privacy.readNotice')}</a>
        </p>
      </section>

      <section aria-labelledby="location-heading">
        <h2 id="location-heading">{t('landing.location.heading')}</h2>
        <dl className="facts">
          <dt>{t('landing.location.address')}</dt>
          <dd>{text(profile?.address, 'address')}</dd>
          <dt>{t('landing.location.hours')}</dt>
          <dd>{text(profile?.opening_hours, 'hours')}</dd>
          <dt>{t('landing.location.phone')}</dt>
          <dd>{text(profile?.phone, 'phone')}</dd>
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
