import { useTranslation } from 'react-i18next';
import { getClinicTimeZone, isClinicTimeZonePlaceholder } from '../../shared/clinicTime';

/** Says which time zone the times on a form are in, and labels it a demo placeholder while the backend has sent none. */
export function ZoneNote() {
  const { t } = useTranslation();
  const zone = getClinicTimeZone();
  return <p className="hint">{isClinicTimeZonePlaceholder() ? t('time.zoneNotePlaceholder', { zone }) : t('time.zoneNote', { zone })}</p>;
}
