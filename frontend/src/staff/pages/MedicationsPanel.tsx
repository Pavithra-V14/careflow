import { useId, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { components } from '../../shared/api/schema';
import { clinicLocalToIso } from '../../shared/clinicTime';
import { unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { ZoneNote } from '../components/ZoneNote';
import { useApiQuery, useIdempotentWrite } from '../hooks';
import { useSession } from '../session/SessionContext';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { Badge, ShortId, Time } from '../ui/bits';
import { QueryView } from '../ui/states';
import { WriteProblem } from '../ui/WriteProblem';

type Row = components['schemas']['MedicationRowOut'];

// A patient's medication statements (nurse and doctor). Append-only: a correction or a stop is a NEW row that names the one it replaces, and
// nothing is edited or deleted. The code is shown exactly as the API sends it (the clinic's own vocabulary decides which codes exist); the
// console holds no list of medicines and gives no meaning to a code. A missing end time is shown as "none recorded", never as "ongoing".
// Nothing is pre-chosen for the person: no code, no verified answer, no start time.
const CODE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$/;

export function MedicationsPanel({ patientId }: { patientId: string }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active } = useSession();
  const [recording, setRecording] = useState<{ mode: 'record' | 'correct' | 'stop'; replaces: Row | null } | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const meds = useApiQuery(['medications', patientId], () => unwrap(api.listMedications(active.clinicId, patientId)));

  return (
    <section aria-labelledby="meds-title">
      <h3 id="meds-title">{t('medications.title')}</h3>
      <p className="hint">{t('medications.note')}</p>
      <div className="toolbar">
        <button
          type="button"
          onClick={() => {
            setNote(null);
            setRecording({ mode: 'record', replaces: null });
          }}
        >
          {t('medications.record')}
        </button>
      </div>
      {note && <p role="status">{note}</p>}
      <QueryView query={meds} isEmpty={(d) => d.medications.length === 0} empty={t('medications.empty')}>
        {(d) => (
          <table>
            <caption className="visually-hidden">{t('medications.title')}</caption>
            <thead>
              <tr>
                <th scope="col">{t('medications.col.code')}</th>
                <th scope="col">{t('medications.col.verified')}</th>
                <th scope="col">{t('medications.col.from')}</th>
                <th scope="col">{t('medications.col.to')}</th>
                <th scope="col">{t('medications.col.recorded')}</th>
                <th scope="col">{t('medications.col.state')}</th>
                <th scope="col">{t('medications.col.action')}</th>
              </tr>
            </thead>
            <tbody>
              {d.medications.map((m) => (
                <tr key={m.medication_id}>
                  <th scope="row">{m.code}</th>
                  <td>{m.verified ? t('common.yes') : t('common.no')}</td>
                  <td>
                    <Time value={m.valid_from} />
                  </td>
                  <td>{m.valid_to ? <Time value={m.valid_to} /> : t('medications.noEnd')}</td>
                  <td>
                    <Time value={m.recorded_at} />
                  </td>
                  <td>
                    {m.superseded ? <Badge tone="neutral">{t('medications.superseded')}</Badge> : t('medications.current')}
                    {m.supersedes_id && (
                      <>
                        {' '}
                        <span className="hint">
                          {t('medications.replaces')} <ShortId id={m.supersedes_id} />
                        </span>
                      </>
                    )}
                  </td>
                  <td>
                    {!m.superseded && (
                      <>
                        <button
                          type="button"
                          aria-label={`${t('medications.correct')} ${m.code}`}
                          onClick={() => {
                            setNote(null);
                            setRecording({ mode: 'correct', replaces: m });
                          }}
                        >
                          {t('medications.correct')}
                        </button>{' '}
                        <button
                          type="button"
                          aria-label={`${t('medications.stop')} ${m.code}`}
                          onClick={() => {
                            setNote(null);
                            setRecording({ mode: 'stop', replaces: m });
                          }}
                        >
                          {t('medications.stop')}
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
      {recording && (
        <RecordDialog
          patientId={patientId}
          replaces={recording.replaces}
          mode={recording.mode}
          onClose={(done) => {
            setRecording(null);
            if (done) setNote(t(recording.mode === 'stop' ? 'medications.stopDone' : 'medications.done'));
          }}
        />
      )}
    </section>
  );
}

function RecordDialog({ patientId, replaces, mode, onClose }: { patientId: string; replaces: Row | null; mode: 'record' | 'correct' | 'stop'; onClose: (done: boolean) => void }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active } = useSession();
  const queryClient = useQueryClient();
  const [code, setCode] = useState('');
  const [validFrom, setValidFrom] = useState('');
  const [validTo, setValidTo] = useState('');
  const [verified, setVerified] = useState<'yes' | 'no' | null>(null); // nothing chosen for the person
  const radio = useId();

  const stopping = mode === 'stop' && replaces !== null;
  // A stop is a new row for the SAME code and start as the row it replaces (shown, not typed); the end time and the verified answer are the person's.
  const fromIso = stopping ? (replaces as Row).valid_from : clinicLocalToIso(validFrom);
  const toIso = validTo === '' ? null : clinicLocalToIso(validTo);
  const codeOut = stopping ? (replaces as Row).code : code.trim();
  const valid = stopping ? toIso !== null && verified !== null : CODE.test(code.trim()) && fromIso !== null && (validTo === '' || toIso !== null) && verified !== null;

  const write = useIdempotentWrite(
    (_: void, key) =>
      unwrap(
        api.recordMedication(
          { clinic_id: active.clinicId, patient_id: patientId, code: codeOut, verified: verified === 'yes', valid_from: fromIso as string, valid_to: toIso, supersedes_id: replaces ? replaces.medication_id : null },
          { idempotencyKey: key },
        ),
      ),
    () => {
      void queryClient.invalidateQueries();
      onClose(true);
    },
  );
  const touch = <T,>(set: (v: T) => void) => (v: T) => {
    set(v);
    write.reset(); // a different statement is a different request: never reuse the old key
  };

  // 422: a code outside the clinic's list, or bad times. 503: the clinic's list of codes is not set up. 409: already replaced, or the key belongs to another row.
  const calm = { unavailable: t('medications.notSetUp'), conflict: t('medications.conflict'), invalid: t('medications.invalid') };
  return (
    <ConfirmDialog
      title={t(stopping ? 'medications.stopTitle' : replaces ? 'medications.correctTitle' : 'medications.recordTitle')}
      confirmLabel={t(stopping ? 'medications.stopSave' : 'medications.save')}
      busy={write.isPending}
      confirmDisabled={!valid}
      onConfirm={() => write.mutate()}
      onCancel={() => {
        write.reset();
        onClose(false);
      }}
      status={<WriteProblem problem={write.error} calm={calm} />}
    >
      <p>{t(stopping ? 'medications.stopBody' : replaces ? 'medications.correctBody' : 'medications.recordBody')}</p>
      {replaces && (
        <p>
          {t('medications.replaces')} <strong>{replaces.code}</strong> (<ShortId id={replaces.medication_id} />)
        </p>
      )}
      {stopping ? (
        <>
          <dl className="facts">
            <dt>{t('medications.col.code')}</dt>
            <dd>{(replaces as Row).code}</dd>
            <dt>{t('medications.validFrom')}</dt>
            <dd>
              <Time value={(replaces as Row).valid_from} />
            </dd>
          </dl>
          <fieldset>
            <legend>{t('medications.verified')}</legend>
            {(['yes', 'no'] as const).map((v) => (
              <label key={v} className="choice">
                <input type="radio" name={radio} checked={verified === v} onChange={() => touch(setVerified)(v)} />
                <span>{t(v === 'yes' ? 'common.yes' : 'common.no')}</span>
              </label>
            ))}
          </fieldset>
          <ZoneNote />
          <label htmlFor="med-to">{t('medications.stoppedAt')}</label>
          <input id="med-to" type="datetime-local" value={validTo} onChange={(e) => touch(setValidTo)(e.target.value)} />
          <p className="hint">{t('medications.stoppedAtHint')}</p>
        </>
      ) : (
        <>
      <label htmlFor="med-code">{t('medications.code')}</label>
      <input id="med-code" type="text" autoComplete="off" spellCheck={false} value={code} onChange={(e) => touch(setCode)(e.target.value)} />
      <p className="hint">{t('medications.codeHint')}</p>
      <fieldset>
        <legend>{t('medications.verified')}</legend>
        {(['yes', 'no'] as const).map((v) => (
          <label key={v} className="choice">
            <input type="radio" name={radio} checked={verified === v} onChange={() => touch(setVerified)(v)} />
            <span>{t(v === 'yes' ? 'common.yes' : 'common.no')}</span>
          </label>
        ))}
      </fieldset>
      <ZoneNote />
      <label htmlFor="med-from">{t('medications.validFrom')}</label>
      <input id="med-from" type="datetime-local" value={validFrom} onChange={(e) => touch(setValidFrom)(e.target.value)} />
      <label htmlFor="med-to">{t('medications.validTo')}</label>
      <input id="med-to" type="datetime-local" value={validTo} onChange={(e) => touch(setValidTo)(e.target.value)} />
      <p className="hint">{t('medications.validToHint')}</p>
        </>
      )}
    </ConfirmDialog>
  );
}
