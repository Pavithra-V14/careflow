import { useId, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { BreakGlassOutcome } from '../../shared/api/client';
import type { components } from '../../shared/api/schema';
import { ApiProblem, toProblem, unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { PatientPicker } from '../components/PatientPicker';
import { useApiQuery, useIdempotentWrite } from '../hooks';
import { CAN_SEARCH_PATIENTS } from '../sections';
import { useSession } from '../session/SessionContext';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { Badge, PageHeading, ShortId, Time, UUID, useWord } from '../ui/bits';
import { ProblemText, QueryView } from '../ui/states';
import { WriteProblem } from '../ui/WriteProblem';

type Grant = { grantId: string; expiresAt: string };
const REASON = /^[a-z][a-z0-9_]{0,59}$/;

/**
 * Break-glass access. A front desk, nurse or doctor with a fresh second factor opens a time-limited grant to read ONE patient's
 * messages, naming a reason CODE from the clinic's list. There is no free text anywhere: the code is a closed word, and the backend
 * refuses one that is not on the list. Every read is audited, and an administrator who did not open the grant reviews it afterwards.
 */
export function AccessPage() {
  const { t } = useTranslation();
  const { hasRole } = useSession();
  return (
    <section aria-labelledby="access-title">
      <PageHeading id="access-title">{t('access.title')}</PageHeading>
      <p className="hint">{t('access.note')}</p>
      {hasRole(['front_desk', 'nurse', 'doctor']) && <OpenGrant />}
      {hasRole(['admin']) && <ReviewGrants />}
    </section>
  );
}

// ---- open a grant and read under it ----------------------------------------------------------------------------------------------

function OpenGrant() {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active, hasRole } = useSession();
  const id = useId();
  const [opening, setOpening] = useState(false);
  const [grant, setGrant] = useState<Grant | null>(null);
  return (
    <>
      <h2>{t('access.openTitle')}</h2>
      <p>
        <button type="button" onClick={() => setOpening(true)}>
          {t('access.open')}
        </button>
      </p>
      {grant && <Reading grant={grant} onClosed={() => setGrant(null)} />}
      {opening && (
        <OpenDialog
          labelId={id}
          canSearch={hasRole(CAN_SEARCH_PATIENTS)}
          onClose={(g) => {
            setOpening(false);
            if (g) setGrant(g);
          }}
          clinicId={active.clinicId}
          api={api}
        />
      )}
    </>
  );
}

function OpenDialog({ clinicId, api, canSearch, labelId, onClose }: { clinicId: string; api: ReturnType<typeof useAuth>['api']; canSearch: boolean; labelId: string; onClose: (g: Grant | null) => void }) {
  const { t } = useTranslation();
  const [patient, setPatient] = useState<string | null>(null);
  const [typed, setTyped] = useState('');
  const [reason, setReason] = useState('');
  const patientId = canSearch ? patient : UUID.test(typed.trim()) ? typed.trim() : null;
  const valid = patientId !== null && REASON.test(reason.trim());
  const write = useIdempotentWrite(
    (_: void, key) => unwrap(api.openBreakGlass({ clinic_id: clinicId, patient_id: patientId as string, reason_code: reason.trim() }, { idempotencyKey: key })),
    (r) => onClose({ grantId: r.grant_id, expiresAt: r.expires_at }),
  );
  const touch = <T,>(set: (v: T) => void) => (v: T) => {
    set(v);
    write.reset(); // a different request never reuses the old key
  };
  return (
    <ConfirmDialog
      title={t('access.open')}
      confirmLabel={t('access.openConfirm')}
      busy={write.isPending}
      confirmDisabled={!valid}
      onConfirm={() => write.mutate()}
      onCancel={() => {
        write.reset();
        onClose(null);
      }}
      status={<WriteProblem problem={write.error} calm={{ invalid: t('access.reasonRefused'), unavailable: t('access.notConfigured'), not_found: t('access.patientNotFound') }} />}
    >
      <p>{t('access.openBody')}</p>
      <p className="hint">{t('admin.stepUp')}</p>
      {canSearch ? (
        <PatientPicker value={patient} onChange={touch(setPatient)} />
      ) : (
        <>
          <label htmlFor={`${labelId}-patient`}>{t('care.patientId')}</label>
          <input id={`${labelId}-patient`} type="text" autoComplete="off" spellCheck={false} value={typed} onChange={(e) => touch(setTyped)(e.target.value)} />
        </>
      )}
      <label htmlFor={`${labelId}-reason`}>{t('access.reasonCode')}</label>
      <input id={`${labelId}-reason`} type="text" autoComplete="off" spellCheck={false} value={reason} onChange={(e) => touch(setReason)(e.target.value)} />
      <p className="hint">{t('access.reasonHint')}</p>
    </ConfirmDialog>
  );
}

/** Reads the messages under an open grant, on a click. Each read is an audit row; the text is held only while shown and is never cached. */
function Reading({ grant, onClosed }: { grant: Grant; onClosed: () => void }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const word = useWord();
  const read = useMutation<components['schemas']['BreakGlassMessages'], ApiProblem, void>({
    gcTime: 0,
    retry: false,
    mutationFn: async () => {
      try {
        return await unwrap(api.readBreakGlass(grant.grantId));
      } catch (e) {
        throw toProblem(e);
      }
    },
  });
  return (
    <div className="state state-empty">
      <p role="status">
        {t('access.opened')} <Time value={grant.expiresAt} />
      </p>
      <p>
        <button type="button" disabled={read.isPending} onClick={() => read.mutate()}>
          {t('access.read')}
        </button>{' '}
        <button
          type="button"
          onClick={() => {
            read.reset(); // the messages are dropped with the panel
            onClosed();
          }}
        >
          {t('access.close')}
        </button>
      </p>
      {read.isError &&
        (read.error.kind === 'forbidden' ? (
          <p role="status">{t('access.windowClosed')}</p>
        ) : read.error.kind === 'unavailable' ? (
          <p role="status">{t('access.keysMissing')}</p>
        ) : (
          <p role="alert">
            <ProblemText kind={read.error.kind} />
          </p>
        ))}
      {read.isSuccess &&
        (read.data.messages.length === 0 ? (
          <p>{t('access.noMessages')}</p>
        ) : (
          <ol className="transcript" aria-label={t('access.messages')}>
            {read.data.messages.map((m) => (
              <li key={m.message_id}>
                <span className="hint">
                  {word('access.sender', m.sender_kind)} · {word('access.topic', m.topic)} · <Time value={m.created_at} />
                </span>
                <br />
                {m.erased ? t('access.erased') : m.readable && m.body !== null ? m.body : t('access.unreadable')}
              </li>
            ))}
          </ol>
        ))}
    </div>
  );
}

// ---- admin review afterwards ----------------------------------------------------------------------------------------------------

function ReviewGrants() {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active } = useSession();
  const word = useWord();
  const queryClient = useQueryClient();
  const [reviewing, setReviewing] = useState<{ id: string; outcome: BreakGlassOutcome } | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const query = useApiQuery(['break-glass'], () => unwrap(api.listBreakGlass(active.clinicId, true)));
  const review = useIdempotentWrite(
    (v: { id: string; outcome: BreakGlassOutcome }, key) => unwrap(api.reviewBreakGlass(v.id, v.outcome, { idempotencyKey: key })),
    (r) => {
      setReviewing(null);
      setNote(t(`access.reviewResult.${r.result}`));
      void queryClient.invalidateQueries();
    },
  );
  return (
    <>
      <h2>{t('access.reviewTitle')}</h2>
      <p className="hint">{t('access.reviewNote')}</p>
      {note && <p role="status">{note}</p>}
      <QueryView query={query} isEmpty={(d) => d.grants.length === 0} empty={t('access.noGrants')}>
        {(d) => (
          <table>
            <caption className="visually-hidden">{t('access.reviewTitle')}</caption>
            <thead>
              <tr>
                <th scope="col">{t('access.col.grant')}</th>
                <th scope="col">{t('access.col.openedBy')}</th>
                <th scope="col">{t('access.col.patient')}</th>
                <th scope="col">{t('access.col.reason')}</th>
                <th scope="col">{t('access.col.opened')}</th>
                <th scope="col">{t('access.col.outcome')}</th>
                <th scope="col">{t('access.col.action')}</th>
              </tr>
            </thead>
            <tbody>
              {d.grants.map((g) => (
                <tr key={g.grant_id}>
                  <th scope="row">
                    <ShortId id={g.grant_id} />
                  </th>
                  <td>
                    <ShortId id={g.opened_by} />
                  </td>
                  <td>
                    <ShortId id={g.patient_id} />
                  </td>
                  <td>
                    <code>{g.reason_code}</code>
                  </td>
                  <td>
                    <Time value={g.opened_at} />
                  </td>
                  <td>{g.review_outcome ? <Badge tone={g.review_outcome === 'appropriate' ? 'ok' : 'warn'}>{word('access.outcome', g.review_outcome)}</Badge> : <Badge tone="neutral">{t('access.unreviewed')}</Badge>}</td>
                  <td>
                    {g.review_outcome ? (
                      '–'
                    ) : (
                      <>
                        <button type="button" aria-label={`${t('access.appropriate')} ${g.grant_id.slice(0, 8)}`} onClick={() => setReviewing({ id: g.grant_id, outcome: 'appropriate' })}>
                          {t('access.appropriate')}
                        </button>{' '}
                        <button type="button" aria-label={`${t('access.inappropriate')} ${g.grant_id.slice(0, 8)}`} onClick={() => setReviewing({ id: g.grant_id, outcome: 'inappropriate' })}>
                          {t('access.inappropriate')}
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
      {reviewing && (
        <ConfirmDialog
          title={t('access.reviewConfirmTitle')}
          confirmLabel={t(reviewing.outcome === 'appropriate' ? 'access.appropriate' : 'access.inappropriate')}
          busy={review.isPending}
          onConfirm={() => review.mutate(reviewing)}
          onCancel={() => {
            review.reset();
            setReviewing(null);
          }}
          status={<WriteProblem problem={review.error} />}
        >
          <p>{t('access.reviewBody')}</p>
        </ConfirmDialog>
      )}
    </>
  );
}

