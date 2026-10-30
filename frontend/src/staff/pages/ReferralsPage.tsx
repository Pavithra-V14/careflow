import { Link } from 'react-router';
import { useTranslation } from 'react-i18next';
import { unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { useApiQuery } from '../hooks';
import { CAN_UPLOAD } from '../sections';
import { useSession } from '../session/SessionContext';
import { Badge, PageHeading, ShortId, Time, useWord } from '../ui/bits';
import { QueryView } from '../ui/states';
import { formatNumber } from '../../shared/i18n/format';
import { useLang } from '../ui/bits';

export const TYPE_LABEL: Record<string, string> = { 'application/pdf': 'PDF', 'image/jpeg': 'JPEG', 'image/png': 'PNG' };

export function ReferralsPage() {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { hasRole } = useSession();
  const word = useWord();
  const lang = useLang();
  const query = useApiQuery(['referrals'], () => unwrap(api.listReferrals()));

  return (
    <section aria-labelledby="referrals-title">
      <PageHeading id="referrals-title">{t('referrals.title')}</PageHeading>
      <div className="toolbar">
        {hasRole(CAN_UPLOAD) && (
          <Link className="button primary" to="/referrals/upload">
            {t('referrals.upload')}
          </Link>
        )}
        <button type="button" onClick={() => void query.refetch()} disabled={query.isFetching}>
          {t('common.refresh')}
        </button>
      </div>
      <QueryView query={query} isEmpty={(d) => d.items.length === 0} empty={t('referrals.empty')}>
        {(d) => (
          <>
            <table>
              <caption className="visually-hidden">{t('referrals.title')}</caption>
              <thead>
                <tr>
                  <th scope="col">{t('referrals.col.document')}</th>
                  <th scope="col">{t('referrals.col.received')}</th>
                  <th scope="col">{t('referrals.col.type')}</th>
                  <th scope="col">{t('referrals.col.state')}</th>
                  <th scope="col">{t('referrals.col.holds')}</th>
                  <th scope="col">{t('referrals.col.decision')}</th>
                </tr>
              </thead>
              <tbody>
                {d.items.map((r) => (
                  <tr key={r.document_id}>
                    <th scope="row">
                      <Link to={`/referrals/${r.document_id}`} aria-label={`${t('referrals.open')} ${r.document_id.slice(0, 8)}`}>
                        <ShortId id={r.document_id} />
                      </Link>
                    </th>
                    <td>
                      <Time value={r.created_at} />
                    </td>
                    <td>
                      {TYPE_LABEL[r.content_type] ?? r.content_type} · {formatNumber(Math.max(1, Math.round(r.size_bytes / 1024)), lang)} KB
                    </td>
                    <td>{word('referrals.runState', r.run_state)}</td>
                    <td>{r.open_holds > 0 ? <Badge tone="warn">{formatNumber(r.open_holds, lang)}</Badge> : formatNumber(0, lang)}</td>
                    <td>
                      {/* proposal_visible false means YOUR ROLE cannot see it. That is not "none". */}
                      {!r.proposal_visible ? t('referrals.hiddenFromRole') : r.decided ? t('referrals.decided') : t('referrals.notDecided')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {d.has_more && <p className="hint">{t('common.moreExist')}</p>}
          </>
        )}
      </QueryView>
    </section>
  );
}
