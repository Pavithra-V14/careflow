import { useState, type FormEvent } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { components } from '../../shared/api/schema';
import { ApiProblem, toProblem, unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { usePatientLabels } from '../components/PatientLabels';
import { useApiQuery, useIdempotentWrite } from '../hooks';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { Badge, PageHeading, ShortId, Time, UUID, useWord } from '../ui/bits';
import { QueryView } from '../ui/states';
import { WriteProblem } from '../ui/WriteProblem';
import { formatNumber } from '../../shared/i18n/format';
import { useLang } from '../ui/bits';

type Thread = components['schemas']['ThreadOut'];
type Header = components['schemas']['MessageHeaderOut'];
type Topic = components['schemas']['ThreadOut']['topic'];
const TOPICS: readonly Topic[] = ['care', 'appointments', 'safety'];

/**
 * Message threads of the clinic (front desk, nurse, doctor). The lists carry ids, a closed topic, the sender kind and times, never a body.
 * A body is read one message at a time, only after a confirmation (every reading is audited), shown as plain text while the person keeps
 * it open, and never kept: it is held in this screen's state and goes when it is hidden or the screen closes. Screening a message runs the
 * red-flag rules on the stored body and records the outcome; no reply goes to a patient from here.
 */
export function MessagesPage() {
  const { t } = useTranslation();
  const { api } = useAuth();
  const word = useWord();
  const lang = useLang();
  const labels = usePatientLabels();
  const [topic, setTopic] = useState<Topic | ''>('');
  const [patient, setPatient] = useState('');
  const [open, setOpen] = useState<string | null>(null);
  const patientFilter = UUID.test(patient.trim()) ? patient.trim() : undefined;
  const threads = useApiQuery(['staff-threads', topic, patientFilter ?? ''], () => unwrap(api.listThreads(patientFilter, topic === '' ? undefined : topic)));

  return (
    <section aria-labelledby="msg-title">
      <PageHeading id="msg-title">{t('messages.title')}</PageHeading>
      <p className="hint">{t('messages.note')}</p>
      <form className="toolbar" onSubmit={(e: FormEvent) => e.preventDefault()}>
        <label htmlFor="msg-topic">{t('messages.topic')}</label>
        <select
          id="msg-topic"
          value={topic}
          onChange={(e) => {
            setTopic(TOPICS.find((x) => x === e.target.value) ?? '');
            setOpen(null);
          }}
        >
          <option value="">{t('messages.allTopics')}</option>
          {TOPICS.map((x) => (
            <option key={x} value={x}>
              {word('messages.topics', x)}
            </option>
          ))}
        </select>
        <label htmlFor="msg-patient">{t('messages.patientFilter')}</label>
        <input
          id="msg-patient"
          type="text"
          autoComplete="off"
          spellCheck={false}
          value={patient}
          aria-invalid={patient.trim() !== '' && !patientFilter}
          onChange={(e) => {
            setPatient(e.target.value);
            setOpen(null);
          }}
        />
        <button type="button" onClick={() => void threads.refetch()} disabled={threads.isFetching}>
          {t('common.refresh')}
        </button>
      </form>
      {patient.trim() !== '' && !patientFilter && <p className="hint">{t('messages.patientInvalid')}</p>}
      <QueryView query={threads} isEmpty={(d) => d.items.length === 0} empty={t('messages.empty')}>
        {(d) => (
          <>
            {labels.panel(d.items.map((th) => th.patient_id))}
            <table>
              <caption className="visually-hidden">{t('messages.title')}</caption>
              <thead>
                <tr>
                  <th scope="col">{t('messages.col.thread')}</th>
                  <th scope="col">{t('messages.col.patient')}</th>
                  <th scope="col">{t('messages.col.topic')}</th>
                  <th scope="col">{t('messages.col.status')}</th>
                  <th scope="col">{t('messages.col.count')}</th>
                  <th scope="col">{t('messages.col.last')}</th>
                  <th scope="col">{t('messages.col.action')}</th>
                </tr>
              </thead>
              <tbody>
                {d.items.map((th: Thread) => (
                  <tr key={th.thread_id}>
                    <th scope="row">
                      <ShortId id={th.thread_id} />
                    </th>
                    <td>{labels.cell(th.patient_id)}</td>
                    <td>{word('messages.topics', th.topic)}</td>
                    <td>
                      <Badge tone={th.status === 'open' ? 'warn' : 'neutral'}>{word('messages.threadStatus', th.status)}</Badge>
                    </td>
                    <td>{formatNumber(th.message_count, lang)}</td>
                    <td>
                      <Time value={th.last_message_at} />
                    </td>
                    <td>
                      <button type="button" aria-expanded={open === th.thread_id} aria-label={`${open === th.thread_id ? t('messages.hide') : t('messages.open')} ${th.thread_id.slice(0, 8)}`} onClick={() => setOpen(open === th.thread_id ? null : th.thread_id)}>
                        {open === th.thread_id ? t('messages.hide') : t('messages.open')}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {d.has_more && <p className="hint">{t('common.moreExist')}</p>}
          </>
        )}
      </QueryView>
      {open && <ThreadView key={open} threadId={open} />}
    </section>
  );
}

function ThreadView({ threadId }: { threadId: string }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const word = useWord();
  const headers = useApiQuery(['staff-thread-headers', threadId], () => unwrap(api.listThreadMessages(threadId)));
  const [reading, setReading] = useState<Header | null>(null);
  const [screening, setScreening] = useState<Header | null>(null);
  const [shown, setShown] = useState<{ id: string; body: string } | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const queryClient = useQueryClient();

  // The body is read through a mutation (not a query) so it is never cached: it lives only in `shown`, and goes when hidden.
  const read = useMutation<{ message_id: string; body: string }, ApiProblem, string>({
    gcTime: 0,
    retry: false,
    mutationFn: async (id) => {
      try {
        return await unwrap(api.readMessage(id));
      } catch (e) {
        throw toProblem(e);
      }
    },
    onSuccess: (m) => {
      setShown({ id: m.message_id, body: m.body });
      setReading(null);
    },
  });
  const screen = useIdempotentWrite(
    (h: Header, key) => unwrap(api.screenMessage(h.message_id, { idempotencyKey: key })),
    (r) => {
      setScreening(null);
      const reply = r.patient_reply ? ` ${t(`messages.screen.reply.${r.patient_reply}`)}` : '';
      setNote(`${t(`messages.screen.outcome.${r.outcome}`)}${r.replayed ? ` ${t('messages.screen.replayed')}` : ''}${reply}`);
      void queryClient.invalidateQueries();
    },
  );

  return (
    <div>
      <h3>{t('messages.threadTitle')}</h3>
      {note && <p role="status">{note}</p>}
      <QueryView query={headers} isEmpty={(d) => d.items.length === 0} empty={t('messages.noMessages')}>
        {(d) => (
          <table>
            <caption className="visually-hidden">{t('messages.threadTitle')}</caption>
            <thead>
              <tr>
                <th scope="col">{t('messages.col.message')}</th>
                <th scope="col">{t('messages.col.sender')}</th>
                <th scope="col">{t('messages.col.time')}</th>
                <th scope="col">{t('messages.col.topic')}</th>
                <th scope="col">{t('messages.col.action')}</th>
              </tr>
            </thead>
            <tbody>
              {d.items.map((h) => (
                <tr key={h.message_id}>
                  <th scope="row">
                    <ShortId id={h.message_id} />
                  </th>
                  <td>{word('messages.sender', h.sender_kind)}</td>
                  <td>
                    <Time value={h.created_at} />
                  </td>
                  <td>{word('messages.topics', h.topic)}</td>
                  <td>
                    {h.erased ? (
                      <Badge tone="neutral">{t('messages.erased')}</Badge>
                    ) : (
                      <>
                        {shown?.id === h.message_id ? (
                          <button type="button" onClick={() => setShown(null)}>
                            {t('messages.hideBody')}
                          </button>
                        ) : (
                          <button
                            type="button"
                            aria-label={`${t('messages.read')} ${h.message_id.slice(0, 8)}`}
                            onClick={() => {
                              read.reset();
                              setReading(h);
                            }}
                          >
                            {t('messages.read')}
                          </button>
                        )}{' '}
                        <button
                          type="button"
                          aria-label={`${t('messages.screenButton')} ${h.message_id.slice(0, 8)}`}
                          onClick={() => {
                            setNote(null);
                            screen.reset();
                            setScreening(h);
                          }}
                        >
                          {t('messages.screenButton')}
                        </button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </QueryView>
      {shown && (
        <section aria-labelledby="msg-body-title">
          <h3 id="msg-body-title">{t('messages.bodyTitle')}</h3>
          {/* The text exactly as stored, as plain text, only while it is open. */}
          <blockquote className="pre-wrap">{shown.body}</blockquote>
        </section>
      )}
      {reading && (
        <ConfirmDialog
          title={t('messages.readTitle')}
          confirmLabel={t('messages.read')}
          busy={read.isPending}
          onConfirm={() => read.mutate(reading.message_id)}
          onCancel={() => {
            read.reset();
            setReading(null);
          }}
          status={<WriteProblem problem={read.error} forbidden={t('messages.readRefused')} calm={{ not_found: t('messages.readGone') }} details={{}} />}
        >
          <p>{t('messages.readBody')}</p>
        </ConfirmDialog>
      )}
      {screening && (
        <ConfirmDialog
          title={t('messages.screenTitle')}
          confirmLabel={t('messages.screenButton')}
          busy={screen.isPending}
          onConfirm={() => screen.mutate(screening)}
          onCancel={() => {
            screen.reset();
            setScreening(null);
          }}
          status={<WriteProblem problem={screen.error} forbidden={t('messages.screenRefused')} calm={{ not_found: t('messages.readGone'), conflict: t('messages.screenErased'), unavailable: t('messages.screenUnavailable') }} />}
        >
          <p>{t('messages.screenBody')}</p>
        </ConfirmDialog>
      )}
    </div>
  );
}
