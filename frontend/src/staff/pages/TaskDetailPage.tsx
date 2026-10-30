import { useState } from 'react';
import { Link, useParams } from 'react-router';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { useApiQuery, useIdempotentWrite } from '../hooks';
import { RESOLVABLE_TASK_KINDS, URGENT_TASK_KINDS } from '../sections';
import { useSession } from '../session/SessionContext';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { Badge, PageHeading, PlaceholderNote, ShortId, Time, UUID, useWord } from '../ui/bits';
import { NotAvailableToRole, ProblemText, QueryView } from '../ui/states';
import type { ApiProblem } from '../apiState';
import { NotFoundPage } from './NotFoundPage';
import { isUrgentTask } from './TasksPage';

export function TaskDetailPage() {
  const { taskId = '' } = useParams();
  return UUID.test(taskId) ? <Detail taskId={taskId} /> : <NotFoundPage />;
}

/** Provenance holds ids and closed words only (migration 0007). Only plain values are shown, clipped; anything nested is not. */
function Provenance({ data }: { data: Record<string, unknown> }) {
  const { t } = useTranslation();
  const rows = Object.entries(data).filter(([, v]) => ['string', 'number', 'boolean'].includes(typeof v));
  if (rows.length === 0) return <p className="state state-empty">{t('tasks.noProvenance')}</p>;
  return (
    <dl className="facts">
      {rows.map(([k, v]) => (
        <div key={k} className="fact-row">
          <dt>{k.replaceAll('_', ' ')}</dt>
          <dd>
            <code>{String(v).slice(0, 80)}</code>
          </dd>
        </div>
      ))}
    </dl>
  );
}

function problemMessage(problem: ApiProblem, conflictKey: string) {
  return problem.kind === 'forbidden' ? <NotAvailableToRole /> : <p role="alert">{problem.kind === 'conflict' ? conflictKey : <ProblemText kind={problem.kind} />}</p>;
}

function Detail({ taskId }: { taskId: string }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { account } = useSession();
  const word = useWord();
  const queryClient = useQueryClient();
  const query = useApiQuery(['task', taskId], () => unwrap(api.openReviewTask(taskId)));
  const [dialog, setDialog] = useState<'acknowledge' | 'resolve' | null>(null);

  const refresh = () => {
    void queryClient.invalidateQueries();
    setDialog(null);
  };
  const acknowledge = useIdempotentWrite((_: void, key) => unwrap(api.acknowledgeTask(taskId, { idempotencyKey: key })), refresh);
  const resolve = useIdempotentWrite((_: void, key) => unwrap(api.resolveTask(taskId, { idempotencyKey: key })), refresh);

  function close() {
    acknowledge.reset();
    resolve.reset();
    setDialog(null);
  }

  return (
    <section aria-labelledby="task-title">
      <PageHeading id="task-title">{t('tasks.detail')}</PageHeading>
      <p>
        <Link to="/tasks">{t('tasks.back')}</Link>
      </p>
      <QueryView query={query}>
        {(task) => {
          const urgent = isUrgentTask(task);
          const closable = (RESOLVABLE_TASK_KINDS as readonly string[]).includes(task.kind);
          const documentId = typeof task.provenance.document_id === 'string' && UUID.test(task.provenance.document_id) ? task.provenance.document_id : null;
          return (
            <>
              {urgent && (
                <p className="urgent-banner" role="note">
                  <Badge tone="urgent">{t('tasks.urgent')}</Badge> {t('tasks.urgentNote')}
                </p>
              )}
              <dl className="facts">
                <dt>{t('tasks.col.task')}</dt>
                <dd>
                  <code>{task.task_id}</code>
                </dd>
                <dt>{t('tasks.col.kind')}</dt>
                <dd>{word('tasks.kind', task.kind)}</dd>
                <dt>{t('tasks.col.priority')}</dt>
                <dd>{t(`tasks.priority.${task.priority}`)}</dd>
                <dt>{t('tasks.col.status')}</dt>
                <dd>
                  <Badge tone={task.status === 'resolved' ? 'ok' : 'warn'}>{t(`tasks.status.${task.status}`)}</Badge>
                </dd>
                <dt>{t('tasks.col.assigned')}</dt>
                <dd>{t(`roles.${task.assigned_role}`, { defaultValue: task.assigned_role })}</dd>
                <dt>{t('tasks.col.created')}</dt>
                <dd>
                  <Time value={task.created_at} />
                </dd>
                <dt>{t('tasks.acknowledgedBy')}</dt>
                <dd>
                  {task.acknowledged_by ? task.acknowledged_by === account.accountId ? t('tasks.you') : <ShortId id={task.acknowledged_by} /> : '–'}
                  {task.acknowledged_at && (
                    <>
                      {' · '}
                      <Time value={task.acknowledged_at} />
                    </>
                  )}
                </dd>
                <dt>{t('tasks.resolvedAt')}</dt>
                <dd>
                  <Time value={task.resolved_at} />
                </dd>
                <dt>{t('tasks.source')}</dt>
                <dd>
                  <code>{task.source_message_id}</code>
                </dd>
              </dl>

              <h2>{t('tasks.provenance')}</h2>
              <Provenance data={task.provenance} />
              {documentId && (
                <p>
                  <Link to={`/referrals/${documentId}`}>{t('tasks.openReferral')}</Link>
                </p>
              )}

              <h2>{t('tasks.actions')}</h2>
              {task.status === 'resolved' ? (
                <p className="state state-empty" role="status">
                  {t('tasks.closed')}
                </p>
              ) : (
                <div className="toolbar">
                  {task.status === 'open' && (
                    <button type="button" onClick={() => setDialog('acknowledge')}>
                      {t('tasks.acknowledge')}
                    </button>
                  )}
                  {closable && (
                    <button type="button" onClick={() => setDialog('resolve')}>
                      {t('tasks.resolve')}
                    </button>
                  )}
                </div>
              )}
              {task.status !== 'resolved' && !closable && (
                // The backend closes this kind through its own route, or only once a clinician defines what closing needs.
                // The console never offers a quiet "resolve" for it.
                <div className="state state-empty">
                  {(URGENT_TASK_KINDS as readonly string[]).includes(task.kind) ? (
                    <PlaceholderNote>{t('placeholder.taskClose')}</PlaceholderNote>
                  ) : (
                    <p>{t('tasks.closedElsewhere')}</p>
                  )}
                </div>
              )}

              {dialog === 'acknowledge' && (
                <ConfirmDialog
                  title={t('tasks.ackTitle')}
                  confirmLabel={t('tasks.acknowledge')}
                  busy={acknowledge.isPending}
                  onConfirm={() => acknowledge.mutate()}
                  onCancel={close}
                  status={acknowledge.error ? problemMessage(acknowledge.error, t('tasks.conflictAck')) : undefined}
                >
                  <p>{t('tasks.ackBody')}</p>
                </ConfirmDialog>
              )}
              {dialog === 'resolve' && (
                <ConfirmDialog
                  title={t('tasks.resolveTitle')}
                  confirmLabel={t('tasks.resolve')}
                  busy={resolve.isPending}
                  onConfirm={() => resolve.mutate()}
                  onCancel={close}
                  status={resolve.error ? problemMessage(resolve.error, t('tasks.conflictResolve')) : undefined}
                >
                  <p>{t('tasks.resolveBody')}</p>
                  {/* Reason codes are a clinician decision. The API takes no reason on resolve, so none is collected or sent. */}
                  <h3>{t('tasks.reasonCodes')}</h3>
                  <ul className="plain">
                    <li>
                      <PlaceholderNote>{t('placeholder.reasonCodes')}</PlaceholderNote>
                    </li>
                  </ul>
                </ConfirmDialog>
              )}
            </>
          );
        }}
      </QueryView>
    </section>
  );
}
