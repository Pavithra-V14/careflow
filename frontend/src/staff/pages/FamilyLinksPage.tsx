import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { components } from '../../shared/api/schema';
import { FAMILY_NO_RULES } from '../../patient/portal/PortalFamily';
import { unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { PatientPicker } from '../components/PatientPicker';
import { usePatientLabels } from '../components/PatientLabels';
import { useApiQuery, useIdempotentWrite } from '../hooks';
import { useSession } from '../session/SessionContext';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { Badge, PageHeading, ShortId, Time, useWord } from '../ui/bits';
import { QueryView } from '../ui/states';
import { WriteProblem } from '../ui/WriteProblem';

type Row = components['schemas']['StaffFamilyLinkOut'];
type Result = components['schemas']['FamilyLinkDecisionOut']['result'];
const REASON = /^[a-z][a-z0-9_]{0,59}$/;

/**
 * Family link requests of the clinic (front desk, with a recent second factor). The list carries ids and a closed type and status, never a
 * name. A VERIFY names the dependant's record (found with the patient search), says whether the linked person's consent was obtained
 * (nothing is pre-chosen), and the server checks the clinic's rule: the age is computed by the server from the stored date of birth and is
 * never sent from here. A REJECT carries one reason word. A verified link grants nothing in this build. While the owner's switch is off or the
 * clinic has not entered counsel's rules, the answer is a fixed-wording state and no decision can be recorded.
 */
export function FamilyLinksPage() {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active } = useSession();
  const word = useWord();
  const queryClient = useQueryClient();
  const labels = usePatientLabels();
  const [acting, setActing] = useState<{ row: Row; decision: 'verify' | 'reject' } | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const query = useApiQuery(['family-links', active.clinicId], () => unwrap(api.listStaffFamilyLinks(active.clinicId)));
  const off = query.isError && query.error.status === 503; // 503: the owner's switch is off, or the clinic has not entered its rules; a 502 or 504 is an ordinary failure
  return (
    <section aria-labelledby="fam-title">
      <PageHeading id="fam-title">{t('familyStaff.title')}</PageHeading>
      <p className="hint">{t('familyStaff.note')}</p>
      {off ? (
        <p role="status" className="state state-empty">
          {query.error?.detail === FAMILY_NO_RULES ? t('familyStaff.noRules') : t('familyStaff.off')}
        </p>
      ) : (
        <>
          <div className="toolbar">
            <button type="button" onClick={() => void query.refetch()} disabled={query.isFetching}>
              {t('common.refresh')}
            </button>
          </div>
          {note && <p role="status">{note}</p>}
          <QueryView query={query} isEmpty={(d) => d.links.length === 0} empty={t('familyStaff.empty')}>
            {(d) => (
              <>
                {labels.panel(d.links.flatMap((l) => [l.requester_patient_id, ...(l.dependant_patient_id ? [l.dependant_patient_id] : [])]))}
                <table>
                  <caption className="visually-hidden">{t('familyStaff.title')}</caption>
                  <thead>
                    <tr>
                      <th scope="col">{t('familyStaff.col.link')}</th>
                      <th scope="col">{t('familyStaff.col.type')}</th>
                      <th scope="col">{t('familyStaff.col.status')}</th>
                      <th scope="col">{t('familyStaff.col.requester')}</th>
                      <th scope="col">{t('familyStaff.col.dependant')}</th>
                      <th scope="col">{t('familyStaff.col.received')}</th>
                      <th scope="col">{t('familyStaff.col.action')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.links.map((l) => (
                      <tr key={l.link_id}>
                        <th scope="row">
                          <ShortId id={l.link_id} />
                        </th>
                        <td>{word('familyStaff.types', l.link_type)}</td>
                        <td>
                          <Badge tone={l.status === 'verified' ? 'ok' : l.status === 'requested' ? 'warn' : 'neutral'}>{word('familyStaff.status', l.status)}</Badge>
                        </td>
                        <td>{labels.cell(l.requester_patient_id)}</td>
                        <td>{l.dependant_patient_id ? labels.cell(l.dependant_patient_id) : '–'}</td>
                        <td>
                          <Time value={l.created_at} />
                        </td>
                        <td>
                          {l.status === 'requested' ? (
                            <>
                              <button type="button" aria-label={`${t('familyStaff.verify')} ${l.link_id.slice(0, 8)}`} onClick={() => { setNote(null); setActing({ row: l, decision: 'verify' }); }}>
                                {t('familyStaff.verify')}
                              </button>{' '}
                              <button type="button" aria-label={`${t('familyStaff.reject')} ${l.link_id.slice(0, 8)}`} onClick={() => { setNote(null); setActing({ row: l, decision: 'reject' }); }}>
                                {t('familyStaff.reject')}
                              </button>
                            </>
                          ) : (
                            '–'
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
          </QueryView>
        </>
      )}
      {acting && (
        <DecisionDialog
          row={acting.row}
          decision={acting.decision}
          onClose={(result: Result | null) => {
            setActing(null);
            if (result) {
              setNote(t(`familyStaff.result.${result}`));
              void queryClient.invalidateQueries();
            }
          }}
        />
      )}
    </section>
  );
}

function DecisionDialog({ row, decision, onClose }: { row: Row; decision: 'verify' | 'reject'; onClose: (result: Result | null) => void }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active } = useSession();
  const [dependant, setDependant] = useState<string | null>(null);
  const [consent, setConsent] = useState<'yes' | 'no' | ''>('');
  const [reason, setReason] = useState('');
  const verify = decision === 'verify';
  const valid = verify ? dependant !== null && consent !== '' : REASON.test(reason);
  const write = useIdempotentWrite(
    (_: void, key) =>
      unwrap(
        api.decideFamilyLink(
          row.link_id,
          verify ? { clinic_id: active.clinicId, decision: 'verify', dependant_patient_id: dependant, dependant_consent: consent === 'yes' } : { clinic_id: active.clinicId, decision: 'reject', reason },
          { idempotencyKey: key },
        ),
      ),
    (r) => onClose(r.result),
  );
  const rulesMissing = write.error?.status === 503 && write.error.detail === FAMILY_NO_RULES;
  return (
    <ConfirmDialog
      title={t(verify ? 'familyStaff.verifyTitle' : 'familyStaff.rejectTitle')}
      confirmLabel={t(verify ? 'familyStaff.verify' : 'familyStaff.reject')}
      busy={write.isPending}
      confirmDisabled={!valid}
      onConfirm={() => write.mutate()}
      onCancel={() => {
        write.reset();
        onClose(null);
      }}
      status={
        rulesMissing ? (
          <p role="status">{t('familyStaff.noRules')}</p>
        ) : (
          <WriteProblem problem={write.error} forbidden={t('familyStaff.refused')} calm={{ not_found: t('familyStaff.gone'), unavailable: t('familyStaff.off'), invalid: t('familyStaff.invalid') }} />
        )
      }
    >
      <p>
        <ShortId id={row.link_id} />
      </p>
      <p>{t(verify ? 'familyStaff.verifyBody' : 'familyStaff.rejectBody')}</p>
      {verify ? (
        <>
          <PatientPicker
            value={dependant}
            onChange={(p) => {
              setDependant(p);
              write.reset(); // a different record is a different request: never reuse the old key
            }}
          />
          <fieldset>
            <legend>{t('familyStaff.consent')}</legend>
            {(['yes', 'no'] as const).map((v) => (
              <label key={v} className="choice">
                <input
                  type="radio"
                  name="fam-consent"
                  value={v}
                  checked={consent === v}
                  onChange={() => {
                    setConsent(v);
                    write.reset();
                  }}
                />
                <span>{t(`familyStaff.consentChoice.${v}`)}</span>
              </label>
            ))}
          </fieldset>
        </>
      ) : (
        <>
          <label htmlFor="fam-reason">{t('familyStaff.reason')}</label>
          <input
            id="fam-reason"
            type="text"
            autoComplete="off"
            spellCheck={false}
            value={reason}
            aria-describedby="fam-reason-hint"
            onChange={(e) => {
              setReason(e.target.value);
              write.reset();
            }}
          />
          <p id="fam-reason-hint" className="hint">
            {t('familyStaff.reasonHint')}
          </p>
        </>
      )}
    </ConfirmDialog>
  );
}
