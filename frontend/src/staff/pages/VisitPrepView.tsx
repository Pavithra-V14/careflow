import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { components } from '../../shared/api/schema';
import { ApiProblem, toProblem, unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { Badge, ShortId, Time } from '../ui/bits';
import { ProblemText } from '../ui/states';

type Prep = components['schemas']['PatientPrepOut'];

/**
 * What the patient has marked on a visit's preparation checklist (nurse and doctor, read-only). The checklist text is the clinic's own,
 * shown exactly as sent as plain text; an item the patient has not marked is "not marked", never "not done". The read needs a recent second
 * factor (the backend checks again) and the owner's switch; a refusal or an off switch is said in fixed words. Nothing is kept: the answer
 * is held in this panel's state only, and goes when it is hidden.
 */
export function VisitPrepView({ visitId }: { visitId: string }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const [shown, setShown] = useState<Prep | null>(null);
  const read = useMutation<Prep, ApiProblem>({
    gcTime: 0,
    retry: false,
    mutationFn: async () => {
      try {
        return await unwrap(api.getStaffVisitPrep(visitId));
      } catch (e) {
        throw toProblem(e);
      }
    },
    onSuccess: (d) => setShown(d),
  });
  const problem = read.error;
  return (
    <section aria-labelledby={`prep-${visitId}`}>
      <h3 id={`prep-${visitId}`}>{t('visitPrep.title')}</h3>
      <p className="hint">{t('visitPrep.note')}</p>
      <div className="toolbar">
        {shown ? (
          <button type="button" onClick={() => setShown(null)}>
            {t('visitPrep.hide')}
          </button>
        ) : (
          <button type="button" disabled={read.isPending} onClick={() => read.mutate()}>
            {read.isPending ? t('common.working') : t('visitPrep.show')}
          </button>
        )}
      </div>
      {problem &&
        (problem.kind === 'unavailable' ? (
          <p role="status">{t('visitPrep.off')}</p>
        ) : problem.kind === 'forbidden' ? (
          <p role="status">{t('visitPrep.refused')}</p>
        ) : problem.kind === 'not_found' ? (
          <p role="status">{t('visitPrep.gone')}</p>
        ) : (
          <p role="alert">
            <ProblemText kind={problem.kind} />
          </p>
        ))}
      {shown && (
        <>
          <dl className="facts">
            <dt>{t('visitPrep.visit')}</dt>
            <dd>
              <ShortId id={shown.booking_id} />
            </dd>
            <dt>{t('visitPrep.visitType')}</dt>
            <dd>{shown.visit_type}</dd>
            <dt>{t('visitPrep.starts')}</dt>
            <dd>
              <Time value={shown.starts_at} />
            </dd>
          </dl>
          {shown.nothing_to_prepare || shown.items.length === 0 ? (
            <p role="status">{t('visitPrep.nothing')}</p>
          ) : (
            <table>
              <caption className="visually-hidden">{t('visitPrep.title')}</caption>
              <thead>
                <tr>
                  <th scope="col">{t('visitPrep.col.item')}</th>
                  <th scope="col">{t('visitPrep.col.state')}</th>
                  <th scope="col">{t('visitPrep.col.when')}</th>
                </tr>
              </thead>
              <tbody>
                {shown.items.map((i) => (
                  <tr key={i.item_id}>
                    <th scope="row" className="pre-wrap">
                      {i.text}
                    </th>
                    <td>
                      <Badge tone={i.done === true ? 'ok' : 'neutral'}>{i.done === true ? t('visitPrep.done') : i.done === false ? t('visitPrep.notDone') : t('visitPrep.notMarked')}</Badge>
                    </td>
                    <td>
                      <Time value={i.marked_at} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </section>
  );
}
