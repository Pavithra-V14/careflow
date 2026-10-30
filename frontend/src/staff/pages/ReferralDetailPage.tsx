import { Link, useParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import { unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { useApiQuery } from '../hooks';
import { Badge, PageHeading, ShortId, Time, UUID, useLang, useWord } from '../ui/bits';
import { QueryView } from '../ui/states';
import { NotFoundPage } from './NotFoundPage';
import { TYPE_LABEL } from './ReferralsPage';
import { formatNumber } from '../../shared/i18n/format';

export function ReferralDetailPage() {
  const { documentId = '' } = useParams();
  // A malformed id is "not found" without asking the server.
  return UUID.test(documentId) ? <Detail documentId={documentId} /> : <NotFoundPage />;
}

function Detail({ documentId }: { documentId: string }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const word = useWord();
  const lang = useLang();
  const query = useApiQuery(['referral', documentId], () => unwrap(api.openReferral(documentId)));

  return (
    <section aria-labelledby="referral-title">
      <PageHeading id="referral-title">{t('referrals.detail')}</PageHeading>
      <p>
        <Link to="/referrals">{t('referrals.back')}</Link>
      </p>
      <QueryView query={query}>
        {(r) => (
          <>
            <dl className="facts">
              <dt>{t('referrals.col.document')}</dt>
              <dd>
                <code>{r.document_id}</code>
              </dd>
              <dt>{t('referrals.col.received')}</dt>
              <dd>
                <Time value={r.created_at} />
              </dd>
              <dt>{t('referrals.col.type')}</dt>
              <dd>
                {TYPE_LABEL[r.content_type] ?? r.content_type} · {formatNumber(Math.max(1, Math.round(r.size_bytes / 1024)), lang)} KB
              </dd>
              <dt>{t('referrals.registeredBy')}</dt>
              <dd>
                <ShortId id={r.registered_by} />
              </dd>
              <dt>{t('referrals.col.state')}</dt>
              <dd>{word('referrals.runState', r.run_state)}</dd>
            </dl>

            <h2>{t('referrals.holds')}</h2>
            {r.holds.length === 0 ? (
              <p role="status" className="state state-empty">
                {t('referrals.noHolds')}
              </p>
            ) : (
              <ul className="plain">
                {r.holds.map((h) => (
                  <li key={h.task_id}>
                    <Link to={`/tasks/${h.task_id}`}>
                      <ShortId id={h.task_id} />
                    </Link>{' '}
                    · {t(`roles.${h.assigned_role}`, { defaultValue: h.assigned_role })} · <Badge tone={h.status === 'resolved' ? 'ok' : 'warn'}>{t(`tasks.status.${h.status}`)}</Badge>
                  </li>
                ))}
              </ul>
            )}

            <h2>{t('referrals.proposal')}</h2>
            {!r.proposal_visible ? (
              // "Not visible to your role" is NOT "there is no proposal".
              <p role="status" className="state state-forbidden">
                {t('referrals.hiddenFromRole')}
              </p>
            ) : r.proposal === null || r.proposal === undefined ? (
              <p role="status" className="state state-empty">
                {t('referrals.noProposal')}
              </p>
            ) : (
              <>
                <dl className="facts">
                  <dt>{t('referrals.proposalMode')}</dt>
                  <dd>{word('referrals.mode', r.proposal.mode)}</dd>
                  <dt>{t('referrals.proposalAbstained')}</dt>
                  <dd>{r.proposal.abstained ? t('common.yes') : t('common.no')}</dd>
                  <dt>{t('referrals.col.decision')}</dt>
                  <dd>{r.decided ? t('referrals.decided') : t('referrals.notDecided')}</dd>
                </dl>
                {r.proposal.candidates.length > 0 && (
                  <ul className="plain">
                    {r.proposal.candidates.map((c) => (
                      <li key={`${c.card_id}:${c.version}`}>
                        {t('referrals.card')} <code>{c.card_id}</code> · {t('referrals.version')} {formatNumber(c.version, lang)}
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </>
        )}
      </QueryView>
    </section>
  );
}
