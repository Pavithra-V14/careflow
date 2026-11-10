import { useState, type ReactNode } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { components } from '../../shared/api/schema';
import type { OfferState } from '../../shared/api/client';
import { ApiProblem, toProblem, unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { PatientPicker } from '../components/PatientPicker';
import { useApiQuery, useIdempotentWrite } from '../hooks';
import { CAN_SCHEDULE, CAN_SET_UP } from '../sections';
import { useSession } from '../session/SessionContext';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { Badge, PlaceholderNote, ShortId, Time, useLang, useWord } from '../ui/bits';
import { NotAvailableToRole, ProblemText, QueryView } from '../ui/states';
import { formatNumber } from '../../shared/i18n/format';
import { BackfillPanel } from './BackfillPanel';
import { clinicLocalToIso } from '../../shared/clinicTime';
import { ZoneNote } from '../components/ZoneNote';

type Slot = components['schemas']['SlotOut'];

// ---- shared helpers --------------------------------------------------------------------------------------------------

/**
 * How a failed write is told inside its dialog. 403 is the shared "not available to your role"; 409 is a normal outcome
 * (something changed under you), so it is a calm status and not an alert; anything else is a fixed message per kind.
 */
function WriteProblem({ problem, conflict, invalid }: { problem: ApiProblem | null; conflict?: string; invalid?: string }) {
  const { t } = useTranslation();
  if (!problem) return null;
  if (problem.kind === 'forbidden') return <NotAvailableToRole />;
  if (problem.kind === 'conflict') return <p role="status">{conflict ?? t('scheduling.conflict')}</p>;
  if (problem.kind === 'invalid' && invalid) return <p role="alert">{invalid}</p>;
  return (
    <p role="alert">
      <ProblemText kind={problem.kind} />
    </p>
  );
}

const nameOf = <T,>(items: T[] | undefined, id: string, idOf: (i: T) => string, label: (i: T) => string): ReactNode => {
  const hit = items?.find((i) => idOf(i) === id);
  return hit ? label(hit) : <ShortId id={id} />;
};

/** The three reference lists, shared by several tabs under the same query keys the reference tabs use. */
function useReferences() {
  const { api } = useAuth();
  const providers = useApiQuery(['providers', false], () => unwrap(api.listProviders(false)));
  const locations = useApiQuery(['locations', false], () => unwrap(api.listLocations(false)));
  const visitTypes = useApiQuery(['visitTypes', false], () => unwrap(api.listVisitTypes(false)));
  return { providers: providers.data?.items, locations: locations.data?.items, visitTypes: visitTypes.data?.items };
}

function Table({ caption, head, children }: { caption: string; head: string[]; children: ReactNode }) {
  return (
    <table>
      <caption className="visually-hidden">{caption}</caption>
      <thead>
        <tr>
          {head.map((h) => (
            <th key={h} scope="col">
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>{children}</tbody>
    </table>
  );
}

const Refresh = ({ onClick, busy }: { onClick: () => void; busy: boolean }) => {
  const { t } = useTranslation();
  return (
    <button type="button" onClick={onClick} disabled={busy}>
      {t('common.refresh')}
    </button>
  );
};

// ---- slots: view, hold, book, and add ---------------------------------------------------------------------------------

export function SlotsList() {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { hasRole } = useSession();
  const word = useWord();
  const queryClient = useQueryClient();
  const refs = useReferences();
  const [providerId, setProviderId] = useState('');
  const [locationId, setLocationId] = useState('');
  const [acting, setActing] = useState<{ kind: 'hold' | 'book'; slot: Slot } | null>(null);
  const [adding, setAdding] = useState(false);
  const [offerFor, setOfferFor] = useState<Slot | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const query = useApiQuery(['slots', providerId, locationId], () => unwrap(api.listSlots({ providerId, locationId })));

  const canAct = hasRole(CAN_SCHEDULE);
  return (
    <>
      <div className="toolbar">
        <label>
          {t('scheduling.filterProvider')}
          <select value={providerId} onChange={(e) => setProviderId(e.target.value)}>
            <option value="">{t('scheduling.all')}</option>
            {refs.providers?.map((p) => (
              <option key={p.provider_id} value={p.provider_id}>
                {p.display_name}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t('scheduling.filterLocation')}
          <select value={locationId} onChange={(e) => setLocationId(e.target.value)}>
            <option value="">{t('scheduling.all')}</option>
            {refs.locations?.map((l) => (
              <option key={l.location_id} value={l.location_id}>
                {l.name}
              </option>
            ))}
          </select>
        </label>
        <Refresh onClick={() => void query.refetch()} busy={query.isFetching} />
        {hasRole(CAN_SET_UP) && (
          <button type="button" onClick={() => setAdding(true)}>
            {t('scheduling.addSlot')}
          </button>
        )}
      </div>
      {done && <p role="status">{done}</p>}
      <QueryView query={query} isEmpty={(d) => d.items.length === 0} empty={t('scheduling.noSlots')}>
        {(d) => (
          <>
            <Table
              caption={t('scheduling.slots')}
              head={[t('scheduling.col.starts'), t('scheduling.col.ends'), t('scheduling.col.provider'), t('scheduling.col.location'), t('scheduling.col.visitType'), t('scheduling.col.state'), t('scheduling.col.heldUntil'), t('scheduling.col.action')]}
            >
              {d.items.map((s) => (
                <tr key={s.slot_id}>
                  <th scope="row">
                    <Time value={s.starts_at} />
                  </th>
                  <td>
                    <Time value={s.ends_at} />
                  </td>
                  <td>{nameOf(refs.providers, s.provider_id, (p) => p.provider_id, (p) => p.display_name)}</td>
                  <td>{nameOf(refs.locations, s.location_id, (l) => l.location_id, (l) => l.name)}</td>
                  <td>{nameOf(refs.visitTypes, s.visit_type_id, (v) => v.visit_type_id, (v) => v.name)}</td>
                  <td>{word('scheduling.slotStatus', s.status)}</td>
                  <td>
                    <Time value={s.hold_expires_at} />
                  </td>
                  <td>
                    {canAct ? (
                      <>
                        <button type="button" onClick={() => setActing({ kind: 'hold', slot: s })} disabled={s.status !== 'open'}>
                          {t('scheduling.hold')}
                        </button>{' '}
                        <button type="button" onClick={() => setActing({ kind: 'book', slot: s })}>
                          {t('scheduling.book')}
                        </button>{' '}
                        <button type="button" disabled={s.status !== 'open'} aria-label={`${t('backfill.prepareShort')} ${s.slot_id.slice(0, 8)}`} onClick={() => setOfferFor(s)}>
                          {t('backfill.prepareShort')}
                        </button>
                      </>
                    ) : (
                      '–'
                    )}
                  </td>
                </tr>
              ))}
            </Table>
            {d.has_more && <p className="hint">{t('common.moreExist')}</p>}
          </>
        )}
      </QueryView>
      {offerFor && <BackfillPanel key={offerFor.slot_id} slotId={offerFor.slot_id} visitTypeId={offerFor.visit_type_id} onClose={() => setOfferFor(null)} />}
      {acting?.kind === 'hold' && (
        <HoldDialog
          slot={acting.slot}
          onClose={(ok) => {
            setActing(null);
            if (ok) {
              setDone(t('scheduling.holdDone'));
              void queryClient.invalidateQueries();
            }
          }}
        />
      )}
      {acting?.kind === 'book' && (
        <BookDialog
          slot={acting.slot}
          onClose={(ok) => {
            setActing(null);
            if (ok) {
              setDone(t('scheduling.bookDone'));
              void queryClient.invalidateQueries();
            }
          }}
        />
      )}
      {adding && (
        <AddSlotDialog
          refs={refs}
          onClose={(ok) => {
            setAdding(false);
            if (ok) {
              setDone(t('scheduling.created'));
              void queryClient.invalidateQueries();
            }
          }}
        />
      )}
    </>
  );
}

function HoldDialog({ slot, onClose }: { slot: Slot; onClose: (ok: boolean) => void }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const [minutes, setMinutes] = useState('');
  const [patient, setPatient] = useState<string | null>(null);
  const n = Number(minutes);
  const valid = Number.isInteger(n) && n > 0;
  const hold = useIdempotentWrite(
    (_: void, key) => unwrap(api.holdSlot(slot.slot_id, { duration_minutes: n, ...(patient ? { patient_id: patient } : {}) }, { idempotencyKey: key })),
    () => onClose(true),
  );
  return (
    <ConfirmDialog
      title={t('scheduling.holdTitle')}
      confirmLabel={t('scheduling.hold')}
      busy={hold.isPending}
      confirmDisabled={!valid}
      onConfirm={() => hold.mutate()}
      onCancel={() => {
        hold.reset();
        onClose(false);
      }}
      status={<WriteProblem problem={hold.error} />}
    >
      <p>{t('scheduling.holdBody')}</p>
      {/* Nothing is filled in for the person: the length of a hold is theirs to enter. */}
      <label htmlFor="hold-minutes">{t('scheduling.holdMinutes')}</label>
      <input
        id="hold-minutes"
        type="number"
        min={1}
        step={1}
        inputMode="numeric"
        value={minutes}
        onChange={(e) => {
          setMinutes(e.target.value);
          hold.reset(); // a different request never reuses the old key
        }}
      />
      <p className="hint">{t('scheduling.holdPatient')}</p>
      <PatientPicker
        value={patient}
        onChange={(p) => {
          setPatient(p);
          hold.reset();
        }}
      />
    </ConfirmDialog>
  );
}

function BookDialog({ slot, onClose }: { slot: Slot; onClose: (ok: boolean) => void }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const [patient, setPatient] = useState<string | null>(null);
  const book = useIdempotentWrite(
    (patientId: string, key) => unwrap(api.bookSlot(slot.slot_id, patientId, { idempotencyKey: key })),
    () => onClose(true),
  );
  return (
    <ConfirmDialog
      title={t('scheduling.bookTitle')}
      confirmLabel={t('scheduling.book')}
      busy={book.isPending}
      confirmDisabled={patient === null}
      onConfirm={() => patient && book.mutate(patient)}
      onCancel={() => {
        book.reset();
        onClose(false);
      }}
      status={<WriteProblem problem={book.error} />}
    >
      <p>{t('scheduling.bookBody')}</p>
      <PatientPicker
        value={patient}
        onChange={(p) => {
          setPatient(p);
          book.reset();
        }}
      />
    </ConfirmDialog>
  );
}

// ---- adding reference data and slots ----------------------------------------------------------------------------------

type Refs = ReturnType<typeof useReferences>;

function AddSlotDialog({ refs, onClose }: { refs: Refs; onClose: (ok: boolean) => void }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active } = useSession();
  const [provider, setProvider] = useState('');
  const [location, setLocation] = useState('');
  const [visitType, setVisitType] = useState('');
  const [starts, setStarts] = useState('');
  const [ends, setEnds] = useState('');
  const complete = provider && location && visitType && clinicLocalToIso(starts) !== null && clinicLocalToIso(ends) !== null;
  const create = useIdempotentWrite(
    (_: void, key) =>
      unwrap(
        api.createSlot(
          { clinic_id: active.clinicId, provider_id: provider, location_id: location, visit_type_id: visitType, starts_at: clinicLocalToIso(starts) as string, ends_at: clinicLocalToIso(ends) as string },
          { idempotencyKey: key },
        ),
      ),
    () => onClose(true),
  );
  const pick = (set: (v: string) => void) => (e: { target: { value: string } }) => {
    set(e.target.value);
    create.reset();
  };
  return (
    <ConfirmDialog
      title={t('scheduling.addSlot')}
      confirmLabel={t('scheduling.add')}
      busy={create.isPending}
      confirmDisabled={!complete}
      onConfirm={() => create.mutate()}
      onCancel={() => {
        create.reset();
        onClose(false);
      }}
      status={<WriteProblem problem={create.error} conflict={t('scheduling.createConflict')} invalid={t('scheduling.createInvalid')} />}
    >
      <p>{t('scheduling.addSlotBody')}</p>
      <label>
        {t('scheduling.col.provider')}
        <select value={provider} onChange={pick(setProvider)}>
          <option value="">{t('scheduling.choose')}</option>
          {refs.providers?.map((p) => (
            <option key={p.provider_id} value={p.provider_id}>
              {p.display_name}
            </option>
          ))}
        </select>
      </label>
      <label>
        {t('scheduling.col.location')}
        <select value={location} onChange={pick(setLocation)}>
          <option value="">{t('scheduling.choose')}</option>
          {refs.locations?.map((l) => (
            <option key={l.location_id} value={l.location_id}>
              {l.name}
            </option>
          ))}
        </select>
      </label>
      <label>
        {t('scheduling.col.visitType')}
        <select value={visitType} onChange={pick(setVisitType)}>
          <option value="">{t('scheduling.choose')}</option>
          {refs.visitTypes?.map((v) => (
            <option key={v.visit_type_id} value={v.visit_type_id}>
              {v.name}
            </option>
          ))}
        </select>
      </label>
      <ZoneNote />
      <label>
        {t('scheduling.startsAt')}
        <input type="datetime-local" value={starts} onChange={pick(setStarts)} />
      </label>
      <label>
        {t('scheduling.endsAt')}
        <input type="datetime-local" value={ends} onChange={pick(setEnds)} />
      </label>
    </ConfirmDialog>
  );
}

/** "Add a provider / location / visit type": one button, one confirmation that holds the fields. Front desk and admin. */
export function AddReference({ kind }: { kind: 'provider' | 'location' | 'visitType' }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active, hasRole } = useSession();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const [minutes, setMinutes] = useState('');
  const [done, setDone] = useState(false);
  const length = Number(minutes);
  const complete = name.trim() !== '' && (kind !== 'visitType' || (Number.isInteger(length) && length > 0));
  const create = useIdempotentWrite(
    (_: void, key) => {
      const o = { idempotencyKey: key };
      if (kind === 'provider') return unwrap(api.createProvider({ clinic_id: active.clinicId, display_name: name.trim() }, o));
      if (kind === 'location') return unwrap(api.createLocation({ clinic_id: active.clinicId, name: name.trim() }, o));
      return unwrap(api.createVisitType({ clinic_id: active.clinicId, name: name.trim(), duration_minutes: length }, o));
    },
    () => {
      setOpen(false);
      setName('');
      setMinutes('');
      setDone(true);
      void queryClient.invalidateQueries();
    },
  );
  if (!hasRole(CAN_SET_UP)) return null;
  const title = t(kind === 'provider' ? 'scheduling.addProvider' : kind === 'location' ? 'scheduling.addLocation' : 'scheduling.addVisitType');
  const body = t(kind === 'provider' ? 'scheduling.addProviderBody' : kind === 'location' ? 'scheduling.addLocationBody' : 'scheduling.addVisitTypeBody');
  return (
    <>
      <p>
        <button
          type="button"
          onClick={() => {
            setDone(false);
            setOpen(true);
          }}
        >
          {title}
        </button>{' '}
        {done && <span role="status">{t('scheduling.created')}</span>}
      </p>
      {open && (
        <ConfirmDialog
          title={title}
          confirmLabel={t('scheduling.add')}
          busy={create.isPending}
          confirmDisabled={!complete}
          onConfirm={() => create.mutate()}
          onCancel={() => {
            create.reset();
            setOpen(false);
          }}
          status={<WriteProblem problem={create.error} />}
        >
          <p>{body}</p>
          <label htmlFor="ref-name">{t('scheduling.name')}</label>
          <input
            id="ref-name"
            type="text"
            autoComplete="off"
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              create.reset();
            }}
          />
          {kind === 'visitType' && (
            <>
              <label htmlFor="ref-minutes">{t('scheduling.durationMinutes')}</label>
              <input
                id="ref-minutes"
                type="number"
                min={1}
                step={1}
                inputMode="numeric"
                value={minutes}
                onChange={(e) => {
                  setMinutes(e.target.value);
                  create.reset();
                }}
              />
            </>
          )}
        </ConfirmDialog>
      )}
    </>
  );
}

// ---- bookings and cancel -----------------------------------------------------------------------------------------------

export function BookingsList() {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { hasRole } = useSession();
  const queryClient = useQueryClient();
  const refs = useReferences();
  const [cancelling, setCancelling] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<(components['schemas']['CancelOut'] & { visitTypeId: string | null }) | null>(null);
  const [preparing, setPreparing] = useState(false);
  const query = useApiQuery(['bookings'], () => unwrap(api.listBookings()));
  const cancel = useIdempotentWrite(
    (bookingId: string, key) => unwrap(api.cancelBooking(bookingId, { idempotencyKey: key })),
    (result) => {
      // Remember the freed slot's visit type: the offer run for that slot needs it, and the cancelled booking leaves the list.
      setOutcome({ ...result, visitTypeId: query.data?.items.find((b) => b.booking_id === result.booking_id)?.visit_type_id ?? null });
      setPreparing(false);
      setCancelling(null);
      void queryClient.invalidateQueries();
    },
  );
  const lang = useLang();
  return (
    <>
      <div className="toolbar">
        <Refresh onClick={() => void query.refetch()} busy={query.isFetching} />
      </div>
      {outcome && (
        <div role="status" className="state state-empty">
          <p>{t('scheduling.cancelDone')}</p>
          <h3>{t('scheduling.cancelCandidates')}</h3>
          {outcome.candidates.length === 0 ? (
            <p>{t('scheduling.noCandidates')}</p>
          ) : (
            <ol>
              {outcome.candidates.map((c) => (
                <li key={c.waitlist_id}>
                  {formatNumber(c.rank, lang)} · <ShortId id={c.patient_id} /> · <code>{c.priority_code}</code>
                </li>
              ))}
            </ol>
          )}
          {hasRole(CAN_SCHEDULE) && outcome.visitTypeId && !preparing && (
            <p>
              <button type="button" className="primary" onClick={() => setPreparing(true)}>
                {t('backfill.prepare')}
              </button>
            </p>
          )}
          {preparing && outcome.visitTypeId && <BackfillPanel slotId={outcome.slot_id} visitTypeId={outcome.visitTypeId} onClose={() => setPreparing(false)} />}
        </div>
      )}
      <QueryView query={query} isEmpty={(d) => d.items.length === 0} empty={t('scheduling.noBookings')}>
        {(d) => (
          <>
            <Table
              caption={t('scheduling.bookings')}
              head={[t('scheduling.col.starts'), t('scheduling.col.ends'), t('scheduling.col.patient'), t('scheduling.col.provider'), t('scheduling.col.location'), t('scheduling.col.visitType'), t('scheduling.col.action')]}
            >
              {d.items.map((b) => (
                <tr key={b.booking_id}>
                  <th scope="row">
                    <Time value={b.starts_at} />
                  </th>
                  <td>
                    <Time value={b.ends_at} />
                  </td>
                  <td>
                    <ShortId id={b.patient_id} />
                  </td>
                  <td>{nameOf(refs.providers, b.provider_id, (p) => p.provider_id, (p) => p.display_name)}</td>
                  <td>{nameOf(refs.locations, b.location_id, (l) => l.location_id, (l) => l.name)}</td>
                  <td>{nameOf(refs.visitTypes, b.visit_type_id, (v) => v.visit_type_id, (v) => v.name)}</td>
                  <td>
                    {hasRole(CAN_SCHEDULE) ? (
                      <button
                        type="button"
                        aria-label={`${t('scheduling.cancelBooking')} ${b.booking_id.slice(0, 8)}`}
                        onClick={() => {
                          setOutcome(null);
                          setCancelling(b.booking_id);
                        }}
                      >
                        {t('scheduling.cancelBooking')}
                      </button>
                    ) : (
                      '–'
                    )}
                  </td>
                </tr>
              ))}
            </Table>
            {d.has_more && <p className="hint">{t('common.moreExist')}</p>}
          </>
        )}
      </QueryView>
      {cancelling && (
        <ConfirmDialog
          title={t('scheduling.cancelTitle')}
          confirmLabel={t('scheduling.cancelBooking')}
          busy={cancel.isPending}
          onConfirm={() => cancel.mutate(cancelling)}
          onCancel={() => {
            cancel.reset();
            setCancelling(null);
          }}
          status={<WriteProblem problem={cancel.error} />}
        >
          <p>{t('scheduling.cancelBody')}</p>
          <PlaceholderNote>{t('scheduling.placeholderConfirm')}</PlaceholderNote>
        </ConfirmDialog>
      )}
    </>
  );
}

// ---- waitlist ------------------------------------------------------------------------------------------------------------

export function WaitlistList() {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { hasRole } = useSession();
  const queryClient = useQueryClient();
  const refs = useReferences();
  const lang = useLang();
  const [visitType, setVisitType] = useState('');
  const [joining, setJoining] = useState(false);
  const [done, setDone] = useState(false);
  const query = useApiQuery(['waitlist', visitType], () => unwrap(api.listWaitlist(visitType || undefined)));
  return (
    <>
      <div className="toolbar">
        <label>
          {t('scheduling.col.visitType')}
          <select value={visitType} onChange={(e) => setVisitType(e.target.value)}>
            <option value="">{t('scheduling.all')}</option>
            {refs.visitTypes?.map((v) => (
              <option key={v.visit_type_id} value={v.visit_type_id}>
                {v.name}
              </option>
            ))}
          </select>
        </label>
        <Refresh onClick={() => void query.refetch()} busy={query.isFetching} />
        {hasRole(CAN_SCHEDULE) && (
          <button
            type="button"
            onClick={() => {
              setDone(false);
              setJoining(true);
            }}
          >
            {t('scheduling.join')}
          </button>
        )}
      </div>
      {done && <p role="status">{t('scheduling.joinDone')}</p>}
      <QueryView query={query} isEmpty={(d) => d.items.length === 0} empty={t('scheduling.noWaitlist')}>
        {(d) => (
          <>
            <Table caption={t('scheduling.waitlist')} head={[t('scheduling.col.rank'), t('scheduling.col.patient'), t('scheduling.col.visitType'), t('scheduling.col.priority'), t('scheduling.col.joined')]}>
              {d.items.map((w) => (
                <tr key={w.waitlist_id}>
                  <th scope="row">{formatNumber(w.rank, lang)}</th>
                  <td>
                    <ShortId id={w.patient_id} />
                  </td>
                  <td>{nameOf(refs.visitTypes, w.visit_type_id, (v) => v.visit_type_id, (v) => v.name)}</td>
                  <td>
                    <code>{w.clinical_priority}</code>
                  </td>
                  <td>
                    <Time value={w.joined_at} />
                  </td>
                </tr>
              ))}
            </Table>
            {d.has_more && <p className="hint">{t('common.moreExist')}</p>}
          </>
        )}
      </QueryView>
      {joining && (
        <JoinDialog
          refs={refs}
          onClose={(ok) => {
            setJoining(false);
            if (ok) {
              setDone(true);
              void queryClient.invalidateQueries();
            }
          }}
        />
      )}
    </>
  );
}

function JoinDialog({ refs, onClose }: { refs: Refs; onClose: (ok: boolean) => void }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const [patient, setPatient] = useState<string | null>(null);
  const [visitType, setVisitType] = useState('');
  const [priority, setPriority] = useState('');
  const levels = useApiQuery(['priorityLevels', false], () => unwrap(api.listPriorityLevels(false)));
  const join = useIdempotentWrite(
    (_: void, key) => unwrap(api.joinWaitlist({ patient_id: patient as string, visit_type_id: visitType, clinical_priority: priority }, { idempotencyKey: key })),
    () => onClose(true),
  );
  const reset = () => join.reset();
  return (
    <ConfirmDialog
      title={t('scheduling.joinTitle')}
      confirmLabel={t('scheduling.join')}
      busy={join.isPending}
      confirmDisabled={!patient || !visitType || !priority}
      onConfirm={() => join.mutate()}
      onCancel={() => {
        join.reset();
        onClose(false);
      }}
      status={<WriteProblem problem={join.error} conflict={t('scheduling.joinConflict')} />}
    >
      <p>{t('scheduling.joinBody')}</p>
      <PatientPicker
        value={patient}
        onChange={(p) => {
          setPatient(p);
          reset();
        }}
      />
      <label>
        {t('scheduling.joinVisitType')}
        <select
          value={visitType}
          onChange={(e) => {
            setVisitType(e.target.value);
            reset();
          }}
        >
          <option value="">{t('scheduling.choose')}</option>
          {refs.visitTypes?.map((v) => (
            <option key={v.visit_type_id} value={v.visit_type_id}>
              {v.name}
            </option>
          ))}
        </select>
      </label>
      <label>
        {t('scheduling.joinPriority')}
        <select
          value={priority}
          onChange={(e) => {
            setPriority(e.target.value);
            reset();
          }}
        >
          <option value="">{t('scheduling.choose')}</option>
          {levels.data?.items.map((l) => (
            <option key={l.code} value={l.code}>
              {l.label}
            </option>
          ))}
        </select>
      </label>
    </ConfirmDialog>
  );
}

// ---- offers: the list, copy the text, mark delivered (the system sends nothing) -------------------------------------------

const OFFER_STATES: readonly OfferState[] = ['awaiting_delivery', 'delivered', 'expired', 'booked', 'released'];
type Offer = components['schemas']['OfferRowOut'];

/** The text of an offer: the message the send recorded, read by its id through the audited message read. No id means no text to copy. */
async function fetchOfferText(api: ReturnType<typeof useAuth>['api'], offer: Offer): Promise<string> {
  if (!offer.message_id) throw new ApiProblem('not_found');
  return (await unwrap(api.readMessage(offer.message_id))).body;
}

export function OffersList() {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { hasRole } = useSession();
  const queryClient = useQueryClient();
  const word = useWord();
  const [state, setState] = useState<OfferState | ''>('');
  const [marking, setMarking] = useState<string | null>(null);
  const [marked, setMarked] = useState(false);
  const [copied, setCopied] = useState<'ok' | 'manual' | null>(null);
  const [manualText, setManualText] = useState<string | null>(null);
  const query = useApiQuery(['offers', state], () => unwrap(api.listOffers(state || undefined)));

  const copy = useMutation<'ok' | 'manual', ApiProblem, Offer>({
    gcTime: 0,
    retry: false,
    mutationFn: async (offer) => {
      try {
        const text = await fetchOfferText(api, offer);
        try {
          await navigator.clipboard.writeText(text);
          setManualText(null);
          return 'ok';
        } catch {
          setManualText(text); // the browser refused: show it for the person to copy by hand
          return 'manual';
        }
      } catch (e) {
        throw toProblem(e);
      }
    },
    onSuccess: (r) => setCopied(r),
  });

  const mark = useIdempotentWrite(
    (holdId: string, key) => unwrap(api.markOfferDelivered(holdId, { idempotencyKey: key })),
    () => {
      setMarking(null);
      setMarked(true);
      void queryClient.invalidateQueries();
    },
  );

  return (
    <>
      <div className="toolbar">
        <div role="group" aria-label={t('scheduling.offerFilter')} className="segmented">
          <button type="button" aria-pressed={state === ''} onClick={() => setState('')}>
            {t('scheduling.all')}
          </button>
          {OFFER_STATES.map((s) => (
            <button key={s} type="button" aria-pressed={state === s} onClick={() => setState(s)}>
              {t(`scheduling.offerState.${s}`)}
            </button>
          ))}
        </div>
        <Refresh onClick={() => void query.refetch()} busy={query.isFetching} />
      </div>
      {marked && <p role="status">{t('scheduling.markDone')}</p>}
      {copy.isError &&
        (copy.error.kind === 'not_found' ? (
          <p role="status">{t('scheduling.copyNotFound')}</p>
        ) : (
          <p role="alert">
            <ProblemText kind={copy.error.kind} />
          </p>
        ))}
      {copied === 'ok' && <p role="status">{t('scheduling.copied')}</p>}
      {copied === 'manual' && manualText !== null && (
        <>
          <p role="status">{t('scheduling.copyManual')}</p>
          <textarea readOnly rows={5} value={manualText} aria-label={t('scheduling.copyText')} onFocus={(e) => e.currentTarget.select()} />
        </>
      )}
      <QueryView query={query} isEmpty={(d) => d.items.length === 0} empty={t('scheduling.noOffers')}>
        {(d) => (
          <>
            <Table
              caption={t('scheduling.offersListTitle')}
              head={[t('scheduling.col.offer'), t('scheduling.col.patient'), t('scheduling.col.state'), t('scheduling.col.heldUntil'), t('scheduling.col.delivered'), t('scheduling.col.channel'), t('scheduling.col.action')]}
            >
              {d.items.map((o) => (
                <tr key={o.hold_id}>
                  <th scope="row">
                    <ShortId id={o.hold_id} />
                  </th>
                  <td>{o.patient_id ? <ShortId id={o.patient_id} /> : '–'}</td>
                  <td>
                    <Badge tone={o.state === 'awaiting_delivery' ? 'warn' : o.state === 'delivered' ? 'ok' : 'neutral'}>{word('scheduling.offerState', o.state)}</Badge>
                  </td>
                  <td>
                    <Time value={o.expires_at} />
                  </td>
                  <td>
                    <Time value={o.delivered_at} />
                  </td>
                  <td>{o.channel ?? '–'}</td>
                  <td>
                    {o.message_id && (o.state === 'awaiting_delivery' || o.state === 'delivered') && (
                      <button
                        type="button"
                        disabled={copy.isPending}
                        aria-label={`${t('scheduling.copyText')} ${o.hold_id.slice(0, 8)}`}
                        onClick={() => {
                          setCopied(null);
                          setManualText(null);
                          copy.mutate(o);
                        }}
                      >
                        {t('scheduling.copyText')}
                      </button>
                    )}{' '}
                    {o.state === 'awaiting_delivery' && hasRole(CAN_SCHEDULE) && (
                      <button
                        type="button"
                        aria-label={`${t('scheduling.markDelivered')} ${o.hold_id.slice(0, 8)}`}
                        onClick={() => {
                          setMarked(false);
                          setMarking(o.hold_id);
                        }}
                      >
                        {t('scheduling.markDelivered')}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </Table>
            {d.has_more && <p className="hint">{t('common.moreExist')}</p>}
          </>
        )}
      </QueryView>
      {marking && (
        <ConfirmDialog
          title={t('scheduling.markTitle')}
          confirmLabel={t('scheduling.markDelivered')}
          busy={mark.isPending}
          onConfirm={() => mark.mutate(marking)}
          onCancel={() => {
            if (mark.error?.kind === 'conflict') void queryClient.invalidateQueries();
            mark.reset();
            setMarking(null);
          }}
          status={<WriteProblem problem={mark.error} conflict={t('scheduling.markConflict')} />}
        >
          <p>{t('scheduling.markBody')}</p>
        </ConfirmDialog>
      )}
    </>
  );
}
