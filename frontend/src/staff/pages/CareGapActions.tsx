import { useId, useState } from 'react';
import { useSearchParams } from 'react-router';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { BarrierKind } from '../../shared/api/client';
import { unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { useIdempotentWrite } from '../hooks';
import { CAN_RECORD_CARE_GAP } from '../sections';
import { useSession } from '../session/SessionContext';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { UUID, useWord } from '../ui/bits';
import { WriteProblem } from '../ui/WriteProblem';

// Care-gap writes (nurse and doctor). The panel itself only reads; these are the four things a person can do about an item on it.
// Every write waits for a confirmation, carries a key that is reused on a retry, and shows the backend's refusals in fixed words.
// The source words are those the backend documents in careflow/care_gaps.py; a row from a source not listed here simply has no action.

const SOURCE_BARRIER = 'unresolved_barrier';
const SOURCE_SAMPLE = 'after_the_fact_review';
const SOURCE_RECURRENCE = 'overdue_recurrence';

/** The barrier types the route accepts (BarrierIn.barrier), in the schema's order. None is pre-selected. */
const BARRIER_KINDS: readonly BarrierKind[] = ['transport', 'cost', 'childcare', 'forgot', 'scheduling_conflict', 'other'];

/** A patient id handed over from the record or referral the person was just looking at (`?patient=`); anything that is not an id is ignored. */
export function usePatientHint(): string {
  const [params] = useSearchParams();
  const hint = params.get('patient') ?? '';
  return UUID.test(hint) ? hint.toLowerCase() : '';
}

function PatientField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const { t } = useTranslation();
  const id = useId();
  const bad = value.trim() !== '' && !UUID.test(value.trim());
  return (
    <>
      <label htmlFor={id}>{t('care.patientId')}</label>
      <input id={id} type="text" autoComplete="off" spellCheck={false} value={value} aria-invalid={bad || undefined} onChange={(e) => onChange(e.target.value)} />
      {bad && <p className="form-error">{t('care.patientIdBad')}</p>}
    </>
  );
}

/** "Record a barrier" and "Start an episode" for a patient. */
export function GapToolbarActions({ onDone }: { onDone: (message: string) => void }) {
  const { t } = useTranslation();
  const { hasRole } = useSession();
  const [open, setOpen] = useState<'barrier' | 'episode' | null>(null);
  if (!hasRole(CAN_RECORD_CARE_GAP)) return null;
  return (
    <>
      <button type="button" onClick={() => setOpen('barrier')}>
        {t('care.gaps.recordBarrier')}
      </button>
      <button type="button" onClick={() => setOpen('episode')}>
        {t('care.gaps.startEpisode')}
      </button>
      {open === 'barrier' && (
        <BarrierDialog
          onClose={(ok) => {
            setOpen(null);
            if (ok) onDone(t('care.gaps.barrierDone'));
          }}
        />
      )}
      {open === 'episode' && (
        <EpisodeDialog
          onClose={(ok) => {
            setOpen(null);
            if (ok) onDone(t('care.gaps.episodeDone'));
          }}
        />
      )}
    </>
  );
}

function BarrierDialog({ onClose }: { onClose: (ok: boolean) => void }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active } = useSession();
  const word = useWord();
  const queryClient = useQueryClient();
  const [patient, setPatient] = useState(usePatientHint());
  const [kind, setKind] = useState<BarrierKind | ''>('');
  const write = useIdempotentWrite(
    (_: void, key) => unwrap(api.recordBarrier({ clinic_id: active.clinicId, patient_id: patient.trim(), barrier: kind as BarrierKind }, { idempotencyKey: key })),
    () => {
      void queryClient.invalidateQueries();
      onClose(true);
    },
  );
  return (
    <ConfirmDialog
      title={t('care.gaps.recordBarrier')}
      confirmLabel={t('care.gaps.record')}
      busy={write.isPending}
      confirmDisabled={!UUID.test(patient.trim()) || kind === ''}
      onConfirm={() => write.mutate()}
      onCancel={() => {
        write.reset();
        onClose(false);
      }}
      status={<WriteProblem problem={write.error} />}
    >
      <p>{t('care.gaps.barrierBody')}</p>
      <PatientField
        value={patient}
        onChange={(v) => {
          setPatient(v);
          write.reset();
        }}
      />
      <label>
        {t('care.gaps.barrierKind')}
        <select
          value={kind}
          onChange={(e) => {
            setKind(e.target.value as BarrierKind | '');
            write.reset();
          }}
        >
          <option value="">{t('scheduling.choose')}</option>
          {BARRIER_KINDS.map((k) => (
            <option key={k} value={k}>
              {word('care.gaps.barrier', k)}
            </option>
          ))}
        </select>
      </label>
    </ConfirmDialog>
  );
}

function EpisodeDialog({ onClose }: { onClose: (ok: boolean) => void }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active } = useSession();
  const queryClient = useQueryClient();
  const [patient, setPatient] = useState(usePatientHint());
  const [code, setCode] = useState('');
  const [started, setStarted] = useState('');
  const write = useIdempotentWrite(
    (_: void, key) => unwrap(api.startEpisode({ clinic_id: active.clinicId, patient_id: patient.trim(), episode_code: code.trim(), started_on: started }, { idempotencyKey: key })),
    () => {
      void queryClient.invalidateQueries();
      onClose(true);
    },
  );
  const touch = (set: (v: string) => void) => (v: string) => {
    set(v);
    write.reset();
  };
  return (
    <ConfirmDialog
      title={t('care.gaps.startEpisode')}
      confirmLabel={t('care.gaps.start')}
      busy={write.isPending}
      confirmDisabled={!UUID.test(patient.trim()) || code.trim() === '' || started === ''}
      onConfirm={() => write.mutate()}
      onCancel={() => {
        write.reset();
        onClose(false);
      }}
      status={<WriteProblem problem={write.error} calm={{ not_found: t('care.gaps.episodeNotFound'), unavailable: t('care.gaps.episodeNotConfigured') }} />}
    >
      <p>{t('care.gaps.episodeBody')}</p>
      <PatientField value={patient} onChange={touch(setPatient)} />
      <label htmlFor="ep-code">{t('care.gaps.episodeCode')}</label>
      <input id="ep-code" type="text" autoComplete="off" value={code} onChange={(e) => touch(setCode)(e.target.value)} />
      <label htmlFor="ep-start">{t('care.gaps.startedOn')}</label>
      <input id="ep-start" type="date" value={started} onChange={(e) => touch(setStarted)(e.target.value)} />
    </ConfirmDialog>
  );
}

/** The one action an item on the panel offers, by its source: resolve a barrier, re-check a sample, or record when a rule was last done. */
export function RowAction({ source, itemId, onDone }: { source: string; itemId: string; onDone: (message: string) => void }) {
  const { t } = useTranslation();
  const { hasRole } = useSession();
  const [open, setOpen] = useState(false);
  if (!hasRole(CAN_RECORD_CARE_GAP)) return <>–</>;
  const label = source === SOURCE_BARRIER ? t('care.gaps.resolve') : source === SOURCE_SAMPLE ? t('care.gaps.recheck') : source === SOURCE_RECURRENCE ? t('care.gaps.markDone') : null;
  if (label === null) return <>–</>;
  return (
    <>
      <button type="button" aria-label={`${label} ${itemId.slice(0, 8)}`} onClick={() => setOpen(true)}>
        {label}
      </button>
      {open && source === SOURCE_RECURRENCE && (
        <RuleDoneDialog
          ruleId={itemId}
          onClose={(ok) => {
            setOpen(false);
            if (ok) onDone(t('care.gaps.doneRecorded'));
          }}
        />
      )}
      {open && source !== SOURCE_RECURRENCE && (
        <OneShotDialog
          source={source}
          itemId={itemId}
          onClose={(ok) => {
            setOpen(false);
            if (ok) onDone(t(source === SOURCE_BARRIER ? 'care.gaps.resolved' : 'care.gaps.rechecked'));
          }}
        />
      )}
    </>
  );
}

function OneShotDialog({ source, itemId, onClose }: { source: string; itemId: string; onClose: (ok: boolean) => void }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const queryClient = useQueryClient();
  const barrier = source === SOURCE_BARRIER;
  const write = useIdempotentWrite(
    async (_: void, key) => {
      if (barrier) await unwrap(api.resolveBarrier(itemId, { idempotencyKey: key }));
      else await unwrap(api.recheckSample(itemId, { idempotencyKey: key }));
    },
    () => {
      void queryClient.invalidateQueries();
      onClose(true);
    },
  );
  return (
    <ConfirmDialog
      title={t(barrier ? 'care.gaps.resolveTitle' : 'care.gaps.recheckTitle')}
      confirmLabel={t(barrier ? 'care.gaps.resolve' : 'care.gaps.recheck')}
      busy={write.isPending}
      onConfirm={() => write.mutate()}
      onCancel={() => {
        if (write.error?.kind === 'conflict') void queryClient.invalidateQueries();
        write.reset();
        onClose(false);
      }}
      // A re-check by the person who made the original decision is refused by the database (409): said in fixed words, with no override.
      status={<WriteProblem problem={write.error} calm={{ conflict: t(barrier ? 'care.gaps.alreadyResolved' : 'care.gaps.sameDecider'), not_found: t('care.gaps.itemGone') }} />}
    >
      <p>{t(barrier ? 'care.gaps.resolveBody' : 'care.gaps.recheckBody')}</p>
    </ConfirmDialog>
  );
}

function RuleDoneDialog({ ruleId, onClose }: { ruleId: string; onClose: (ok: boolean) => void }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const queryClient = useQueryClient();
  const [date, setDate] = useState('');
  const write = useIdempotentWrite(
    (_: void, key) => unwrap(api.markRuleDone(ruleId, date, { idempotencyKey: key })),
    () => {
      void queryClient.invalidateQueries();
      onClose(true);
    },
  );
  return (
    <ConfirmDialog
      title={t('care.gaps.markDone')}
      confirmLabel={t('care.gaps.record')}
      busy={write.isPending}
      confirmDisabled={date === ''}
      onConfirm={() => write.mutate()}
      onCancel={() => {
        write.reset();
        onClose(false);
      }}
      status={<WriteProblem problem={write.error} calm={{ conflict: t('care.gaps.dateRefused'), not_found: t('care.gaps.itemGone') }} />}
    >
      <p>{t('care.gaps.doneBody')}</p>
      <label htmlFor="rule-done">{t('care.gaps.doneOn')}</label>
      <input
        id="rule-done"
        type="date"
        value={date}
        onChange={(e) => {
          setDate(e.target.value);
          write.reset();
        }}
      />
    </ConfirmDialog>
  );
}
