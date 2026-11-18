import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { Api } from '../../shared/api/client';
import type { components } from '../../shared/api/schema';
import { unwrap } from '../../staff/apiState';
import { useIdempotentWrite } from '../../staff/hooks';
import { Badge } from '../../staff/ui/bits';
import { ProblemText } from '../../staff/ui/states';
import { WriteProblem } from '../../staff/ui/WriteProblem';
import { usePortalQuery } from './portalQuery';

type Item = components['schemas']['PrepItemOut'];

/**
 * What to prepare for one of the patient's own upcoming visits: the clinic's own checklist, each item's text EXACTLY as sent (plain text,
 * never reworded and never invented here), and what the patient has marked. An item the patient has not marked (`done` null) is "not marked
 * yet", never "not done". Marking is the patient's own, reversible state; it carries no words and is sent with a key. With the owner's
 * switch off the route answers 503 and the panel says so calmly.
 */
export function PrepPanel({ api, bookingId }: { api: Api; bookingId: string }) {
  const { t } = useTranslation();
  const prep = usePortalQuery(['portal-prep', bookingId], () => unwrap(api.getMyVisitPrep(bookingId)));
  if (prep.isPending)
    return (
      <p role="status" className="loading">
        {t('common.loading')}
      </p>
    );
  if (prep.isError) {
    const kind = prep.error.kind;
    return kind === 'unavailable' ? (
      <p role="status">{t('portal.prep.off')}</p>
    ) : kind === 'not_found' ? (
      <p role="status">{t('portal.prep.gone')}</p>
    ) : kind === 'forbidden' ? (
      <p role="status">{t('portal.notAvailable')}</p>
    ) : (
      <p role="alert">
        <ProblemText kind={kind} />
      </p>
    );
  }
  const d = prep.data;
  if (d.nothing_to_prepare || d.items.length === 0) return <p role="status">{t('portal.prep.nothing')}</p>;
  return (
    <ul className="plain-list" aria-label={t('portal.prep.title')}>
      {d.items.map((item) => (
        <PrepItem key={item.item_id} api={api} bookingId={bookingId} item={item} />
      ))}
    </ul>
  );
}

function PrepItem({ api, bookingId, item }: { api: Api; bookingId: string; item: Item }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const mark = useIdempotentWrite(
    (done: boolean, key) => unwrap(api.markMyPrepItem(bookingId, item.item_id, done, { idempotencyKey: key })),
    () => void queryClient.invalidateQueries({ queryKey: ['portal-prep', bookingId] }),
  );
  return (
    <li>
      {/* The clinic's wording exactly as sent, as plain text. */}
      <span className="pre-wrap">{item.text}</span>
      <br />
      <Badge tone={item.done === true ? 'ok' : 'neutral'}>{item.done === true ? t('portal.prep.markedDone') : item.done === false ? t('portal.prep.markedNotDone') : t('portal.prep.notMarked')}</Badge>{' '}
      <button type="button" className="button button-secondary" aria-pressed={item.done === true} disabled={mark.isPending} onClick={() => mark.mutate(true)}>
        {t('portal.prep.markDone')}
      </button>{' '}
      <button type="button" className="button button-secondary" aria-pressed={item.done === false} disabled={mark.isPending} onClick={() => mark.mutate(false)}>
        {t('portal.prep.markNotDone')}
      </button>
      {mark.error && <WriteProblem problem={mark.error} forbidden={t('portal.notAvailable')} calm={{ not_found: t('portal.prep.markGone'), conflict: t('portal.prep.markConflict'), unavailable: t('portal.prep.off') }} />}
    </li>
  );
}
