import { useId, useState } from 'react';
import { Link } from 'react-router';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { ErasureStatus } from '../../shared/api/client';
import type { components } from '../../shared/api/schema';
import { unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { PatientPicker } from '../components/PatientPicker';
import { erasureActionsEnabled } from '../featureFlags';
import { useErasureReasons } from './erasureReasons';
import { useApiQuery, useIdempotentWrite } from '../hooks';
import { CAN_ERASE } from '../sections';
import { useSession } from '../session/SessionContext';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { Badge, PageHeading, PlaceholderNote, ShortId, Time, useWord } from '../ui/bits';
import { NotAvailableToRole, QueryView } from '../ui/states';
import { WriteProblem } from '../ui/WriteProblem';

type Row = components['schemas']['ErasureRequestRow'];

/**
 * Two-person erasure of a patient's identity (front desk). One person asks, a DIFFERENT person approves. Every write needs a recently
 * verified second factor: the backend answers 401 and the console asks for the code (the same dialog as every step-up). The write
 * buttons are behind a flag that is OFF until the step-up claim spike is recorded; the list of requests is read-only and always shown.
 */
export function ErasurePage() {
  const { t } = useTranslation();
  const { hasRole } = useSession();
  return (
    <section aria-labelledby="erasure-title">
      <PageHeading id="erasure-title">{t('erasure.title')}</PageHeading>
      <p className="hint">{t('erasure.note')}</p>
      {hasRole(CAN_ERASE) ? <Body /> : <NotAvailableToRole />}
    </section>
  );
}

function Body() {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { account } = useSession();
  const id = useId();
  const word = useWord();
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<ErasureStatus | ''>('');
  const [acting, setActing] = useState<{ row: Row; kind: 'approve' | 'cancel' } | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const enabled = erasureActionsEnabled();
  const reasons = useErasureReasons();
  const query = useApiQuery(['erasure-requests', status], () => unwrap(api.listErasureRequests(status === '' ? undefined : status)));

  return (
    <>
      {!enabled && (
        <p role="status" className="state state-forbidden">
          {t('erasure.actionsOff')}
        </p>
      )}
      {note && <p role="status">{note}</p>}

      <h2>{t('erasure.listTitle')}</h2>
      <label htmlFor={`${id}-status`}>{t('erasure.statusFilter')}</label>
      <select id={`${id}-status`} value={status} onChange={(e) => setStatus(e.target.value as ErasureStatus | '')}>
        <option value="">{t('erasure.openRequests')}</option>
        <option value="requested">{t('erasure.status.requested')}</option>
        <option value="erased">{t('erasure.status.erased')}</option>
        <option value="cancelled">{t('erasure.status.cancelled')}</option>
      </select>
      <QueryView query={query} isEmpty={(d) => d.items.length === 0} empty={t('erasure.empty')}>
        {(d) => (
          <>
            <table>
              <caption className="visually-hidden">{t('erasure.listTitle')}</caption>
              <thead>
                <tr>
                  <th scope="col">{t('erasure.col.request')}</th>
                  <th scope="col">{t('erasure.col.patient')}</th>
                  <th scope="col">{t('erasure.col.reason')}</th>
                  <th scope="col">{t('erasure.col.status')}</th>
                  <th scope="col">{t('erasure.col.requestedBy')}</th>
                  <th scope="col">{t('erasure.col.created')}</th>
                  <th scope="col">{t('erasure.col.decided')}</th>
                  <th scope="col">{t('erasure.col.action')}</th>
                </tr>
              </thead>
              <tbody>
                {d.items.map((r) => {
                  const mine = r.requested_by === account.accountId;
                  return (
                    <tr key={r.request_id}>
                      <th scope="row">
                        <ShortId id={r.request_id} />
                      </th>
                      <td>
                        {r.status === 'erased' ? (
                          <ShortId id={r.patient_id} />
                        ) : (
                          <Link to={`/patients/${r.patient_id}`}>
                            <ShortId id={r.patient_id} />
                          </Link>
                        )}
                      </td>
                      <td>{word('erasure.reasonCode', r.reason)}</td>
                      <td>
                        <Badge tone={r.status === 'requested' ? 'warn' : r.status === 'erased' ? 'urgent' : 'neutral'}>{word('erasure.status', r.status)}</Badge>
                      </td>
                      <td>{mine ? t('erasure.you') : <ShortId id={r.requested_by} />}</td>
                      <td>
                        <Time value={r.created_at} />
                      </td>
                      <td>
                        <Time value={r.decided_at} />
                      </td>
                      <td>
                        {r.status !== 'requested' ? (
                          '–'
                        ) : !enabled ? (
                          t('erasure.off')
                        ) : (
                          <>
                            {mine ? (
                              <span>{t('erasure.otherMustApprove')}</span>
                            ) : (
                              <button
                                type="button"
                                aria-label={`${t('erasure.approve')} ${r.request_id.slice(0, 8)}`}
                                onClick={() => {
                                  setNote(null);
                                  setActing({ row: r, kind: 'approve' });
                                }}
                              >
                                {t('erasure.approve')}
                              </button>
                            )}{' '}
                            <button
                              type="button"
                              aria-label={`${t('erasure.cancel')} ${r.request_id.slice(0, 8)}`}
                              onClick={() => {
                                setNote(null);
                                setActing({ row: r, kind: 'cancel' });
                              }}
                            >
                              {t('erasure.cancel')}
                            </button>
                          </>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {d.has_more && <p className="hint">{t('erasure.hasMore')}</p>}
          </>
        )}
      </QueryView>

      <h2>{t('erasure.requestTitle')}</h2>
      {enabled ? (
        <RequestForm
          reasons={reasons}
          onDone={() => {
            setNote(t('erasure.requested'));
            void queryClient.invalidateQueries();
          }}
        />
      ) : (
        <p className="hint">{t('erasure.actionsOff')}</p>
      )}

      {acting && (
        <DecideDialog
          row={acting.row}
          kind={acting.kind}
          onClose={(done) => {
            setActing(null);
            if (done) {
              setNote(done === 'nothing' ? t('erasure.nothingNew') : t(acting.kind === 'approve' ? 'erasure.approved' : 'erasure.cancelled'));
              void queryClient.invalidateQueries();
            }
          }}
        />
      )}
    </>
  );
}

/** Ask for a patient's identity to be erased. Nothing is pre-selected; the reason comes only from the clinic's list. */
function RequestForm({ reasons, onDone }: { reasons: readonly string[] | null; onDone: () => void }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const id = useId();
  const word = useWord();
  const [patient, setPatient] = useState<string | null>(null);
  const [reason, setReason] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  const request = useIdempotentWrite(
    (v: { patient: string; reason: string }, key) => unwrap(api.requestErasure(v.patient, v.reason, { idempotencyKey: key })),
    () => {
      setConfirming(false);
      setPatient(null);
      setReason(null);
      onDone();
    },
  );

  if (reasons === null || reasons.length === 0) {
    return (
      <p role="status" className="state state-empty">
        {t('erasure.reasonsNotSetUp')}
      </p>
    );
  }
  return (
    <>
      <PatientPicker
        value={patient}
        onChange={(p) => {
          setPatient(p);
          request.reset();
        }}
      />
      <fieldset>
        <legend>{t('erasure.reason')}</legend>
        {reasons.map((r) => (
          <label key={r} className="choice">
            <input
              type="radio"
              name={id}
              value={r}
              checked={reason === r}
              onChange={() => {
                setReason(r);
                request.reset(); // another reason is another request: a new key
              }}
            />
            <span>{word('erasure.reasonCode', r)}</span>
          </label>
        ))}
      </fieldset>
      <p>
        <button type="button" disabled={patient === null || reason === null} onClick={() => setConfirming(true)}>
          {t('erasure.requestButton')}
        </button>
      </p>
      {confirming && patient !== null && reason !== null && (
        <ConfirmDialog
          title={t('erasure.requestConfirmTitle')}
          confirmLabel={t('erasure.requestButton')}
          busy={request.isPending}
          onConfirm={() => request.mutate({ patient, reason })}
          onCancel={() => {
            request.reset();
            setConfirming(false);
          }}
          status={
            <WriteProblem
              problem={request.error}
              calm={{ conflict: t('erasure.alreadyOpen'), invalid: t('erasure.reasonRefused'), unavailable: t('erasure.reasonsNotSetUp'), not_found: t('erasure.patientNotFound') }}
            />
          }
        >
          <ErasureFacts />
          <p>{t('erasure.requestBody')}</p>
        </ConfirmDialog>
      )}
    </>
  );
}

/** What an erasure removes and what it leaves, from docs/design/patient-erasure-and-update-narrowing.md. The wording awaits the privacy owner. */
function ErasureFacts() {
  const { t } = useTranslation();
  return (
    <>
      <p>{t('erasure.erasesBody')}</p>
      <p>{t('erasure.keepsBody')}</p>
      <PlaceholderNote>{t('placeholder.erasureWording')}</PlaceholderNote>
    </>
  );
}

function DecideDialog({ row, kind, onClose }: { row: Row; kind: 'approve' | 'cancel'; onClose: (done: 'done' | 'nothing' | null) => void }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const approve = kind === 'approve';
  const write = useIdempotentWrite(
    async (_: void, key) =>
      // true only when the route says it did it: `false` is a no-op (a replay, or the request was no longer open), never shown as done.
      approve ? (await unwrap(api.approveErasure(row.request_id, { idempotencyKey: key }))).erased : (await unwrap(api.cancelErasure(row.request_id, { idempotencyKey: key }))).cancelled,
    (done) => onClose(done ? 'done' : 'nothing'),
  );
  return (
    <ConfirmDialog
      title={t(approve ? 'erasure.approveTitle' : 'erasure.cancelTitle')}
      confirmLabel={t(approve ? 'erasure.approve' : 'erasure.cancel')}
      busy={write.isPending}
      onConfirm={() => write.mutate()}
      onCancel={() => {
        write.reset();
        onClose(null);
      }}
      status={
        <WriteProblem
          problem={write.error}
          calm={{ conflict: t(approve ? 'erasure.approveConflict' : 'erasure.cancelConflict') }}
          forbidden={t(approve ? 'erasure.approveForbidden' : 'erasure.cancelForbidden')}
        />
      }
    >
      <p>
        {t('erasure.requestIs')} <ShortId id={row.request_id} />
      </p>
      {approve ? (
        <>
          <p>
            <strong>{t('erasure.approveBody')}</strong>
          </p>
          <ErasureFacts />
        </>
      ) : (
        <p>{t('erasure.cancelBody')}</p>
      )}
      <p className="hint">{t('erasure.codeHint')}</p>
    </ConfirmDialog>
  );
}
