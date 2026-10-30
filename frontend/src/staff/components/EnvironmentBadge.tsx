import { useTranslation } from 'react-i18next';
import type { ClinicOrigin } from '../me';

// The persistent LIVE / TEST badge (BUILD-SPEC 19.3). The text carries the meaning, colour only reinforces it.
// When the backend has not said which it is, the badge says so: unknown is never shown as LIVE or TEST.
export function EnvironmentBadge({ origin }: { origin: ClinicOrigin | undefined }) {
  const { t } = useTranslation();
  const kind = origin ?? 'unknown';
  return (
    <span className={`env-badge env-${kind}`} role="status" aria-label={`${t('shell.environment')}: ${t(`env.${kind}`)}`}>
      {t(`env.${kind}`)}
    </span>
  );
}
