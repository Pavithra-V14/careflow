import { useTranslation } from 'react-i18next';
import { LanguageSwitcher } from '../../shared/i18n/LanguageSwitcher';
import { PATHS } from '../paths';

// Placeholder mark: a plain shape, not a logo and not a photograph.
function LogoMark() {
  return (
    <svg className="logo" width="40" height="40" viewBox="0 0 40 40" aria-hidden="true" focusable="false">
      <rect width="40" height="40" rx="8" fill="var(--color-primary)" />
      <path d="M20 10v20M10 20h20" stroke="var(--color-on-primary)" strokeWidth="5" strokeLinecap="round" />
    </svg>
  );
}

export function SiteHeader({ heading = 'h1' }: { heading?: 'h1' | 'p' }) {
  const { t } = useTranslation();
  const Name = heading;
  return (
    <header className="site-header">
      <a className="brand" href={PATHS.home}>
        <LogoMark />
        <Name className="brand-name">{t('app.hospitalName')}</Name>
      </a>
      <div className="header-tools">
        <LanguageSwitcher />
        <nav aria-label={t('landing.account')} className="account-links">
          <a className="button button-secondary" href={PATHS.signIn}>
            {t('common.signIn')}
          </a>
          <a className="button button-primary" href={PATHS.signUp}>
            {t('common.signUp')}
          </a>
        </nav>
      </div>
    </header>
  );
}
