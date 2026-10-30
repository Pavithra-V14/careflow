import { useState } from 'react';
import { Link } from 'react-router';
import { useTranslation } from 'react-i18next';
import type { components } from '../../shared/api/schema';
import { unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { useApiQuery } from '../hooks';
import { URGENT_TASK_KINDS } from '../sections';
import { Badge, PageHeading, ShortId, Time, useWord } from '../ui/bits';
import { QueryView } from '../ui/states';

type Task = components['schemas']['ReviewTaskOut'];
type Status = Task['status'];
const STATUSES: readonly Status[] = ['open', 'acknowledged', 'resolved'];

export const isUrgentTask = (t: Pick<Task, 'kind' | 'priority'>): boolean =>
  t.priority === 'immediate' || (URGENT_TASK_KINDS as readonly string[]).includes(t.kind);

/**
 * Urgent tasks (red flag, could not be assessed, anything marked immediate) first, then the server's own order
 * (immediate before standard, then oldest first). A stable sort, so nothing else is reordered.
 */
export function urgentFirst<T extends Pick<Task, 'kind' | 'priority'>>(items: readonly T[]): T[] {
  return items
    .map((item, index) => ({ item, index }))
    .sort((a, b) => Number(isUrgentTask(b.item)) - Number(isUrgentTask(a.item)) || a.index - b.index)
    .map((x) => x.item);
}

export function TasksPage() {
  const { t } = useTranslation();
  const { api } = useAuth();
  const word = useWord();
  const [status, setStatus] = useState<Status>('open');
  const query = useApiQuery(['tasks', status], () => unwrap(api.listReviewTasks({ status })));

  return (
    <section aria-labelledby="tasks-title">
      <PageHeading id="tasks-title">{t('tasks.title')}</PageHeading>
      <div className="toolbar">
        <div role="group" aria-label={t('tasks.filter')} className="segmented">
          {STATUSES.map((s) => (
            <button key={s} type="button" aria-pressed={status === s} onClick={() => setStatus(s)}>
              {t(`tasks.status.${s}`)}
            </button>
          ))}
        </div>
        <button type="button" onClick={() => void query.refetch()} disabled={query.isFetching}>
          {t('common.refresh')}
        </button>
      </div>
      <QueryView query={query} isEmpty={(d) => d.items.length === 0} empty={t('tasks.empty')}>
        {(d) => (
          <>
            <table>
              <caption className="visually-hidden">{t('tasks.title')}</caption>
              <thead>
                <tr>
                  <th scope="col">{t('tasks.col.task')}</th>
                  <th scope="col">{t('tasks.col.kind')}</th>
                  <th scope="col">{t('tasks.col.priority')}</th>
                  <th scope="col">{t('tasks.col.status')}</th>
                  <th scope="col">{t('tasks.col.assigned')}</th>
                  <th scope="col">{t('tasks.col.created')}</th>
                </tr>
              </thead>
              <tbody>
                {urgentFirst(d.items).map((task) => {
                  const urgent = isUrgentTask(task);
                  return (
                    <tr key={task.task_id} className={urgent ? 'task-urgent' : undefined}>
                      <th scope="row">
                        <Link to={`/tasks/${task.task_id}`}>
                          <span className="visually-hidden">{t('tasks.open')} </span>
                          <ShortId id={task.task_id} />
                        </Link>
                      </th>
                      <td>
                        {urgent && <Badge tone="urgent">{t('tasks.urgent')}</Badge>} {word('tasks.kind', task.kind)}
                      </td>
                      <td>{t(`tasks.priority.${task.priority}`)}</td>
                      <td>
                        <Badge tone={task.status === 'resolved' ? 'ok' : task.status === 'acknowledged' ? 'neutral' : 'warn'}>{t(`tasks.status.${task.status}`)}</Badge>
                      </td>
                      <td>{t(`roles.${task.assigned_role}`, { defaultValue: task.assigned_role })}</td>
                      <td>
                        <Time value={task.created_at} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {d.has_more && <p className="hint">{t('common.moreExist')}</p>}
          </>
        )}
      </QueryView>
    </section>
  );
}
