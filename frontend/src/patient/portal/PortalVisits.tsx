import { useState, type FormEvent } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { Api } from '../../shared/api/client';
import type { components } from '../../shared/api/schema';
import { unwrap, type ApiProblem } from '../../staff/apiState';
import { useIdempotentWrite } from '../../staff/hooks';
import { ConfirmDialog } from '../../staff/ui/ConfirmDialog';
import { Badge, Time, UUID, useWord } from '../../staff/ui/bits';
import { ProblemText } from '../../staff/ui/states';
import { WriteProblem } from '../../staff/ui/WriteProblem';
import { usePortalQuery } from './portalQuery';
import { useClinicField } from './PortalSections';

type Booking = components['schemas']['PatientBookingOut'];
type Slot = components['schemas']['PatientSlotOut'];
type Topic = components['schemas']['PatientMessageIn']['topic'];

const TOPICS: readonly Topic[] = ['care', 'appointments'];

const Loading = () => {
  const { t } = useTranslation();
  return (
    <p role="status" className="loading">
      {t('common.loading')}
    </p>
  );
};

/** A failed read. 503 is the owner's switch being off (a normal state, said calmly); 403 is the calm "not available" line. */
function ReadProblem({ problem, off }: { problem: ApiProblem; off: string }) {
  const { t } = useTranslation();
  if (problem.kind === 'unavailable') return <p role="status">{off}</p>;
  if (problem.kind === 'forbidden') return <p role="status">{t('portal.notAvailable')}</p>;
  return (
    <p role="alert">
      <ProblemText kind={problem.kind} />
    </p>
  );
}

// ---- appointments: your own bookings (cancel a future one) and the open slots the hospital lets patients book --------------------------

export function BookingSection({ api }: { api: Api }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [clinic, setClinic] = useState('');
  const [cancelling, setCancelling] = useState<Booking | null>(null);
  const [booking, setBooking] = useState<Slot | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const mine = usePortalQuery(['portal-bookings'], () => unwrap(api.listMyBookings()));
  const clinicField = useClinicField(api, 'bk', clinic, setClinic);
  const clinicId = clinicField.clinicId;
  const slotsEnabled = clinicField.ready && UUID.test(clinicId);
  const slots = usePortalQuery(['portal-slots', clinicId], () => (slotsEnabled ? unwrap(api.listOpenSlots(clinicId)) : Promise.resolve(null)));
  const off = mine.isError && mine.error.kind === 'unavailable';

  const cancel = useIdempotentWrite(
    (b: Booking, key) => unwrap(api.cancelMyBooking(b.booking_id, { idempotencyKey: key })),
    () => {
      setCancelling(null);
      setNote(t('portal.booking.cancelled'));
      void queryClient.invalidateQueries({ queryKey: ['portal-bookings'] });
      void queryClient.invalidateQueries({ queryKey: ['portal-slots'] });
    },
  );
  const book = useIdempotentWrite(
    (s: Slot, key) => unwrap(api.bookOpenSlot(s.slot_id, { idempotencyKey: key })),
    (r) => {
      setBooking(null);
      setNote(r.created ? t('portal.booking.booked') : t('portal.booking.alreadyBooked'));
      void queryClient.invalidateQueries({ queryKey: ['portal-bookings'] });
      void queryClient.invalidateQueries({ queryKey: ['portal-slots'] });
    },
  );

  return (
    <section aria-labelledby="portal-booking">
      <h2 id="portal-booking">{t('portal.booking.title')}</h2>
      {note && <p role="status">{note}</p>}
      {off ? (
        <p role="status">{t('portal.booking.off')}</p>
      ) : (
        <>
          <h3>{t('portal.booking.mine')}</h3>
          {mine.isPending && <Loading />}
          {mine.isError && <ReadProblem problem={mine.error} off={t('portal.booking.off')} />}
          {mine.isSuccess && mine.data.bookings.length === 0 && <p>{t('portal.booking.none')}</p>}
          {mine.isSuccess && mine.data.bookings.length > 0 && (
            <ul className="plain-list">
              {mine.data.bookings.map((b) => (
                <li key={b.booking_id}>
                  <Badge tone={b.status === 'confirmed' ? 'ok' : 'neutral'}>{t(`portal.booking.status.${b.status}`)}</Badge> {b.visit_type_name} · {b.location_name}
                  <br />
                  <Time value={b.starts_at} /> – <Time value={b.ends_at} />
                  {b.cancellable && (
                    <>
                      {' '}
                      <button type="button" className="button button-secondary" aria-label={`${t('portal.booking.cancel')}: ${b.visit_type_name}`} onClick={() => setCancelling(b)}>
                        {t('portal.booking.cancel')}
                      </button>
                    </>
                  )}
                </li>
              ))}
            </ul>
          )}
          <h3>{t('portal.booking.open')}</h3>
          <p>{t('portal.booking.openBody')}</p>
          {clinicField.node}
          {!slotsEnabled && clinicField.ready && <p className="hint">{t('portal.booking.needClinic')}</p>}
          {slotsEnabled && slots.isPending && <Loading />}
          {slotsEnabled && slots.isError && <ReadProblem problem={slots.error} off={t('portal.booking.off')} />}
          {slotsEnabled && slots.isSuccess && slots.data !== null && slots.data.slots.length === 0 && <p>{t('portal.booking.noSlots')}</p>}
          {slotsEnabled && slots.isSuccess && slots.data !== null && slots.data.slots.length > 0 && (
            <ul className="plain-list">
              {slots.data.slots.map((s) => (
                <li key={s.slot_id}>
                  {s.visit_type_name} · {s.location_name}
                  <br />
                  <Time value={s.starts_at} /> – <Time value={s.ends_at} />{' '}
                  <button type="button" className="button button-secondary" aria-label={`${t('portal.booking.book')}: ${s.visit_type_name}`} onClick={() => setBooking(s)}>
                    {t('portal.booking.book')}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      {cancelling && (
        <ConfirmDialog
          title={t('portal.booking.cancelTitle')}
          confirmLabel={t('portal.booking.cancel')}
          busy={cancel.isPending}
          onConfirm={() => cancel.mutate(cancelling)}
          onCancel={() => {
            cancel.reset();
            setCancelling(null);
          }}
          status={
            <WriteProblem
              problem={cancel.error}
              forbidden={t('portal.notAvailable')}
              calm={{ not_found: t('portal.booking.cancelGone'), conflict: t('portal.booking.cancelConflict'), invalid: t('portal.booking.cancelConflict'), unavailable: t('portal.booking.off') }}
            />
          }
        >
          <p>
            {cancelling.visit_type_name} · <Time value={cancelling.starts_at} />
          </p>
          <p>{t('portal.booking.cancelBody')}</p>
        </ConfirmDialog>
      )}
      {booking && (
        <ConfirmDialog
          title={t('portal.booking.bookTitle')}
          confirmLabel={t('portal.booking.book')}
          busy={book.isPending}
          onConfirm={() => book.mutate(booking)}
          onCancel={() => {
            book.reset();
            setBooking(null);
          }}
          status={
            <WriteProblem
              problem={book.error}
              forbidden={t('portal.booking.bookRefused')}
              calm={{ conflict: t('portal.booking.bookConflict'), invalid: t('portal.booking.bookRefused'), unavailable: t('portal.booking.off') }}
            />
          }
        >
          <p>
            {booking.visit_type_name} · {booking.location_name}
          </p>
          <p>
            <Time value={booking.starts_at} /> – <Time value={booking.ends_at} />
          </p>
        </ConfirmDialog>
      )}
    </section>
  );
}

// ---- messages to the hospital: your own threads, and a new message on care or appointments ---------------------------------------------

export function MessagesSection({ api }: { api: Api }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const word = useWord();
  const [clinic, setClinic] = useState('');
  const [topic, setTopic] = useState<Topic | ''>('');
  const [text, setText] = useState('');
  const [open, setOpen] = useState(false);
  const [thread, setThread] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const threads = usePortalQuery(['portal-threads'], () => unwrap(api.listMyThreads()));
  const off = threads.isError && threads.error.kind === 'unavailable';
  const send = useIdempotentWrite(
    (v: { clinicId: string; topic: Topic }, key) => unwrap(api.sendPatientMessage({ clinic_id: v.clinicId, topic: v.topic, text }, { idempotencyKey: key })),
    () => {
      setOpen(false);
      setNote(t('portal.messages.sent')); // the same words whatever the screening found; the answer says nothing about it
      setText(''); // the text is dropped from the page once it is sent
      void queryClient.invalidateQueries({ queryKey: ['portal-threads'] });
      void queryClient.invalidateQueries({ queryKey: ['portal-thread'] });
    },
  );
  const clinicField = useClinicField(api, 'ms', clinic, (v) => {
    setClinic(v);
    send.reset();
  });
  const valid = UUID.test(clinicField.clinicId) && topic !== '' && text.trim() !== '' && text.length <= 10000;
  const touch = (set: (v: string) => void) => (v: string) => {
    set(v);
    setNote(null);
    send.reset();
  };
  return (
    <section aria-labelledby="portal-messages">
      <h2 id="portal-messages">{t('portal.messages.title')}</h2>
      {note && <p role="status">{note}</p>}
      {off ? (
        <p role="status">{t('portal.messages.off')}</p>
      ) : (
        <>
          <form
            onSubmit={(e: FormEvent) => {
              e.preventDefault();
              if (valid) setOpen(true);
            }}
            autoComplete="off"
            noValidate
          >
            {clinicField.node}
            <label htmlFor="ms-topic">{t('portal.messages.topic')}</label>
            {/* Nothing is pre-chosen: the person picks what the message is about. */}
            <select
              id="ms-topic"
              value={topic}
              onChange={(e) => {
                setTopic(TOPICS.find((x) => x === e.target.value) ?? '');
                setNote(null);
                send.reset();
              }}
            >
              <option value="">{t('portal.messages.chooseTopic')}</option>
              {TOPICS.map((x) => (
                <option key={x} value={x}>
                  {t(`portal.messages.topics.${x}`)}
                </option>
              ))}
            </select>
            <label htmlFor="ms-text">{t('portal.messages.text')}</label>
            <textarea id="ms-text" rows={5} maxLength={10000} value={text} onChange={(e) => touch(setText)(e.target.value)} />
            <button type="submit" className="button button-primary" disabled={!valid}>
              {t('portal.messages.send')}
            </button>
          </form>
          <h3>{t('portal.messages.mine')}</h3>
          {threads.isPending && <Loading />}
          {threads.isError && <ReadProblem problem={threads.error} off={t('portal.messages.off')} />}
          {threads.isSuccess && threads.data.threads.length === 0 && <p>{t('portal.messages.none')}</p>}
          {threads.isSuccess && threads.data.threads.length > 0 && (
            <ul className="plain-list">
              {threads.data.threads.map((th) => (
                <li key={th.thread_id}>
                  {word('portal.messages.topics', th.topic)} · {t('portal.messages.count', { n: th.message_count })}
                  {th.last_message_at && (
                    <>
                      {' '}
                      · <Time value={th.last_message_at} />
                    </>
                  )}{' '}
                  <button type="button" className="button button-secondary" aria-expanded={thread === th.thread_id} onClick={() => setThread(thread === th.thread_id ? null : th.thread_id)}>
                    {thread === th.thread_id ? t('portal.messages.hide') : t('portal.messages.read')}
                  </button>
                  {thread === th.thread_id && <ThreadMessages api={api} threadId={th.thread_id} />}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      {open && (
        <ConfirmDialog
          title={t('portal.messages.confirmTitle')}
          confirmLabel={t('portal.messages.send')}
          busy={send.isPending}
          onConfirm={() => topic !== '' && send.mutate({ clinicId: clinicField.clinicId, topic })}
          onCancel={() => {
            send.reset();
            setOpen(false);
          }}
          status={<WriteProblem problem={send.error} forbidden={t('portal.messages.refused')} calm={{ invalid: t('portal.messages.invalid'), unavailable: t('portal.messages.off') }} />}
        >
          <p>{t('portal.messages.confirmBody')}</p>
        </ConfirmDialog>
      )}
    </section>
  );
}

function ThreadMessages({ api, threadId }: { api: Api; threadId: string }) {
  const { t } = useTranslation();
  const word = useWord();
  const messages = usePortalQuery(['portal-thread', threadId], () => unwrap(api.readMyThread(threadId)));
  return (
    <div>
      {messages.isPending && <Loading />}
      {messages.isError &&
        (messages.error.kind === 'not_found' ? (
          <p role="status">{t('portal.messages.gone')}</p>
        ) : (
          <ReadProblem problem={messages.error} off={t('portal.messages.off')} />
        ))}
      {messages.isSuccess && messages.data.messages.length === 0 && <p>{t('portal.messages.empty')}</p>}
      {messages.isSuccess && messages.data.messages.length > 0 && (
        <ol className="plain-list">
          {messages.data.messages.map((m) => (
            <li key={m.message_id}>
              <strong>{word('portal.messages.sender', m.sender)}</strong> · <Time value={m.created_at} />
              <br />
              {/* The text exactly as stored, as plain text. */}
              {m.erased ? <em>{t('portal.erased')}</em> : m.text === null || !m.readable ? <em>{t('portal.unreadable')}</em> : <span style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{m.text}</span>}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
