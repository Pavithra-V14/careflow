import { useState } from 'react';
import { Link, useParams } from 'react-router';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { unwrap, type ApiProblem } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { useApiQuery, useIdempotentWrite } from '../hooks';
import { CAN_CONFIRM_IDENTITY, CAN_REVOKE_IDENTITY } from '../sections';
import { useSession } from '../session/SessionContext';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { Badge, PageHeading, PlaceholderNote, Time, UUID, useWord } from '../ui/bits';
import { NotAvailableToRole, ProblemText, QueryView } from '../ui/states';
import { NotFoundPage } from './NotFoundPage';
import { IdentityRevoke, type RevokeOutcome } from './IdentityRevoke';

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
  // Kept here, not in the revoke control: once revoked the record is unverified and that control is no longer rendered.
  const [revoked, setRevoked] = useState<RevokeOutcome | null>(null);

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

            {revoked && (
              // `on_record` means NOTHING NEW was recorded (a replayed key, or the record was already unverified): it must not be
              // shown as "revoked", and an unknown word is never read as success either. Both get a neutral box, not the green one.
              <div role="status" className={revoked === 'raised' || revoked === 'not_configured' ? 'state state-ok' : 'state'}>
                {revoked === 'raised' || revoked === 'not_configured' ? (
                  <>
                    <p>{t('patients.revoke.done')}</p>
                    <p>{t(`patients.revoke.task.${revoked}`)}</p>
                  </>
                ) : (
                  <p>{revoked === 'on_record' ? t('patients.revoke.task.on_record') : t('patients.revoke.accepted')}</p>
                )}
              </div>
            )}

            {/* Front desk only, and only for a record that is currently verified. The backend decides. */}
            {hasRole(CAN_REVOKE_IDENTITY) && p.verified && !p.merged_into && <IdentityRevoke patientId={patientId} onDone={setRevoked} />}

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
