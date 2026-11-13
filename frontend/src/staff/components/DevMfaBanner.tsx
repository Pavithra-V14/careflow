import { useTranslation } from 'react-i18next';
import { devSkipStaffMfa } from '../featureFlags';

/** DEV ONLY: shown on every staff page (sign-in included) while the second factor is skipped. Renders nothing otherwise. */
export function DevMfaBanner() {
  const { t } = useTranslation();
  if (!devSkipStaffMfa()) return null;
  return (
    <p role="status" className="offline-banner" data-dev-mfa-skipped>
      {t('devMfa.banner')}
    </p>
  );
}
