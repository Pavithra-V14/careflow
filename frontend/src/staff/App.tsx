import { useTranslation } from 'react-i18next';
import { LanguageSwitcher } from '../shared/i18n/LanguageSwitcher';
import { readConfig } from '../shared/config';

// F0 shell: sign-in and the console shell (F2) replace the body.
export function App() {
  const { t } = useTranslation();
  const { configured } = readConfig();
  return (
    <>
      <a className="skip-link" href="#main">
        {t('common.skipToContent')}
      </a>
      <header>
        <p>{t('app.hospitalName')}</p>
        <LanguageSwitcher />
      </header>
      <main id="main" tabIndex={-1}>
        <h1>{t('app.staffTitle')}</h1>
        {!configured && <p role="alert">{t('common.notConfigured')}</p>}
      </main>
    </>
  );
}
