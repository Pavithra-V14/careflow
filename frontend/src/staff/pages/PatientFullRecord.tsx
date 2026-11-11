import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { components } from '../../shared/api/schema';
import { ApiProblem, toProblem, unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { PlaceholderNote } from '../ui/bits';
import { NotAvailableToRole, ProblemText } from '../ui/states';

type View = components['schemas']['PatientViewOut'];

/**
 * The decrypted record (name, date of birth, phone) for front desk, nurse and doctor of the patient's clinic. PENDING THE PRIVACY
 * OWNER'S CONFIRMATION before any real data (docs/frontend-status.md); the backend also refuses it outside the demo profile with
 * synthetic data (503), and the console says so plainly.
 *
 * Opened ONLY by a click: the open is an audited read (who, role, patient id; never the contents). The result is a mutation result,
 * not a query: never cached, never refetched on focus or reconnect, held in this component only, dropped by "Hide", by leaving the page
 * and by a role change (the component unmounts). Nothing here is written to storage, a URL or a log.
 */
export function PatientFullRecord({ patientId }: { patientId: string }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const [shown, setShown] = useState<View | null>(null);
  const open = useMutation<View, ApiProblem, void>({
    gcTime: 0,
    retry: false,
    mutationFn: async () => {
      try {
        return await unwrap(api.viewPatientRecord(patientId));
      } catch (e) {
        throw toProblem(e);
      }
    },
    onSuccess: (v) => setShown(v),
  });
  const problem = open.error;

  return (
    <>
      <h2>{t('patients.full.title')}</h2>
      <PlaceholderNote>{t('placeholder.fullRecordPending')}</PlaceholderNote>
      {shown === null ? (
        <>
          <p className="hint">{t('patients.full.audited')}</p>
          <div className="toolbar">
            <button type="button" disabled={open.isPending} onClick={() => open.mutate()}>
              {open.isPending ? t('common.working') : t('patients.full.open')}
            </button>
          </div>
        </>
      ) : (
        <>
          <dl className="facts">
            <dt>{t('patients.name')}</dt>
            <dd>{shown.name}</dd>
            <dt>{t('patients.dob')}</dt>
            <dd>{shown.date_of_birth}</dd>
            <dt>{t('patients.phone')}</dt>
            <dd>{shown.phone ?? t('patients.full.noPhone')}</dd>
          </dl>
          <div className="toolbar">
            <button
              type="button"
              onClick={() => {
                setShown(null);
                open.reset();
              }}
            >
              {t('patients.full.hide')}
            </button>
          </div>
        </>
      )}
      {problem &&
        (problem.kind === 'forbidden' ? (
          <NotAvailableToRole />
        ) : problem.kind === 'unavailable' ? (
          <p role="status" className="state state-forbidden">
            {t('patients.full.unavailable')}
          </p>
        ) : problem.kind === 'rate_limited' ? (
          <p role="status">{t('patients.full.rateLimited')}</p>
        ) : (
          <div role="alert" className="state state-error">
            <p>
              <ProblemText kind={problem.kind} />
            </p>
          </div>
        ))}
    </>
  );
}
