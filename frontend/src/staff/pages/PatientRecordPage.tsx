import { useState } from 'react';
import { Link, useParams } from 'react-router';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { unwrap, type ApiProblem } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { useApiQuery, useIdempotentWrite } from '../hooks';
import { CAN_CONFIRM_IDENTITY } from '../sections';
import { useSession } from '../session/SessionContext';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { Badge, PageHeading, PlaceholderNote, Time, UUID, useWord } from '../ui/bits';
import { NotAvailableToRole, ProblemText, QueryView } from '../ui/states';
import { NotFoundPage } from './NotFoundPage';

export function PatientRecordPage() {
  const { patientId = '' } = useParams();
  return UUID.test(patientId) ? <RecordView patientId={patientId} /> : <NotFoundPage />;
}

function confirmProblem(problem: ApiProblem, conflict: string) {
  if (problem.kind === 'forbidden') return <NotAvailableToRole />;
  return <p role="alert">{problem.kind === 'conflict' ? conflict : <ProblemText kind={problem.kind} />}</p>;
}

function RecordView({ patientId }: { patientId: string }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { hasRole } = useSession();
  const word = useWord();
  const queryClient = useQueryClient();
  const query = useApiQuery(['patient', patientId], () => unwrap(api.openPatient(patientId)));
  const [confirming, setConfirming] = useState(false);

  const confirm = useIdempotentWrite(
    (_: void, key) => unwrap(api.confirmIdentity(patientId, { idempotencyKey: key })),
    () => {
      void queryClient.invalidateQueries();
      setConfirming(false);
    },
  );

  return (
    <section aria-labelledby="record-title">
      <PageHeading id="record-title">{t('patients.record')}</PageHeading>
      <p>
        <Link to="/patients">{t('patients.back')}</Link>
      </p>
      <QueryView query={query}>
        {(p) => (
          <>
            <dl className="facts">
              <dt>{t('patients.col.record')}</dt>
              <dd>
                <code>{p.patient_id}</code>
              </dd>
              <dt>{t('patients.col.verified')}</dt>
              <dd>{p.verified ? <Badge tone="ok">{t('patients.verified')}</Badge> : <Badge tone="warn">{t('patients.notVerified')}</Badge>}</dd>
              <dt>{t('patients.col.via')}</dt>
              <dd>{word('patients.via', p.registered_via)}</dd>
              <dt>{t('patients.latestEvent')}</dt>
              <dd>
                {p.latest_identity_event ? word('patients.event', p.latest_identity_event) : t('patients.noEvent')}
                {p.latest_identity_event_at && (
                  <>
                    {' · '}
                    <Time value={p.latest_identity_event_at} />
                  </>
                )}
              </dd>
            </dl>

            {p.merged_into && (
              <p role="status" className="state state-forbidden">
                {t('patients.mergedInto')} <Link to={`/patients/${p.merged_into}`}>{t('patients.openMerged')}</Link>
              </p>
            )}

            {hasRole(CAN_CONFIRM_IDENTITY) && !p.verified && !p.merged_into && (
              <div className="toolbar">
                <button type="button" onClick={() => setConfirming(true)}>
                  {t('patients.confirm')}
                </button>
              </div>
            )}

            {confirming && (
              <ConfirmDialog
                title={t('patients.confirmTitle')}
                confirmLabel={t('patients.confirm')}
                busy={confirm.isPending}
                onConfirm={() => confirm.mutate()}
                onCancel={() => {
                  confirm.reset();
                  setConfirming(false);
                }}
                status={confirm.error ? confirmProblem(confirm.error, t('patients.confirmConflict')) : undefined}
              >
                <p>{t('patients.confirmBody')}</p>
                <PlaceholderNote>{t('placeholder.identityChecks')}</PlaceholderNote>
              </ConfirmDialog>
            )}
          </>
        )}
      </QueryView>
    </section>
  );
}
