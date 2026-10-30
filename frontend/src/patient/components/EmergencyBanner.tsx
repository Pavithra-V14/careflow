import { useTranslation } from 'react-i18next';

// Static and backend-free by design: no fetch, no state. index.html carries the same text in a
// <noscript> block so it is still there if scripts fail.
export function EmergencyBanner() {
  const { t } = useTranslation();
  return (
    <div className="emergency" role="region" aria-label={t('landing.emergencyLabel')}>
      <a href="tel:108">{t('common.emergencyBanner')}</a>
    </div>
  );
}
