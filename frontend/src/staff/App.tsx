import { useState } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { LanguageSwitcher } from '../shared/i18n/LanguageSwitcher';
import { AuthProvider, useAuth } from './auth/AuthProvider';
import type { AuthPort } from './auth/port';
import { Console } from './screens/Console';
import { MfaChallenge } from './screens/MfaChallenge';
import { MfaEnrol } from './screens/MfaEnrol';
import { Loading, Message } from './screens/Message';
import { SignIn } from './screens/SignIn';

function Screens() {
  const { t } = useTranslation();
  const { phase } = useAuth();
  switch (phase) {
    case 'loading':
      return <Loading label={t('common.loading')} />;
    case 'signed-out':
      return <SignIn />;
    case 'mfa-enrol':
      return <MfaEnrol />;
    case 'mfa-challenge':
      return <MfaChallenge />;
    case 'ready':
      return <Console />;
  }
}

/**
 * `port` is null when the site is not configured (missing Supabase values): the app then says so plainly
 * instead of crashing or showing a sign-in that cannot work.
 */
export function App({ port, apiBaseUrl }: { port: AuthPort | null; apiBaseUrl: string }) {
  const { t } = useTranslation();
  const [queryClient] = useState(() => new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false } } }));
  return (
    <>
      {/* The staff app routes by URL hash, so the skip link focuses <main> itself instead of navigating to #main. */}
      <a
        className="skip-link"
        href="#main"
        onClick={(e) => {
          e.preventDefault();
          document.getElementById('main')?.focus();
        }}
      >
        {t('common.skipToContent')}
      </a>
      <header className="staff-header">
        <p className="app-name">{t('app.hospitalName')}</p>
        <LanguageSwitcher />
      </header>
      <main id="main" tabIndex={-1}>
        {port ? (
          <QueryClientProvider client={queryClient}>
            <AuthProvider port={port} apiBaseUrl={apiBaseUrl}>
              <Screens />
            </AuthProvider>
          </QueryClientProvider>
        ) : (
          <Message id="not-configured-title" title={t('app.staffTitle')} alert>
            <p>{t('common.notConfigured')}</p>
          </Message>
        )}
      </main>
    </>
  );
}
