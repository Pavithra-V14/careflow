import { useTranslation } from 'react-i18next';
import { EmergencyBanner } from './components/EmergencyBanner';
import { SiteFooter } from './components/SiteFooter';
import { SiteHeader } from './components/SiteHeader';
import { Landing } from './pages/Landing';
import { NoticePage, type NoticeKind } from './pages/NoticePage';
import { PortalApp } from './portal/PortalApp';
import type { PortalAuth } from './portal/auth';

export type PatientPage = 'landing' | 'sign-in' | 'sign-up' | NoticeKind;

/** `portal` is null when the site is not configured (missing sign-in values): the portal pages then say so plainly. */
interface PortalProps {
  auth: PortalAuth | null;
  apiBaseUrl: string;
}

export function App({ page = 'landing', portal = { auth: null, apiBaseUrl: '' } }: { page?: PatientPage; portal?: PortalProps }) {
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
        {page === 'landing' ? <Landing /> : page === 'privacy' ? <NoticePage kind={page} /> : <PortalApp auth={portal.auth} apiBaseUrl={portal.apiBaseUrl} />}
      </main>
      <SiteFooter />
    </>
  );
}
