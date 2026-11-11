import { useId, useState, type FormEvent } from 'react';
import { Link } from 'react-router';
import { useTranslation } from 'react-i18next';
import type { components } from '../../shared/api/schema';
import { unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { useIdempotentWrite } from '../hooks';
import { useSession } from '../session/SessionContext';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { PlaceholderNote, ShortId } from '../ui/bits';
import { WriteProblem } from '../ui/WriteProblem';

type Outcome = components['schemas']['IngestOut'];

/**
 * Register a walk-in or resolve a person to an existing record (front desk). The route either matches an existing record, creates a
 * new one, or sends a possible match to a person: the outcome is shown as the route returned it and nothing is decided here. The form
 * starts empty, a confirmation comes first, and once saved the typed details are dropped from the page (only the record id stays,
 * as a link). The details are never put in a URL, a log or the cache.
 */
export function RegisterPatient() {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active } = useSession();
  const id = useId();
  const [name, setName] = useState('');
  const [dob, setDob] = useState('');
  const [phone, setPhone] = useState('');
  const [confirming, setConfirming] = useState(false);
  const [result, setResult] = useState<Outcome | null>(null);

  const register = useIdempotentWrite(
    (_: void, key) =>
      unwrap(api.registerPatient({ clinic_id: active.clinicId, name: name.trim(), dob, ...(phone.trim() ? { phone: phone.trim() } : {}) }, { idempotencyKey: key })),
    (r) => {
      setResult(r);
      setConfirming(false);
      // The person's details leave the page: only the record id (a link) remains.
      setName('');
      setDob('');
      setPhone('');
    },
  );

  const ready = name.trim() !== '' && dob !== '';
  const changed = () => register.reset(); // a different person is a different request: never reuse the old key

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!ready) return;
    setResult(null);
    setConfirming(true);
  }

  return (
    <>
      <h2>{t('patients.register.title')}</h2>
      <form onSubmit={submit} autoComplete="off" noValidate>
        <p className="hint">{t('patients.register.hint')}</p>
        <label htmlFor={`${id}-name`}>{t('patients.register.name')}</label>
        <input id={`${id}-name`} type="text" autoComplete="off" maxLength={200} value={name} onChange={(e) => { changed(); setName(e.target.value); }} required />
        <label htmlFor={`${id}-dob`}>{t('patients.register.dob')}</label>
        <input id={`${id}-dob`} type="date" autoComplete="off" value={dob} onChange={(e) => { changed(); setDob(e.target.value); }} required />
        <label htmlFor={`${id}-phone`}>{t('patients.register.phone')}</label>
        <input id={`${id}-phone`} type="tel" autoComplete="off" maxLength={40} value={phone} onChange={(e) => { changed(); setPhone(e.target.value); }} />
        <button type="submit" className="primary" disabled={!ready}>
          {t('patients.register.button')}
        </button>
      </form>

      {result && (
        <div role="status" className="state state-ok">
          <p>{t(`patients.register.outcome.${result.outcome}`, { defaultValue: t('patients.register.outcome.unknown') })}</p>
          <p>
            <Link to={`/patients/${result.patient_id}`}>
              {t('patients.openRecord')} <ShortId id={result.patient_id} />
            </Link>
          </p>
          {result.candidate_ids.length > 0 && (
            <>
              <p>{t('patients.register.candidates')}</p>
              <ul className="plain">
                {result.candidate_ids.map((c) => (
                  <li key={c}>
                    <Link to={`/patients/${c}`}>
                      {t('patients.openRecord')} <ShortId id={c} />
                    </Link>
                  </li>
                ))}
              </ul>
            </>
          )}
          <PlaceholderNote>{t('placeholder.registerOutcomes')}</PlaceholderNote>
        </div>
      )}

      {confirming && (
        <ConfirmDialog
          title={t('patients.register.confirmTitle')}
          confirmLabel={t('patients.register.button')}
          busy={register.isPending}
          onConfirm={() => register.mutate()}
          onCancel={() => {
            register.reset(); // what was typed stays in the form so it can be corrected
            setConfirming(false);
          }}
          status={
            <WriteProblem
              problem={register.error}
              calm={{
                conflict: t('patients.register.conflict'),
                unavailable: t('patients.register.unavailable'),
                invalid: t('patients.register.invalid'),
                not_found: t('patients.register.notFound'),
              }}
            />
          }
        >
          <p>{t('patients.register.confirmBody')}</p>
        </ConfirmDialog>
      )}
    </>
  );
}
