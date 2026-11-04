import { useTranslation } from 'react-i18next';
import { useOffline } from '../online';

/** Shown only while the browser reports no connection. It never claims anything was saved. */
export function OfflineBanner() {
  const { t } = useTranslation();
  const offline = useOffline();
  if (!offline) return null;
  return (
    <p role="status" className="offline-banner">
      {t('offline.banner')}
    </p>
  );
}
