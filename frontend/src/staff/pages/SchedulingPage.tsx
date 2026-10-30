import { useState, type ReactNode } from 'react';
import { NavLink, useParams } from 'react-router';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { components } from '../../shared/api/schema';
import type { HoldStatus } from '../../shared/api/client';
import { ApiProblem, toProblem, unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { useApiQuery, useIdempotentWrite } from '../hooks';
import { CAN_RELEASE_HOLD } from '../sections';
import { useSession } from '../session/SessionContext';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { Badge, ComingSoon, PageHeading, ShortId, Time, useLang, useWord } from '../ui/bits';
import { NotAvailableToRole, ProblemText, QueryView } from '../ui/states';
import { formatNumber } from '../../shared/i18n/format';
import { NotFoundPage } from './NotFoundPage';

export const SCHEDULING_LISTS = ['providers', 'locations', 'visit-types', 'priority-levels', 'holds', 'offers'] as const;
type ListId = (typeof SCHEDULING_LISTS)[number];
const isListId = (v: string): v is ListId => (SCHEDULING_LISTS as readonly string[]).includes(v);
const LABEL: Record<ListId, string> = {
  providers: 'scheduling.providers',
  locations: 'scheduling.locations',
  'visit-types': 'scheduling.visitTypes',
  'priority-levels': 'scheduling.priorityLevels',
  holds: 'scheduling.holds',
  offers: 'scheduling.offers',
};

export function SchedulingPage() {
  const { t } = useTranslation();
  const { list = 'providers' } = useParams();
  if (!isListId(list)) return <NotFoundPage />;
  return (
    <section aria-labelledby="sched-title">
      <PageHeading id="sched-title">{t('scheduling.title')}</PageHeading>
      <nav aria-label={t('scheduling.lists')} className="sub-nav">
        <ul>
          {SCHEDULING_LISTS.map((l) => (
            <li key={l}>
              <NavLink to={`/scheduling/${l}`}>{t(LABEL[l])}</NavLink>
            </li>
          ))}
        </ul>
      </nav>
      <h2>{t(LABEL[list])}</h2>
      {list === 'providers' && <ProvidersList />}
      {list === 'locations' && <LocationsList />}
      {list === 'visit-types' && <VisitTypesList />}
      {list === 'priority-levels' && <PriorityLevelsList />}
      {list === 'holds' && <HoldsList />}
      {list === 'offers' && <OffersView />}
    </section>
  );
}

const ActiveBadge = ({ active }: { active: boolean }) => {
  const { t } = useTranslation();
  return <Badge tone={active ? 'ok' : 'neutral'}>{active ? t('scheduling.active') : t('scheduling.inactive')}</Badge>;
};

/** A read-only reference list: show-inactive toggle, the shared states, and a "more exist" note. */
function RefList<T extends { active: boolean }>({
  keyName,
  load,
  columns,
  row,
  rowKey,
}: {
  keyName: string;
  load: (includeInactive: boolean) => Promise<{ items: T[]; has_more: boolean }>;
  columns: string[];
  row: (item: T) => ReactNode[];
  rowKey: (item: T) => string;
}) {
  const { t } = useTranslation();
  const [inactive, setInactive] = useState(false);
  const query = useApiQuery([keyName, inactive], () => load(inactive));
  return (
    <>
      <label className="choice">
        <input type="checkbox" checked={inactive} onChange={(e) => setInactive(e.target.checked)} />
        <span>{t('scheduling.showInactive')}</span>
      </label>
      <QueryView query={query} isEmpty={(d) => d.items.length === 0} empty={t('scheduling.empty')}>
        {(d) => (
          <>
            <table>
              <caption className="visually-hidden">{t(`scheduling.${keyName}`)}</caption>
              <thead>
                <tr>
                  {columns.map((c) => (
                    <th key={c} scope="col">
                      {t(c)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {d.items.map((item) => {
                  const [first, ...rest] = row(item);
                  return (
                    <tr key={rowKey(item)}>
                      <th scope="row">{first}</th>
                      {rest.map((cell, i) => (
                        <td key={i}>{cell}</td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {d.has_more && <p className="hint">{t('common.moreExist')}</p>}
          </>
        )}
      </QueryView>
    </>
  );
}

function ProvidersList() {
  const { api } = useAuth();
  return (
    <RefList<components['schemas']['ProviderOut']>
      keyName="providers"
      load={(i) => unwrap(api.listProviders(i))}
      columns={['scheduling.col.name', 'scheduling.col.status']}
      row={(p) => [p.display_name, <ActiveBadge key="a" active={p.active} />]}
      rowKey={(p) => p.provider_id}
    />
  );
}

function LocationsList() {
  const { api } = useAuth();
  return (
    <RefList<components['schemas']['LocationOut']>
      keyName="locations"
      load={(i) => unwrap(api.listLocations(i))}
      columns={['scheduling.col.name', 'scheduling.col.status']}
      row={(l) => [l.name, <ActiveBadge key="a" active={l.active} />]}
      rowKey={(l) => l.location_id}
    />
  );
}

function VisitTypesList() {
  const { api } = useAuth();
  const lang = useLang();
  const { t } = useTranslation();
  return (
    <RefList<components['schemas']['VisitTypeOut']>
      keyName="visitTypes"
      load={(i) => unwrap(api.listVisitTypes(i))}
      columns={['scheduling.col.name', 'scheduling.col.duration', 'scheduling.col.status']}
      row={(v) => [v.name, `${formatNumber(v.duration_minutes, lang)} ${t('scheduling.minutes')}`, <ActiveBadge key="a" active={v.active} />]}
      rowKey={(v) => v.visit_type_id}
    />
  );
}

function PriorityLevelsList() {
  const { api } = useAuth();
  const lang = useLang();
  return (
    <RefList<components['schemas']['PriorityLevelOut']>
      keyName="priorityLevels"
      load={(i) => unwrap(api.listPriorityLevels(i))}
      columns={['scheduling.col.rank', 'scheduling.col.code', 'scheduling.col.label', 'scheduling.col.status']}
      row={(p) => [formatNumber(p.rank, lang), <code key="c">{p.code}</code>, p.label, <ActiveBadge key="a" active={p.active} />]}
      rowKey={(p) => p.code}
    />
  );
}

const HOLD_STATUSES: readonly NonNullable<HoldStatus>[] = ['active', 'released', 'expired', 'converted'];

function HoldsList() {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { hasRole } = useSession();
  const word = useWord();
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<NonNullable<HoldStatus>>('active');
  const [releasing, setReleasing] = useState<string | null>(null);
  const query = useApiQuery(['holds', status], () => unwrap(api.listHolds(status)));

  const release = useIdempotentWrite(
    (holdId: string, key) => unwrap(api.releaseHold(holdId, { idempotencyKey: key })),
    () => {
      void queryClient.invalidateQueries();
      setReleasing(null);
    },
  );

  function closeDialog() {
    release.reset();
    setReleasing(null);
  }

  const problem = release.error;
  return (
    <>
      <div className="toolbar">
        <div role="group" aria-label={t('scheduling.holdFilter')} className="segmented">
          {HOLD_STATUSES.map((s) => (
            <button key={s} type="button" aria-pressed={status === s} onClick={() => setStatus(s)}>
              {t(`scheduling.holdStatus.${s}`)}
            </button>
          ))}
        </div>
        <button type="button" onClick={() => void query.refetch()} disabled={query.isFetching}>
          {t('common.refresh')}
        </button>
      </div>
      <QueryView query={query} isEmpty={(d) => d.items.length === 0} empty={t('scheduling.noHolds')}>
        {(d) => (
          <>
            <table>
              <caption className="visually-hidden">{t('scheduling.holds')}</caption>
              <thead>
                <tr>
                  <th scope="col">{t('scheduling.col.hold')}</th>
                  <th scope="col">{t('scheduling.col.slot')}</th>
                  <th scope="col">{t('scheduling.col.patient')}</th>
                  <th scope="col">{t('scheduling.col.status')}</th>
                  <th scope="col">{t('scheduling.col.expires')}</th>
                  <th scope="col">{t('scheduling.col.action')}</th>
                </tr>
              </thead>
              <tbody>
                {d.items.map((h) => (
                  <tr key={h.hold_id}>
                    <th scope="row">
                      <ShortId id={h.hold_id} />
                    </th>
                    <td>
                      <ShortId id={h.slot_id} />
                    </td>
                    <td>{h.patient_id ? <ShortId id={h.patient_id} /> : '–'}</td>
                    <td>{word('scheduling.holdStatus', h.status)}</td>
                    <td>
                      <Time value={h.expires_at} />
                    </td>
                    <td>
                      {h.status === 'active' && hasRole(CAN_RELEASE_HOLD) ? (
                        <button type="button" aria-label={`${t('scheduling.release')} ${h.hold_id.slice(0, 8)}`} onClick={() => setReleasing(h.hold_id)}>
                          {t('scheduling.release')}
                        </button>
                      ) : (
                        '–'
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {d.has_more && <p className="hint">{t('common.moreExist')}</p>}
          </>
        )}
      </QueryView>

      {releasing && (
        <ConfirmDialog
          title={t('scheduling.releaseTitle')}
          confirmLabel={t('scheduling.release')}
          busy={release.isPending}
          onConfirm={() => release.mutate(releasing)}
          onCancel={() => {
            // A 409 means the hold was booked or expired meanwhile: the list is stale, so refresh it on the way out.
            if (problem?.kind === 'conflict') void queryClient.invalidateQueries();
            closeDialog();
          }}
          status={
            problem &&
            (problem.kind === 'forbidden' ? (
              <NotAvailableToRole />
            ) : problem.kind === 'conflict' ? (
              // A normal outcome, not an error: someone booked it or it ran out. Calm, and not an alert.
              <p role="status">{t('scheduling.releaseConflict')}</p>
            ) : (
              <p role="alert">
                <ProblemText kind={problem.kind} />
              </p>
            ))
          }
        >
          <p>{t('scheduling.releaseBody')}</p>
        </ConfirmDialog>
      )}
    </>
  );
}

/** Read-only: the draft is computed now and stored nowhere. Sending an offer is not built in the console yet. */
function OffersView() {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active } = useSession();
  const [visitType, setVisitType] = useState('');
  const types = useApiQuery(['offerTypes'], () => unwrap(api.listVisitTypes(false)));
  const draft = useMutation<components['schemas']['Envelope'], ApiProblem, string>({
    gcTime: 0,
    retry: false,
    mutationFn: async (visitTypeId) => {
      try {
        return await unwrap(api.draftOffer(active.clinicId, visitTypeId));
      } catch (e) {
        throw toProblem(e);
      }
    },
  });

  return (
    <>
      <p className="hint">{t('scheduling.offersNote')}</p>
      <QueryView query={types} isEmpty={(d) => d.items.length === 0} empty={t('scheduling.empty')}>
        {(d) => (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (visitType) draft.mutate(visitType);
            }}
          >
            <label htmlFor="offer-visit-type">{t('scheduling.col.visitType')}</label>
            {/* Nothing is pre-selected: the person chooses. */}
            <select id="offer-visit-type" value={visitType} onChange={(e) => { setVisitType(e.target.value); draft.reset(); }}>
              <option value="">{t('scheduling.choose')}</option>
              {d.items.map((v) => (
                <option key={v.visit_type_id} value={v.visit_type_id}>
                  {v.name}
                </option>
              ))}
            </select>
            <button type="submit" className="primary" disabled={!visitType || draft.isPending}>
              {draft.isPending ? t('common.working') : t('scheduling.showDraft')}
            </button>
          </form>
        )}
      </QueryView>

      {draft.error &&
        (draft.error.kind === 'forbidden' ? (
          <NotAvailableToRole />
        ) : (
          <div role="alert" className="state state-error">
            <p>
              <ProblemText kind={draft.error.kind} />
            </p>
          </div>
        ))}
      {draft.data && (
        // Only the status word is shown. `reason` and `data` are not rendered: reason can name clinician-owned settings
        // and data has no declared shape yet. Both are "Coming soon" below.
        <div role="status" className="state state-empty">
          <p>
            <Badge tone={draft.data.status === 'ok' ? 'ok' : 'warn'}>{t(`scheduling.offerStatus.${draft.data.status}`)}</Badge>
          </p>
          <p>
            {t('scheduling.offerCandidates')} <ComingSoon />
          </p>
        </div>
      )}
      <p>
        <button type="button" disabled>
          {t('scheduling.sendOffer')}
        </button>{' '}
        <ComingSoon />
      </p>
    </>
  );
}
