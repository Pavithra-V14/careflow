import { useTranslation } from 'react-i18next';
import { LanguageSwitcher } from '../shared/i18n/LanguageSwitcher';

// F0 shell: the landing page (F1) replaces the body. The structure (landmarks, skip link, language
// switcher) is what later screens build on.
export function App() {
  const { t } = useTranslation();
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
        <h1>{t('app.patientTitle')}</h1>
      </main>
    </>
  );
}
