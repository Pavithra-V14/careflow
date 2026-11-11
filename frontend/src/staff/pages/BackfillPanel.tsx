import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { BackfillAction } from '../../shared/api/client';
import type { components } from '../../shared/api/schema';
import { ApiProblem, toProblem, unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { useApiQuery, useIdempotentWrite } from '../hooks';
import { useSession } from '../session/SessionContext';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { Badge, ShortId, Time, useLang, useWord } from '../ui/bits';
import { NotAvailableToRole, ProblemText } from '../ui/states';
import { WriteProblem } from '../ui/WriteProblem';
import { formatNumber } from '../../shared/i18n/format';
import { inClinic } from '../ui/clinic';

type Run = components['schemas']['BackfillOut'];
type Reading = components['schemas']['InterpretReplyOut'];

/**
 * An offer run for a freed slot (front desk). "Prepare offer" starts it: the waitlist is ranked by clinical priority and the run stops at
 * the first pause, where a person decides to Send, Skip or Stop. Send holds the slot for the current candidate and records the offer; the
 * SYSTEM SENDS NOTHING to a patient (the front desk delivers the offer by hand from the Offers tab). When the run waits for the patient's
 * reply, the person picks the stored reply, reads it, may ask for a suggested reading, and confirms what to enter.
 */
export function BackfillPanel({ slotId, visitTypeId, onClose }: { slotId: string; visitTypeId: string; onClose?: () => void }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active, hasRole } = useSession();
  const lang = useLang();
  const word = useWord();
  const queryClient = useQueryClient();
  const [starting, setStarting] = useState(false);
  const [deciding, setDeciding] = useState<BackfillAction | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const state = useApiQuery(['backfill', slotId], () => unwrap(api.backfillState(slotId)));
  const waitlist = useApiQuery(['backfill-waitlist', visitTypeId], async () => inClinic(await unwrap(api.listWaitlist(visitTypeId, active.clinicId)), active.clinicId));
  const canAct = hasRole(['front_desk']);

  const start = useIdempotentWrite(
    (_: void, key) => unwrap(api.startBackfill({ clinic_id: active.clinicId, slot_id: slotId, visit_type_id: visitTypeId }, { idempotencyKey: key })),
    () => {
      setStarting(false);
      setNote(t('backfill.started'));
      void queryClient.invalidateQueries();
    },
  );
  const decide = useIdempotentWrite(
    (action: BackfillAction, key) => unwrap(api.decideBackfill(slotId, action, { idempotencyKey: key })),
    (r) => {
      const action = deciding;
      setDeciding(null);
      setNote(t(`backfill.decided.${action ?? 'stop'}`, { finished: r.finished ? t('common.yes') : t('common.no') }));
      void queryClient.invalidateQueries();
    },
  );

  const noRun = state.isError && state.error.kind === 'not_found';
  const run: Run | undefined = state.data;
  return (
    <section aria-labelledby={`bf-${slotId}`} className="state state-empty">
      <h3 id={`bf-${slotId}`}>
        {t('backfill.title')} <ShortId id={slotId} />
      </h3>
      <p className="hint">{t('backfill.note')}</p>
      {note && <p role="status">{note}</p>}
      {state.isPending && (
        <p role="status" className="loading">
          {t('common.loading')}
        </p>
      )}
      {state.isError && state.error.kind === 'forbidden' && <NotAvailableToRole />}
      {state.isError && state.error.kind === 'unavailable' && <p role="status">{t('backfill.notConfigured')}</p>}
      {state.isError && !noRun && state.error.kind !== 'forbidden' && state.error.kind !== 'unavailable' && (
        <p role="alert">
          <ProblemText kind={state.error.kind} />
        </p>
      )}
      {noRun && <p role="status">{t('backfill.noRun')}</p>}

      {run && (
        <dl className="facts">
          <dt>{t('backfill.status')}</dt>
          <dd>
            <Badge tone={run.finished ? 'ok' : 'warn'}>{word('backfill.statuses', run.status)}</Badge>
          </dd>
          <dt>{t('backfill.waitingFor')}</dt>
          <dd>{t(`backfill.waiting.${run.waiting_for}`)}</dd>
          <dt>{t('backfill.candidate')}</dt>
          <dd>
            {run.candidate_patient_id ? (
              <>
                <ShortId id={run.candidate_patient_id} /> {run.candidate_rank !== null && <>({t('backfill.rank', { rank: formatNumber(run.candidate_rank, lang) })})</>}
              </>
            ) : (
              '–'
            )}
          </dd>
          <dt>{t('backfill.hold')}</dt>
          <dd>{run.hold_id ? <ShortId id={run.hold_id} /> : '–'}</dd>
          <dt>{t('backfill.task')}</dt>
          <dd>{run.task_id ? <ShortId id={run.task_id} /> : t('backfill.noTask')}</dd>
          <dt>{t('backfill.finished')}</dt>
          <dd>{run.finished ? t('common.yes') : t('common.no')}</dd>
        </dl>
      )}

      <h4>{t('backfill.ranked')}</h4>
      {waitlist.isPending && (
        <p role="status" className="loading">
          {t('common.loading')}
        </p>
      )}
      {waitlist.isError && (
        <p role="alert">
          <ProblemText kind={waitlist.error.kind} />
        </p>
      )}
      {waitlist.isSuccess && waitlist.data.items.length === 0 && <p>{t('backfill.noWaiting')}</p>}
      {waitlist.isSuccess && waitlist.data.items.length > 0 && (
        <table>
          <caption className="visually-hidden">{t('backfill.ranked')}</caption>
          <thead>
            <tr>
              <th scope="col">{t('scheduling.col.rank')}</th>
              <th scope="col">{t('scheduling.col.patient')}</th>
              <th scope="col">{t('scheduling.col.priority')}</th>
              <th scope="col">{t('scheduling.col.joined')}</th>
              <th scope="col">{t('backfill.current')}</th>
            </tr>
          </thead>
          <tbody>
            {waitlist.data.items.map((w) => (
              <tr key={w.waitlist_id}>
                <th scope="row">{formatNumber(w.rank, lang)}</th>
                <td>
                  <ShortId id={w.patient_id} />
                </td>
                <td>
                  <code>{w.clinical_priority}</code>
                </td>
                <td>
                  <Time value={w.joined_at} />
                </td>
                <td>{run?.candidate_patient_id === w.patient_id ? <Badge tone="warn">{t('backfill.currentYes')}</Badge> : '–'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {canAct && (
        <div className="toolbar">
          {noRun && (
            <button type="button" className="primary" onClick={() => { setNote(null); setStarting(true); }}>
              {t('backfill.prepare')}
            </button>
          )}
          {run?.waiting_for === 'send_decision' &&
            (['send', 'skip', 'stop'] as const).map((a) => (
              <button key={a} type="button" onClick={() => { setNote(null); setDeciding(a); }}>
                {t(`backfill.action.${a}`)}
              </button>
            ))}
          <button type="button" onClick={() => void state.refetch()} disabled={state.isFetching}>
            {t('common.refresh')}
          </button>
          {onClose && (
            <button type="button" onClick={onClose}>
              {t('backfill.close')}
            </button>
          )}
        </div>
      )}

      {run?.waiting_for === 'reply_entry' && canAct && run.candidate_patient_id && (
        <ReplyEntry
          slotId={slotId}
          patientId={run.candidate_patient_id}
          onDone={(finished) => {
            setNote(t('backfill.replyEntered', { finished: finished ? t('common.yes') : t('common.no') }));
            void queryClient.invalidateQueries();
          }}
        />
      )}

      {starting && (
        <ConfirmDialog
          title={t('backfill.prepare')}
          confirmLabel={t('backfill.prepare')}
          busy={start.isPending}
          onConfirm={() => start.mutate()}
          onCancel={() => {
            start.reset();
            setStarting(false);
          }}
          status={<WriteProblem problem={start.error} calm={{ unavailable: t('backfill.notConfigured') }} />}
        >
          <p>{t('backfill.prepareBody')}</p>
        </ConfirmDialog>
      )}
      {deciding && (
        <ConfirmDialog
          title={t(`backfill.confirm.${deciding}.title`)}
          confirmLabel={t(`backfill.action.${deciding}`)}
          busy={decide.isPending}
          onConfirm={() => decide.mutate(deciding)}
          onCancel={() => {
            if (decide.error?.kind === 'conflict') void queryClient.invalidateQueries();
            decide.reset();
            setDeciding(null);
          }}
          status={<WriteProblem problem={decide.error} calm={{ conflict: t('backfill.notWaitingForThis'), unavailable: t('backfill.notConfigured') }} />}
        >
          <p>{t(`backfill.confirm.${deciding}.body`)}</p>
        </ConfirmDialog>
      )}
    </section>
  );
}

// ---- the patient's reply: pick the stored message, read it, see a suggested reading, then confirm what to enter ----------------

function ReplyEntry({ slotId, patientId, onDone }: { slotId: string; patientId: string; onDone: (finished: boolean) => void }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const word = useWord();
  const lang = useLang();
  const queryClient = useQueryClient();
  const { active } = useSession();
  const [messageId, setMessageId] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<'replied' | 'timed_out' | null>(null);
  // What the patient told the front desk, typed here. Stored (encrypted) on the patient's appointments thread only after a confirmation, and
  // dropped from the page once it is saved. Nothing is sent to the patient.
  const [typed, setTyped] = useState('');
  const [savingReply, setSavingReply] = useState(false);
  const [savedOutcome, setSavedOutcome] = useState<'clear' | 'red_flag' | 'unassessable' | null>(null);

  // The patient's appointments thread holds the stored reply. Only headers are listed (a time and who it is from), never the text.
  const thread = useApiQuery(['reply-thread', patientId], async () => {
    const threads = await unwrap(api.listThreads(patientId, 'appointments'));
    const first = threads.items[0];
    return first ? (await unwrap(api.listThreadMessages(first.thread_id))).items.filter((m) => !m.erased) : [];
  });

  // The suggested reading: a suggestion only. The person confirms what is entered, and the red-flag rules ran first on the backend.
  const reading = useIdempotentWrite(
    (id: string, key) => unwrap(api.interpretReply(id, { idempotencyKey: key })),
  );
  // The text itself, read on a click (an audited read), held only while shown.
  const text = useMutation<string, ApiProblem, string>({
    gcTime: 0,
    retry: false,
    mutationFn: async (id) => {
      try {
        return (await unwrap(api.readMessage(id))).body;
      } catch (e) {
        throw toProblem(e);
      }
    },
  });
  const enter = useIdempotentWrite(
    (outcome: 'replied' | 'timed_out', key) =>
      unwrap(api.replyBackfill(slotId, outcome === 'replied' ? { outcome, message_id: messageId } : { outcome }, { idempotencyKey: key })),
    (r) => {
      setConfirming(null);
      text.reset();
      reading.reset();
      void queryClient.invalidateQueries();
      onDone(r.finished);
    },
  );
  const save = useIdempotentWrite(
    (_: void, key) => unwrap(api.recordReply({ clinic_id: active.clinicId, patient_id: patientId, text: typed }, { idempotencyKey: key })),
    (r) => {
      setSavingReply(false);
      setTyped(''); // the text is gone from the page: it is stored, and read back only on a click
      setMessageId(r.message_id);
      setSavedOutcome(r.outcome);
      text.reset();
      reading.reset();
      enter.reset();
      void queryClient.invalidateQueries();
    },
  );
  const pick = (id: string) => {
    setMessageId(id);
    text.reset();
    reading.reset();
    enter.reset();
  };
  const r: Reading | undefined = reading.data;

  return (
    <div>
      <h4>{t('backfill.reply.title')}</h4>
      <p className="hint">{t('backfill.reply.note')}</p>
      <h5>{t('backfill.reply.typeTitle')}</h5>
      <label htmlFor={`reply-text-${slotId}`}>{t('backfill.reply.typeLabel')}</label>
      <textarea
        id={`reply-text-${slotId}`}
        rows={4}
        maxLength={10000}
        value={typed}
        onChange={(e) => {
          setTyped(e.target.value);
          save.reset(); // a different text is a different request: never reuse the old key
        }}
      />
      <p>
        <button type="button" disabled={typed.trim() === ''} onClick={() => setSavingReply(true)}>
          {t('backfill.reply.save')}
        </button>
      </p>
      {savedOutcome && (
        <p role="status">
          {t('backfill.reply.saved')} {t(`backfill.reply.savedOutcome.${savedOutcome}`)}
        </p>
      )}
      <h5>{t('backfill.reply.storedTitle')}</h5>
      {thread.isPending && (
        <p role="status" className="loading">
          {t('common.loading')}
        </p>
      )}
      {thread.isError && (
        <p role="alert">
          <ProblemText kind={thread.error.kind} />
        </p>
      )}
      {thread.isSuccess && thread.data.length === 0 && <p role="status">{t('backfill.reply.none')}</p>}
      {thread.isSuccess && thread.data.length > 0 && (
        <fieldset>
          <legend>{t('backfill.reply.pick')}</legend>
          {thread.data.map((m) => (
            <label key={m.message_id} className="choice">
              <input type="radio" name="reply-message" checked={messageId === m.message_id} onChange={() => pick(m.message_id)} />
              <span>
                <Time value={m.created_at} /> · {word('access.sender', m.sender_kind)} · <ShortId id={m.message_id} />
              </span>
            </label>
          ))}
        </fieldset>
      )}

      {messageId && (
        <p>
          <button type="button" disabled={text.isPending} onClick={() => text.mutate(messageId)}>
            {t('backfill.reply.read')}
          </button>{' '}
          <button type="button" disabled={reading.isPending} onClick={() => reading.mutate(messageId)}>
            {t('backfill.reply.suggest')}
          </button>
        </p>
      )}
      {text.isSuccess && (
        <blockquote className="reply-text">
          {text.data}
          <br />
          <button type="button" onClick={() => text.reset()}>
            {t('backfill.reply.hide')}
          </button>
        </blockquote>
      )}
      {text.isError && (
        <p role="alert">
          <ProblemText kind={text.error.kind} />
        </p>
      )}

      {reading.error && <WriteProblem problem={reading.error} calm={{ unavailable: t('backfill.reply.notConfigured'), conflict: t('backfill.reply.erased') }} />}
      {r && (
        <div role="status" className="state state-empty">
          <h5>{t('backfill.reply.suggested')}</h5>
          <dl className="facts">
            <dt>{t('backfill.reply.screening')}</dt>
            <dd>
              <Badge tone={r.outcome === 'clear' ? 'ok' : 'urgent'}>{t(`backfill.reply.outcome.${r.outcome}`)}</Badge>
            </dd>
            <dt>{t('backfill.reply.suggestion')}</dt>
            <dd>{r.suggestion ? t(`backfill.reply.suggestions.${r.suggestion}`) : t('backfill.reply.noSuggestion')}</dd>
            {r.reason && (
              <>
                <dt>{t('backfill.reply.why')}</dt>
                <dd>{t(`backfill.reply.reasons.${r.reason}`)}</dd>
              </>
            )}
            <dt>{t('backfill.reply.needsPerson')}</dt>
            <dd>{r.needs_person ? t('common.yes') : t('common.no')}</dd>
            <dt>{t('backfill.reply.confidence')}</dt>
            <dd>{r.confidence === null ? <span className="not-recorded">{t('review.notRecorded')}</span> : formatNumber(r.confidence, lang, { style: 'percent', maximumFractionDigits: 0 })}</dd>
            <dt>{t('backfill.reply.engine')}</dt>
            <dd>{r.engine ?? <span className="not-recorded">{t('review.notRecorded')}</span>}</dd>
          </dl>
          {r.outcome !== 'clear' && <p role="note">{t('backfill.reply.screenedNote')}</p>}
          <p className="hint">{t('backfill.reply.yourCall')}</p>
        </div>
      )}

      <div className="toolbar">
        <button type="button" className="primary" disabled={!messageId} onClick={() => setConfirming('replied')}>
          {t('backfill.reply.enter')}
        </button>
        <button type="button" onClick={() => setConfirming('timed_out')}>
          {t('backfill.reply.noReply')}
        </button>
      </div>

      {savingReply && (
        <ConfirmDialog
          title={t('backfill.reply.saveTitle')}
          confirmLabel={t('backfill.reply.save')}
          busy={save.isPending}
          onConfirm={() => save.mutate()}
          onCancel={() => {
            save.reset(); // what was typed stays in the box so it can be corrected and saved again
            setSavingReply(false);
          }}
          status={
            <WriteProblem
              problem={save.error}
              calm={{
                unavailable: t('backfill.reply.saveUnavailable'),
                conflict: t('backfill.reply.saveConflict'),
                invalid: t('backfill.reply.saveInvalid'),
                not_found: t('backfill.reply.saveNotFound'),
              }}
            />
          }
        >
          <p>{t('backfill.reply.saveBody')}</p>
        </ConfirmDialog>
      )}
      {confirming && (
        <ConfirmDialog
          title={t(confirming === 'replied' ? 'backfill.reply.enterTitle' : 'backfill.reply.noReplyTitle')}
          confirmLabel={t(confirming === 'replied' ? 'backfill.reply.enter' : 'backfill.reply.noReply')}
          busy={enter.isPending}
          onConfirm={() => enter.mutate(confirming)}
          onCancel={() => {
            if (enter.error?.kind === 'conflict') void queryClient.invalidateQueries();
            enter.reset();
            setConfirming(null);
          }}
          status={<WriteProblem problem={enter.error} calm={{ conflict: t('backfill.reply.notWaiting'), unavailable: t('backfill.notConfigured') }} />}
        >
          <p>{t(confirming === 'replied' ? 'backfill.reply.enterBody' : 'backfill.reply.noReplyBody')}</p>
        </ConfirmDialog>
      )}
    </div>
  );
}
