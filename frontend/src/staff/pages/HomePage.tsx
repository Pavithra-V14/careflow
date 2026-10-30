import { lazy, Suspense } from 'react';
import { Link } from 'react-router';
import { useTranslation } from 'react-i18next';
import { sectionsFor } from '../sections';
import { useSession } from '../session/SessionContext';
import { EmptyState } from '../ui/states';
import { PageHeading } from '../ui/bits';

// The claim spike panel exists only in development builds: the import is behind import.meta.env.DEV so the
// production bundle never contains it (checked by scripts/check-bundle-secrets.mjs).
const DevClaimSpike = import.meta.env.DEV ? lazy(() => import('../components/DevClaimSpike')) : null;

export function HomePage() {
  const { t } = useTranslation();
  const { active } = useSession();
  const sections = sectionsFor(active.role);
  return (
    <section aria-labelledby="console-title">
      <PageHeading id="console-title">{t('app.staffTitle')}</PageHeading>
      <p>{t('staff.console.welcome')}</p>
      {sections.length === 0 ? (
        <EmptyState>{t('nav.none')}</EmptyState>
      ) : (
        <ul className="home-links">
          {sections.map((s) => (
            <li key={s.id}>
              <Link to={s.path}>{t(s.labelKey)}</Link>
            </li>
          ))}
        </ul>
      )}
      {DevClaimSpike && (
        <Suspense fallback={null}>
          <DevClaimSpike />
        </Suspense>
      )}
    </section>
  );
}
