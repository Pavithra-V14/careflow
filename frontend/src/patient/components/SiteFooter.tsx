import { useTranslation } from 'react-i18next';
import { PATHS } from '../paths';
import { Placeholder } from './Placeholder';

export function SiteFooter() {
  const { t } = useTranslation();
  return (
    <footer className="site-footer">
      <section aria-labelledby="grievance-heading">
        <h2 id="grievance-heading">{t('landing.footer.grievance')}</h2>
        <Placeholder>{t('placeholder.grievance')}</Placeholder>
      </section>
      <p>
        <a href={PATHS.privacy}>{t('landing.footer.privacyNotice')}</a>
      </p>
      <p className="branding-note">{t('landing.footer.branding')}</p>
      <p className="staff-link">
        <a href={PATHS.staff}>{t('common.staffSignIn')}</a>
      </p>
    </footer>
  );
}
