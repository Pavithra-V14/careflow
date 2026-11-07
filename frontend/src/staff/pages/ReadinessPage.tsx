import { useId, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { components } from '../../shared/api/schema';
import { unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { useApiQuery, useIdempotentWrite } from '../hooks';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { Badge, PageHeading, ShortId, UUID, useWord } from '../ui/bits';
import { NotAvailableToRole, ProblemText } from '../ui/states';
import { WriteProblem } from '../ui/WriteProblem';

type View = components['schemas']['ReadinessViewOut'];

/**
 * Visit readiness (nurse and doctor). There is no list of visits, so the person brings the visit id. The screen shows where the
 * run is (a closed status word, what it waits for, the id of the current item), starts it, and relays the patient's reply. The
 * checklist items and questions are the clinic's content and are never shown here: only their ids.
 */
export function ReadinessPage() {
  const { t } = useTranslation();
  const id = useId();
  const [text, setText] = useState('');
  const [visitId, setVisitId] = useState<string | null>(null);
  const bad = text.trim() !== '' && !UUID.test(text.trim());
  return (
    <section aria-labelledby="ready-title">
      <PageHeading id="ready-title">{t('readiness.title')}</PageHeading>
      <p className="hint">{t('readiness.note')}</p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (UUID.test(text.trim())) setVisitId(text.trim().toLowerCase());
        }}
      >
        <label htmlFor={id}>{t('readiness.visitId')}</label>
        <input id={id} type="text" autoComplete="off" spellCheck={false} value={text} aria-invalid={bad || undefined} onChange={(e) => setText(e.target.value)} />
        {bad && <p className="form-error">{t('readiness.visitIdBad')}</p>}
        <button type="submit" className="primary" disabled={!UUID.test(text.trim())}>
          {t('readiness.open')}
        </button>
      </form>
      {visitId && <Run key={visitId} visitId={visitId} />}
    </section>
  );
}

function Run({ visitId }: { visitId: string }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const word = useWord();
  const queryClient = useQueryClient();
  const [starting, setStarting] = useState(false);
  const [replying, setReplying] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const query = useApiQuery(['readiness', visitId], () => unwrap(api.readinessView(visitId)));
  const start = useIdempotentWrite(
    (_: void, key) => unwrap(api.startReadiness(visitId, { idempotencyKey: key })),
    () => {
      setStarting(false);
      setNote(t('readiness.started'));
      void queryClient.invalidateQueries();
    },
  );

  if (query.isPending)
    return (
      <p role="status" className="loading">
        {t('common.loading')}
      </p>
    );
  const problem = query.error;
  // 404 is "no run yet (or the visit is not visible)": starting is offered. 503 is the clinic's content not being set up.
  const noRun = problem?.kind === 'not_found';
  const view: View | undefined = query.data;
  return (
    <>
      <h2>
        {t('readiness.run')} <ShortId id={visitId} />
      </h2>
      {note && <p role="status">{note}</p>}
      {problem && problem.kind === 'forbidden' && <NotAvailableToRole />}
      {problem && problem.kind === 'unavailable' && <p role="status">{t('readiness.notConfigured')}</p>}
      {problem && !noRun && problem.kind !== 'forbidden' && problem.kind !== 'unavailable' && (
        <p role="alert">
          <ProblemText kind={problem.kind} />
        </p>
      )}
      {noRun && <p role="status">{t('readiness.noRun')}</p>}
      {view && (
        <dl className="facts">
          <dt>{t('readiness.status')}</dt>
          <dd>
            <Badge tone={view.finished ? 'ok' : 'warn'}>{word('readiness.statuses', view.status)}</Badge>
          </dd>
          <dt>{t('readiness.waitingFor')}</dt>
          <dd>{t(`readiness.waiting.${view.waiting_for}`)}</dd>
          <dt>{t('readiness.currentItem')}</dt>
          <dd>{view.current_item ? <code>{view.current_item}</code> : '–'}</dd>
          <dt>{t('readiness.escalation')}</dt>
          <dd>{view.task_id ? <ShortId id={view.task_id} /> : t('readiness.noEscalation')}</dd>
          <dt>{t('readiness.finished')}</dt>
          <dd>{view.finished ? t('common.yes') : t('common.no')}</dd>
        </dl>
      )}
      <div className="toolbar">
        {(noRun || view) && (
          <button
            type="button"
            onClick={() => {
              setNote(null);
              setStarting(true);
            }}
          >
            {t('readiness.start')}
          </button>
        )}
        {view?.waiting_for === 'reply' && (
          <button
            type="button"
            onClick={() => {
              setNote(null);
              setReplying(true);
            }}
          >
            {t('readiness.relay')}
          </button>
        )}
        <button type="button" onClick={() => void query.refetch()} disabled={query.isFetching}>
          {t('common.refresh')}
        </button>
      </div>
      {starting && (
        <ConfirmDialog
          title={t('readiness.startTitle')}
          confirmLabel={t('readiness.start')}
          busy={start.isPending}
          onConfirm={() => start.mutate()}
          onCancel={() => {
            start.reset();
            setStarting(false);
          }}
          status={<WriteProblem problem={start.error} calm={{ conflict: t('readiness.noContent'), unavailable: t('readiness.notConfigured') }} />}
        >
          <p>{t('readiness.startBody')}</p>
        </ConfirmDialog>
      )}
      {replying && (
        <ReplyDialog
          visitId={visitId}
          onClose={(done) => {
            setReplying(false);
            if (done) {
              setNote(t('readiness.relayed'));
              void queryClient.invalidateQueries();
            }
          }}
        />
      )}
    </>
  );
}

/** Relays what the patient said (typed in, stored encrypted by the backend) or records that nothing came. Nothing is chosen for the person. */
function ReplyDialog({ visitId, onClose }: { visitId: string; onClose: (done: boolean) => void }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const radio = useId();
  const [outcome, setOutcome] = useState<'replied' | 'no_reply' | null>(null);
  const [body, setBody] = useState('');
  const valid = outcome === 'no_reply' || (outcome === 'replied' && body.trim() !== '');
  const write = useIdempotentWrite(
    (_: void, key) => unwrap(api.replyReadiness(visitId, outcome === 'replied' ? { outcome: 'replied', body } : { outcome: 'no_reply' }, { idempotencyKey: key })),
    () => onClose(true),
  );
  return (
    <ConfirmDialog
      title={t('readiness.relay')}
      confirmLabel={t('readiness.send')}
      busy={write.isPending}
      confirmDisabled={!valid}
      onConfirm={() => write.mutate()}
      onCancel={() => {
        write.reset(); // the typed reply is dropped with the dialog
        onClose(false);
      }}
      status={<WriteProblem problem={write.error} calm={{ conflict: t('readiness.notWaiting'), unavailable: t('readiness.notConfigured'), invalid: t('readiness.replyNeedsText') }} />}
    >
      <p>{t('readiness.relayBody')}</p>
      <fieldset>
        <legend>{t('readiness.outcome')}</legend>
        {(['replied', 'no_reply'] as const).map((o) => (
          <label key={o} className="choice">
            <input
              type="radio"
              name={radio}
              checked={outcome === o}
              onChange={() => {
                setOutcome(o);
                write.reset();
              }}
            />
            <span>{t(`readiness.outcomes.${o}`)}</span>
          </label>
        ))}
      </fieldset>
      {outcome === 'replied' && (
        <>
          <label htmlFor={`${radio}-text`}>{t('readiness.replyText')}</label>
          <textarea
            id={`${radio}-text`}
            rows={5}
            value={body}
            onChange={(e) => {
              setBody(e.target.value);
              write.reset();
            }}
          />
        </>
      )}
    </ConfirmDialog>
  );
}
