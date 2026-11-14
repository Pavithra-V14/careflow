import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { components } from '../../shared/api/schema';
import { unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { useApiQuery, useIdempotentWrite } from '../hooks';
import { useSession } from '../session/SessionContext';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { Badge, PageHeading, ShortId, Time, useWord } from '../ui/bits';
import { QueryView } from '../ui/states';
import { WriteProblem } from '../ui/WriteProblem';

type Row = components['schemas']['StaffPatientRequestOut'];
type NewStatus = components['schemas']['PatientRequestStatusIn']['status'];
type Result = components['schemas']['PatientRequestStatusOut']['result'];

/**
 * Patients' requests about their own record (front desk): erasure, correction (the field NAME only, never a value) and nominee. The list
 * carries ids, a closed kind, the field name and a status, and no text. The buttons of a row are exactly the statuses the API lists in its `allowed_next` (nothing is hard-coded here); a row with none shows none. Changing a status asks for a confirmation, needs a recent second
 * factor (the step-up prompt) and tells the patient with an in-app notice. For an erasure the front desk may link the two-person erasure
 * request they started for the same patient; nothing here starts, approves or changes an erasure.
 */
export function PatientRequestsPage() {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active } = useSession();
  const word = useWord();
  const queryClient = useQueryClient();
  const [acting, setActing] = useState<{ row: Row; status: NewStatus } | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const query = useApiQuery(['patient-requests', active.clinicId], () => unwrap(api.listPatientRequests(active.clinicId)));
  return (
    <section aria-labelledby="preq-title">
      <PageHeading id="preq-title">{t('preq.title')}</PageHeading>
      <p className="hint">{t('preq.note')}</p>
      <div className="toolbar">
        <button type="button" onClick={() => void query.refetch()} disabled={query.isFetching}>
          {t('common.refresh')}
        </button>
      </div>
      {note && <p role="status">{note}</p>}
      <QueryView query={query} isEmpty={(d) => d.requests.length === 0} empty={t('preq.empty')}>
        {(d) => (
          <table>
            <caption className="visually-hidden">{t('preq.title')}</caption>
            <thead>
              <tr>
                <th scope="col">{t('preq.col.request')}</th>
                <th scope="col">{t('preq.col.patient')}</th>
                <th scope="col">{t('preq.col.kind')}</th>
                <th scope="col">{t('preq.col.field')}</th>
                <th scope="col">{t('preq.col.status')}</th>
                <th scope="col">{t('preq.col.received')}</th>
                <th scope="col">{t('preq.col.erasure')}</th>
                <th scope="col">{t('preq.col.action')}</th>
              </tr>
            </thead>
            <tbody>
              {d.requests.map((r) => (
                <tr key={r.request_id}>
                  <th scope="row">
                    <ShortId id={r.request_id} />
                  </th>
                  <td>
                    <ShortId id={r.patient_id} />
                  </td>
                  <td>{word('preq.kinds', r.kind)}</td>
                  <td>{r.field ? word('preq.fields', r.field) : '–'}</td>
                  <td>
                    <Badge tone={r.status === 'resolved' ? 'ok' : r.status === 'open' ? 'warn' : 'neutral'}>{word('preq.status', r.status)}</Badge>
                  </td>
                  <td>
                    <Time value={r.created_at} />
                  </td>
                  <td>{r.erasure_request_id ? <ShortId id={r.erasure_request_id} /> : '–'}</td>
                  <td>
                    {r.allowed_next.length === 0
                      ? '–'
                      : r.allowed_next.map((s) => (
                          <button
                            key={s}
                            type="button"
                            aria-label={`${t(`preq.action.${s}`)} ${r.request_id.slice(0, 8)}`}
                            onClick={() => {
                              setNote(null);
                              setActing({ row: r, status: s });
                            }}
                          >
                            {t(`preq.action.${s}`)}
                          </button>
                        ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </QueryView>
      {acting && (
        <StatusDialog
          row={acting.row}
          status={acting.status}
          onClose={(result) => {
            setActing(null);
            if (result) {
              setNote(t(`preq.result.${result}`));
              void queryClient.invalidateQueries();
            }
          }}
        />
      )}
    </section>
  );
}

function StatusDialog({ row, status, onClose }: { row: Row; status: NewStatus; onClose: (result: Result | null) => void }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const [link, setLink] = useState('');
  const isErasure = row.kind === 'erasure';
  // Only erasure requests of the same patient that are still waiting can be linked; nothing is linked unless the person picks one.
  const erasures = useApiQuery(['patient-requests-erasures'], () => unwrap(api.listErasureRequests('requested')), isErasure);
  const candidates = erasures.data?.items.filter((e) => e.patient_id === row.patient_id) ?? [];
  const write = useIdempotentWrite(
    (_: void, key) => unwrap(api.setPatientRequestStatus(row.request_id, status, link === '' ? null : link, { idempotencyKey: key })),
    (r) => onClose(r.result),
  );
  return (
    <ConfirmDialog
      title={t(`preq.confirmTitle.${status}`)}
      confirmLabel={t(`preq.action.${status}`)}
      busy={write.isPending}
      onConfirm={() => write.mutate()}
      onCancel={() => {
        write.reset();
        onClose(null);
      }}
      status={<WriteProblem problem={write.error} calm={{ invalid: t('preq.invalid'), not_found: t('preq.gone') }} />}
    >
      <p>
        <ShortId id={row.request_id} />
      </p>
      <p>{t('preq.confirmBody')}</p>
      {isErasure && candidates.length > 0 && (
        <>
          <label htmlFor="preq-link">{t('preq.link')}</label>
          <select
            id="preq-link"
            value={link}
            onChange={(e) => {
              setLink(e.target.value);
              write.reset(); // a different request never reuses the old key
            }}
          >
            <option value="">{t('preq.noLink')}</option>
            {candidates.map((e) => (
              <option key={e.request_id} value={e.request_id}>
                {e.request_id.slice(0, 8)}
              </option>
            ))}
          </select>
        </>
      )}
    </ConfirmDialog>
  );
}
