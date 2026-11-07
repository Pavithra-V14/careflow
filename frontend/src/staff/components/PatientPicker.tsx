import { useId, useState, type FormEvent } from 'react';
import { useMutation } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { ApiProblem, toProblem, unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { useSession } from '../session/SessionContext';
import { Badge, ShortId } from '../ui/bits';
import { ProblemText } from '../ui/states';

/**
 * Chooses ONE patient for a write (book, hold, waitlist) by the same search the Patients page uses: name and date of birth.
 * Nothing is pre-selected, the search result is not cached, and only the chosen id leaves this component. The id is never shown
 * in full: the label (when the backend sends one) or a short id identifies the row.
 */
export function PatientPicker({ value, onChange }: { value: string | null; onChange: (patientId: string | null) => void }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active } = useSession();
  const id = useId();
  const [name, setName] = useState('');
  const [dob, setDob] = useState('');

  const search = useMutation<Awaited<ReturnType<typeof api.searchPatients>>['data'], ApiProblem, void>({
    gcTime: 0,
    retry: false,
    mutationFn: async () => {
      try {
        return await unwrap(api.searchPatients({ clinic_id: active.clinicId, name: name.trim(), dob }));
      } catch (e) {
        throw toProblem(e);
      }
    },
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    if (name.trim() === '' || dob === '') return;
    onChange(null); // a new search clears the earlier choice
    search.mutate();
  }

  const items = search.data?.items ?? [];
  return (
    <fieldset className="patient-picker">
      <legend>{t('scheduling.patientSearch')}</legend>
      <div className="inline-form">
        <label htmlFor={`${id}-name`}>{t('scheduling.patientName')}</label>
        <input id={`${id}-name`} type="text" autoComplete="off" value={name} onChange={(e) => setName(e.target.value)} />
        <label htmlFor={`${id}-dob`}>{t('scheduling.patientDob')}</label>
        <input id={`${id}-dob`} type="date" autoComplete="off" value={dob} onChange={(e) => setDob(e.target.value)} />
        <button type="button" disabled={search.isPending || name.trim() === '' || dob === ''} onClick={submit}>
          {search.isPending ? t('common.working') : t('scheduling.patientFind')}
        </button>
      </div>
      {search.error && (
        <p role="alert">
          <ProblemText kind={search.error.kind} />
        </p>
      )}
      {search.data && items.length === 0 && <p role="status">{t('scheduling.patientNone')}</p>}
      {items.length > 0 && (
        <div role="radiogroup" aria-label={t('scheduling.patientPick')}>
          {items.map((hit) => {
            const label = (hit as { label?: unknown }).label;
            return (
              <label key={hit.patient_id} className="choice">
                <input type="radio" name={`${id}-patient`} value={hit.patient_id} checked={value === hit.patient_id} onChange={() => onChange(hit.patient_id)} />
                <span>
                  {typeof label === 'string' && label.trim() !== '' ? label : <ShortId id={hit.patient_id} />}{' '}
                  {hit.verified ? <Badge tone="ok">{t('patients.verified')}</Badge> : <Badge tone="warn">{t('patients.notVerified')}</Badge>}
                </span>
              </label>
            );
          })}
        </div>
      )}
      {value && <p role="status">{t('scheduling.patientChosen')}</p>}
    </fieldset>
  );
}
