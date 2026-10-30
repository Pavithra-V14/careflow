import { useTranslation } from 'react-i18next';
import { EmergencyBanner } from './components/EmergencyBanner';
import { SiteFooter } from './components/SiteFooter';
import { SiteHeader } from './components/SiteHeader';
import { Landing } from './pages/Landing';
import { NoticePage, type NoticeKind } from './pages/NoticePage';

export type PatientPage = 'landing' | NoticeKind;

export function App({ page = 'landing' }: { page?: PatientPage }) {
  const { t } = useTranslation();
  return (
    <>
      <a className="skip-link" href="#main">
        {t('common.skipToContent')}
      </a>
      {/* Only the landing page's h1 is the hospital name; other pages' h1 is their own title. */}
      <SiteHeader heading={page === 'landing' ? 'h1' : 'p'} />
      <EmergencyBanner />
      <main id="main" tabIndex={-1}>
        {page === 'landing' ? <Landing /> : <NoticePage kind={page} />}
      </main>
      <SiteFooter />
    </>
  );
}
