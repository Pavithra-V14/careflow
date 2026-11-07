import { useTranslation } from 'react-i18next';
import { unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { useApiQuery } from '../hooks';
import { useSession } from '../session/SessionContext';
import { Badge, PageHeading, useLang } from '../ui/bits';
import { QueryView } from '../ui/states';
import { formatNumber } from '../../shared/i18n/format';

/**
 * The admin's metrics. A value is shown exactly as the API sends it. A metric that could not be read has state `unreadable` and a
 * null value and is shown as "Not readable", never as zero; the metrics with no query behind them are listed as "Not measured".
 * Names are the catalog ids the backend sends (metrics-catalog.yaml is the one place a metric is defined); nothing is explained here.
 */
export function DashboardPage() {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active } = useSession();
  const lang = useLang();
  const query = useApiQuery(['dashboard'], () => unwrap(api.dashboard(active.clinicId)));
  return (
    <section aria-labelledby="dash-title">
      <PageHeading id="dash-title">{t('dashboard.title')}</PageHeading>
      <div className="toolbar">
        <button type="button" onClick={() => void query.refetch()} disabled={query.isFetching}>
          {t('common.refresh')}
        </button>
      </div>
      <QueryView query={query} isEmpty={(d) => d.metrics.length === 0 && d.not_measured.length === 0} empty={t('dashboard.empty')}>
        {(d) => (
          <>
            <h2>{t('dashboard.measured')}</h2>
            {d.metrics.length === 0 ? (
              <p>{t('dashboard.noneMeasured')}</p>
            ) : (
              <table>
                <caption className="visually-hidden">{t('dashboard.measured')}</caption>
                <thead>
                  <tr>
                    <th scope="col">{t('dashboard.col.metric')}</th>
                    <th scope="col">{t('dashboard.col.state')}</th>
                    <th scope="col">{t('dashboard.col.value')}</th>
                  </tr>
                </thead>
                <tbody>
                  {d.metrics.map((m) => (
                    <tr key={m.metric_id}>
                      <th scope="row">
                        <code>{m.metric_id}</code>
                      </th>
                      <td>{m.state === 'measured' ? <Badge tone="ok">{t('dashboard.state.measured')}</Badge> : <Badge tone="warn">{t('dashboard.state.unreadable')}</Badge>}</td>
                      <td>{m.state === 'measured' && m.value !== null ? formatNumber(m.value, lang) : t('dashboard.notReadable')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <h2>{t('dashboard.notMeasured')}</h2>
            {d.not_measured.length === 0 ? (
              <p>{t('dashboard.noneMissing')}</p>
            ) : (
              <ul>
                {d.not_measured.map((id) => (
                  <li key={id}>
                    <code>{id}</code>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </QueryView>
    </section>
  );
}
