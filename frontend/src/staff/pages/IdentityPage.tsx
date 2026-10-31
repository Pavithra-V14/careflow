import { useState } from 'react';
import { Link, NavLink, useParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import type { DuplicateStatus, MergeStatus } from '../../shared/api/client';
import { unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { useApiQuery } from '../hooks';
import { CAN_SEE_DUPLICATES } from '../sections';
import { useSession } from '../session/SessionContext';
import { Badge, PageHeading, ShortId, Time, useWord } from '../ui/bits';
import { QueryView } from '../ui/states';
import { NotFoundPage } from './NotFoundPage';

export const IDENTITY_LISTS = ['duplicates', 'merges'] as const;
type ListId = (typeof IDENTITY_LISTS)[number];
const isListId = (v: string): v is ListId => (IDENTITY_LISTS as readonly string[]).includes(v);

export const DUPLICATE_STATUSES: readonly NonNullable<DuplicateStatus>[] = ['pending', 'resolved'];
export const MERGE_STATUSES: readonly NonNullable<MergeStatus>[] = ['awaiting_second_approval', 'active', 'unmerged'];

/** Read-only identity lists: possible duplicates (front desk) and merges (front desk, nurse, doctor). */
export function IdentityPage() {
  const { t } = useTranslation();
  const { hasRole } = useSession();
  const { list = 'merges' } = useParams();
  if (!isListId(list)) return <NotFoundPage />;
  return (
    <section aria-labelledby="identity-title">
      <PageHeading id="identity-title">{t('identity.title')}</PageHeading>
      <nav aria-label={t('identity.lists')} className="sub-nav">
        <ul>
          {/* Convenience only: a role that cannot read duplicates is not shown the link; the backend answers 403 anyway. */}
          {hasRole(CAN_SEE_DUPLICATES) && (
            <li>
              <NavLink to="/identity/duplicates">{t('identity.duplicates')}</NavLink>
            </li>
          )}
          <li>
            <NavLink to="/identity/merges">{t('identity.merges')}</NavLink>
          </li>
        </ul>
      </nav>
      <h2>{t(`identity.${list}`)}</h2>
      {list === 'duplicates' ? <DuplicatesList /> : <MergesList />}
    </section>
  );
}

function PatientLink({ id }: { id: string }) {
  const { t } = useTranslation();
  return (
    <Link to={`/patients/${id}`} aria-label={`${t('patients.openRecord')} ${id.slice(0, 8)}`}>
      <ShortId id={id} />
    </Link>
  );
}

function DuplicatesList() {
  const { t } = useTranslation();
  const { api } = useAuth();
  const word = useWord();
  const [status, setStatus] = useState<NonNullable<DuplicateStatus>>('pending');
  const query = useApiQuery(['duplicates', status], () => unwrap(api.listDuplicates(status)));
  return (
    <>
      <div className="toolbar">
        <div role="group" aria-label={t('identity.filter')} className="segmented">
          {DUPLICATE_STATUSES.map((s) => (
            <button key={s} type="button" aria-pressed={status === s} onClick={() => setStatus(s)}>
              {t(`identity.dupStatus.${s}`)}
            </button>
          ))}
        </div>
        <button type="button" onClick={() => void query.refetch()} disabled={query.isFetching}>
          {t('common.refresh')}
        </button>
      </div>
      <QueryView query={query} isEmpty={(d) => d.items.length === 0} empty={t('identity.emptyDuplicates')}>
        {(d) => (
          <>
            <table>
              <caption className="visually-hidden">{t('identity.duplicates')}</caption>
              <thead>
                <tr>
                  <th scope="col">{t('identity.col.candidate')}</th>
                  <th scope="col">{t('identity.col.patient')}</th>
                  <th scope="col">{t('identity.col.other')}</th>
                  <th scope="col">{t('identity.col.reason')}</th>
                  <th scope="col">{t('identity.col.matched')}</th>
                  <th scope="col">{t('identity.col.status')}</th>
                  <th scope="col">{t('identity.col.created')}</th>
                </tr>
              </thead>
              <tbody>
                {d.items.map((c) => (
                  <tr key={c.candidate_id}>
                    <th scope="row">
                      <ShortId id={c.candidate_id} />
                    </th>
                    <td>
                      <PatientLink id={c.patient_id} />
                    </td>
                    <td>
                      <PatientLink id={c.candidate_patient_id} />
                    </td>
                    <td>{word('identity.dupReason', c.reason)}</td>
                    <td>{c.matched_kinds.map((k) => word('patients.matchedKind', k)).join(', ')}</td>
                    <td>
                      <Badge tone={c.status === 'resolved' ? 'ok' : 'warn'}>{t(`identity.dupStatus.${c.status}`)}</Badge>
                    </td>
                    <td>
                      <Time value={c.created_at} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {d.has_more && <p className="hint">{t('common.moreExist')}</p>}
          </>
        )}
      </QueryView>
    </>
  );
}

function MergesList() {
  const { t } = useTranslation();
  const { api } = useAuth();
  const [status, setStatus] = useState<NonNullable<MergeStatus> | 'all'>('all');
  const query = useApiQuery(['merges', status], () => unwrap(api.listMerges(status === 'all' ? undefined : status)));
  return (
    <>
      <div className="toolbar">
        <div role="group" aria-label={t('identity.filter')} className="segmented">
          {(['all', ...MERGE_STATUSES] as const).map((s) => (
            <button key={s} type="button" aria-pressed={status === s} onClick={() => setStatus(s)}>
              {s === 'all' ? t('identity.all') : t(`identity.mergeStatus.${s}`)}
            </button>
          ))}
        </div>
        <button type="button" onClick={() => void query.refetch()} disabled={query.isFetching}>
          {t('common.refresh')}
        </button>
      </div>
      <QueryView query={query} isEmpty={(d) => d.items.length === 0} empty={t('identity.emptyMerges')}>
        {(d) => (
          <>
            <table>
              <caption className="visually-hidden">{t('identity.merges')}</caption>
              <thead>
                <tr>
                  <th scope="col">{t('identity.col.merge')}</th>
                  <th scope="col">{t('identity.col.status')}</th>
                  <th scope="col">{t('identity.col.master')}</th>
                  <th scope="col">{t('identity.col.subordinate')}</th>
                  <th scope="col">{t('identity.col.created')}</th>
                </tr>
              </thead>
              <tbody>
                {d.items.map((m) => (
                  <tr key={m.merge_id}>
                    <th scope="row">
                      <Link to={`/identity/merges/${m.merge_id}`} aria-label={`${t('identity.openMerge')} ${m.merge_id.slice(0, 8)}`}>
                        <ShortId id={m.merge_id} />
                      </Link>
                    </th>
                    <td>
                      <Badge tone={m.status === 'active' ? 'ok' : m.status === 'unmerged' ? 'neutral' : 'warn'}>{t(`identity.mergeStatus.${m.status}`)}</Badge>
                    </td>
                    <td>
                      <PatientLink id={m.master_patient_id} />
                    </td>
                    <td>
                      <PatientLink id={m.subordinate_patient_id} />
                    </td>
                    <td>
                      <Time value={m.created_at} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {d.has_more && <p className="hint">{t('common.moreExist')}</p>}
          </>
        )}
      </QueryView>
    </>
  );
}
