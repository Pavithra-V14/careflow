import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { GrievanceStatus } from '../../shared/api/client';
import { unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { useApiQuery, useIdempotentWrite } from '../hooks';
import { useSession } from '../session/SessionContext';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { Badge, ShortId, Time, useWord } from '../ui/bits';
import { QueryView } from '../ui/states';
import { WriteProblem } from '../ui/WriteProblem';

// ---- staff recovery (admin): reset a staff member's second factor; a second admin must approve ----------------------------------

type RecoveryAction = 'approve' | 'cancel' | 'complete';

export function Recovery() {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active, account } = useSession();
  const word = useWord();
  const queryClient = useQueryClient();
  const [openOnly, setOpenOnly] = useState(true);
  const [asking, setAsking] = useState(false);
  const [acting, setActing] = useState<{ id: string; action: RecoveryAction } | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const query = useApiQuery(['recovery', openOnly], () => unwrap(api.listRecovery(active.clinicId, openOnly)));
  const act = useIdempotentWrite(
    (v: { id: string; action: RecoveryAction }, key) => {
      const o = { idempotencyKey: key };
      return v.action === 'approve' ? unwrap(api.approveRecovery(v.id, o)) : v.action === 'cancel' ? unwrap(api.cancelRecovery(v.id, o)) : unwrap(api.completeRecovery(v.id, o));
    },
    (r) => {
      setActing(null);
      setNote(t(`admin.recovery.result.${r.result}`));
      void queryClient.invalidateQueries();
    },
  );
  return (
    <>
      <p className="hint">{t('admin.recovery.note')}</p>
      <div className="toolbar">
        <label className="choice">
          <input type="checkbox" checked={!openOnly} onChange={(e) => setOpenOnly(!e.target.checked)} />
          <span>{t('admin.recovery.includeClosed')}</span>
        </label>
        <button
          type="button"
          onClick={() => {
            setNote(null);
            setAsking(true);
          }}
        >
          {t('admin.recovery.request')}
        </button>
        <button type="button" onClick={() => void query.refetch()} disabled={query.isFetching}>
          {t('common.refresh')}
        </button>
      </div>
      {note && <p role="status">{note}</p>}
      <QueryView query={query} isEmpty={(d) => d.requests.length === 0} empty={t('admin.recovery.empty')}>
        {(d) => (
          <table>
            <caption className="visually-hidden">{t('admin.tab.recovery')}</caption>
            <thead>
              <tr>
                <th scope="col">{t('admin.recovery.col.request')}</th>
                <th scope="col">{t('admin.recovery.col.account')}</th>
                <th scope="col">{t('admin.recovery.col.by')}</th>
                <th scope="col">{t('admin.recovery.col.status')}</th>
                <th scope="col">{t('admin.recovery.col.created')}</th>
                <th scope="col">{t('admin.recovery.col.action')}</th>
              </tr>
            </thead>
            <tbody>
              {d.requests.map((r) => {
                // A convenience: the second admin is neither the requester nor the account being recovered. The database decides.
                const canApprove = r.status === 'requested' && r.requested_by !== account.accountId && r.account_id !== account.accountId;
                const btn = (action: RecoveryAction) => (
                  <button key={action} type="button" aria-label={`${t(`admin.recovery.${action}`)} ${r.request_id.slice(0, 8)}`} onClick={() => { setNote(null); setActing({ id: r.request_id, action }); }}>
                    {t(`admin.recovery.${action}`)}
                  </button>
                );
                return (
                  <tr key={r.request_id}>
                    <th scope="row">
                      <ShortId id={r.request_id} />
                    </th>
                    <td>
                      <ShortId id={r.account_id} />
                    </td>
                    <td>{r.requested_by === account.accountId ? t('admin.autonomy.you') : <ShortId id={r.requested_by} />}</td>
                    <td>
                      <Badge tone={r.status === 'completed' ? 'ok' : r.status === 'cancelled' ? 'neutral' : 'warn'}>{word('admin.recovery.status', r.status)}</Badge>
                    </td>
                    <td>
                      <Time value={r.created_at} />
                    </td>
                    <td>
                      {canApprove && btn('approve')} {r.status === 'approved' && btn('complete')} {(r.status === 'requested' || r.status === 'approved') && btn('cancel')}
                      {r.status === 'completed' || r.status === 'cancelled' ? '–' : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </QueryView>
      {asking && (
        <RequestDialog
          onClose={(ok) => {
            setAsking(false);
            if (ok) {
              setNote(t('admin.recovery.requested'));
              void queryClient.invalidateQueries();
            }
          }}
        />
      )}
      {acting && (
        <ConfirmDialog
          title={t(`admin.recovery.${acting.action}Title`)}
          confirmLabel={t(`admin.recovery.${acting.action}`)}
          busy={act.isPending}
          onConfirm={() => act.mutate(acting)}
          onCancel={() => {
            act.reset();
            setActing(null);
          }}
          status={<WriteProblem problem={act.error} forbidden={t('admin.recovery.refused')} calm={{ unavailable: t('admin.recovery.notConfigured') }} />}
        >
          <p>{t(`admin.recovery.${acting.action}Body`)}</p>
          {acting.action === 'complete' && <p className="hint">{t('admin.stepUp')}</p>}
        </ConfirmDialog>
      )}
    </>
  );
}

function RequestDialog({ onClose }: { onClose: (ok: boolean) => void }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active, account } = useSession();
  const word = useWord();
  const [target, setTarget] = useState('');
  const accounts = useApiQuery(['accounts'], () => unwrap(api.listAccounts(active.clinicId)));
  const write = useIdempotentWrite(
    (_: void, key) => unwrap(api.requestRecovery({ account_id: target, clinic_id: active.clinicId }, { idempotencyKey: key })),
    () => onClose(true),
  );
  // You cannot ask for your own recovery, so your own account is not offered. Nothing is pre-selected.
  const choices = accounts.data?.items.filter((a) => a.account_id !== account.accountId && a.status !== 'closed') ?? [];
  return (
    <ConfirmDialog
      title={t('admin.recovery.request')}
      confirmLabel={t('admin.recovery.requestConfirm')}
      busy={write.isPending}
      confirmDisabled={target === ''}
      onConfirm={() => write.mutate()}
      onCancel={() => {
        write.reset();
        onClose(false);
      }}
      status={<WriteProblem problem={write.error} forbidden={t('admin.recovery.cannotRequest')} />}
    >
      <p>{t('admin.recovery.requestBody')}</p>
      <label>
        {t('admin.recovery.col.account')}
        <select
          value={target}
          onChange={(e) => {
            setTarget(e.target.value);
            write.reset();
          }}
        >
          <option value="">{t('scheduling.choose')}</option>
          {choices.map((a) => (
            <option key={a.account_id} value={a.account_id}>
              {a.account_id.slice(0, 8)}… ({a.roles.map((r) => word('roles', r)).join(', ') || '–'})
            </option>
          ))}
        </select>
      </label>
    </ConfirmDialog>
  );
}

// ---- grievances (admin): acknowledge and resolve ---------------------------------------------------------------------------------

export function Grievances() {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active } = useSession();
  const word = useWord();
  const queryClient = useQueryClient();
  const [acting, setActing] = useState<{ id: string; status: GrievanceStatus } | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const query = useApiQuery(['grievances'], () => unwrap(api.listGrievances(active.clinicId)));
  const set = useIdempotentWrite(
    (v: { id: string; status: GrievanceStatus }, key) => unwrap(api.setGrievanceStatus(v.id, v.status, { idempotencyKey: key })),
    (r) => {
      setActing(null);
      setNote(t(`admin.grievances.result.${r.result}`));
      void queryClient.invalidateQueries();
    },
  );
  return (
    <>
      <p className="hint">{t('admin.grievances.note')}</p>
      <div className="toolbar">
        <button type="button" onClick={() => void query.refetch()} disabled={query.isFetching}>
          {t('common.refresh')}
        </button>
      </div>
      {note && <p role="status">{note}</p>}
      <QueryView query={query} isEmpty={(d) => d.grievances.length === 0} empty={t('admin.grievances.empty')}>
        {(d) => (
          <table>
            <caption className="visually-hidden">{t('admin.tab.grievances')}</caption>
            <thead>
              <tr>
                <th scope="col">{t('admin.grievances.col.grievance')}</th>
                <th scope="col">{t('admin.grievances.col.category')}</th>
                <th scope="col">{t('admin.grievances.col.status')}</th>
                <th scope="col">{t('admin.grievances.col.received')}</th>
                <th scope="col">{t('admin.grievances.col.text')}</th>
                <th scope="col">{t('admin.grievances.col.action')}</th>
              </tr>
            </thead>
            <tbody>
              {d.grievances.map((g) => (
                <tr key={g.grievance_id}>
                  <th scope="row">
                    <ShortId id={g.grievance_id} />
                  </th>
                  <td>{word('admin.grievances.category', g.category)}</td>
                  <td>
                    <Badge tone={g.status === 'resolved' ? 'ok' : g.status === 'open' ? 'warn' : 'neutral'}>{word('admin.grievances.status', g.status)}</Badge>
                  </td>
                  <td>
                    <Time value={g.created_at} />
                  </td>
                  {/* The patient's own words, as written, while they can be read; erased or unreadable says so. */}
                  <td>{g.erased ? <em>{t('admin.grievances.erased')}</em> : g.text === null ? <em>{t('admin.grievances.unreadable')}</em> : g.text}</td>
                  <td>
                    {g.status === 'open' && (
                      <button type="button" aria-label={`${t('admin.grievances.acknowledge')} ${g.grievance_id.slice(0, 8)}`} onClick={() => { setNote(null); setActing({ id: g.grievance_id, status: 'acknowledged' }); }}>
                        {t('admin.grievances.acknowledge')}
                      </button>
                    )}{' '}
                    {g.status !== 'resolved' && (
                      <button type="button" aria-label={`${t('admin.grievances.resolve')} ${g.grievance_id.slice(0, 8)}`} onClick={() => { setNote(null); setActing({ id: g.grievance_id, status: 'resolved' }); }}>
                        {t('admin.grievances.resolve')}
                      </button>
                    )}
                    {g.status === 'resolved' && '–'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </QueryView>
      {acting && (
        <ConfirmDialog
          title={t(`admin.grievances.${acting.status === 'resolved' ? 'resolve' : 'acknowledge'}Title`)}
          confirmLabel={t(`admin.grievances.${acting.status === 'resolved' ? 'resolve' : 'acknowledge'}`)}
          busy={set.isPending}
          onConfirm={() => set.mutate(acting)}
          onCancel={() => {
            set.reset();
            setActing(null);
          }}
          status={<WriteProblem problem={set.error} />}
        >
          <p>{t(acting.status === 'resolved' ? 'admin.grievances.resolveBody' : 'admin.grievances.acknowledgeBody')}</p>
        </ConfirmDialog>
      )}
    </>
  );
}
