import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { ClaimDecision } from '../../shared/api/client';
import type { components } from '../../shared/api/schema';

type ClaimResult = components['schemas']['ClaimResolveOut']['result'];
import { unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { PatientPicker } from '../components/PatientPicker';
import { useApiQuery, useIdempotentWrite } from '../hooks';
import { useSession } from '../session/SessionContext';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { PageHeading, ShortId, Time } from '../ui/bits';
import { QueryView } from '../ui/states';
import { WriteProblem } from '../ui/WriteProblem';

/**
 * Pending portal claims (front desk). The list holds an opaque request id and a time, nothing about the claimant, so the person
 * decides from what the claimant told them in person: they find the CONFIRMED record themselves and approve onto it, or reject.
 */
export function ClaimsPage() {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active } = useSession();
  const queryClient = useQueryClient();
  const [acting, setActing] = useState<{ id: string; decision: ClaimDecision } | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const query = useApiQuery(['portal-claims'], () => unwrap(api.listPortalClaims(active.clinicId)));
  return (
    <section aria-labelledby="claims-title">
      <PageHeading id="claims-title">{t('claims.title')}</PageHeading>
      <p className="hint">{t('claims.note')}</p>
      <div className="toolbar">
        <button type="button" onClick={() => void query.refetch()} disabled={query.isFetching}>
          {t('common.refresh')}
        </button>
      </div>
      {note && <p role="status">{note}</p>}
      <QueryView query={query} isEmpty={(d) => d.claims.length === 0} empty={t('claims.empty')}>
        {(d) => (
          <table>
            <caption className="visually-hidden">{t('claims.title')}</caption>
            <thead>
              <tr>
                <th scope="col">{t('claims.col.request')}</th>
                <th scope="col">{t('claims.col.received')}</th>
                <th scope="col">{t('claims.col.action')}</th>
              </tr>
            </thead>
            <tbody>
              {d.claims.map((c) => (
                <tr key={c.request_id}>
                  <th scope="row">
                    <ShortId id={c.request_id} />
                  </th>
                  <td>
                    <Time value={c.created_at} />
                  </td>
                  <td>
                    <button
                      type="button"
                      aria-label={`${t('claims.approve')} ${c.request_id.slice(0, 8)}`}
                      onClick={() => {
                        setNote(null);
                        setActing({ id: c.request_id, decision: 'approve' });
                      }}
                    >
                      {t('claims.approve')}
                    </button>{' '}
                    <button
                      type="button"
                      aria-label={`${t('claims.reject')} ${c.request_id.slice(0, 8)}`}
                      onClick={() => {
                        setNote(null);
                        setActing({ id: c.request_id, decision: 'reject' });
                      }}
                    >
                      {t('claims.reject')}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </QueryView>
      {acting && (
        <ResolveDialog
          requestId={acting.id}
          decision={acting.decision}
          onClose={(result) => {
            setActing(null);
            if (result) {
              setNote(t(`claims.result.${result}`));
              void queryClient.invalidateQueries();
            }
          }}
        />
      )}
    </section>
  );
}

function ResolveDialog({ requestId, decision, onClose }: { requestId: string; decision: ClaimDecision; onClose: (result: ClaimResult | null) => void }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const [patient, setPatient] = useState<string | null>(null);
  const approve = decision === 'approve';
  const write = useIdempotentWrite(
    (_: void, key) => unwrap(api.resolvePortalClaim(requestId, decision, approve ? patient : null, { idempotencyKey: key })),
    (r) => onClose(r.result),
  );
  return (
    <ConfirmDialog
      title={t(approve ? 'claims.approveTitle' : 'claims.rejectTitle')}
      confirmLabel={t(approve ? 'claims.approve' : 'claims.reject')}
      busy={write.isPending}
      confirmDisabled={approve && patient === null}
      onConfirm={() => write.mutate()}
      onCancel={() => {
        write.reset();
        onClose(null);
      }}
      status={<WriteProblem problem={write.error} calm={{ invalid: t('claims.needsPatient') }} />}
    >
      <p>{t(approve ? 'claims.approveBody' : 'claims.rejectBody')}</p>
      {approve && (
        <PatientPicker
          value={patient}
          onChange={(p) => {
            setPatient(p);
            write.reset(); // a different record is a different request: never reuse the old key
          }}
        />
      )}
    </ConfirmDialog>
  );
}
