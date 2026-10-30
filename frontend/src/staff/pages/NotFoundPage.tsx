import { Link } from 'react-router';
import { useTranslation } from 'react-i18next';
import { PageHeading } from '../ui/bits';

export function NotFoundPage() {
  const { t } = useTranslation();
  return (
    <section aria-labelledby="nf-title">
      <PageHeading id="nf-title">{t('common.notFoundTitle')}</PageHeading>
      <p role="status">{t('problem.not_found')}</p>
      <p>
        <Link to="/">{t('nav.home')}</Link>
      </p>
    </section>
  );
}
