import { Link } from 'react-router';
import { useTranslation } from 'react-i18next';
import { CAN_RECORD_CARE_GAP } from '../sections';
import { useSession } from '../session/SessionContext';

/**
 * Hands the patient on screen to the care screens, so the person does not copy an id. Nurse and doctor have no patient search, so
 * the id comes from the record or referral they are looking at. It only fills a field in; nothing is written by following a link.
 */
export function CareLinks({ patientId }: { patientId: string }) {
  const { t } = useTranslation();
  const { hasRole } = useSession();
  if (!hasRole(CAN_RECORD_CARE_GAP)) return null;
  const q = `?patient=${encodeURIComponent(patientId)}`;
  return (
    <nav aria-label={t('care.links.title')} className="sub-nav">
      <h3>{t('care.links.title')}</h3>
      <ul>
        <li>
          <Link to={`/care/gaps${q}`}>{t('care.tab.gaps')}</Link>
        </li>
        <li>
          <Link to={`/care/plans${q}`}>{t('care.tab.plans')}</Link>
        </li>
        <li>
          <Link to={`/care/results${q}`}>{t('care.tab.results')}</Link>
        </li>
        <li>
          <Link to={`/notes${q}`}>{t('care.links.notes')}</Link>
        </li>
      </ul>
    </nav>
  );
}
