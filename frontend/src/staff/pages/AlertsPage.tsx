import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { useApiQuery, useIdempotentWrite } from '../hooks';
import { useSession } from '../session/SessionContext';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { Badge, PageHeading, ShortId, Time, useLang, useWord } from '../ui/bits';
import { NotAvailableToRole, ProblemText, QueryView } from '../ui/states';
import { formatNumber } from '../../shared/i18n/format';

/**
 * Alerts aimed at a role you hold in this clinic. The API sends the class, a count and an opaque reference only; the audience
 * role IS the escalation state (an overdue item escalates up nurse -> doctor -> admin, and the alert is aimed at whoever it
 * has reached), so it is shown as "Aimed at". Acknowledging is one press, once, as yourself, behind a confirmation.
 */
export function AlertsPage() {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active } = useSession();
  const word = useWord();
  const lang = useLang();
  const queryClient = useQueryClient();
  const [all, setAll] = useState(false);
  const [acking, setAcking] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const query = useApiQuery(['alerts', all], () => unwrap(api.listAlerts(active.clinicId, all)));
  const ack = useIdempotentWrite(
    (alertId: string, key) => unwrap(api.acknowledgeAlert(alertId, { idempotencyKey: key })),
    () => {
      setAcking(null);
      setDone(true);
      void queryClient.invalidateQueries();
    },
  );
  const problem = ack.error;

  return (
    <section aria-labelledby="alerts-title">
      <PageHeading id="alerts-title">{t('alerts.title')}</PageHeading>
      <p className="hint">{t('alerts.note')}</p>
      <div className="toolbar">
        <label className="choice">
          <input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} />
          <span>{t('alerts.includeAcknowledged')}</span>
        </label>
        <button type="button" onClick={() => void query.refetch()} disabled={query.isFetching}>
          {t('common.refresh')}
        </button>
      </div>
      {done && <p role="status">{t('alerts.acknowledged')}</p>}
      <QueryView query={query} isEmpty={(d) => d.items.length === 0} empty={t('alerts.empty')}>
        {(d) => (
          <>
            <table>
              <caption className="visually-hidden">{t('alerts.title')}</caption>
              <thead>
                <tr>
                  <th scope="col">{t('alerts.col.class')}</th>
                  <th scope="col">{t('alerts.col.aimedAt')}</th>
                  <th scope="col">{t('alerts.col.count')}</th>
                  <th scope="col">{t('alerts.col.ref')}</th>
                  <th scope="col">{t('alerts.col.raised')}</th>
                  <th scope="col">{t('alerts.col.state')}</th>
                  <th scope="col">{t('alerts.col.action')}</th>
                </tr>
              </thead>
              <tbody>
                {d.items.map((a) => (
                  <tr key={a.alert_id}>
                    <th scope="row">{word('alerts.class', a.alert_class)}</th>
                    <td>{word('roles', a.audience_role)}</td>
                    <td>{formatNumber(a.count, lang)}</td>
                    <td>{a.ref ? <ShortId id={a.ref} /> : '–'}</td>
                    <td>
                      <Time value={a.created_at} />
                    </td>
                    <td>{a.acknowledged ? <Badge tone="neutral">{t('alerts.stateDone')}</Badge> : <Badge tone="warn">{t('alerts.stateOpen')}</Badge>}</td>
                    <td>
                      {a.acknowledged ? (
                        '–'
                      ) : (
                        <button
                          type="button"
                          aria-label={`${t('alerts.acknowledge')} ${a.alert_id.slice(0, 8)}`}
                          onClick={() => {
                            setDone(false);
                            setAcking(a.alert_id);
                          }}
                        >
                          {t('alerts.acknowledge')}
                        </button>
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
      {acking && (
        <ConfirmDialog
          title={t('alerts.confirmTitle')}
          confirmLabel={t('alerts.acknowledge')}
          busy={ack.isPending}
          onConfirm={() => ack.mutate(acking)}
          onCancel={() => {
            if (problem?.kind === 'conflict' || problem?.kind === 'not_found') void queryClient.invalidateQueries();
            ack.reset();
            setAcking(null);
          }}
          status={
            problem &&
            (problem.kind === 'forbidden' ? (
              <NotAvailableToRole />
            ) : problem.kind === 'conflict' ? (
              <p role="status">{t('alerts.already')}</p>
            ) : (
              <p role="alert">
                <ProblemText kind={problem.kind} />
              </p>
            ))
          }
        >
          <p>{t('alerts.confirmBody')}</p>
        </ConfirmDialog>
      )}
    </section>
  );
}
